"""稍后观看：想看的电影或整部剧集，按用户隔离。

只能放当前用户可见资料库里、至少有一个可用文件的条目。电影标记已看、剧集全部集都已看时
自动移出；这个判断和浏览接口的「已看」完全一致，所以直接复用 browse_rows 里的 SQL 片段。
列表只返回当前仍然可见的条目，失去资料库权限或文件全部失效的条目留在表里等恢复。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from app.auth.users import User
from app.clock import Clock, to_iso
from app.errors import AppError, NotFound
from app.library.browse import visible_library_ids
from app.library.browse_rows import PLAYABLE, WATCHED
from app.library.review import Page

# 稍后观看只收电影和整部剧集，季和单集没有对应入口
_WATCHLIST_KINDS = ("movie", "series")


class Watchlist:
    def __init__(
        self, connection: sqlite3.Connection, user: User, clock: Clock | None = None
    ) -> None:
        self._db = connection
        self._clock = clock
        self._user = user
        self._library_ids = visible_library_ids(connection, user)
        self._scope = f"m.library_id IN ({', '.join('?' for _ in self._library_ids) or 'NULL'})"

    def add(self, item_id: int) -> None:
        """幂等：已经加入时保留原来的加入时间。"""
        self._require_target(item_id)
        if self._clock is None:
            raise RuntimeError("稍后观看写入需要时钟")
        self._db.execute(
            "INSERT INTO user_watchlist (user_id, item_id, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT (user_id, item_id) DO NOTHING",
            (self._user.id, item_id, to_iso(self._clock.now())),
        )

    def remove(self, item_id: int) -> None:
        self._require_target(item_id)
        self._db.execute(
            "DELETE FROM user_watchlist WHERE user_id = ? AND item_id = ?",
            (self._user.id, item_id),
        )

    def is_watchlisted(self, item_id: int) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM user_watchlist WHERE user_id = ? AND item_id = ?",
            (self._user.id, item_id),
        ).fetchone()
        return row is not None

    def ids_for(self, item_ids: Sequence[int]) -> set[int]:
        """其中当前用户加入了稍后观看的 ID，供批量查询。"""
        ids = sorted(set(item_ids))
        if not ids:
            return set()
        rows = self._db.execute(
            "SELECT item_id FROM user_watchlist WHERE user_id = ? "
            f"AND item_id IN ({', '.join('?' for _ in ids)})",
            (self._user.id, *ids),
        ).fetchall()
        return {row["item_id"] for row in rows}

    def list_ids(self, offset: int, limit: int) -> Page[int]:
        """可见条目的 ID，按加入时间从新到旧；同一秒加入的按写入顺序倒序。"""
        condition = f"{self._scope} AND {PLAYABLE}"
        args = (*self._library_ids,)
        total = self._db.execute(
            "SELECT COUNT(*) FROM user_watchlist w JOIN media_item m ON m.id = w.item_id "
            f"WHERE w.user_id = ? AND {condition}",
            (self._user.id, *args),
        ).fetchone()[0]
        rows = self._db.execute(
            "SELECT w.item_id FROM user_watchlist w JOIN media_item m ON m.id = w.item_id "
            f"WHERE w.user_id = ? AND {condition} "
            "ORDER BY w.created_at DESC, w.rowid DESC LIMIT ? OFFSET ?",
            (self._user.id, *args, limit, offset),
        ).fetchall()
        return Page([row["item_id"] for row in rows], total)

    def recent_item_ids(self, limit: int) -> list[int]:
        """最近加入的可见条目，首页「稍后观看」用。"""
        rows = self._db.execute(
            "SELECT w.item_id FROM user_watchlist w JOIN media_item m ON m.id = w.item_id "
            f"WHERE w.user_id = ? AND {self._scope} AND {PLAYABLE} "
            "ORDER BY w.created_at DESC, w.rowid DESC LIMIT ?",
            (self._user.id, *self._library_ids, limit),
        ).fetchall()
        return [row["item_id"] for row in rows]

    def _require_target(self, item_id: int) -> None:
        """目标存在且在当前用户的可见范围内；不存在和范围外一样返回 404。"""
        row = self._db.execute(
            f"SELECT m.kind FROM media_item m WHERE m.id = ? AND {self._scope} AND {PLAYABLE}",
            (item_id, *self._library_ids),
        ).fetchone()
        if row is None:
            raise NotFound("条目不存在")
        if row["kind"] not in _WATCHLIST_KINDS:
            raise AppError(400, "not_watchlistable", "只有电影和剧集可以加入稍后观看")


def drop_watched(connection: sqlite3.Connection, user_id: int, item_ids: Sequence[int]) -> None:
    """把用户已看完的条目移出稍后观看。

    判断和浏览接口的「已看」一致：条目的每个有可用文件的可播放条目都标记了已看。
    电影只有一个可播放条目，剧集要全部集都看完；未加入稍后观看的条目不受影响。
    """
    ids = sorted(set(item_ids))
    if not ids:
        return
    marks = ", ".join("?" for _ in ids)
    connection.execute(
        f"""
        DELETE FROM user_watchlist
        WHERE user_id = ? AND item_id IN ({marks})
            AND EXISTS (
                SELECT 1 FROM media_item m
                WHERE m.id = user_watchlist.item_id AND {PLAYABLE} AND {WATCHED}
            )
        """,
        (user_id, *ids, user_id),
    )


def drop_watched_for_item(connection: sqlite3.Connection, item_id: int) -> None:
    """合并版本等操作会一次性改动所有用户的播放记录，按各自的已看状态清理。"""
    rows = connection.execute(
        "SELECT user_id FROM user_watchlist WHERE item_id = ?", (item_id,)
    ).fetchall()
    for row in rows:
        drop_watched(connection, int(row["user_id"]), [item_id])
