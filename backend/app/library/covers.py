"""资料库封面：管理员上传的优先，没有时取最近入库、有背景图的条目，再退回海报。"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from app.errors import NotFound
from app.library.browse_rows import PLAYABLE
from app.library.images import ImageKind, ImageSize, ImageStore, image_sizes


@dataclass(frozen=True)
class LibraryCover:
    hash: str
    kind: ImageKind
    # 管理员上传的封面；False 表示自动选取
    custom: bool

    @property
    def sizes(self) -> list[ImageSize]:
        return image_sizes(self.hash, self.kind)


def library_cover(connection: sqlite3.Connection, library_id: int) -> LibraryCover | None:
    row = connection.execute(
        "SELECT i.hash, i.kind FROM library l JOIN image i ON i.id = l.cover_image_id "
        "WHERE l.id = ?",
        (library_id,),
    ).fetchone()
    if row is not None:
        return LibraryCover(row["hash"], cast(ImageKind, row["kind"]), True)
    for column in ("backdrop_image_id", "poster_image_id"):
        row = connection.execute(
            f"SELECT i.hash, i.kind FROM media_item m JOIN image i ON i.id = m.{column} "
            f"WHERE m.library_id = ? AND {PLAYABLE} "
            "ORDER BY m.added_at DESC, m.id DESC LIMIT 1",
            (library_id,),
        ).fetchone()
        if row is not None:
            return LibraryCover(row["hash"], cast(ImageKind, row["kind"]), False)
    return None


def library_covers(
    connection: sqlite3.Connection, library_ids: Sequence[int]
) -> dict[int, LibraryCover]:
    covers = {}
    for library_id in library_ids:
        cover = library_cover(connection, library_id)
        if cover is not None:
            covers[library_id] = cover
    return covers


def set_library_cover(
    connection: sqlite3.Connection, images: ImageStore, library_id: int, source: str, data: bytes
) -> None:
    """上传的封面按横版背景图的尺寸存；旧封面没有其他引用时由图片清理任务删除。"""
    _ensure_library(connection, library_id)
    image_id = images.save(connection, "backdrop", source, data)
    connection.execute("UPDATE library SET cover_image_id = ? WHERE id = ?", (image_id, library_id))


def clear_library_cover(connection: sqlite3.Connection, library_id: int) -> None:
    _ensure_library(connection, library_id)
    connection.execute("UPDATE library SET cover_image_id = NULL WHERE id = ?", (library_id,))


def _ensure_library(connection: sqlite3.Connection, library_id: int) -> None:
    if connection.execute("SELECT 1 FROM library WHERE id = ?", (library_id,)).fetchone() is None:
        raise NotFound("资料库不存在")
