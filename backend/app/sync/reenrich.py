"""重新补全：管理员觉得某个条目的文件信息过时时，回到所属资源来源重新读取。"""

from __future__ import annotations

from dataclasses import dataclass

from iris_plugin_sdk import ResourceNotFound, ResourceUnavailable

from app.clock import Clock, to_iso
from app.db import Database
from app.errors import AppError
from app.library.catalog import CatalogRepository
from app.library.repository import LibraryRepository
from app.library.scraper import require_item
from app.resources.service import ResourceSources, file_key
from app.sync.files import FileEntry, MediaFileRepository
from app.sync.jobs import SyncJobRepository

# 条目下的视频，以及挂在这些视频上的字幕
_ITEM_FILES = """
    SELECT c.id, c.folder_id, c.rel_path FROM media_file c
    JOIN version v ON v.media_file_id = c.id JOIN playable p ON p.id = v.playable_id
    WHERE p.media_item_id = ?
    UNION
    SELECT c.id, c.folder_id, c.rel_path FROM media_file c
    JOIN subtitle s ON s.media_file_id = c.id JOIN version v ON v.id = s.version_id
    JOIN playable p ON p.id = v.playable_id
    WHERE p.media_item_id = ?
"""


@dataclass(frozen=True)
class ReenrichResult:
    updated: int
    # 来源上已经找不到、这次标记为失效的文件数
    missing: int


class Reenricher:
    def __init__(self, database: Database, clock: Clock, resources: ResourceSources) -> None:
        self._database = database
        self._clock = clock
        self._resources = resources

    def item(self, item_id: int) -> ReenrichResult:
        with self._database.session() as connection:
            if SyncJobRepository(connection, self._clock).running() is not None:
                # 同步也在写这些文件，并发补全会互相覆盖
                raise AppError(409, "sync_running", "同步进行中，等同步结束后再重新补全")
            require_item(connection, item_id)
            rows = connection.execute(_ITEM_FILES, (item_id, item_id)).fetchall()
            folders = {f.id: f for f in LibraryRepository(connection, self._clock).folders()}
            sources = ResourceSources.folders(connection, sorted(folders))

        updated = missing = 0
        for folder_id in {int(row["folder_id"]) for row in rows}:
            source = sources[folder_id]
            if not source.enabled:
                raise AppError(503, "resource_source_disabled", source.disabled_reason())
            try:
                entries = self._resources.open(source).scan(source.locator)
            except (ResourceUnavailable, ResourceNotFound) as exc:
                raise AppError(503, "resource_unavailable", exc.message) from exc
            by_path = {entry.relative_path: entry for entry in entries}
            now = to_iso(self._clock.now())
            with self._database.session() as connection:
                files = MediaFileRepository(connection, self._clock)
                for row in (row for row in rows if int(row["folder_id"]) == folder_id):
                    entry = by_path.get(str(row["rel_path"]))
                    if entry is None:
                        files.mark_missing(row["id"], now)
                        missing += 1
                    else:
                        files.set_active(
                            row["id"],
                            FileEntry(
                                identity=(
                                    entry.key
                                    or entry.identity
                                    or file_key(source.source_id, entry.locator)
                                ),
                                name=str(row["rel_path"]).rpartition("/")[2],
                                size=entry.size or 0,
                                playback_locator=entry.playback_locator or entry.locator,
                                sha1=entry.sha1 or "",
                            ),
                            now,
                        )
                        updated += 1
        with self._database.session() as connection:
            catalog = CatalogRepository(connection, self._clock, self._resources.local_subtitles())
            for folder_id in {int(row["folder_id"]) for row in rows}:
                info = catalog.folder(folder_id)
                catalog.place_folder(info.id, info.kind, info.root_name)
        return ReenrichResult(updated=updated, missing=missing)
