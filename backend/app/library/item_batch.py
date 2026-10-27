"""条目批量操作：把"选中的条目"或"全选筛选结果"解析成条目 ID，再统一打标签、改观看状态。"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from app.errors import AppError
from app.library.watchlist import drop_watched

# 一次批量操作最多处理的条目数
MAX_SELECTION = 1000

_WATCHED_EXISTS = (
    "EXISTS (SELECT 1 FROM playback_state ps "
    "JOIN playable wp ON wp.id = ps.playable_id "
    "WHERE ps.user_id = ? AND wp.media_item_id = m.id AND ps.watched = 1)"
)
_ACTIVE_VERSIONS = (
    "SELECT COUNT(*) FROM playable p JOIN version v ON v.playable_id = p.id "
    "JOIN media_file c ON c.id = v.media_file_id "
    "WHERE p.media_item_id = m.id AND +c.state = 'active'"
)
# 问题筛选的条件；键就是接口接受的取值
_STATUS_SQL = {
    "no_poster": "m.poster_image_id IS NULL AND m.poster_path IS NULL",
    "multiple_versions": f"({_ACTIVE_VERSIONS}) > 1",
    "missing": "EXISTS (SELECT 1 FROM playable p JOIN version v ON v.playable_id = p.id "
    "JOIN media_file c ON c.id = v.media_file_id "
    "WHERE p.media_item_id = m.id AND +c.state = 'missing')",
    "recent_metadata": "m.scraped_at >= datetime('now', '-7 days')",
}


class ItemBatchRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def existing(self, item_ids: Sequence[int]) -> list[int]:
        """选中的条目里仍然存在的那些。"""
        marks = ",".join("?" * len(item_ids))
        rows = self._db.execute(
            f"SELECT id FROM media_item WHERE id IN ({marks})", tuple(item_ids)
        ).fetchall()
        return [int(row[0]) for row in rows]

    def filtered(self, selection: Mapping[str, Any], user_id: int) -> list[int]:
        """按"全选筛选结果"的条件找条目；观看状态按 user_id 的播放记录判断。"""
        library_id = selection.get("library_id")
        if not isinstance(library_id, int):
            raise AppError(400, "invalid_filter", "全选筛选结果必须包含资料库")
        conditions = ["m.library_id = ?"]
        args: list[Any] = [library_id]
        genre = selection.get("genre")
        year = selection.get("year")
        watched = selection.get("watched")
        status = selection.get("status")
        if genre is not None:
            if not isinstance(genre, str) or len(genre) > 50:
                raise AppError(400, "invalid_filter", "类型筛选无效")
            conditions.append("EXISTS (SELECT 1 FROM json_each(m.genres) WHERE value = ?)")
            args.append(genre)
        if year is not None:
            if not isinstance(year, int) or not 1800 <= year <= 3000:
                raise AppError(400, "invalid_filter", "年份筛选无效")
            conditions.append("m.year = ?")
            args.append(year)
        if watched is not None:
            if not isinstance(watched, bool):
                raise AppError(400, "invalid_filter", "观看状态筛选无效")
            conditions.append(_WATCHED_EXISTS if watched else f"NOT {_WATCHED_EXISTS}")
            args.append(user_id)
        if status is not None:
            if not isinstance(status, str) or status not in _STATUS_SQL:
                raise AppError(400, "invalid_filter", "问题筛选无效")
            conditions.append(_STATUS_SQL[status])
        rows = self._db.execute(
            f"SELECT m.id FROM media_item m WHERE {' AND '.join(conditions)} "
            f"ORDER BY m.id LIMIT {MAX_SELECTION + 1}",
            tuple(args),
        ).fetchall()
        if len(rows) > MAX_SELECTION:
            raise AppError(413, "selection_too_large", "筛选结果超过 1000 条，请缩小范围")
        return [int(row[0]) for row in rows]

    def add_label(self, item_ids: Sequence[int], label: str) -> None:
        self._db.executemany(
            "INSERT OR IGNORE INTO item_label(media_item_id, label) VALUES (?, ?)",
            [(item_id, label) for item_id in item_ids],
        )

    def remove_label(self, item_ids: Sequence[int], label: str) -> None:
        self._db.executemany(
            "DELETE FROM item_label WHERE media_item_id = ? AND label = ?",
            [(item_id, label) for item_id in item_ids],
        )

    def set_watched(self, user_id: int, item_ids: Sequence[int], watched: bool) -> None:
        """只改已有的播放记录，没播放过的条目不会凭空多出一条记录。"""
        marks = ",".join("?" * len(item_ids))
        self._db.execute(
            "UPDATE playback_state SET watched = ? WHERE user_id = ? AND playable_id IN "
            f"(SELECT id FROM playable WHERE media_item_id IN ({marks}))",
            (int(watched), user_id, *item_ids),
        )
        if watched:
            drop_watched(self._db, user_id, item_ids)
