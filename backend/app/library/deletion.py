"""删除条目前的规划：找出要删的云端目录和文件，确认没有别的条目共用，以及删除后的本地清理。"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from app.clock import Clock
from app.errors import AppError
from app.resources.repository import model_locator
from app.sync.files import MediaFileRepository

# 同一个目录或文件被其他条目引用的三种途径：视频版本、字幕、作品下的零散文件
_SHARED_BY_OTHER_ITEM = """
    SELECT DISTINCT p.media_item_id
    FROM media_file c
    JOIN version v ON v.media_file_id = c.id
    JOIN playable p ON p.id = v.playable_id
    WHERE c.folder_id = ? AND c.state != 'missing'
        AND (c.rel_path = ? OR c.rel_path LIKE ? ESCAPE '\\')
        AND p.media_item_id != ?
    UNION
    SELECT DISTINCT p.media_item_id
    FROM media_file c
    JOIN subtitle s ON s.media_file_id = c.id
    JOIN version v ON v.id = s.version_id
    JOIN playable p ON p.id = v.playable_id
    WHERE c.folder_id = ? AND c.state != 'missing'
        AND (c.rel_path = ? OR c.rel_path LIKE ? ESCAPE '\\')
        AND p.media_item_id != ?
    UNION
    SELECT DISTINCT w.media_item_id
    FROM media_file c
    JOIN work w ON w.id = c.work_id
    WHERE c.folder_id = ? AND c.state != 'missing'
        AND (c.rel_path = ? OR c.rel_path LIKE ? ESCAPE '\\')
        AND w.media_item_id != ?
    LIMIT 1
"""


def escape_like(value: str) -> str:
    """把来源里的路径转成 SQLite LIKE 的字面量前缀。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def file_folder_ids(connection: sqlite3.Connection, identities: Sequence[str]) -> set[int]:
    """这些来源文件所在的资料库目录。"""
    marks = ", ".join("?" for _ in identities) or "NULL"
    return {
        int(row[0])
        for row in connection.execute(
            f"SELECT DISTINCT folder_id FROM media_file WHERE identity IN ({marks})",
            tuple(identities),
        )
    }


@dataclass(frozen=True)
class ItemDeletePlan:
    # 涉及的资料库目录 ID -> 来源目录的定位符；None 表示来源根目录
    roots: dict[int, str | None]
    # 作品自己的目录：(资料库目录 ID, 相对路径)，整个目录一起删除
    own_dirs: list[tuple[int, str]]
    # 不在作品目录里的散放文件，逐个删除
    identities: list[str]


class ItemDeletionRepository:
    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        self._db = connection
        self._clock = clock

    def plan(self, item_id: int) -> ItemDeletePlan:
        if (
            self._db.execute("SELECT title FROM media_item WHERE id = ?", (item_id,)).fetchone()
            is None
        ):
            raise AppError(404, "not_found", "条目不存在")
        dirs = MediaFileRepository(self._db, self._clock).item_dirs(item_id)
        folder_rows = self._db.execute(
            "SELECT f.id, rr.locator FROM library_folder f "
            "JOIN resource_root rr ON rr.id = f.resource_root_id WHERE f.id IN ({})".format(
                ", ".join("?" for _ in dirs) or "NULL"
            ),
            tuple(folder_id for folder_id, _, _ in dirs),
        ).fetchall()
        roots = {int(folder["id"]): model_locator(folder["locator"]) for folder in folder_rows}
        own_dirs = [(folder_id, path) for folder_id, path, own in dirs if own and path]
        for folder_id, path in own_dirs:
            self._require_unshared(item_id, folder_id, path)
        files = self._db.execute(
            """
            SELECT DISTINCT c.identity, c.folder_id, c.rel_path FROM media_file c
            JOIN version v ON v.media_file_id = c.id
            JOIN playable p ON p.id = v.playable_id
            WHERE p.media_item_id = ? AND c.identity IS NOT NULL AND c.state = 'active'
            UNION
            SELECT DISTINCT c.identity, c.folder_id, c.rel_path FROM media_file c
            JOIN subtitle s ON s.media_file_id = c.id
            JOIN version v ON v.id = s.version_id
            JOIN playable p ON p.id = v.playable_id
            WHERE p.media_item_id = ? AND c.identity IS NOT NULL AND c.state = 'active'
            UNION
            SELECT DISTINCT c.identity, c.folder_id, c.rel_path FROM media_file c
            JOIN work w ON w.id = c.work_id
            WHERE w.media_item_id = ? AND c.identity IS NOT NULL AND c.state = 'active'
            """,
            (item_id, item_id, item_id),
        ).fetchall()
        own_prefixes = {(folder_id, f"{path.rstrip('/')}/") for folder_id, path in own_dirs}
        identities = [
            str(file["identity"])
            for file in files
            if not any(
                file["folder_id"] == folder_id and file["rel_path"].startswith(prefix)
                for folder_id, prefix in own_prefixes
            )
        ]
        return ItemDeletePlan(roots, own_dirs, identities)

    def delete_records(self, item_id: int) -> None:
        """云端删除成功后清理本地的文件记录、作品和条目。"""
        self._db.execute(
            "DELETE FROM media_file WHERE id IN (SELECT v.media_file_id FROM version v "
            "JOIN playable p ON p.id = v.playable_id WHERE p.media_item_id = ?)"
            " OR id IN (SELECT s.media_file_id FROM subtitle s "
            "JOIN version v ON v.id = s.version_id "
            "JOIN playable p ON p.id = v.playable_id WHERE p.media_item_id = ?)"
            " OR id IN (SELECT c.id FROM media_file c JOIN work w ON w.id = c.work_id "
            "WHERE w.media_item_id = ?)",
            (item_id, item_id, item_id),
        )
        self._db.execute("DELETE FROM work WHERE media_item_id = ?", (item_id,))
        self._db.execute("DELETE FROM media_item WHERE id = ?", (item_id,))

    def _require_unshared(self, item_id: int, folder_id: int, path: str) -> None:
        """作品目录要整体删除，里面有其他条目的文件时拒绝。"""
        prefix = f"{escape_like(path.rstrip('/'))}/%"
        other = self._db.execute(
            _SHARED_BY_OTHER_ITEM,
            (folder_id, path, prefix, item_id) * 3,
        ).fetchone()
        if other is not None:
            raise AppError(
                409, "media_folder_shared", f"媒体目录「{path}」还包含其他条目，未执行删除"
            )
