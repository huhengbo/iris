"""收藏：电影、剧集、单集和人物，按用户隔离（ADR 0024）。

收藏是多态目标：电影和剧集存在 media_item，单集存在 episode，人物存在 person。
这里只处理收藏表和可见范围，条目卡片等展示结构由 BrowseQueries 构造。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Literal

from app.auth.users import User
from app.clock import Clock, to_iso
from app.errors import NotFound
from app.library.browse import visible_library_ids
from app.library.browse_rows import EPISODE_PLAYABLE, PLAYABLE
from app.library.review import Page

# 接口和前端使用的类型；季不参与收藏
FavoriteKind = Literal["movie", "series", "episode", "person"]
TargetKind = Literal["item", "episode", "person"]

_TARGET_KIND: dict[FavoriteKind, TargetKind] = {
    "movie": "item",
    "series": "item",
    "episode": "episode",
    "person": "person",
}


class Favorites:
    def __init__(
        self, connection: sqlite3.Connection, user: User, clock: Clock | None = None
    ) -> None:
        self._db = connection
        self._clock = clock
        self._user = user
        self._library_ids = visible_library_ids(connection, user)
        self._scope = f"m.library_id IN ({', '.join('?' for _ in self._library_ids) or 'NULL'})"

    def add(self, kind: FavoriteKind, target_id: int) -> None:
        """幂等：已经收藏过时保留原来的收藏时间。"""
        target_kind = _TARGET_KIND[kind]
        self._require_target(kind, target_id)
        if self._clock is None:
            raise RuntimeError("收藏写入需要时钟")
        self._db.execute(
            "INSERT INTO user_favorite (user_id, target_kind, target_id, created_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (user_id, target_kind, target_id) DO NOTHING",
            (self._user.id, target_kind, target_id, to_iso(self._clock.now())),
        )

    def remove(self, kind: FavoriteKind, target_id: int) -> None:
        target_kind = _TARGET_KIND[kind]
        self._require_target(kind, target_id)
        self._db.execute(
            "DELETE FROM user_favorite WHERE user_id = ? AND target_kind = ? AND target_id = ?",
            (self._user.id, target_kind, target_id),
        )

    def item_kind(self, item_id: int) -> str | None:
        """条目的实际类型；路由用它把 /items/{id}/favorite 分到电影或剧集。"""
        row = self._db.execute("SELECT kind FROM media_item WHERE id = ?", (item_id,)).fetchone()
        return None if row is None else str(row["kind"])

    def is_favorite(self, kind: FavoriteKind, target_id: int) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM user_favorite WHERE user_id = ? AND target_kind = ? AND target_id = ?",
            (self._user.id, _TARGET_KIND[kind], target_id),
        ).fetchone()
        return row is not None

    def ids_for(self, kind: FavoriteKind, target_ids: Sequence[int]) -> set[int]:
        """其中当前用户收藏了的 ID，供详情页批量带上 favorite 字段。"""
        ids = sorted(set(target_ids))
        if not ids:
            return set()
        rows = self._db.execute(
            "SELECT target_id FROM user_favorite WHERE user_id = ? AND target_kind = ? "
            f"AND target_id IN ({', '.join('?' for _ in ids)})",
            (self._user.id, _TARGET_KIND[kind], *ids),
        ).fetchall()
        return {row["target_id"] for row in rows}

    def list_ids(self, kind: FavoriteKind, offset: int, limit: int) -> Page[int]:
        """收藏的可见目标，按收藏时间从新到旧。"""
        condition, args = self._list_condition(kind)
        total = self._db.execute(
            f"SELECT COUNT(*) FROM user_favorite f {self._list_joins(kind)} "
            f"WHERE f.user_id = ? AND {condition}",
            (self._user.id, *args),
        ).fetchone()[0]
        rows = self._db.execute(
            f"SELECT f.target_id FROM user_favorite f {self._list_joins(kind)} "
            f"WHERE f.user_id = ? AND {condition} "
            "ORDER BY f.created_at DESC, f.rowid DESC LIMIT ? OFFSET ?",
            (self._user.id, *args, limit, offset),
        ).fetchall()
        return Page([row["target_id"] for row in rows], total)

    def created_at_for(self, kind: FavoriteKind, target_ids: Sequence[int]) -> dict[int, str]:
        """收藏时间，供收藏列表里沿用「继续观看」卡片时填 played_at。"""
        ids = sorted(set(target_ids))
        if not ids:
            return {}
        rows = self._db.execute(
            "SELECT target_id, created_at FROM user_favorite WHERE user_id = ? "
            "AND target_kind = ? "
            f"AND target_id IN ({', '.join('?' for _ in ids)})",
            (self._user.id, _TARGET_KIND[kind], *ids),
        ).fetchall()
        return {row["target_id"]: row["created_at"] for row in rows}

    def recent_item_ids(self, limit: int) -> list[int]:
        """最近收藏的电影和剧集，首页「我的收藏」用。"""
        rows = self._db.execute(
            f"SELECT f.target_id FROM user_favorite f JOIN media_item m ON m.id = f.target_id "
            f"WHERE f.user_id = ? AND f.target_kind = 'item' AND {self._scope} AND {PLAYABLE} "
            "ORDER BY f.created_at DESC, f.rowid DESC LIMIT ?",
            (self._user.id, *self._library_ids, limit),
        ).fetchall()
        return [row["target_id"] for row in rows]

    def _require_target(self, kind: FavoriteKind, target_id: int) -> None:
        """目标存在且在可见范围内；不存在和范围外一样返回 404。"""
        if kind == "person":
            row = self._db.execute(
                "SELECT 1 FROM person p WHERE p.id = ? AND EXISTS ("
                "SELECT 1 FROM credit c JOIN media_item m ON m.id = c.media_item_id "
                f"WHERE c.person_id = p.id AND {self._scope} AND {PLAYABLE})",
                (target_id, *self._library_ids),
            ).fetchone()
        elif kind == "episode":
            row = self._db.execute(
                "SELECT 1 FROM episode e JOIN season s ON s.id = e.season_id "
                "JOIN media_item m ON m.id = s.media_item_id "
                "JOIN playable p ON p.episode_id = e.id "
                f"WHERE e.id = ? AND {self._scope} AND {EPISODE_PLAYABLE}",
                (target_id, *self._library_ids),
            ).fetchone()
        else:
            row = self._db.execute(
                f"SELECT 1 FROM media_item m WHERE m.id = ? AND m.kind = ? "
                f"AND {self._scope} AND {PLAYABLE}",
                (target_id, "movie" if kind == "movie" else "series", *self._library_ids),
            ).fetchone()
        if row is None:
            raise NotFound("收藏目标不存在")

    @staticmethod
    def _list_joins(kind: FavoriteKind) -> str:
        if kind == "episode":
            return (
                "JOIN episode e ON e.id = f.target_id "
                "JOIN season s ON s.id = e.season_id "
                "JOIN media_item m ON m.id = s.media_item_id "
                "JOIN playable p ON p.episode_id = e.id"
            )
        if kind in ("movie", "series"):
            return "JOIN media_item m ON m.id = f.target_id"
        return ""

    def _list_condition(self, kind: FavoriteKind) -> tuple[str, tuple[object, ...]]:
        if kind == "episode":
            condition = f"f.target_kind = 'episode' AND {self._scope} AND {EPISODE_PLAYABLE}"
            return condition, tuple(self._library_ids)
        if kind == "person":
            condition = (
                "f.target_kind = 'person' AND EXISTS ("
                "SELECT 1 FROM credit c JOIN media_item m ON m.id = c.media_item_id "
                f"WHERE c.person_id = f.target_id AND {self._scope} AND {PLAYABLE})"
            )
            return condition, tuple(self._library_ids)
        item_kind = "movie" if kind == "movie" else "series"
        condition = f"f.target_kind = 'item' AND {self._scope} AND {PLAYABLE} AND m.kind = ?"
        return condition, (*self._library_ids, item_kind)
