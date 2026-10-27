"""图片：把条目、季、集、人物引用的插件图片下载到本地缓存，清理不再引用的图片（ADR 0008、0022）。"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from iris_plugin_sdk import MetadataNotFound, MetadataUnavailable

from app.clock import Clock, to_iso
from app.db import Database
from app.errors import AppError, NotFound
from app.library.images import SOURCE_WIDTH, ImageError, ImageKind, ImageStore
from app.library.metadata_providers import MetadataProviders, image_key
from app.library.recognize import LibraryKind
from app.priority import Priority

# 管理员可以手动设置的条目图片类型
ARTWORK_KINDS = frozenset({"poster", "backdrop", "thumb", "logo"})
# 需要下载图片的列：（表，列名前缀，图片类型）。表名和列名都是固定的，不来自输入
_IMAGE_COLUMNS: tuple[tuple[str, str, ImageKind], ...] = (
    ("media_item", "poster", "poster"),
    ("media_item", "backdrop", "backdrop"),
    ("media_item", "thumb", "thumb"),
    ("media_item", "logo", "logo"),
    ("season", "poster", "poster"),
    ("episode", "still", "still"),
    ("person", "profile", "profile"),
)
# 每张表限定到指定资料库的条件
_IMAGE_SCOPE = {
    "media_item": "t.library_id IN ({marks})",
    "season": "t.media_item_id IN (SELECT id FROM media_item WHERE library_id IN ({marks}))",
    "episode": "t.season_id IN (SELECT s.id FROM season s JOIN media_item m "
    "ON m.id = s.media_item_id WHERE m.library_id IN ({marks}))",
    "person": "t.id IN (SELECT c.person_id FROM credit c JOIN media_item m "
    "ON m.id = c.media_item_id WHERE m.library_id IN ({marks}))",
}
# 图片来源属于哪个元数据插件：条目和人物记在自己身上，季和集跟随所属条目
_IMAGE_PROVIDER = {
    "media_item": "t.provider_id",
    "season": "(SELECT provider_id FROM media_item WHERE id = t.media_item_id)",
    "episode": "(SELECT m.provider_id FROM season s JOIN media_item m "
    "ON m.id = s.media_item_id WHERE s.id = t.season_id)",
    "person": "t.provider_id",
}


@dataclass(frozen=True)
class ImageTask:
    table: str
    column: str
    kind: ImageKind
    row_id: int
    provider_id: str
    path: str


class Artwork:
    def __init__(
        self, database: Database, providers: MetadataProviders, images: ImageStore
    ) -> None:
        self._database = database
        self._providers = providers
        self._images = images

    def pending(self, library_ids: Sequence[int]) -> list[ImageTask]:
        if not library_ids:
            return []
        marks = ", ".join("?" for _ in library_ids)
        tasks: list[ImageTask] = []
        with self._database.read_session() as connection:
            for table, column, kind in _IMAGE_COLUMNS:
                scope = _IMAGE_SCOPE[table].format(marks=marks)
                provider = _IMAGE_PROVIDER[table]
                if table == "media_item":
                    # 补缺选中的图片可能来自另一个插件，优先用它自己的获取器
                    provider = f"COALESCE(t.{column}_provider, t.provider_id)"
                rows = connection.execute(
                    f"SELECT t.id, {provider} AS provider_id, t.{column}_path AS path "
                    f"FROM {table} t "
                    f"WHERE t.{column}_path IS NOT NULL AND t.{column}_image_id IS NULL "
                    f"AND {scope} ORDER BY t.id",
                    tuple(library_ids),
                ).fetchall()
                tasks.extend(
                    ImageTask(table, column, kind, r["id"], r["provider_id"], r["path"])
                    for r in rows
                )
        return tasks

    def download(self, task: ImageTask, priority: Priority) -> None:
        """下载一张图片。插件那边已经没有这张图时清掉路径；内容不是图片时抛出 ImageError。

        插件停用时抛出 MetadataUnavailable；插件不提供图片时什么都不做。
        """
        key = image_key(task.provider_id, task.path)
        with self._database.session() as connection:
            image_id = self._images.find(connection, key)
            if image_id is not None:
                _link(connection, task, image_id)
                return
        source = self._providers.image_source(task.provider_id)
        if source is None:
            return
        try:
            data = source.image(task.path, SOURCE_WIDTH[task.kind], priority)
        except MetadataNotFound:
            with self._database.session() as connection:
                connection.execute(
                    f"UPDATE {task.table} SET {task.column}_path = NULL WHERE id = ?",
                    (task.row_id,),
                )
            return
        with self._database.session() as connection:
            image_id = self._images.save(connection, task.kind, key, data)
            _link(connection, task, image_id)

    def download_all(self, library_ids: Sequence[int], priority: Priority) -> None:
        """管理员操作后补齐图片；个别图片损坏或插件不可用时跳过。"""
        unavailable: set[str] = set()
        for task in self.pending(library_ids):
            if task.provider_id in unavailable:
                continue
            try:
                self.download(task, priority)
            except ImageError:
                continue
            except MetadataUnavailable:
                unavailable.add(task.provider_id)

    def cleanup(self) -> int:
        with self._database.session() as connection:
            return self._images.cleanup(connection)


def _link(connection: sqlite3.Connection, task: ImageTask, image_id: int) -> None:
    # 路径没变时才写入：下载期间条目可能被刷新成了另一张图
    connection.execute(
        f"UPDATE {task.table} SET {task.column}_image_id = ? "
        f"WHERE id = ? AND {task.column}_path = ?",
        (image_id, task.row_id, task.path),
    )


def check_artwork_kind(kind: str) -> None:
    if kind not in ARTWORK_KINDS:
        raise AppError(400, "invalid_artwork_kind", "不支持的图片类型")


@dataclass(frozen=True)
class ItemArtwork:
    id: int
    kind: LibraryKind
    provider_id: str
    remote_id: str
    # 这个类型的图片当前使用的来源，没有图片时为空
    current: str | None
    # 当前图片来自哪个插件；空表示沿用条目自己的插件
    current_provider: str | None


class ItemArtworkRepository:
    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        self._db = connection
        self._clock = clock

    def get(self, item_id: int, kind: str) -> ItemArtwork:
        check_artwork_kind(kind)
        row = self._db.execute(
            f"SELECT id, kind, provider_id, remote_id, {kind}_path AS current, "
            f"{kind}_provider AS current_provider FROM media_item WHERE id = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            raise NotFound("条目不存在")
        return ItemArtwork(
            row["id"],
            row["kind"],
            row["provider_id"],
            row["remote_id"],
            row["current"],
            row["current_provider"],
        )

    def store(
        self,
        images: ImageStore,
        item_id: int,
        kind: str,
        image_source: str,
        data: bytes,
        current: str | None,
    ) -> str:
        """保存图片并把条目指向它，字段锁定后刷新不会覆盖。current 为空时记为上传的图片。"""
        field = f"{kind}_path"
        image_id = images.save(self._db, cast(ImageKind, kind), image_source, data)
        current_value = current if current is not None else f"upload:{image_id}"
        self._db.execute(
            f"UPDATE media_item SET {field} = ?, {kind}_image_id = ? WHERE id = ?",
            (current_value, image_id, item_id),
        )
        self._db.execute(
            "INSERT INTO metadata_field(entity_kind,entity_id,field,source,locked,updated_at) "
            "VALUES ('item',?,?, 'manual',1,?) ON CONFLICT(entity_kind,entity_id,field) "
            "DO UPDATE SET source='manual',locked=1,updated_at=excluded.updated_at",
            (item_id, field, to_iso(self._clock.now())),
        )
        return current_value
