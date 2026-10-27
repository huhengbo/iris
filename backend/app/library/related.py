"""更多类似：详情页底部一行和当前条目类型相同、类型重合度高的条目（ADR 0026）。

只从当前用户可见的资料库里取，排除条目自己；范围外的条目和不存在一样返回 404。
排序依次是类型重合数、评分、年份接近，年份为空时排在后面。
"""

from __future__ import annotations

import json
import sqlite3

from app.auth.users import User
from app.errors import NotFound
from app.library.browse import BrowseQueries, visible_library_ids
from app.library.browse_models import ItemCard
from app.library.browse_rows import CARD_FROM, CARD_SELECT_FIELDS, PLAYABLE

# 返回条数的默认值和上限
DEFAULT_RELATED_LIMIT = 20
MAX_RELATED_LIMIT = 50


class RelatedQueries:
    def __init__(self, connection: sqlite3.Connection, user: User) -> None:
        self._db = connection
        self._user = user
        self._library_ids = visible_library_ids(connection, user)
        self._scope = f"m.library_id IN ({', '.join('?' for _ in self._library_ids) or 'NULL'})"

    def related(self, item_id: int, limit: int = DEFAULT_RELATED_LIMIT) -> list[ItemCard]:
        """和 [item_id] 同类型的条目：类型重合多的在前，其次评分高的、年份接近的。"""
        row = self._db.execute(
            f"{CARD_SELECT_FIELDS} {CARD_FROM} WHERE m.id = ? AND {self._scope} AND {PLAYABLE}",
            (item_id, *self._library_ids),
        ).fetchone()
        if row is None:
            raise NotFound("条目不存在")
        genres = json.dumps(json.loads(row["genres"]), ensure_ascii=False)
        rows = self._db.execute(
            f"""
            {CARD_SELECT_FIELDS},
                (SELECT COUNT(*) FROM json_each(m.genres) g
                 JOIN json_each(?) t ON t.value = g.value) AS overlap
            {CARD_FROM}
            WHERE {self._scope} AND {PLAYABLE} AND m.kind = ? AND m.id <> ?
            -- 评分和年份都可能为空：空值排在后面；年份同样接近时按 ID 稳定排序
            ORDER BY overlap DESC, m.rating IS NULL, m.rating DESC,
                CASE WHEN ? IS NULL OR m.year IS NULL THEN 1 ELSE 0 END,
                CASE WHEN ? IS NULL THEN 0 ELSE ABS(m.year - ?) END, m.id
            LIMIT ?
            """,
            (
                genres,
                *self._library_ids,
                row["kind"],
                item_id,
                row["year"],
                row["year"],
                row["year"],
                limit,
            ),
        ).fetchall()
        # 已看标记依赖当前用户，交给浏览查询统一算，避免两套判断不一致
        return BrowseQueries(self._db, self._user).cards_by_ids([r["id"] for r in rows])
