from __future__ import annotations

import hashlib
import io
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image, UnidentifiedImageError

from app.clock import Clock, to_iso

ImageKind = Literal["poster", "backdrop", "thumb", "logo", "still", "profile"]

# 每类图片从 TMDB 下载的尺寸和本地保存的各档宽度（ADR 0016）。第一档就是下载的尺寸，
# 其余由本地缩放：海报 342 用于海报墙、780 用于详情页；背景图 780 用于手机、1280 用于宽屏；
# 横版图用于首页的横向列表，片名图叠在详情页背景图上
SOURCE_WIDTH: dict[ImageKind, int] = {
    "poster": 780,
    "backdrop": 1280,
    "thumb": 780,
    "logo": 500,
    "still": 300,
    "profile": 185,
}
WIDTHS: dict[ImageKind, tuple[int, ...]] = {
    "poster": (780, 342),
    "backdrop": (1280, 780),
    "thumb": (780,),
    "logo": (500,),
    "still": (300,),
    "profile": (185,),
}
# 片名图要保留透明底，存成 PNG；其余都是 JPEG
PNG_KINDS: frozenset[ImageKind] = frozenset({"logo"})
JPEG_QUALITY = 85
# 文件名里的哈希长度（十六进制位数）；128 位足够避免冲突，也无法被枚举
HASH_LENGTH = 32


class ImageError(Exception):
    """下载到的不是可识别的图片。"""


class ImageStore:
    """本地图片缓存：按内容哈希命名，同一张 TMDB 图片只下载一次。

    文件路径形如 images/ab/abcdef…_342.jpg（片名图是 .png），按哈希前两位分子目录，
    避免单个目录文件过多。
    """

    def __init__(self, root: Path, clock: Clock) -> None:
        self.root = root
        self._clock = clock

    def find(self, connection: sqlite3.Connection, source: str) -> int | None:
        row = connection.execute("SELECT id FROM image WHERE source = ?", (source,)).fetchone()
        return None if row is None else int(row["id"])

    def save(
        self, connection: sqlite3.Connection, kind: ImageKind, source: str, data: bytes
    ) -> int:
        """写入各档尺寸的文件并登记，返回图片 ID。同一 source 已登记过时直接返回已有的。"""
        existing = self.find(connection, source)
        if existing is not None:
            return existing
        digest = hashlib.sha256(data).hexdigest()[:HASH_LENGTH]
        self._write_files(digest, kind, data)
        cursor = connection.execute(
            "INSERT INTO image (hash, kind, source, created_at) VALUES (?, ?, ?, ?)",
            (digest, kind, source, to_iso(self._clock.now())),
        )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def path(self, name: str) -> Path | None:
        """按文件名找到缓存文件；文件名不合法或文件不存在时返回 None。"""
        digest, _, rest = name.partition("_")
        width, _, extension = rest.partition(".")
        valid = (
            len(digest) == HASH_LENGTH
            and all(c in "0123456789abcdef" for c in digest)
            and width.isdigit()
            and extension in ("jpg", "png")
        )
        if not valid:
            return None
        path = self.root / digest[:2] / name
        return path if path.is_file() else None

    def cleanup(self, connection: sqlite3.Connection) -> int:
        """先删掉不再出演任何条目的人物，再删除没有引用的图片，返回删除的图片数。"""
        connection.execute("DELETE FROM person WHERE id NOT IN (SELECT person_id FROM credit)")
        rows = connection.execute(
            """
            SELECT id, hash, kind FROM image WHERE id NOT IN (
                SELECT poster_image_id FROM media_item WHERE poster_image_id IS NOT NULL
                UNION SELECT backdrop_image_id FROM media_item WHERE backdrop_image_id IS NOT NULL
                UNION SELECT thumb_image_id FROM media_item WHERE thumb_image_id IS NOT NULL
                UNION SELECT logo_image_id FROM media_item WHERE logo_image_id IS NOT NULL
                UNION SELECT poster_image_id FROM season WHERE poster_image_id IS NOT NULL
                UNION SELECT still_image_id FROM episode WHERE still_image_id IS NOT NULL
                UNION SELECT profile_image_id FROM person WHERE profile_image_id IS NOT NULL
                UNION SELECT cover_image_id FROM library WHERE cover_image_id IS NOT NULL
            )
            """
        ).fetchall()
        for row in rows:
            connection.execute("DELETE FROM image WHERE id = ?", (row["id"],))
        # 内容相同的图片共用文件，而不同类型的宽度档位不同：只保留剩下的图片还用得到的文件
        for digest in {row["hash"] for row in rows}:
            keep = {
                file_name(digest, width, kind["kind"])
                for kind in connection.execute(
                    "SELECT DISTINCT kind FROM image WHERE hash = ?", (digest,)
                )
                for width in WIDTHS[kind["kind"]]
            }
            for path in (self.root / digest[:2]).glob(f"{digest}_*"):
                if path.name not in keep:
                    path.unlink(missing_ok=True)
        return len(rows)

    def _write_files(self, digest: str, kind: ImageKind, data: bytes) -> None:
        try:
            image = Image.open(io.BytesIO(data))
            image.load()
        except (UnidentifiedImageError, OSError) as exc:
            raise ImageError("不是可识别的图片") from exc
        directory = self.root / digest[:2]
        directory.mkdir(parents=True, exist_ok=True)
        source_format = image.format
        png = kind in PNG_KINDS
        mode = "RGBA" if png else "RGB"
        converted: Image.Image = image if image.mode == mode else image.convert(mode)
        for index, width in enumerate(WIDTHS[kind]):
            target = directory / file_name(digest, width, kind)
            if target.exists():
                continue
            if index == 0 and source_format == ("PNG" if png else "JPEG"):
                # 下载的尺寸原样保存，避免二次压缩
                _atomic_write(target, data)
                continue
            resized = converted
            if converted.width > width:
                height = round(converted.height * width / converted.width)
                resized = converted.resize((width, height), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            if png:
                resized.save(buffer, "PNG", optimize=True)
            else:
                resized.save(buffer, "JPEG", quality=JPEG_QUALITY, optimize=True)
            _atomic_write(target, buffer.getvalue())


def file_name(digest: str, width: int, kind: ImageKind) -> str:
    return f"{digest}_{width}.{'png' if kind in PNG_KINDS else 'jpg'}"


# 图片地址的前缀。接口返回的都是相对服务器根的路径，客户端按自己连接的服务器地址拼接（ADR 0017）
IMAGE_URL_PREFIX = "/api/images/"


def image_url(digest: str, width: int, kind: ImageKind) -> str:
    return IMAGE_URL_PREFIX + file_name(digest, width, kind)


@dataclass(frozen=True)
class ImageSize:
    width: int
    url: str


def image_sizes(digest: str | None, kind: ImageKind) -> list[ImageSize]:
    """图片的各档尺寸，从小到大；还没下载时为空。"""
    if not digest:
        return []
    return [ImageSize(width, image_url(digest, width, kind)) for width in sorted(WIDTHS[kind])]


def _atomic_write(path: Path, data: bytes) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_bytes(data)
    os.replace(temp, path)
