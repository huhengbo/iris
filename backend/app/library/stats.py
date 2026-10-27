from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class LibraryStats:
    items: int
    # 资料库没有条目时 SUM 没有可加的行，保持为空而不是 0
    movies: int | None
    series: int | None
    episodes: int
    # 未失效视频文件的总字节数
    bytes: int
    unmatched: int
    missing: int


class LibraryStatsQueries:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def summary(self, library_id: int) -> LibraryStats:
        row = self._db.execute(
            "SELECT COUNT(*) AS items, SUM(kind='movie') AS movies, SUM(kind='series') AS series, "
            "(SELECT COUNT(*) FROM episode e JOIN season s ON s.id=e.season_id "
            "JOIN media_item mi ON mi.id=s.media_item_id WHERE mi.library_id=?) AS episodes, "
            "(SELECT COALESCE(SUM(c.size),0) FROM media_file c "
            "JOIN library_folder f ON f.id=c.folder_id "
            "WHERE f.library_id=? AND c.kind='video') AS bytes, "
            "(SELECT COUNT(*) FROM work w JOIN library_folder f ON f.id=w.folder_id "
            "WHERE f.library_id=? AND w.state='unmatched') AS unmatched, "
            "(SELECT COUNT(*) FROM media_file c JOIN library_folder f ON f.id=c.folder_id "
            "WHERE f.library_id=? AND c.state='missing') AS missing "
            "FROM media_item WHERE library_id = ?",
            (library_id, library_id, library_id, library_id, library_id),
        ).fetchone()
        return LibraryStats(*row)
