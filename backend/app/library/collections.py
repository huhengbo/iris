from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from app.auth.users import User
from app.clock import Clock, to_iso
from app.errors import NotFound
from app.library.browse import visible_library_ids

_COLUMNS = (
    "id, library_id, title, sort_title, overview, poster, tmdb_collection_id, sort_mode, "
    "smart_filter, automatic, cover_item_id, created_at, updated_at"
)
# 自动合集刚建好时的名称，抓到 TMDB 系列名之前一直用它
PLACEHOLDER_PREFIX = "TMDB 系列 "
_ORDER = "COALESCE(sort_title, title), id"


@dataclass(frozen=True)
class Collection:
    id: int
    library_id: int
    title: str
    sort_title: str | None
    overview: str | None
    poster: str | None
    tmdb_collection_id: int | None
    sort_mode: str
    # 智能筛选条件以 JSON 文本原样保存和返回，由客户端解释
    smart_filter: str | None
    automatic: bool
    # 管理员指定用哪个成员的海报作封面，没有指定时为空
    cover_item_id: int | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class CollectionDraft:
    """管理员填写的合集内容，创建和整体更新共用。"""

    library_id: int
    title: str
    sort_title: str | None
    overview: str | None
    poster: str | None
    sort_mode: str
    smart_filter: dict[str, Any] | None


@dataclass(frozen=True)
class CollectionMember:
    collection_id: int
    media_item_id: int
    position: int
    manual: bool


@dataclass(frozen=True)
class AutoSyncResult:
    created: int
    updated: int
    groups: int


class CollectionRepository:
    """合集及其成员。成员分手动（管理员挑选）和自动（按 TMDB 系列归并）两种。"""

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        self._db = connection
        self._clock = clock

    def list_all(self, library_id: int | None = None) -> list[Collection]:
        """不指定资料库时按资料库分组列出全部。"""
        if library_id is None:
            rows = self._db.execute(
                f"SELECT {_COLUMNS} FROM collection ORDER BY library_id, {_ORDER}"
            ).fetchall()
        else:
            rows = self._db.execute(
                f"SELECT {_COLUMNS} FROM collection WHERE library_id = ? ORDER BY {_ORDER}",
                (library_id,),
            ).fetchall()
        return [_to_collection(row) for row in rows]

    def list_visible(self, library_id: int, user: User) -> list[Collection]:
        if library_id not in visible_library_ids(self._db, user):
            raise NotFound("资料库不存在")
        return self.list_all(library_id)

    def require(self, collection_id: int) -> Collection:
        row = self._db.execute(
            f"SELECT {_COLUMNS} FROM collection WHERE id = ?", (collection_id,)
        ).fetchone()
        if row is None:
            raise NotFound("合集不存在")
        return _to_collection(row)

    def require_visible(self, collection_id: int, user: User) -> Collection:
        """看不到所在资料库的合集和不存在的合集一样处理，避免泄露其存在。"""
        row = self._db.execute(
            f"SELECT {_COLUMNS} FROM collection WHERE id = ?", (collection_id,)
        ).fetchone()
        if row is None or row["library_id"] not in visible_library_ids(self._db, user):
            raise NotFound("合集不存在")
        return _to_collection(row)

    def create(self, draft: CollectionDraft) -> Collection:
        self._require_library(draft.library_id)
        now = to_iso(self._clock.now())
        cursor = self._db.execute(
            "INSERT INTO collection(library_id,title,sort_title,overview,poster,sort_mode,"
            "smart_filter,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                draft.library_id,
                draft.title,
                draft.sort_title,
                draft.overview,
                draft.poster,
                draft.sort_mode,
                _filter_text(draft.smart_filter),
                now,
                now,
            ),
        )
        assert cursor.lastrowid is not None
        return self.require(cursor.lastrowid)

    def update(self, collection_id: int, draft: CollectionDraft) -> Collection:
        self.require(collection_id)
        self._db.execute(
            "UPDATE collection SET library_id=?, title=?, sort_title=?, overview=?, poster=?, "
            "sort_mode=?, smart_filter=?, updated_at=? WHERE id=?",
            (
                draft.library_id,
                draft.title,
                draft.sort_title,
                draft.overview,
                draft.poster,
                draft.sort_mode,
                _filter_text(draft.smart_filter),
                to_iso(self._clock.now()),
                collection_id,
            ),
        )
        return self.require(collection_id)

    def delete(self, collection_id: int) -> None:
        if self._db.execute("DELETE FROM collection WHERE id = ?", (collection_id,)).rowcount == 0:
            raise NotFound("合集不存在")

    def set_members(self, collection_id: int, item_ids: list[int]) -> list[CollectionMember]:
        """整体替换成员。只收合集所在资料库里的条目，重复和无效的 ID 直接忽略。"""
        library_id = self.require(collection_id).library_id
        marks = ",".join("?" for _ in item_ids) or "NULL"
        valid = [
            int(row[0])
            for row in self._db.execute(
                f"SELECT id FROM media_item WHERE library_id = ? AND id IN ({marks})",
                (library_id, *item_ids),
            ).fetchall()
        ]
        self._db.execute("DELETE FROM collection_item WHERE collection_id = ?", (collection_id,))
        self._db.executemany(
            "INSERT INTO collection_item(collection_id,media_item_id,position,manual) "
            "VALUES (?,?,?,1)",
            [(collection_id, item_id, n) for n, item_id in enumerate(dict.fromkeys(valid))],
        )
        return self.members(collection_id)

    def members(self, collection_id: int) -> list[CollectionMember]:
        rows = self._db.execute(
            "SELECT collection_id, media_item_id, position, manual FROM collection_item "
            "WHERE collection_id = ? ORDER BY position",
            (collection_id,),
        ).fetchall()
        return [
            CollectionMember(
                collection_id=row["collection_id"],
                media_item_id=row["media_item_id"],
                position=row["position"],
                manual=bool(row["manual"]),
            )
            for row in rows
        ]

    def add_member(self, collection_id: int, item_id: int) -> None:
        """追加一个手动成员；已经在合集里时什么也不做。"""
        library_id = self.require(collection_id).library_id
        in_library = self._db.execute(
            "SELECT 1 FROM media_item WHERE id = ? AND library_id = ?", (item_id, library_id)
        ).fetchone()
        if in_library is None:
            raise NotFound("条目不存在")
        self._db.execute(
            "INSERT OR IGNORE INTO collection_item(collection_id,media_item_id,position,manual) "
            "SELECT ?, ?, COALESCE(MAX(position), -1) + 1, 1 FROM collection_item "
            "WHERE collection_id = ?",
            (collection_id, item_id, collection_id),
        )

    def remove_member(self, collection_id: int, item_id: int) -> None:
        self.require(collection_id)
        removed = self._db.execute(
            "DELETE FROM collection_item WHERE collection_id = ? AND media_item_id = ?",
            (collection_id, item_id),
        ).rowcount
        if removed == 0:
            raise NotFound("条目不在合集里")

    def sync_automatic(self, library_id: int) -> AutoSyncResult:
        """按 TMDB collection id 重建自动合集；手动成员不会被覆盖。"""
        self._require_library(library_id)
        groups = self._db.execute(
            "SELECT tmdb_collection_id, GROUP_CONCAT(id) AS ids "
            "FROM media_item WHERE library_id = ? AND tmdb_collection_id IS NOT NULL "
            "GROUP BY tmdb_collection_id",
            (library_id,),
        ).fetchall()
        created = updated = 0
        now = to_iso(self._clock.now())
        for group in groups:
            tmdb_id = int(group["tmdb_collection_id"])
            row = self._db.execute(
                "SELECT id FROM collection WHERE library_id = ? AND tmdb_collection_id = ?",
                (library_id, tmdb_id),
            ).fetchone()
            if row is None:
                cursor = self._db.execute(
                    "INSERT INTO collection(library_id,title,sort_title,tmdb_collection_id,"
                    "automatic,created_at,updated_at) VALUES (?,?,?,?,1,?,?)",
                    (library_id, f"{PLACEHOLDER_PREFIX}{tmdb_id}", None, tmdb_id, now, now),
                )
                assert cursor.lastrowid is not None
                collection_id = int(cursor.lastrowid)
                created += 1
            else:
                collection_id = int(row["id"])
                updated += 1
            fill_from_tmdb(self._db, collection_id, tmdb_id)
            ids = [int(value) for value in str(group["ids"]).split(",") if value]
            self._db.executemany(
                "INSERT INTO collection_item(collection_id,media_item_id,position,manual) "
                "VALUES (?,?,?,0) "
                "ON CONFLICT(collection_id,media_item_id) DO UPDATE SET position=excluded.position",
                [(collection_id, item_id, position) for position, item_id in enumerate(ids)],
            )
        return AutoSyncResult(created=created, updated=updated, groups=len(groups))

    def set_cover(self, collection_id: int, item_id: int | None) -> Collection:
        """指定用某个成员的海报作封面；item_id 为空时恢复默认。"""
        self.require(collection_id)
        if (
            item_id is not None
            and not self._db.execute(
                "SELECT 1 FROM collection_item WHERE collection_id = ? AND media_item_id = ?",
                (collection_id, item_id),
            ).fetchone()
        ):
            raise NotFound("条目不在合集里")
        self._db.execute(
            "UPDATE collection SET cover_item_id = ?, updated_at = ? WHERE id = ?",
            (item_id, to_iso(self._clock.now()), collection_id),
        )
        return self.require(collection_id)

    def _require_library(self, library_id: int) -> None:
        if self._db.execute("SELECT 1 FROM library WHERE id = ?", (library_id,)).fetchone() is None:
            raise NotFound("资料库不存在")


def fill_from_tmdb(connection: sqlite3.Connection, collection_id: int, tmdb_id: int) -> None:
    """自动合集用 TMDB 系列的名称和图片补空：还是初始名称才改名，已有的图片不覆盖，
    这样管理员改过的名称不会被下一次同步改回去。"""
    connection.execute(
        "UPDATE collection SET "
        "title = CASE WHEN title LIKE ? || '%' THEN "
        "COALESCE((SELECT name FROM tmdb_collection WHERE id = ?), title) ELSE title END, "
        "poster_path = COALESCE(poster_path, (SELECT poster_path FROM tmdb_collection "
        "WHERE id = ?)), "
        "backdrop_path = COALESCE(backdrop_path, (SELECT backdrop_path FROM tmdb_collection "
        "WHERE id = ?)) WHERE id = ?",
        (PLACEHOLDER_PREFIX, tmdb_id, tmdb_id, tmdb_id, collection_id),
    )


def _filter_text(smart_filter: dict[str, Any] | None) -> str | None:
    # 空条件等同于没有条件
    if not smart_filter:
        return None
    return json.dumps(smart_filter, ensure_ascii=False, separators=(",", ":"))


def _to_collection(row: sqlite3.Row) -> Collection:
    return Collection(
        id=row["id"],
        library_id=row["library_id"],
        title=row["title"],
        sort_title=row["sort_title"],
        overview=row["overview"],
        poster=row["poster"],
        tmdb_collection_id=row["tmdb_collection_id"],
        sort_mode=row["sort_mode"],
        smart_filter=row["smart_filter"],
        automatic=bool(row["automatic"]),
        cover_item_id=row["cover_item_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
