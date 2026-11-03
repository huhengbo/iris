"""浏览：普通用户和管理员看到的影视墙、详情、演员页和搜索（ADR 0014）。

所有查询都限定在用户的可见范围内（ADR 0009），范围外的资料库、条目、季和人物一律当作不存在。
只列出至少有一个可用文件的条目、季和集：失效或被拆走的文件不能播放，列出来没有意义。
已看标记和最近播放只取当前用户自己的记录。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from app.auth.users import User
from app.errors import NotFound
from app.library.browse_models import (
    CategorySummary,
    CollectionCover,
    CreditRole,
    DiscoverFilters,
    Episode,
    EpisodeRef,
    Home,
    HubRow,
    HubShelf,
    ItemCard,
    ItemDetail,
    LibraryDetail,
    LibrarySummary,
    PersonDetail,
    PersonWork,
    ProblemStatus,
    Rating,
    RecentPlay,
    SearchEpisode,
    SearchPage,
    SearchPerson,
    SeasonDetail,
    SeasonSummary,
    SortKey,
    SortOrder,
    Subtitle,
    Version,
)
from app.library.browse_rows import (
    CARD_FROM,
    CARD_SELECT,
    CARD_SELECT_FIELDS,
    EPISODE_PLAYABLE,
    PLAYABLE,
    WATCHED,
    row_card,
    row_credit,
    row_episode_ref,
    row_media_info,
    row_progress,
)
from app.library.covers import library_covers
from app.library.images import image_sizes
from app.library.review import Page
from app.library.settings import (
    DEFAULT_GENRE_HUBS,
    HUB_SIZE,
    LibraryHubSpec,
    hub_title,
    library_hubs,
)

if TYPE_CHECKING:
    from app.library.favorites import Favorites
    from app.library.watchlist import Watchlist

# 首页「最近播放」的条数
HOME_RECENT_SIZE = 16
# 首页各资料库推荐行最多显示的条数，和资料库「推荐」标签一致
HOME_HUB_SIZE = HUB_SIZE
# 首页「我的收藏」最多显示的电影和剧集
HOME_FAVORITES_SIZE = 20
# 首页「稍后观看」最多显示的电影和剧集
HOME_WATCHLIST_SIZE = 20
# 详情页的演员数；库里每部作品存了前 20 位，供演员页列出本地作品
DETAIL_CAST_SIZE = 20
# 名称默认从 A 到 Z，其余默认从新到旧、从高到低；空值始终排在最后
DEFAULT_ORDER: dict[SortKey, SortOrder] = {
    "added": "desc",
    "release": "desc",
    "rating": "desc",
    "name": "asc",
}
_SORT_COLUMN: dict[SortKey, str] = {
    "added": "m.added_at",
    "release": "m.release_date",
    "rating": "m.rating",
    "name": "m.title",
}


def visible_library_ids(connection: sqlite3.Connection, user: User) -> list[int]:
    """开了「全部资料库」的用户看到全部，其他用户只看到分配给自己的，管理员也一样。
    按资料库的排序返回。"""
    if user.all_libraries:
        rows = connection.execute("SELECT id FROM library ORDER BY sort_order, id").fetchall()
    else:
        rows = connection.execute(
            "SELECT l.id FROM library l JOIN library_access a ON a.library_id = l.id "
            "WHERE a.user_id = ? ORDER BY l.sort_order, l.id",
            (user.id,),
        ).fetchall()
    return [row["id"] for row in rows]


class BrowseQueries:
    def __init__(self, connection: sqlite3.Connection, user: User) -> None:
        self._db = connection
        self._user = user
        self._user_id = user.id
        self._library_ids = visible_library_ids(connection, user)
        self._scope = f"m.library_id IN ({', '.join('?' for _ in self._library_ids) or 'NULL'})"

    # ---- 资料库 ----

    def libraries(self) -> list[LibrarySummary]:
        if not self._library_ids:
            return []
        rows = self._db.execute(
            f"""
            SELECT l.id, l.name, l.kind, COUNT(m.id) AS item_count
            FROM library l LEFT JOIN media_item m ON m.library_id = l.id AND {PLAYABLE}
            WHERE l.id IN ({", ".join("?" for _ in self._library_ids)})
            GROUP BY l.id ORDER BY l.sort_order, l.id
            """,
            self._library_ids,
        ).fetchall()
        covers = library_covers(self._db, [r["id"] for r in rows])
        return [
            LibrarySummary(
                r["id"],
                r["name"],
                r["kind"],
                r["item_count"],
                covers[r["id"]].sizes if r["id"] in covers else [],
                covers[r["id"]].hash if r["id"] in covers else None,
            )
            for r in rows
        ]

    def library(self, library_id: int) -> LibraryDetail:
        summary = self._library(library_id)
        genres = self._db.execute(
            f"SELECT DISTINCT g.value FROM media_item m, json_each(m.genres) g "
            f"WHERE m.library_id = ? AND {PLAYABLE} ORDER BY g.value",
            (library_id,),
        ).fetchall()
        years = self._db.execute(
            f"SELECT DISTINCT m.year FROM media_item m WHERE m.library_id = ? "
            f"AND m.year IS NOT NULL AND {PLAYABLE} ORDER BY m.year DESC",
            (library_id,),
        ).fetchall()
        return LibraryDetail(
            summary,
            [r[0] for r in genres],
            [r[0] for r in years],
            self._categories(library_id),
        )

    def _categories(self, library_id: int) -> list[CategorySummary]:
        """类型卡片：每个类型一条，带条目数和最近添加条目的一张代表图。"""
        rows = self._db.execute(
            f"""
            SELECT name, item_count, backdrop_hash, poster_hash FROM (
                SELECT g.value AS name,
                    COUNT(*) OVER (PARTITION BY g.value) AS item_count,
                    bi.hash AS backdrop_hash, pi.hash AS poster_hash,
                    ROW_NUMBER() OVER (
                        PARTITION BY g.value ORDER BY m.added_at DESC, m.id DESC
                    ) AS rank
                FROM media_item m, json_each(m.genres) g
                LEFT JOIN image pi ON pi.id = m.poster_image_id
                LEFT JOIN image bi ON bi.id = m.backdrop_image_id
                WHERE m.library_id = ? AND {PLAYABLE}
            ) WHERE rank = 1 ORDER BY item_count DESC, name
            """,
            (library_id,),
        ).fetchall()
        return [
            CategorySummary(
                name=str(row["name"]),
                item_count=int(row["item_count"]),
                image=image_sizes(
                    row["backdrop_hash"] or row["poster_hash"],
                    "backdrop" if row["backdrop_hash"] else "poster",
                ),
                image_hash=row["backdrop_hash"] or row["poster_hash"],
            )
            for row in rows
        ]

    def library_items(
        self,
        library_id: int,
        *,
        sort: SortKey,
        order: SortOrder | None,
        genre: str | None,
        year: int | None,
        watched: bool | None = None,
        favorite: bool | None = None,
        watchlist: bool | None = None,
        status: ProblemStatus | None = None,
        letter: str | None = None,
        offset: int,
        limit: int,
    ) -> Page[ItemCard]:
        self._library(library_id)
        conditions = ["m.library_id = ?", PLAYABLE]
        args: list[object] = [library_id]
        if genre:
            conditions.append("EXISTS (SELECT 1 FROM json_each(m.genres) WHERE value = ?)")
            args.append(genre)
        if year is not None:
            conditions.append("m.year = ?")
            args.append(year)
        if watched is not None:
            conditions.append(WATCHED if watched else f"NOT {WATCHED}")
            args.append(self._user_id)
        if favorite is not None:
            conditions.append(
                ("EXISTS" if favorite else "NOT EXISTS")
                + " (SELECT 1 FROM user_favorite f WHERE f.user_id = ? "
                "AND f.target_kind = 'item' AND f.target_id = m.id)"
            )
            args.append(self._user_id)
        if watchlist is not None:
            conditions.append(
                ("EXISTS" if watchlist else "NOT EXISTS")
                + " (SELECT 1 FROM user_watchlist w WHERE w.user_id = ? "
                "AND w.item_id = m.id)"
            )
            args.append(self._user_id)
        if letter == "#":
            # 非拉丁字母（中文、数字、符号）在名称排序里归到最后一组
            conditions.append("m.title NOT GLOB '[A-Za-z]*'")
        elif letter:
            # SQLite 的 lower 只处理 ASCII，正好和 A-Z 跳转条对应
            conditions.append("lower(substr(m.title, 1, 1)) = ?")
            args.append(letter.lower())
        if status == "no_poster":
            conditions.append("m.poster_image_id IS NULL AND m.poster_path IS NULL")
        elif status == "multiple_versions":
            conditions.append(
                "(SELECT COUNT(*) FROM playable p JOIN version v ON v.playable_id = p.id "
                "JOIN media_file c ON c.id = v.media_file_id WHERE p.media_item_id = m.id "
                "AND +c.state = 'active') > 1"
            )
        elif status == "missing":
            conditions.append(
                "EXISTS (SELECT 1 FROM playable p JOIN version v ON v.playable_id = p.id "
                "JOIN media_file c ON c.id = v.media_file_id WHERE p.media_item_id = m.id "
                "AND +c.state = 'missing')"
            )
        elif status == "recent_metadata":
            conditions.append("m.scraped_at >= datetime('now', '-7 days')")
        where = " AND ".join(conditions)
        direction = (order or DEFAULT_ORDER[sort]).upper()
        column = _SORT_COLUMN[sort]
        total = self._db.execute(
            f"SELECT COUNT(*) FROM media_item m WHERE {where}", args
        ).fetchone()[0]
        rows = self._db.execute(
            f"{CARD_SELECT} WHERE {where} "
            f"ORDER BY {column} {direction} NULLS LAST, m.id {direction} LIMIT ? OFFSET ?",
            (*args, limit, offset),
        ).fetchall()
        return Page(self._cards(rows), total)

    # ---- 按条件找片 ----

    def discover(self, filters: DiscoverFilters, offset: int, limit: int) -> Page[ItemCard]:
        """按条件筛选，评分高的在前；评分相同时新的在前。"""
        where, args = self._discover_where(filters)
        total = self._db.execute(
            f"SELECT COUNT(*) FROM media_item m WHERE {where}", args
        ).fetchone()[0]
        rows = self._db.execute(
            f"{CARD_SELECT} WHERE {where} "
            "ORDER BY m.rating DESC NULLS LAST, m.year DESC NULLS LAST, m.id DESC LIMIT ? OFFSET ?",
            (*args, limit, offset),
        ).fetchall()
        return Page(self._cards(rows), total)

    def accept(self, filters: DiscoverFilters, item_ids: Sequence[int]) -> list[int]:
        """item_ids 里满足条件且在可见范围内的，保持原有顺序。"""
        if not item_ids:
            return []
        where, args = self._discover_where(filters)
        marks = ", ".join("?" for _ in item_ids)
        rows = self._db.execute(
            f"SELECT m.id FROM media_item m WHERE {where} AND m.id IN ({marks})",
            (*args, *item_ids),
        ).fetchall()
        allowed = {row["id"] for row in rows}
        return [item_id for item_id in item_ids if item_id in allowed]

    def watched_items(self, limit: int) -> list[ItemCard]:
        """当前用户看完的条目，最近看的在前。"""
        rows = self._db.execute(
            f"{CARD_SELECT} WHERE {self._scope} AND {PLAYABLE} AND {WATCHED} "
            "ORDER BY (SELECT MAX(s.last_played_at) FROM playback_state s "
            "JOIN playable p ON p.id = s.playable_id "
            "WHERE s.user_id = ? AND p.media_item_id = m.id) DESC NULLS LAST, m.id DESC LIMIT ?",
            (*self._library_ids, self._user_id, self._user_id, limit),
        ).fetchall()
        return self._cards(rows)

    def recent_plays(self) -> list[RecentPlay]:
        """最近播放过的条目（剧集带最近播放的那一集和续播位置），最近的在前。"""
        return self._recent()

    def cards(self, item_ids: Sequence[int]) -> list[ItemCard]:
        """可见范围内的条目卡片，顺序同 item_ids；范围外和不存在的跳过。"""
        if not item_ids:
            return []
        marks = ", ".join("?" for _ in item_ids)
        rows = self._db.execute(
            f"{CARD_SELECT} WHERE {self._scope} AND {PLAYABLE} AND m.id IN ({marks})",
            (*self._library_ids, *item_ids),
        ).fetchall()
        by_id = {card.id: card for card in self._cards(rows)}
        return [by_id[item_id] for item_id in item_ids if item_id in by_id]

    def _discover_where(self, filters: DiscoverFilters) -> tuple[str, list[object]]:
        conditions = [self._scope, PLAYABLE]
        args: list[object] = list(self._library_ids)
        if filters.kind is not None:
            conditions.append("m.kind = ?")
            args.append(filters.kind)
        if filters.library_id is not None:
            conditions.append("m.library_id = ?")
            args.append(filters.library_id)
        if filters.genres:
            marks = ", ".join("?" for _ in filters.genres)
            conditions.append(
                f"EXISTS (SELECT 1 FROM json_each(m.genres) WHERE value IN ({marks}))"
            )
            args.extend(filters.genres)
        if filters.year_from is not None:
            conditions.append("m.year >= ?")
            args.append(filters.year_from)
        if filters.year_to is not None:
            conditions.append("m.year <= ?")
            args.append(filters.year_to)
        if filters.min_rating is not None:
            conditions.append("m.rating >= ?")
            args.append(filters.min_rating)
        if filters.max_runtime is not None:
            conditions.append("m.runtime IS NOT NULL AND m.runtime <= ?")
            args.append(filters.max_runtime)
        if filters.watched is not None:
            conditions.append(WATCHED if filters.watched else f"NOT {WATCHED}")
            args.append(self._user_id)
        return " AND ".join(conditions), args

    # ---- 首页 ----

    def home(self) -> Home:
        libraries = self.libraries()
        recent = self._recent()
        favorite_ids = self._favorites().recent_item_ids(HOME_FAVORITES_SIZE)
        watchlist_ids = self._watchlist().recent_item_ids(HOME_WATCHLIST_SIZE)
        return Home(
            libraries,
            recent,
            self.cards_by_ids(favorite_ids),
            self.cards_by_ids(watchlist_ids),
            self._home_shelves(libraries),
        )

    def _home_shelves(self, libraries: Sequence[LibrarySummary]) -> list[HubShelf]:
        """首页显示的推荐行，按资料库顺序再按行顺序；空行不出。"""
        shelves: list[HubShelf] = []
        for library in libraries:
            # 没有保存过配置时首页只有「最近添加」，不用为了首页去算默认类型行
            specs = self._saved_hubs(library.id) or [LibraryHubSpec("latest", None, 0, True, True)]
            for spec in specs:
                if not spec.show_home:
                    continue
                row = self._hub_row(library, spec)
                if not row.items:
                    continue
                shelves.append(HubShelf(library, row))
        return shelves

    def library_hubs(self, library_id: int) -> list[HubRow]:
        """资料库「推荐」标签的行：配置为显示且不为空的行。"""
        library = self._library(library_id)
        rows = []
        for spec in self.effective_hubs(library_id):
            if not spec.show_library:
                continue
            row = self._hub_row(library, spec)
            if row.items:
                rows.append(row)
        return rows

    def effective_hubs(self, library_id: int) -> list[LibraryHubSpec]:
        """资料库实际生效的推荐行：管理员保存过就用保存的，否则用默认布局。

        默认布局是「继续观看 / 最近添加 / 最近上映 / 评分最高」加条目数最多的几个类型，
        其中只有「最近添加」同时出现在首页。
        """
        saved = self._saved_hubs(library_id)
        if saved is not None:
            return saved
        genres = self._top_genres(library_id, DEFAULT_GENRE_HUBS)
        kinds = [
            ("continue", None),
            ("latest", None),
            ("released", None),
            ("rating", None),
            *(("genre", genre) for genre in genres),
        ]
        return [
            LibraryHubSpec(kind, arg, index, kind == "latest", True)
            for index, (kind, arg) in enumerate(kinds)
        ]

    def _saved_hubs(self, library_id: int) -> list[LibraryHubSpec] | None:
        """管理员保存过的推荐行；没保存过时为 None（用默认布局）。"""
        saved = library_hubs(self._db, library_id)
        if not saved:
            return None
        return [
            LibraryHubSpec(hub.kind, hub.arg, index, hub.show_home, hub.show_library)
            for index, hub in enumerate(saved)
        ]

    def _top_genres(self, library_id: int, limit: int) -> list[str]:
        rows = self._db.execute(
            f"SELECT g.value AS name, COUNT(*) AS item_count "
            f"FROM media_item m, json_each(m.genres) g "
            f"WHERE m.library_id = ? AND {PLAYABLE} "
            f"GROUP BY g.value ORDER BY item_count DESC, name LIMIT ?",
            (library_id, limit),
        ).fetchall()
        return [str(row["name"]) for row in rows]

    def _hub_row(self, library: LibrarySummary, spec: LibraryHubSpec) -> HubRow:
        title = hub_title(library.name, spec.kind, spec.arg)
        if spec.kind == "continue":
            recent = self._recent(library_id=library.id, limit=HOME_HUB_SIZE)
            # 同一部作品只出现一次，顺序沿用最近播放
            items = [play.item for play in recent]
            return HubRow(spec.kind, spec.arg, title, items, recent)
        return HubRow(spec.kind, spec.arg, title, self._hub_cards(library.id, spec))

    def _hub_cards(self, library_id: int, spec: LibraryHubSpec) -> list[ItemCard]:
        conditions = ["m.library_id = ?", PLAYABLE]
        args: list[object] = [library_id]
        order = "m.added_at DESC, m.id DESC"
        if spec.kind == "released":
            conditions.append("m.release_date IS NOT NULL")
            order = "m.release_date DESC, m.id DESC"
        elif spec.kind == "rating":
            conditions.append("m.rating IS NOT NULL AND m.rating > 0")
            order = "m.rating DESC, m.id DESC"
        elif spec.kind == "genre":
            conditions.append("EXISTS (SELECT 1 FROM json_each(m.genres) WHERE value = ?)")
            args.append(spec.arg)
        rows = self._db.execute(
            f"{CARD_SELECT} WHERE {' AND '.join(conditions)} ORDER BY {order} LIMIT ?",
            (*args, HOME_HUB_SIZE),
        ).fetchall()
        return self._cards(rows)

    # ---- 详情 ----

    def item(self, item_id: int) -> ItemDetail:
        row = self._db.execute(
            f"""
            {CARD_SELECT_FIELDS}, m.en_title, m.release_date, m.overview, m.content_rating,
                m.studios, m.countries, m.provider_id, ti.hash AS thumb_hash, gi.hash AS logo_hash
            {CARD_FROM}
            LEFT JOIN image ti ON ti.id = m.thumb_image_id
            LEFT JOIN image gi ON gi.id = m.logo_image_id
            WHERE m.id = ? AND {self._scope} AND {PLAYABLE}
            """,
            (item_id, *self._library_ids),
        ).fetchone()
        if row is None:
            raise NotFound("条目不存在")
        favorite = self._favorites().is_favorite(
            "series" if row["kind"] == "series" else "movie", item_id
        )
        watchlisted = self._watchlist().is_watchlisted(item_id)
        credits = self._db.execute(
            """
            SELECT c.role, c.character, p.id, p.name, i.hash AS profile_hash
            FROM credit c JOIN person p ON p.id = c.person_id
            LEFT JOIN image i ON i.id = p.profile_image_id
            WHERE c.media_item_id = ? ORDER BY c.sort_order
            """,
            (item_id,),
        ).fetchall()
        directors = [row_credit(c) for c in credits if c["role"] == "director"]
        writers = [row_credit(c) for c in credits if c["role"] == "writer"]
        cast = [row_credit(c) for c in credits if c["role"] == "cast"][:DETAIL_CAST_SIZE]
        versions: list[Version] = []
        seasons: list[SeasonSummary] = []
        progress = None
        if row["kind"] == "movie":
            versions = self._versions(
                "p.media_item_id = ? AND p.episode_id IS NULL", (item_id,)
            ).get(item_id, [])
            state = self._db.execute(
                "SELECT s.position, s.duration FROM playback_state s "
                "JOIN playable p ON p.id = s.playable_id "
                "WHERE s.user_id = ? AND p.media_item_id = ? AND p.episode_id IS NULL",
                (self._user_id, item_id),
            ).fetchone()
            progress = row_progress(state) if state else None
        else:
            seasons = self._seasons(item_id)
        return ItemDetail(
            card=row_card(row, item_id in self._watched_ids([item_id])),
            en_title=row["en_title"],
            release_date=row["release_date"],
            overview=row["overview"],
            content_rating=row["content_rating"],
            studios=json.loads(row["studios"]),
            countries=json.loads(row["countries"]),
            thumb=image_sizes(row["thumb_hash"], "thumb"),
            logo=image_sizes(row["logo_hash"], "logo"),
            directors=directors,
            writers=writers,
            cast=cast,
            versions=versions,
            seasons=seasons,
            next_episode=self._next_episode(item_id) if seasons else None,
            progress=progress,
            ratings=self._ratings(item_id, row["library_id"], row["provider_id"]),
            watchlisted=watchlisted,
            favorite=favorite,
        )

    def _ratings(self, item_id: int, library_id: int, provider_id: str) -> list[Rating]:
        """各来源的评分：主插件在前，其余按资料库元数据列表的顺序。"""
        rows = self._db.execute(
            "SELECT provider_id, rating FROM media_item_source "
            "WHERE media_item_id = ? AND rating IS NOT NULL",
            (item_id,),
        ).fetchall()
        if not rows:
            return []
        saved = [
            str(row[0])
            for row in self._db.execute(
                "SELECT plugin_id FROM library_plugin WHERE library_id = ? AND role = 'metadata' "
                "ORDER BY position",
                (library_id,),
            ).fetchall()
        ]
        if not saved:
            saved = ["builtin.tmdb"]
        order = {plugin: index for index, plugin in enumerate(saved)}
        names = {
            str(row[0]): str(row[1])
            for row in self._db.execute("SELECT id, name FROM plugin").fetchall()
        }
        ordered = sorted(
            rows,
            key=lambda row: (
                0 if row["provider_id"] == provider_id else 1,
                order.get(str(row["provider_id"]), len(order)),
                str(row["provider_id"]),
            ),
        )
        return [
            Rating(
                source=str(row["provider_id"]),
                label=names.get(str(row["provider_id"]), str(row["provider_id"])),
                value=float(row["rating"]),
            )
            for row in ordered
        ]

    def season(self, season_id: int) -> SeasonDetail:
        row = self._db.execute(
            f"""
            SELECT s.id, s.media_item_id, s.number, s.name, s.overview, s.air_date,
                i.hash AS poster_hash
            FROM season s JOIN media_item m ON m.id = s.media_item_id
            LEFT JOIN image i ON i.id = s.poster_image_id
            WHERE s.id = ? AND {self._scope}
            """,
            (season_id, *self._library_ids),
        ).fetchone()
        if row is None:
            raise NotFound("这一季不存在")
        episodes = self._db.execute(
            f"""
            SELECT e.id, e.number, e.name, e.overview, e.air_date, e.runtime, e.rating,
                p.id AS playable_id, i.hash AS still_hash, COALESCE(s.watched, 0) AS watched,
                s.position, s.duration
            FROM episode e JOIN playable p ON p.episode_id = e.id
            LEFT JOIN image i ON i.id = e.still_image_id
            LEFT JOIN playback_state s ON s.playable_id = p.id AND s.user_id = ?
            WHERE e.season_id = ? AND {EPISODE_PLAYABLE} ORDER BY e.number
            """,
            (self._user_id, season_id),
        ).fetchall()
        if not episodes:
            raise NotFound("这一季不存在")
        episode_favorites = self._favorites().ids_for(
            "episode", [episode["id"] for episode in episodes]
        )
        versions = self._versions("e.season_id = ?", (season_id,), by="p.id")
        return SeasonDetail(
            id=row["id"],
            item_id=row["media_item_id"],
            number=row["number"],
            name=row["name"],
            overview=row["overview"],
            air_date=row["air_date"],
            poster=image_sizes(row["poster_hash"], "poster"),
            episodes=[
                Episode(
                    id=e["id"],
                    number=e["number"],
                    name=e["name"],
                    overview=e["overview"],
                    air_date=e["air_date"],
                    runtime=e["runtime"],
                    rating=e["rating"],
                    still=image_sizes(e["still_hash"], "still"),
                    versions=versions.get(e["playable_id"], []),
                    watched=bool(e["watched"]),
                    progress=row_progress(e),
                    favorite=e["id"] in episode_favorites,
                )
                for e in episodes
            ],
        )

    # ---- 人物 ----

    def person_visible(self, person_id: int) -> bool:
        """人物在可见范围内至少有一部作品。范围外的人物和不存在一样处理。"""
        row = self._db.execute(
            f"SELECT 1 FROM credit c JOIN media_item m ON m.id = c.media_item_id "
            f"WHERE c.person_id = ? AND {self._scope} AND {PLAYABLE} LIMIT 1",
            (person_id, *self._library_ids),
        ).fetchone()
        return row is not None

    def person(self, person_id: int) -> PersonDetail:
        row = self._db.execute(
            """
            SELECT p.id, p.provider_id, p.remote_id, p.name, p.original_name, p.biography,
                p.birthday, p.deathday, p.place_of_birth, p.detail_at, i.hash AS profile_hash
            FROM person p LEFT JOIN image i ON i.id = p.profile_image_id WHERE p.id = ?
            """,
            (person_id,),
        ).fetchone()
        if row is None or not self.person_visible(person_id):
            raise NotFound("找不到这个人")
        credits = self._db.execute(
            f"""
            {CARD_SELECT_FIELDS}, c.role, c.character
            {CARD_FROM} JOIN credit c ON c.media_item_id = m.id
            WHERE c.person_id = ? AND {self._scope} AND {PLAYABLE}
            ORDER BY m.year IS NULL, m.year DESC, m.release_date DESC, m.id, c.sort_order
            """,
            (person_id, *self._library_ids),
        ).fetchall()
        # 同一部作品里又导又演的合成一条
        watched = self._watched_ids([credit["id"] for credit in credits])
        works: dict[int, PersonWork] = {}
        for credit in credits:
            work = works.get(credit["id"])
            roles: list[CreditRole] = [*(work.roles if work else []), credit["role"]]
            character = (work.character if work else None) or credit["character"]
            card = row_card(credit, credit["id"] in watched)
            works[credit["id"]] = PersonWork(card, roles, character)
        return PersonDetail(
            id=row["id"],
            provider_id=row["provider_id"],
            remote_id=row["remote_id"],
            name=row["name"],
            original_name=row["original_name"],
            biography=row["biography"],
            birthday=row["birthday"],
            deathday=row["deathday"],
            place_of_birth=row["place_of_birth"],
            profile=image_sizes(row["profile_hash"], "profile"),
            detailed=row["detail_at"] is not None,
            works=list(works.values()),
            favorite=self._favorites().is_favorite("person", person_id),
        )

    # ---- 搜索 ----

    def search(
        self, query: str, offset: int, limit: int, *, favorite: bool | None = None
    ) -> Page[ItemCard]:
        """在中文名、原名、英文名和演职员姓名里查找；片名完全一致的排最前，其次是开头一致的，
        只靠演职员命中的排在片名命中之后。演职员包括演员、导演和编剧。"""
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        contains, prefix = f"%{escaped}%", f"{escaped}%"
        where = (
            f"{self._scope} AND {PLAYABLE} AND (m.title LIKE ? ESCAPE '\\' "
            "OR m.original_title LIKE ? ESCAPE '\\' OR m.en_title LIKE ? ESCAPE '\\' "
            # 人物先查一次（不相关子查询），不要对每个条目各跑一次 EXISTS：大库里慢 60 倍
            "OR m.id IN (SELECT c.media_item_id FROM credit c WHERE c.person_id IN "
            "(SELECT p.id FROM person p WHERE p.name LIKE ? ESCAPE '\\' "
            "OR p.original_name LIKE ? ESCAPE '\\')))"
        )
        args: list[object] = [*self._library_ids, *(contains,) * 5]
        if favorite is not None:
            where += (
                (" AND EXISTS" if favorite else " AND NOT EXISTS")
                + " (SELECT 1 FROM user_favorite f WHERE f.user_id = ? "
                "AND f.target_kind = 'item' AND f.target_id = m.id)"
            )
            args.append(self._user_id)
        total = self._db.execute(
            f"SELECT COUNT(*) FROM media_item m WHERE {where}", args
        ).fetchone()[0]
        rows = self._db.execute(
            f"""
            {CARD_SELECT} WHERE {where}
            ORDER BY CASE
                WHEN lower(m.title) = lower(?) OR lower(m.original_title) = lower(?)
                    OR lower(m.en_title) = lower(?) THEN 0
                WHEN m.title LIKE ? ESCAPE '\\' OR m.original_title LIKE ? ESCAPE '\\'
                    OR m.en_title LIKE ? ESCAPE '\\' THEN 1
                ELSE 2 END, m.year DESC, m.id DESC
            LIMIT ? OFFSET ?
            """,
            (*args, query, query, query, prefix, prefix, prefix, limit, offset),
        ).fetchall()
        return Page(self._cards(rows), total)

    def search_all(
        self, query: str, offset: int, limit: int, *, favorite: bool | None = None
    ) -> SearchPage:
        """搜索作品、人物和单集；人物与单集只返回少量发现结果，作品保持分页。"""
        page = self.search(query, offset, limit, favorite=favorite)
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        contains, prefix = f"%{escaped}%", f"{escaped}%"
        people = self._db.execute(
            f"""
            SELECT p.id, p.name, p.original_name, i.hash AS profile_hash
            FROM person p
            LEFT JOIN image i ON i.id = p.profile_image_id
            WHERE (p.name LIKE ? ESCAPE '\\' OR p.original_name LIKE ? ESCAPE '\\')
              AND EXISTS (
                SELECT 1 FROM credit c JOIN media_item m ON m.id = c.media_item_id
                WHERE c.person_id = p.id AND {self._scope} AND {PLAYABLE})
            ORDER BY CASE WHEN lower(p.name) = lower(?) THEN 0
                          WHEN p.name LIKE ? ESCAPE '\\' THEN 1 ELSE 2 END,
                     p.name, p.id
            LIMIT 8
            """,
            (contains, contains, *self._library_ids, query, prefix),
        ).fetchall()
        episodes = self._db.execute(
            f"""
            SELECT e.id, m.id AS item_id, s.id AS season_id, s.number AS season_number,
                e.number, e.name, m.title AS show_title, si.hash AS still_hash
            FROM episode e
            JOIN season s ON s.id = e.season_id
            JOIN media_item m ON m.id = s.media_item_id
            JOIN playable p ON p.episode_id = e.id
            LEFT JOIN image si ON si.id = e.still_image_id
            WHERE {self._scope} AND {EPISODE_PLAYABLE}
              AND (e.name LIKE ? ESCAPE '\\' OR m.title LIKE ? ESCAPE '\\'
                   OR m.original_title LIKE ? ESCAPE '\\' OR m.en_title LIKE ? ESCAPE '\\')
            ORDER BY CASE WHEN lower(e.name) = lower(?) THEN 0
                          WHEN e.name LIKE ? ESCAPE '\\' THEN 1 ELSE 2 END,
                     m.title, s.number, e.number
            LIMIT 12
            """,
            (
                *self._library_ids,
                contains,
                contains,
                contains,
                contains,
                query,
                prefix,
            ),
        ).fetchall()
        return SearchPage(
            items=page.items,
            total=page.total,
            people=[
                SearchPerson(
                    id=row["id"],
                    name=row["name"],
                    original_name=row["original_name"],
                    profile=image_sizes(row["profile_hash"], "profile"),
                )
                for row in people
            ],
            episodes=[
                SearchEpisode(
                    id=row["id"],
                    item_id=row["item_id"],
                    season_id=row["season_id"],
                    season_number=row["season_number"],
                    number=row["number"],
                    name=row["name"],
                    show_title=row["show_title"],
                    still=image_sizes(row["still_hash"], "still"),
                )
                for row in episodes
            ],
        )

    # ---- 合集 ----

    def collection_cards(self, collection_id: int) -> list[ItemCard]:
        """合集里会出现在影视墙上的成员，按合集顺序；合集本身是否可见由调用方检查。"""
        rows = self._db.execute(
            f"{CARD_SELECT} JOIN collection_item ci ON ci.media_item_id = m.id "
            f"WHERE ci.collection_id = ? AND {self._scope} AND {PLAYABLE} "
            "ORDER BY ci.position, m.title, m.id",
            (collection_id, *self._library_ids),
        ).fetchall()
        return self._cards(rows)

    def collection_covers(self, collection_ids: Sequence[int]) -> dict[int, CollectionCover]:
        """合集封面、头图和成员数。成员只算当前用户看得到、有可用文件的。

        封面依次取：管理员指定的成员的海报、合集自己的海报（TMDB 系列）、合集顺序里第一个
        有海报的成员的海报；头图取合集自己的背景图，没有时用第一个有背景图的成员的。"""
        if not collection_ids:
            return {}
        marks = ", ".join("?" for _ in collection_ids)
        rows = self._db.execute(
            "SELECT ci.collection_id, ci.media_item_id, pi.hash AS poster_hash, "
            "bi.hash AS backdrop_hash FROM collection_item ci "
            "JOIN media_item m ON m.id = ci.media_item_id "
            "LEFT JOIN image pi ON pi.id = m.poster_image_id "
            "LEFT JOIN image bi ON bi.id = m.backdrop_image_id "
            f"WHERE ci.collection_id IN ({marks}) AND {self._scope} AND {PLAYABLE} "
            "ORDER BY ci.collection_id, ci.position, m.title, m.id",
            (*collection_ids, *self._library_ids),
        ).fetchall()
        own = {
            row["id"]: row
            for row in self._db.execute(
                "SELECT c.id, c.cover_item_id, pi.hash AS poster_hash, bi.hash AS backdrop_hash "
                "FROM collection c LEFT JOIN image pi ON pi.id = c.poster_image_id "
                f"LEFT JOIN image bi ON bi.id = c.backdrop_image_id WHERE c.id IN ({marks})",
                tuple(collection_ids),
            )
        }
        counts: dict[int, int] = {}
        first_poster: dict[int, str] = {}
        first_backdrop: dict[int, str] = {}
        chosen: dict[int, str] = {}
        for row in rows:
            cid = row["collection_id"]
            counts[cid] = counts.get(cid, 0) + 1
            if row["poster_hash"]:
                first_poster.setdefault(cid, row["poster_hash"])
                if own[cid]["cover_item_id"] == row["media_item_id"]:
                    chosen[cid] = row["poster_hash"]
            if row["backdrop_hash"]:
                first_backdrop.setdefault(cid, row["backdrop_hash"])
        covers: dict[int, CollectionCover] = {}
        for cid in collection_ids:
            mine = own.get(cid)
            poster = chosen.get(cid) or (mine and mine["poster_hash"]) or first_poster.get(cid)
            backdrop = (mine and mine["backdrop_hash"]) or first_backdrop.get(cid)
            covers[cid] = CollectionCover(
                counts.get(cid, 0), image_sizes(poster, "poster"), image_sizes(backdrop, "backdrop")
            )
        return covers

    # ---- 内部 ----

    def _library(self, library_id: int) -> LibrarySummary:
        for library in self.libraries():
            if library.id == library_id:
                return library
        raise NotFound("资料库不存在")

    def _cards(self, rows: list[sqlite3.Row]) -> list[ItemCard]:
        watched = self._watched_ids([row["id"] for row in rows])
        return [row_card(row, row["id"] in watched) for row in rows]

    def cards_by_ids(self, item_ids: Sequence[int]) -> list[ItemCard]:
        """按给定顺序取卡片，收藏列表用。可见范围由调用方负责。"""
        ids = [item_id for item_id in dict.fromkeys(item_ids)]
        if not ids:
            return []
        rows = self._db.execute(
            f"{CARD_SELECT} WHERE m.id IN ({', '.join('?' for _ in ids)})", ids
        ).fetchall()
        cards = {card.id: card for card in self._cards(rows)}
        return [cards[item_id] for item_id in ids if item_id in cards]

    def episode_refs_by_ids(self, episode_ids: Sequence[int]) -> list[EpisodeRef]:
        """按给定顺序取单集卡片（首页继续观看的模型），收藏列表用。"""
        ids = [episode_id for episode_id in dict.fromkeys(episode_ids)]
        if not ids:
            return []
        rows = self._db.execute(
            f"""
            SELECT e.id, e.season_id, s.number AS season_number, e.number, e.name,
                ps.position, ps.duration
            FROM episode e JOIN season s ON s.id = e.season_id
            LEFT JOIN playable p ON p.episode_id = e.id
            LEFT JOIN playback_state ps ON ps.playable_id = p.id AND ps.user_id = ?
            WHERE e.id IN ({", ".join("?" for _ in ids)})
            """,
            (self._user_id, *ids),
        ).fetchall()
        refs = {row["id"]: row_episode_ref(row, row_progress(row)) for row in rows}
        return [refs[episode_id] for episode_id in ids if episode_id in refs]

    def episode_play_cards(
        self, episode_ids: Sequence[int], played_at: Mapping[int, str]
    ) -> list[RecentPlay]:
        """按给定顺序取首页「继续观看」的单集卡片，收藏列表用。

        收藏的单集不一定播放过，这时 played_at 用收藏时间；进度仍然来自播放记录。
        """
        ids = [episode_id for episode_id in dict.fromkeys(episode_ids)]
        if not ids:
            return []
        rows = self._db.execute(
            f"""
            SELECT e.id, e.season_id, s.number AS season_number, e.number, e.name,
                s.media_item_id AS item_id, i.hash AS still_hash,
                ps.last_played_at, ps.position, ps.duration
            FROM episode e JOIN season s ON s.id = e.season_id
            JOIN media_item m ON m.id = s.media_item_id
            LEFT JOIN image i ON i.id = e.still_image_id
            LEFT JOIN playable p ON p.episode_id = e.id
            LEFT JOIN playback_state ps ON ps.playable_id = p.id AND ps.user_id = ?
            WHERE e.id IN ({", ".join("?" for _ in ids)})
            """,
            (self._user_id, *ids),
        ).fetchall()
        by_id = {row["id"]: row for row in rows}
        cards = {card.id: card for card in self.cards_by_ids([row["item_id"] for row in rows])}
        result = []
        for episode_id in ids:
            row = by_id.get(episode_id)
            if row is None or row["item_id"] not in cards:
                continue
            progress = row_progress(row)
            result.append(
                RecentPlay(
                    item=cards[row["item_id"]],
                    episode=row_episode_ref(row, progress),
                    still=image_sizes(row["still_hash"], "still"),
                    played_at=row["last_played_at"] or played_at.get(episode_id, ""),
                    progress=progress,
                )
            )
        return result

    def people_by_ids(self, person_ids: Sequence[int]) -> list[SearchPerson]:
        """按给定顺序取演员卡片，收藏列表用。可见范围由调用方负责。"""
        ids = [person_id for person_id in dict.fromkeys(person_ids)]
        if not ids:
            return []
        rows = self._db.execute(
            f"""
            SELECT p.id, p.name, p.original_name, i.hash AS profile_hash
            FROM person p LEFT JOIN image i ON i.id = p.profile_image_id
            WHERE p.id IN ({", ".join("?" for _ in ids)})
            """,
            ids,
        ).fetchall()
        people = {
            row["id"]: SearchPerson(
                id=row["id"],
                name=row["name"],
                original_name=row["original_name"],
                profile=image_sizes(row["profile_hash"], "profile"),
            )
            for row in rows
        }
        return [people[person_id] for person_id in ids if person_id in people]

    def _favorites(self) -> Favorites:
        # 收藏模块要用 visible_library_ids，放在函数里导入避免循环依赖
        from app.library.favorites import Favorites

        return Favorites(self._db, self._user)

    def _watchlist(self) -> Watchlist:
        # 稍后观看模块要用 visible_library_ids，放在函数里导入避免循环依赖
        from app.library.watchlist import Watchlist

        return Watchlist(self._db, self._user)

    def _watched_ids(self, item_ids: Sequence[int]) -> set[int]:
        """其中当前用户已看完的条目。"""
        if not item_ids:
            return set()
        ids = sorted(set(item_ids))
        rows = self._db.execute(
            f"""
            WITH active_playable AS (
                SELECT DISTINCT p.id, p.media_item_id
                FROM playable p
                JOIN version v ON v.playable_id = p.id
                JOIN media_file c ON c.id = v.media_file_id AND +c.state = 'active'
                WHERE p.media_item_id IN ({", ".join("?" for _ in ids)})
            )
            SELECT ap.media_item_id AS id
            FROM active_playable ap
            LEFT JOIN playback_state s
                ON s.playable_id = ap.id AND s.user_id = ? AND s.watched = 1
            GROUP BY ap.media_item_id
            HAVING COUNT(*) = COUNT(s.playable_id)
            """,
            (*ids, self._user_id),
        ).fetchall()
        return {row["id"] for row in rows}

    def _recent(
        self, *, library_id: int | None = None, limit: int = HOME_RECENT_SIZE
    ) -> list[RecentPlay]:
        """当前用户最近播放的作品；给定 library_id 时只取这个资料库的。"""
        scope = "m.library_id = ?" if library_id is not None else self._scope
        args: list[object] = [self._user_id]
        if library_id is None:
            args.extend(self._library_ids)
        else:
            args.append(library_id)
        rows = self._db.execute(
            f"""
            SELECT media_item_id, episode_id, last_played_at, position, duration FROM (
                SELECT p.media_item_id, p.episode_id, s.last_played_at, s.position, s.duration,
                    ROW_NUMBER() OVER (
                        PARTITION BY p.media_item_id ORDER BY s.last_played_at DESC, p.id DESC
                    ) AS rank
                FROM playback_state s JOIN playable p ON p.id = s.playable_id
                JOIN media_item m ON m.id = p.media_item_id
                WHERE s.user_id = ? AND s.last_played_at IS NOT NULL
                    AND {scope} AND {EPISODE_PLAYABLE}
            ) WHERE rank = 1 ORDER BY last_played_at DESC, media_item_id DESC LIMIT ?
            """,
            (*args, limit),
        ).fetchall()
        if not rows:
            return []
        item_ids = [row["media_item_id"] for row in rows]
        cards = {
            card.id: card
            for card in self._cards(
                self._db.execute(
                    f"{CARD_SELECT} WHERE m.id IN ({', '.join('?' for _ in item_ids)})",
                    item_ids,
                ).fetchall()
            )
        }
        episode_ids = [row["episode_id"] for row in rows if row["episode_id"] is not None]
        episodes = {
            row["id"]: row
            for row in self._db.execute(
                f"""
                SELECT e.id, e.season_id, s.number AS season_number, e.number, e.name,
                    i.hash AS still_hash
                FROM episode e JOIN season s ON s.id = e.season_id
                LEFT JOIN image i ON i.id = e.still_image_id
                WHERE e.id IN ({", ".join("?" for _ in episode_ids)})
                """,
                episode_ids,
            ).fetchall()
        }
        recent = []
        for row in rows:
            episode = episodes.get(row["episode_id"])
            progress = row_progress(row)
            recent.append(
                RecentPlay(
                    item=cards[row["media_item_id"]],
                    episode=row_episode_ref(episode, progress) if episode else None,
                    still=image_sizes(episode["still_hash"], "still") if episode else [],
                    played_at=row["last_played_at"],
                    progress=progress,
                )
            )
        return recent

    def _seasons(self, item_id: int) -> list[SeasonSummary]:
        rows = self._db.execute(
            f"""
            SELECT s.id, s.number, s.name, s.air_date, i.hash AS poster_hash,
                COUNT(e.id) AS episode_count
            FROM season s JOIN episode e ON e.season_id = s.id
            JOIN playable p ON p.episode_id = e.id
            LEFT JOIN image i ON i.id = s.poster_image_id
            WHERE s.media_item_id = ? AND {EPISODE_PLAYABLE}
            GROUP BY s.id
            -- 特别篇（第 0 季）排在最后
            ORDER BY s.number = 0, s.number
            """,
            (item_id,),
        ).fetchall()
        return [
            SeasonSummary(
                id=r["id"],
                number=r["number"],
                name=r["name"],
                air_date=r["air_date"],
                poster=image_sizes(r["poster_hash"], "poster"),
                episode_count=r["episode_count"],
            )
            for r in rows
        ]

    def _next_episode(self, item_id: int) -> EpisodeRef | None:
        """推荐接着看的一集。

        最近播放的那一集还没标记已看就接着看它，否则往后找第一集没看的；后面都看过了，
        从头找第一集没看的；全都看过了给第一集。
        """
        rows = self._db.execute(
            f"""
            SELECT e.id, e.season_id, s.number AS season_number, e.number, e.name,
                COALESCE(ps.watched, 0) AS watched, ps.last_played_at, ps.position, ps.duration
            FROM episode e JOIN season s ON s.id = e.season_id
            JOIN playable p ON p.episode_id = e.id
            LEFT JOIN playback_state ps ON ps.playable_id = p.id AND ps.user_id = ?
            WHERE s.media_item_id = ? AND {EPISODE_PLAYABLE}
            ORDER BY s.number = 0, s.number, e.number
            """,
            (self._user_id, item_id),
        ).fetchall()
        # 特别篇不参与推荐，除非这部剧只有特别篇
        rows = [row for row in rows if row["season_number"] != 0] or rows
        if not rows:
            return None
        played = [i for i, row in enumerate(rows) if row["last_played_at"]]
        start = max(played, key=lambda i: rows[i]["last_played_at"]) if played else 0
        unwatched = [row for row in rows if not row["watched"]]
        later = [row for row in rows[start:] if not row["watched"]]
        row = (later or unwatched or rows)[0]
        return row_episode_ref(row, row_progress(row))

    def _versions(
        self, condition: str, args: Sequence[object], by: str = "p.media_item_id"
    ) -> dict[int, list[Version]]:
        """可用文件对应的版本，按 by 分组，大的在前。"""
        rows = self._db.execute(
            f"""
            SELECT {by} AS owner, v.id, v.label, c.size,
                s.id AS subtitle_id, s.label AS subtitle_label,
                s.language AS subtitle_language, s.forced AS subtitle_forced,
                s.source AS subtitle_source,
                mi.media_file_id, mi.duration AS media_duration,
                mi.width AS media_width, mi.height AS media_height,
                mi.video_codec AS media_video_codec, mi.hdr AS media_hdr,
                mi.audio AS media_audio, mi.subtitles AS media_subtitles
            FROM version v JOIN playable p ON p.id = v.playable_id
            JOIN media_file c ON c.id = v.media_file_id
            -- 上报之后文件被替换（大小或 sha1 变了）的流信息不再可信
            LEFT JOIN media_info mi ON mi.media_file_id = c.id
                AND mi.size IS c.size AND mi.sha1 IS c.sha1
            LEFT JOIN episode e ON e.id = p.episode_id
            -- 字幕文件不可用时只忽略这条字幕，版本本身仍要出现在详情里
            LEFT JOIN subtitle s ON s.version_id = v.id
                AND (s.source = 'online' OR EXISTS (
                    SELECT 1 FROM media_file sc
                    WHERE sc.id = s.media_file_id AND sc.state = 'active'
                        AND sc.playback_locator IS NOT NULL
                ))
            WHERE {condition} AND c.state = 'active'
            ORDER BY c.size IS NULL, c.size DESC, v.id
            """,
            args,
        ).fetchall()
        grouped: dict[int, list[Version]] = {}
        for row in rows:
            versions = grouped.setdefault(row["owner"], [])
            version = next((item for item in versions if item.id == row["id"]), None)
            if version is None:
                version = Version(row["id"], row["label"], row["size"], [], row_media_info(row))
                versions.append(version)
            if row["subtitle_id"] is not None:
                version.subtitles.append(
                    Subtitle(
                        id=row["subtitle_id"],
                        label=row["subtitle_label"],
                        language=row["subtitle_language"],
                        forced=bool(row["subtitle_forced"]),
                        source=row["subtitle_source"],
                    )
                )
        return grouped
