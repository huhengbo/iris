from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.clock import Clock, to_iso
from app.sync.purge import MissingFilePurger


@dataclass(frozen=True)
class RecycledFile:
    media_file_id: int
    rel_path: str
    size: int | None
    missing_at: str | None
    library_id: int
    library_name: str
    # 文件对应的条目；没有版本引用它时为空
    media_item_id: int | None
    title: str | None


class RecycleBin:
    """回收站：在来源上已经消失、等待保留期结束后清除的文件。"""

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        self._db = connection
        self._clock = clock

    def files(self, library_id: int | None = None) -> list[RecycledFile]:
        args: tuple[int, ...] = () if library_id is None else (library_id,)
        where = "" if library_id is None else "AND l.id = ?"
        rows = self._db.execute(
            "SELECT c.id AS media_file_id,c.rel_path,c.size,c.missing_at,l.id AS library_id,"
            "l.name AS library_name,p.media_item_id,m.title "
            "FROM media_file c JOIN library_folder f ON f.id=c.folder_id "
            "JOIN library l ON l.id=f.library_id LEFT JOIN version v ON v.media_file_id=c.id "
            "LEFT JOIN playable p ON p.id=v.playable_id "
            "LEFT JOIN media_item m ON m.id=p.media_item_id "
            f"WHERE c.state='missing' {where} ORDER BY c.missing_at DESC,c.id DESC",
            args,
        ).fetchall()
        return [RecycledFile(*row) for row in rows]

    def empty(self, library_id: int | None = None) -> int:
        """立即清空回收站（不等保留期）并返回删除的文件数；资料库不存在时什么也不做。"""
        query = (
            "SELECT id FROM library"
            if library_id is None
            else "SELECT id FROM library WHERE id = ?"
        )
        args: tuple[int, ...] = () if library_id is None else (library_id,)
        ids = [int(row[0]) for row in self._db.execute(query, args).fetchall()]
        deleted = 0
        for current in ids:
            self._db.execute(
                "UPDATE media_file SET missing_at = COALESCE(missing_at, ?) "
                "WHERE state='missing' AND folder_id IN "
                "(SELECT id FROM library_folder WHERE library_id=?)",
                (to_iso(self._clock.now()), current),
            )
            deleted += MissingFilePurger(self._db, self._clock).purge(current, 0)
        return deleted
