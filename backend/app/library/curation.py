"""管理员整理资料库：手动指定匹配、拆分和改挂版本、刷新和重新匹配条目（ADR 0016）。

手动指定的结果（作品的 manual、版本的 manual）同步不会改动。每个操作结束时补拉新出现的季、
下载图片、清理不再引用的图片，管理员看到的就是最终结果。
"""

from __future__ import annotations

import logging
from contextlib import suppress

from iris_plugin_sdk import MetadataUnavailable

from app.clock import Clock, to_iso
from app.db import Database
from app.errors import AppError, NotFound
from app.library.catalog import CatalogRepository, FolderInfo, Target
from app.library.metadata import Metadata
from app.library.metadata_fields import ALL_METADATA_FIELDS, MetadataField
from app.library.metadata_providers import BoundProvider
from app.library.recognize import recognize
from app.library.scraper import require_item
from app.priority import Priority
from app.resources.service import ResourceSources

logger = logging.getLogger(__name__)

_PRIORITY = Priority.INTERACTIVE


class Curation:
    def __init__(
        self, database: Database, clock: Clock, metadata: Metadata, resources: ResourceSources
    ) -> None:
        self._database = database
        self._clock = clock
        self._metadata = metadata
        self._resources = resources

    def assign_work(self, work_id: int, remote_id: str, provider_id: str | None = None) -> int:
        """手动指定作品对应的插件条目，不指定插件时用资料库选择的插件。返回条目 ID。"""
        with self._database.session() as connection:
            row = connection.execute(
                "SELECT folder_id FROM work WHERE id = ?", (work_id,)
            ).fetchone()
            if row is None:
                raise NotFound("作品不存在")
            folder = CatalogRepository(connection, self._clock).folder(row["folder_id"])
        item_id = self._metadata.scraper.ensure_item(
            folder.library_id,
            folder.kind,
            self._provider(folder.library_id, provider_id),
            remote_id,
            _PRIORITY,
        )
        self._fill(item_id)
        self._metadata.matcher.assign(work_id, item_id)
        self._place(folder)
        self._finish(folder.library_id)
        return item_id

    def auto_assign_work(
        self, work_id: int, remote_id: str, provider_id: str, priority: Priority
    ) -> int | None:
        """AI 自动匹配：和规则匹配一样不标记手动。作品已不是未匹配状态时返回 None。"""
        with self._database.session() as connection:
            row = connection.execute(
                "SELECT folder_id FROM work WHERE id = ? AND state = 'unmatched' AND manual = 0",
                (work_id,),
            ).fetchone()
            if row is None:
                return None
            folder = CatalogRepository(connection, self._clock).folder(row["folder_id"])
        item_id = self._metadata.scraper.ensure_item(
            folder.library_id,
            folder.kind,
            self._provider(folder.library_id, provider_id),
            remote_id,
            priority,
        )
        if not self._metadata.matcher.auto_assign(work_id, item_id):
            return None
        self._place(folder)
        self._finish(folder.library_id)
        return item_id

    def unassign_auto_match(self, work_id: int) -> None:
        """撤销自动匹配：作品回到待处理，按规则重新匹配一次，得不到结果就留在未匹配。"""
        with self._database.session() as connection:
            row = connection.execute(
                "SELECT w.state, w.manual, w.folder_id FROM work w WHERE w.id = ?", (work_id,)
            ).fetchone()
            if row is None:
                raise NotFound("作品不存在")
            if row["state"] != "matched" or row["manual"]:
                raise AppError(409, "work_not_auto_matched", "只能撤销自动匹配的作品")
            folder = CatalogRepository(connection, self._clock).folder(row["folder_id"])
        matcher = self._metadata.matcher
        matcher.reset([work_id])
        pending = [w for w in matcher.pending_works([folder.library_id]) if w.id == work_id]
        if pending:
            # 元数据来源暂时不可用时留在待匹配，下次同步再试
            with suppress(MetadataUnavailable):
                matcher.match(pending[0], _PRIORITY)
        self._place(folder)
        self._finish(folder.library_id)

    def assign_file(
        self,
        media_file_id: int,
        remote_id: str,
        season: int | None,
        episode: int | None,
        provider_id: str | None = None,
    ) -> int:
        """把一个视频手动挂到指定的电影，或剧集的某一季某一集。返回条目 ID。"""
        with self._database.session() as connection:
            catalog = CatalogRepository(connection, self._clock)
            file = catalog.file(media_file_id)
            if file is None or file["kind"] != "video":
                raise NotFound("文件不存在")
            folder = catalog.folder(file["folder_id"])
        if folder.kind == "series" and (
            season is None or episode is None or season < 0 or episode < 1
        ):
            raise AppError(400, "episode_required", "剧集要指定季号（0 是特别篇）和集号")
        item_id = self._metadata.scraper.ensure_item(
            folder.library_id,
            folder.kind,
            self._provider(folder.library_id, provider_id),
            remote_id,
            _PRIORITY,
        )
        self._fill(item_id)
        target = Target(item_id) if folder.kind == "movie" else Target(item_id, season, episode)
        now = to_iso(self._clock.now())
        with self._database.session() as connection:
            catalog = CatalogRepository(connection, self._clock)
            file = catalog.file(media_file_id)
            if file is None:
                raise NotFound("文件不存在")
            connection.execute(
                "UPDATE media_file SET detached = 0, state = CASE WHEN state = 'ignored' "
                "THEN 'active' ELSE state END, ignore_reason = NULL, updated_at = ? WHERE id = ?",
                (now, media_file_id),
            )
            rec = recognize(folder.kind, file["rel_path"], folder.root_name)
            catalog.put_version(file, rec, target, manual=True, now=now)
            catalog.prune()
        self._place(folder)
        self._finish(folder.library_id)
        return item_id

    def ignore_file(self, media_file_id: int) -> None:
        """把视频移到已忽略列表；这是可逆的管理员整理操作。"""
        with self._database.session() as connection:
            row = connection.execute(
                "SELECT c.id, c.kind, c.state, v.id AS version_id FROM media_file c "
                "LEFT JOIN version v ON v.media_file_id = c.id WHERE c.id = ?",
                (media_file_id,),
            ).fetchone()
            if row is None or row["kind"] != "video":
                raise NotFound("文件不存在")
            if row["state"] != "active":
                raise AppError(409, "file_not_unplaced", "只能忽略未归入列表中的视频")
            connection.execute(
                "UPDATE media_file SET state = 'ignored', detached = 0, work_id = NULL, "
                "ignore_reason = ?, updated_at = ? WHERE id = ?",
                ("管理员手动忽略", to_iso(self._clock.now()), media_file_id),
            )
            if row["version_id"] is not None:
                connection.execute("DELETE FROM version WHERE id = ?", (row["version_id"],))
            CatalogRepository(connection, self._clock).prune()

    def restore_file(self, media_file_id: int) -> None:
        """撤销忽略并放回未归入列表，等待管理员指定或重新扫描。"""
        with self._database.session() as connection:
            row = connection.execute(
                "SELECT id, kind, state FROM media_file WHERE id = ?", (media_file_id,)
            ).fetchone()
            if row is None or row["kind"] != "video":
                raise NotFound("文件不存在")
            if row["state"] != "ignored":
                raise AppError(409, "file_not_ignored", "只能恢复已忽略的视频")
            connection.execute(
                "UPDATE media_file SET state = 'active', detached = 1, work_id = NULL, "
                "ignore_reason = NULL, updated_at = ? WHERE id = ?",
                (to_iso(self._clock.now()), media_file_id),
            )

    def rematch_work(self, work_id: int) -> int | None:
        """重新自动匹配一个未匹配作品，返回条目 ID；仍无唯一结果时返回 None。"""
        with self._database.session() as connection:
            row = connection.execute(
                "SELECT w.state, w.folder_id, f.library_id "
                "FROM work w JOIN library_folder f ON f.id = w.folder_id WHERE w.id = ?",
                (work_id,),
            ).fetchone()
            if row is None:
                raise NotFound("作品不存在")
            if row["state"] != "unmatched":
                raise AppError(409, "work_not_unmatched", "只能重新匹配未匹配的作品")
            folder = CatalogRepository(connection, self._clock).folder(row["folder_id"])

        matcher = self._metadata.matcher
        matcher.reset([work_id])
        pending = [
            work for work in matcher.pending_works([folder.library_id]) if work.id == work_id
        ]
        if not pending:
            raise NotFound("作品不存在")
        matched = matcher.match(pending[0], _PRIORITY) == "matched"
        self._place(folder)
        self._finish(folder.library_id)
        if not matched:
            return None
        with self._database.session() as connection:
            row = connection.execute(
                "SELECT media_item_id FROM work WHERE id = ?", (work_id,)
            ).fetchone()
        return row["media_item_id"] if row else None

    def detach_version(self, version_id: int) -> None:
        """把版本从条目上拆下来，文件进入待处理列表等手动指定。"""
        with self._database.session() as connection:
            row = connection.execute(
                "SELECT v.media_file_id, c.folder_id FROM version v "
                "JOIN media_file c ON c.id = v.media_file_id WHERE v.id = ?",
                (version_id,),
            ).fetchone()
            if row is None:
                raise NotFound("版本不存在")
            connection.execute("DELETE FROM version WHERE id = ?", (version_id,))
            connection.execute(
                "UPDATE media_file SET detached = 1, updated_at = ? WHERE id = ?",
                (to_iso(self._clock.now()), row["media_file_id"]),
            )
            folder = CatalogRepository(connection, self._clock).folder(row["folder_id"])
        self._place(folder)
        self._metadata.artwork.cleanup()

    def refresh_item(self, item_id: int) -> None:
        """重新拉取条目详情、演职员和全部季的信息，图片变了的重新下载。"""
        library_id = self._metadata.scraper.refresh_item(item_id, _PRIORITY)
        self._finish(library_id)

    def metadata(self, item_id: int) -> list[dict[str, object]]:
        """返回条目元数据当前值、来源和锁定状态。"""
        with self._database.read_session() as connection:
            item = connection.execute(
                "SELECT * FROM media_item WHERE id = ?", (item_id,)
            ).fetchone()
            if item is None:
                raise NotFound("条目不存在")
            rows = connection.execute(
                "SELECT field, source, locked, updated_at FROM metadata_field "
                "WHERE entity_kind = 'item' AND entity_id = ? ORDER BY field",
                (item_id,),
            ).fetchall()
            return [
                {
                    "field": row["field"],
                    "value": item[row["field"]],
                    "source": row["source"],
                    "locked": bool(row["locked"]),
                    "updated_at": row["updated_at"],
                }
                for row in rows
            ]

    def update_metadata(
        self, item_id: int, field: MetadataField, value: str | None, locked: bool
    ) -> None:
        """修改一项条目元数据，并决定后续刷新是否允许覆盖。"""
        if field not in ALL_METADATA_FIELDS:
            raise AppError(400, "invalid_metadata_field", "不支持锁定这个元数据字段")
        if field == "title" and not (value or "").strip():
            raise AppError(400, "invalid_metadata_value", "标题不能为空")
        now = to_iso(self._clock.now())
        with self._database.session() as connection:
            item = connection.execute(
                "SELECT id FROM media_item WHERE id = ?", (item_id,)
            ).fetchone()
            if item is None:
                raise NotFound("条目不存在")
            connection.execute(f"UPDATE media_item SET {field} = ? WHERE id = ?", (value, item_id))
            connection.execute(
                "INSERT INTO metadata_field(entity_kind, entity_id, field, source, locked, "
                "updated_at) "
                "VALUES ('item', ?, ?, 'manual', ?, ?) "
                "ON CONFLICT(entity_kind, entity_id, field) DO UPDATE SET source='manual', "
                "locked=excluded.locked, updated_at=excluded.updated_at",
                (item_id, field, int(locked), now),
            )

    def unlock_metadata(self, item_id: int, field: MetadataField) -> None:
        """解除一项元数据的锁定：当前值保留，下一次刷新按插件数据覆盖。"""
        if field not in ALL_METADATA_FIELDS:
            raise AppError(400, "invalid_metadata_field", "不支持锁定这个元数据字段")
        with self._database.session() as connection:
            require_item(connection, item_id)
            connection.execute(
                "UPDATE metadata_field SET locked = 0, updated_at = ? "
                "WHERE entity_kind = 'item' AND entity_id = ? AND field = ?",
                (to_iso(self._clock.now()), item_id, field),
            )

    def rematch_item(
        self, item_id: int, remote_id: str | None, provider_id: str | None = None
    ) -> int | None:
        """重新匹配条目下的作品。指定条目 ID 时按手动指定处理，否则清掉匹配结果重新自动匹配。

        指定条目 ID 但不指定插件时，用资料库当前选择的插件。

        返回新的条目 ID；自动匹配没有结果（作品进入待处理）时返回 None。
        """
        with self._database.session() as connection:
            item = require_item(connection, item_id)
            catalog = CatalogRepository(connection, self._clock)
            rows = connection.execute(
                "SELECT id, folder_id FROM work WHERE media_item_id = ?", (item_id,)
            ).fetchall()
            folders = [catalog.folder(f) for f in dict.fromkeys(r["folder_id"] for r in rows)]
        works = [row["id"] for row in rows]
        if not works:
            raise AppError(409, "no_work", "这个条目是逐个文件手动指定的，请在版本上改挂")
        matcher = self._metadata.matcher
        new_item: int | None = None
        if remote_id is not None:
            new_item = self._metadata.scraper.ensure_item(
                item["library_id"],
                item["kind"],
                self._provider(item["library_id"], provider_id),
                remote_id,
                _PRIORITY,
            )
            for work_id in works:
                matcher.assign(work_id, new_item)
        else:
            matcher.reset(works)
            for work in matcher.pending_works([item["library_id"]]):
                if work.id in works and matcher.match(work, _PRIORITY) == "matched":
                    with self._database.session() as connection:
                        row = connection.execute(
                            "SELECT media_item_id FROM work WHERE id = ?", (work.id,)
                        ).fetchone()
                    new_item = row["media_item_id"]
        if new_item is not None:
            # 手动指定或重新匹配后立刻补一次缺，失败只记日志，不影响匹配结果
            self._fill(new_item)
        for folder in folders:
            self._place(folder)
        self._finish(item["library_id"])
        return new_item

    def _fill(self, item_id: int) -> None:
        try:
            self._metadata.filler.fill_item(item_id, _PRIORITY)
        except Exception:
            logger.exception("补缺条目 %s 失败", item_id)

    def _provider(self, library_id: int, provider_id: str | None) -> BoundProvider:
        providers = self._metadata.providers
        return (
            providers.for_library(library_id) if provider_id is None else providers.get(provider_id)
        )

    def _place(self, folder: FolderInfo) -> None:
        with self._database.session() as connection:
            CatalogRepository(
                connection, self._clock, self._resources.local_subtitles()
            ).place_folder(folder.id, folder.kind, folder.root_name)

    def _finish(self, library_id: int) -> None:
        scraper = self._metadata.scraper
        # 其他插件的条目可能因插件停用而拉不到季信息，不影响这次操作，留给之后的同步
        unavailable: set[str] = set()
        for season_id, provider_id, remote_id, number in scraper.stale_seasons([library_id]):
            if provider_id in unavailable:
                continue
            try:
                scraper.scrape_season(season_id, provider_id, remote_id, number, _PRIORITY)
            except MetadataUnavailable:
                unavailable.add(provider_id)
        self._metadata.artwork.download_all([library_id], _PRIORITY)
        self._metadata.artwork.cleanup()
