"""Emby 兼容接口的浏览查询：条目、季、单集、人物的统一列表和筛选（ADR 0024）。

和影视墙一样限定在用户的可见范围内；只列出至少有一个可用文件的目标。查询结果用
EmbyEntry 表示，PascalCase 的 DTO 由 app.emby.dto 构造，SQL 不散落在路由里。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from app.auth.users import User
from app.library.browse import visible_library_ids
from app.library.browse_rows import EPISODE_PLAYABLE, PLAYABLE, WATCHED

# Emby 的 BaseItemDto.Type；资料库由 CollectionFolder 单独表示
ItemType = str
ITEM_TYPES = ("Movie", "Series", "Season", "Episode", "Person")

_FAVORITE_ITEM = (
    "EXISTS (SELECT 1 FROM user_favorite uf WHERE uf.user_id = ? "
    "AND uf.target_kind = 'item' AND uf.target_id = m.id)"
)
_FAVORITE_EPISODE = (
    "EXISTS (SELECT 1 FROM user_favorite uf WHERE uf.user_id = ? "
    "AND uf.target_kind = 'episode' AND uf.target_id = e.id)"
)
_FAVORITE_PERSON = (
    "EXISTS (SELECT 1 FROM user_favorite uf WHERE uf.user_id = ? "
    "AND uf.target_kind = 'person' AND uf.target_id = p.id)"
)

_COLUMNS = """
    type, id, media_item_id, library_id, title, sort_name, original_title, overview,
    year, rating, runtime, added_at, release_date, genres, content_rating,
    poster_hash, backdrop_hash, thumb_hash, logo_hash, profile_hash,
    series_name, season_name, series_id, season_id, season_number, episode_number,
    index_number, playable_id, watched, favorite, position, duration, last_played_at,
    play_count, unplayed_count, child_count, birthday, deathday, place_of_birth
"""


@dataclass(frozen=True)
class EmbyEntry:
    type: str
    id: int
    media_item_id: int | None
    library_id: int | None
    title: str
    sort_name: str
    original_title: str | None
    overview: str | None
    year: int | None
    rating: float | None
    # 分钟
    runtime: int | None
    added_at: str | None
    release_date: str | None
    genres: str
    content_rating: str | None
    poster_hash: str | None
    backdrop_hash: str | None
    thumb_hash: str | None
    logo_hash: str | None
    profile_hash: str | None
    series_name: str | None
    season_name: str | None
    series_id: int | None
    season_id: int | None
    season_number: int | None
    episode_number: int | None
    index_number: int | None
    playable_id: int | None
    watched: bool
    favorite: bool
    position: int | None
    duration: int | None
    last_played_at: str | None
    play_count: int
    unplayed_count: int
    child_count: int
    birthday: str | None
    deathday: str | None
    place_of_birth: str | None


@dataclass(frozen=True)
class EmbyBrowse:
    items: list[EmbyEntry]
    total: int


class EmbyQueries:
    def __init__(self, connection: sqlite3.Connection, user: User) -> None:
        self._db = connection
        self._user = user
        self._library_ids = visible_library_ids(connection, user)
        self._scope = f"m.library_id IN ({', '.join('?' for _ in self._library_ids) or 'NULL'})"

    # ---- 列表 ----

    def browse(
        self,
        *,
        include_types: Sequence[str],
        parent_type: str | None = None,
        parent_id: int | None = None,
        favorite: bool | None = None,
        played: bool | None = None,
        resumable: bool = False,
        search: str | None = None,
        ids: Sequence[tuple[str, int]] | None = None,
        genres: Sequence[str] = (),
        years: Sequence[int] = (),
        person_ids: Sequence[int] = (),
        sort_by: Sequence[str] = ("SortName",),
        descending: bool = False,
        offset: int = 0,
        limit: int = 100,
    ) -> EmbyBrowse:
        types = [item_type for item_type in ITEM_TYPES if item_type in include_types]
        if not types:
            return EmbyBrowse([], 0)
        union, params = self._union(types)
        conditions, filter_params = self._filters(
            parent_type=parent_type,
            parent_id=parent_id,
            favorite=favorite,
            played=played,
            resumable=resumable,
            search=search,
            ids=ids,
            genres=genres,
            years=years,
            person_ids=person_ids,
        )
        where = " AND ".join(conditions) or "1 = 1"
        total = self._db.execute(
            f"SELECT COUNT(*) FROM ({union}) entries WHERE {where}",
            (*params, *filter_params),
        ).fetchone()[0]
        order = _order_by(sort_by, descending)
        rows = self._db.execute(
            f"SELECT {_COLUMNS} FROM ({union}) entries WHERE {where} "
            f"ORDER BY {order} LIMIT ? OFFSET ?",
            (*params, *filter_params, limit, offset),
        ).fetchall()
        return EmbyBrowse([_entry(row) for row in rows], total)

    def get(self, item_type: str, item_id: int) -> EmbyEntry | None:
        page = self.browse(include_types=[item_type], ids=[(item_type, item_id)], limit=1, offset=0)
        return page.items[0] if page.items else None

    def item_kind(self, item_id: int) -> str | None:
        row = self._db.execute("SELECT kind FROM media_item WHERE id = ?", (item_id,)).fetchone()
        return None if row is None else str(row["kind"])

    def seasons_of(self, series_id: int) -> list[EmbyEntry]:
        seasons = self.browse(
            include_types=["Season"], parent_type="Series", parent_id=series_id, limit=1000
        ).items
        # 特别篇（第 0 季）排在最后，和内置详情页一致
        return sorted(seasons, key=lambda entry: (entry.season_number == 0, entry.season_number))

    def episodes_of(self, series_id: int, season_id: int | None = None) -> list[EmbyEntry]:
        episodes = self.browse(
            include_types=["Episode"],
            parent_type="Season" if season_id is not None else "Series",
            parent_id=season_id if season_id is not None else series_id,
            sort_by=["ParentIndexNumber", "IndexNumber"],
            limit=10000,
        ).items
        return sorted(
            episodes,
            key=lambda entry: (
                entry.season_number == 0,
                entry.season_number or 0,
                entry.index_number or 0,
            ),
        )

    def next_up(self, limit: int) -> list[EmbyEntry]:
        """每部剧推荐接着看的一集：最近播放且没看完的那集，否则往后第一集没看的。

        和首页 next_episode 的规则一致；这里只返回有可播放文件、未看完的单集。
        """
        rows = self._db.execute(
            f"SELECT DISTINCT m.id FROM media_item m WHERE m.kind = 'series' "
            f"AND {self._scope} AND {PLAYABLE} ORDER BY m.added_at DESC, m.id DESC LIMIT ?",
            (*self._library_ids, max(limit * 3, limit)),
        ).fetchall()
        result: list[EmbyEntry] = []
        for row in rows:
            episodes = self.episodes_of(int(row["id"]))
            # 特别篇不参与推荐，除非这部剧只有特别篇
            episodes = [episode for episode in episodes if episode.season_number != 0] or episodes
            if not episodes:
                continue
            played = [
                (position, episode)
                for position, episode in enumerate(episodes)
                if episode.last_played_at is not None
            ]
            start = max(played, key=lambda item: item[1].last_played_at or "")[0] if played else 0
            unwatched = [episode for episode in episodes if not episode.watched]
            later = [episode for episode in episodes[start:] if not episode.watched]
            if not unwatched:
                continue
            result.append((later or unwatched)[0])
            if len(result) >= limit:
                break
        return result

    def resume(self, limit: int) -> list[EmbyEntry]:
        """电影和单集有续播位置且没看完的内容，按最近播放排序。"""
        pages = []
        for item_type in ("Movie", "Episode"):
            pages.extend(
                self.browse(
                    include_types=[item_type],
                    resumable=True,
                    sort_by=["DatePlayed"],
                    descending=True,
                    limit=limit,
                ).items
            )
        pages.sort(key=lambda entry: entry.last_played_at or "", reverse=True)
        return pages[:limit]

    def latest(self, parent_type: str | None, parent_id: int | None, limit: int) -> list[EmbyEntry]:
        """最近添加：资料库里是电影和剧集，剧集里是单集。"""
        if parent_type == "Series" and parent_id is not None:
            page = self.browse(
                include_types=["Episode"],
                parent_type="Series",
                parent_id=parent_id,
                sort_by=["DateCreated"],
                descending=True,
                limit=limit,
            )
            return page.items
        page = self.browse(
            include_types=["Movie", "Series"],
            parent_type=parent_type,
            parent_id=parent_id,
            sort_by=["DateCreated"],
            descending=True,
            limit=limit,
        )
        return page.items

    def playable_duration(self, playable_id: int) -> int | None:
        """播放进度需要总时长：优先媒体信息，其次单集或条目的分钟数。"""
        row = self._db.execute(
            """
            SELECT COALESCE(
                (SELECT mi.duration FROM version v
                    JOIN media_file c ON c.id = v.media_file_id
                    JOIN media_info mi ON mi.media_file_id = c.id
                        AND mi.size IS c.size AND mi.sha1 IS c.sha1
                    WHERE v.playable_id = p.id AND c.state = 'active'
                    ORDER BY c.size IS NULL, c.size DESC LIMIT 1),
                e.runtime * 60, m.runtime * 60
            ) AS duration
            FROM playable p JOIN media_item m ON m.id = p.media_item_id
            LEFT JOIN episode e ON e.id = p.episode_id
            WHERE p.id = ?
            """,
            (playable_id,),
        ).fetchone()
        if row is None or row["duration"] is None:
            return None
        return int(row["duration"])

    # ---- SQL ----

    def _union(self, types: Sequence[str]) -> tuple[str, list[object]]:
        branches = {
            "Movie": self._movie,
            "Series": self._series,
            "Season": self._season,
            "Episode": self._episode,
            "Person": self._person,
        }
        selects: list[str] = []
        params: list[object] = []
        for item_type in types:
            sql, branch_params = branches[item_type]()
            selects.append(sql)
            params.extend(branch_params)
        return " UNION ALL ".join(selects), params

    def _movie(self) -> tuple[str, list[object]]:
        sql = f"""
            SELECT 'Movie' AS type, m.id AS id, m.id AS media_item_id, m.library_id,
                m.title, COALESCE(m.sort_title, m.title) AS sort_name, m.original_title,
                m.overview, m.year, m.rating, m.runtime, m.added_at, m.release_date,
                m.genres, m.content_rating, pi.hash AS poster_hash, bi.hash AS backdrop_hash,
                ti.hash AS thumb_hash, gi.hash AS logo_hash, NULL AS profile_hash,
                NULL AS series_name, NULL AS season_name, m.id AS series_id, NULL AS season_id,
                NULL AS season_number, NULL AS episode_number, NULL AS index_number,
                mp.id AS playable_id,
                {WATCHED} AS watched, {_FAVORITE_ITEM} AS favorite,
                ps.position, ps.duration, ps.last_played_at,
                (SELECT COUNT(*) FROM play_event ev WHERE ev.user_id = ? AND ev.playable_id = mp.id)
                    AS play_count,
                0 AS unplayed_count, 0 AS child_count,
                NULL AS birthday, NULL AS deathday, NULL AS place_of_birth
            FROM media_item m
            LEFT JOIN image pi ON pi.id = m.poster_image_id
            LEFT JOIN image bi ON bi.id = m.backdrop_image_id
            LEFT JOIN image ti ON ti.id = m.thumb_image_id
            LEFT JOIN image gi ON gi.id = m.logo_image_id
            LEFT JOIN playable mp ON mp.media_item_id = m.id AND mp.episode_id IS NULL
            LEFT JOIN playback_state ps ON ps.playable_id = mp.id AND ps.user_id = ?
            WHERE {self._scope} AND {PLAYABLE} AND m.kind = 'movie'
        """
        params: list[object] = [
            self._user.id,
            self._user.id,
            self._user.id,
            self._user.id,
            *self._library_ids,
        ]
        return sql, params

    def _series(self) -> tuple[str, list[object]]:
        sql = f"""
            SELECT 'Series' AS type, m.id AS id, m.id AS media_item_id, m.library_id,
                m.title, COALESCE(m.sort_title, m.title) AS sort_name, m.original_title,
                m.overview, m.year, m.rating, m.runtime, m.added_at, m.release_date,
                m.genres, m.content_rating, pi.hash AS poster_hash, bi.hash AS backdrop_hash,
                ti.hash AS thumb_hash, gi.hash AS logo_hash, NULL AS profile_hash,
                NULL AS series_name, NULL AS season_name, m.id AS series_id, NULL AS season_id,
                NULL AS season_number, NULL AS episode_number, NULL AS index_number,
                NULL AS playable_id, {WATCHED} AS watched, {_FAVORITE_ITEM} AS favorite,
                NULL AS position, NULL AS duration, NULL AS last_played_at, 0 AS play_count,
                (SELECT COUNT(*) FROM playable up
                    WHERE up.media_item_id = m.id AND EXISTS (
                        SELECT 1 FROM version uv JOIN media_file uc ON uc.id = uv.media_file_id
                        WHERE uv.playable_id = up.id AND uc.state = 'active')
                        AND NOT EXISTS (SELECT 1 FROM playback_state us
                            WHERE us.user_id = ? AND us.playable_id = up.id AND us.watched = 1))
                    AS unplayed_count,
                (SELECT COUNT(*) FROM season ss WHERE ss.media_item_id = m.id AND EXISTS (
                    SELECT 1 FROM episode ee JOIN playable ep ON ep.episode_id = ee.id
                    WHERE ee.season_id = ss.id AND EXISTS (
                        SELECT 1 FROM version ev JOIN media_file ec ON ec.id = ev.media_file_id
                        WHERE ev.playable_id = ep.id AND ec.state = 'active')))
                    AS child_count,
                NULL AS birthday, NULL AS deathday, NULL AS place_of_birth
            FROM media_item m
            LEFT JOIN image pi ON pi.id = m.poster_image_id
            LEFT JOIN image bi ON bi.id = m.backdrop_image_id
            LEFT JOIN image ti ON ti.id = m.thumb_image_id
            LEFT JOIN image gi ON gi.id = m.logo_image_id
            WHERE {self._scope} AND {PLAYABLE} AND m.kind = 'series'
        """
        params: list[object] = [
            self._user.id,
            self._user.id,
            self._user.id,
            *self._library_ids,
        ]
        return sql, params

    def _season(self) -> tuple[str, list[object]]:
        sql = f"""
            SELECT 'Season' AS type, s.id AS id, m.id AS media_item_id, m.library_id,
                COALESCE(s.name, '第 ' || s.number || ' 季') AS title,
                COALESCE(s.name, '第 ' || s.number || ' 季') AS sort_name, m.original_title,
                s.overview, m.year, NULL AS rating, NULL AS runtime, m.added_at,
                s.air_date AS release_date,
                m.genres, m.content_rating, si.hash AS poster_hash, bi.hash AS backdrop_hash,
                NULL AS thumb_hash, NULL AS logo_hash, NULL AS profile_hash,
                m.title AS series_name, s.name AS season_name, m.id AS series_id,
                s.id AS season_id, s.number AS season_number, NULL AS episode_number,
                s.number AS index_number, NULL AS playable_id,
                (NOT EXISTS (SELECT 1 FROM episode we JOIN playable wp ON wp.episode_id = we.id
                    WHERE we.season_id = s.id AND EXISTS (
                        SELECT 1 FROM version wv JOIN media_file wc ON wc.id = wv.media_file_id
                        WHERE wv.playable_id = wp.id AND wc.state = 'active')
                    AND NOT EXISTS (SELECT 1 FROM playback_state ws
                        WHERE ws.user_id = ? AND ws.playable_id = wp.id AND ws.watched = 1)))
                    AS watched,
                0 AS favorite, NULL AS position, NULL AS duration, NULL AS last_played_at,
                0 AS play_count,
                (SELECT COUNT(*) FROM episode ue JOIN playable up ON up.episode_id = ue.id
                    WHERE ue.season_id = s.id AND EXISTS (
                        SELECT 1 FROM version uv JOIN media_file uc ON uc.id = uv.media_file_id
                        WHERE uv.playable_id = up.id AND uc.state = 'active')
                    AND NOT EXISTS (SELECT 1 FROM playback_state us
                        WHERE us.user_id = ? AND us.playable_id = up.id AND us.watched = 1))
                    AS unplayed_count,
                (SELECT COUNT(*) FROM episode ce JOIN playable cp ON cp.episode_id = ce.id
                    WHERE ce.season_id = s.id AND EXISTS (
                        SELECT 1 FROM version cv JOIN media_file cc ON cc.id = cv.media_file_id
                        WHERE cv.playable_id = cp.id AND cc.state = 'active'))
                    AS child_count,
                NULL AS birthday, NULL AS deathday, NULL AS place_of_birth
            FROM season s JOIN media_item m ON m.id = s.media_item_id
            LEFT JOIN image si ON si.id = s.poster_image_id
            LEFT JOIN image bi ON bi.id = m.backdrop_image_id
            WHERE {self._scope} AND EXISTS (
                SELECT 1 FROM episode pe JOIN playable pp ON pp.episode_id = pe.id
                WHERE pe.season_id = s.id AND EXISTS (
                    SELECT 1 FROM version pv JOIN media_file pc ON pc.id = pv.media_file_id
                    WHERE pv.playable_id = pp.id AND pc.state = 'active'))
        """
        params: list[object] = [
            self._user.id,
            self._user.id,
            *self._library_ids,
        ]
        return sql, params

    def _episode(self) -> tuple[str, list[object]]:
        sql = f"""
            SELECT 'Episode' AS type, e.id AS id, m.id AS media_item_id, m.library_id,
                COALESCE(e.name, '第 ' || e.number || ' 集') AS title,
                COALESCE(e.name, '第 ' || e.number || ' 集') AS sort_name, m.original_title,
                e.overview, m.year, e.rating, e.runtime, m.added_at,
                e.air_date AS release_date, m.genres,
                m.content_rating, ei.hash AS poster_hash, bi.hash AS backdrop_hash,
                ei.hash AS thumb_hash, gi.hash AS logo_hash, NULL AS profile_hash,
                m.title AS series_name, s.name AS season_name, m.id AS series_id,
                s.id AS season_id, s.number AS season_number, e.number AS episode_number,
                e.number AS index_number, p.id AS playable_id, COALESCE(ps.watched, 0) AS watched,
                {_FAVORITE_EPISODE} AS favorite, ps.position, ps.duration, ps.last_played_at,
                (SELECT COUNT(*) FROM play_event ev WHERE ev.user_id = ? AND ev.playable_id = p.id)
                    AS play_count,
                0 AS unplayed_count, 0 AS child_count,
                NULL AS birthday, NULL AS deathday, NULL AS place_of_birth
            FROM episode e JOIN season s ON s.id = e.season_id
            JOIN media_item m ON m.id = s.media_item_id
            JOIN playable p ON p.episode_id = e.id
            LEFT JOIN image ei ON ei.id = e.still_image_id
            LEFT JOIN image bi ON bi.id = m.backdrop_image_id
            LEFT JOIN image gi ON gi.id = m.logo_image_id
            LEFT JOIN playback_state ps ON ps.playable_id = p.id AND ps.user_id = ?
            WHERE {self._scope} AND {EPISODE_PLAYABLE}
        """
        params: list[object] = [
            self._user.id,
            self._user.id,
            self._user.id,
            *self._library_ids,
        ]
        return sql, params

    def _person(self) -> tuple[str, list[object]]:
        sql = f"""
            SELECT 'Person' AS type, p.id AS id, NULL AS media_item_id, NULL AS library_id,
                p.name AS title, p.name AS sort_name, p.original_name AS original_title,
                p.biography AS overview,
                NULL AS year, NULL AS rating, NULL AS runtime, NULL AS added_at,
                p.birthday AS release_date, '[]' AS genres, NULL AS content_rating,
                NULL AS poster_hash, NULL AS backdrop_hash, NULL AS thumb_hash,
                NULL AS logo_hash, pi.hash AS profile_hash, NULL AS series_name,
                NULL AS season_name, NULL AS series_id, NULL AS season_id,
                NULL AS season_number, NULL AS episode_number, NULL AS index_number,
                NULL AS playable_id, 0 AS watched, {_FAVORITE_PERSON} AS favorite,
                NULL AS position, NULL AS duration, NULL AS last_played_at, 0 AS play_count,
                0 AS unplayed_count, 0 AS child_count,
                p.birthday, p.deathday, p.place_of_birth
            FROM person p LEFT JOIN image pi ON pi.id = p.profile_image_id
            WHERE EXISTS (
                SELECT 1 FROM credit c JOIN media_item m ON m.id = c.media_item_id
                WHERE c.person_id = p.id AND {self._scope} AND {PLAYABLE})
        """
        params: list[object] = [self._user.id, *self._library_ids]
        return sql, params

    def _filters(
        self,
        *,
        parent_type: str | None,
        parent_id: int | None,
        favorite: bool | None,
        played: bool | None,
        resumable: bool,
        search: str | None,
        ids: Sequence[tuple[str, int]] | None,
        genres: Sequence[str],
        years: Sequence[int],
        person_ids: Sequence[int],
    ) -> tuple[list[str], list[object]]:
        conditions: list[str] = []
        args: list[object] = []
        parent_column = {
            "Library": "library_id",
            "Series": "series_id",
            "Season": "season_id",
            "Movie": "media_item_id",
        }
        if parent_id is not None and parent_type in parent_column:
            conditions.append(f"{parent_column[parent_type]} = ?")
            args.append(parent_id)
        if favorite is not None:
            conditions.append("favorite = ?")
            args.append(int(favorite))
        if played is not None:
            conditions.append("watched = ?")
            args.append(int(played))
        if resumable:
            conditions.append("position IS NOT NULL AND duration IS NOT NULL AND watched = 0")
        if search:
            conditions.append(
                "(lower(title) LIKE ? ESCAPE '\\' OR lower(COALESCE(series_name, '')) LIKE ? "
                "ESCAPE '\\')"
            )
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            args.extend([f"%{escaped.lower()}%", f"%{escaped.lower()}%"])
        if ids:
            conditions.append("(" + " OR ".join("(type = ? AND id = ?)" for _ in ids) + ")")
            for item_type, item_id in ids:
                args.extend([item_type, item_id])
        if genres:
            conditions.append(
                "EXISTS (SELECT 1 FROM json_each(entries.genres) "
                f"WHERE value IN ({', '.join('?' for _ in genres)}))"
            )
            args.extend(genres)
        if years:
            conditions.append(f"year IN ({', '.join('?' for _ in years)})")
            args.extend(years)
        if person_ids:
            marks = ", ".join("?" for _ in person_ids)
            conditions.append(
                "(EXISTS (SELECT 1 FROM credit c "
                "WHERE c.media_item_id = entries.media_item_id "
                f"AND c.person_id IN ({marks})) OR (type = 'Person' AND id IN ({marks})))"
            )
            args.extend([*person_ids, *person_ids])
        return conditions, args


_ORDER_COLUMNS = {
    "SortName": "sort_name COLLATE NOCASE",
    "DateCreated": "added_at",
    "PremiereDate": "release_date",
    "ProductionYear": "year",
    "CommunityRating": "rating",
    "DatePlayed": "last_played_at",
    "IndexNumber": "index_number",
    "ParentIndexNumber": "season_number",
    "Random": "RANDOM()",
}


def _order_by(sort_by: Sequence[str], descending: bool) -> str:
    direction = "DESC" if descending else "ASC"
    parts: list[str] = []
    for key in sort_by:
        column = _ORDER_COLUMNS.get(key)
        if column is None:
            continue
        if key == "Random":
            parts.append(column)
        else:
            parts.append(f"{column} {direction} NULLS LAST")
    return ", ".join(parts) or "sort_name COLLATE NOCASE ASC NULLS LAST"


def _entry(row: sqlite3.Row) -> EmbyEntry:
    return EmbyEntry(
        type=str(row["type"]),
        id=int(row["id"]),
        media_item_id=row["media_item_id"],
        library_id=row["library_id"],
        title=str(row["title"]),
        sort_name=str(row["sort_name"]),
        original_title=row["original_title"],
        overview=row["overview"],
        year=row["year"],
        rating=row["rating"],
        runtime=row["runtime"],
        added_at=row["added_at"],
        release_date=row["release_date"],
        genres=str(row["genres"]),
        content_rating=row["content_rating"],
        poster_hash=row["poster_hash"],
        backdrop_hash=row["backdrop_hash"],
        thumb_hash=row["thumb_hash"],
        logo_hash=row["logo_hash"],
        profile_hash=row["profile_hash"],
        series_name=row["series_name"],
        season_name=row["season_name"],
        series_id=row["series_id"],
        season_id=row["season_id"],
        season_number=row["season_number"],
        episode_number=row["episode_number"],
        index_number=row["index_number"],
        playable_id=row["playable_id"],
        watched=bool(row["watched"]),
        favorite=bool(row["favorite"]),
        position=row["position"],
        duration=row["duration"],
        last_played_at=row["last_played_at"],
        play_count=row["play_count"],
        unplayed_count=row["unplayed_count"],
        child_count=row["child_count"],
        birthday=row["birthday"],
        deathday=row["deathday"],
        place_of_birth=row["place_of_birth"],
    )


def find_item_image(
    connection: sqlite3.Connection, item_type: str, item_id: int, image_type: str
) -> tuple[str, str] | None:
    """返回 (图片哈希, 图片类型)，供 302 跳到 /api/images。

    不按用户可见范围过滤：Emby 客户端拉图片不带令牌，/api/images 本身也是公开的。
    """
    image_type = image_type.lower()
    if item_type == "Person":
        if image_type != "primary":
            return None
        row = connection.execute(
            "SELECT i.hash, i.kind FROM person p JOIN image i ON i.id = p.profile_image_id "
            "WHERE p.id = ?",
            (item_id,),
        ).fetchone()
    elif item_type == "Episode":
        column = {
            "primary": "e.still_image_id",
            "thumb": "e.still_image_id",
            "backdrop": "m.backdrop_image_id",
            "logo": "m.logo_image_id",
        }.get(image_type)
        if column is None:
            return None
        row = connection.execute(
            f"SELECT i.hash, i.kind FROM episode e "
            f"JOIN season s ON s.id = e.season_id JOIN media_item m ON m.id = s.media_item_id "
            f"JOIN image i ON i.id = {column} WHERE e.id = ?",
            (item_id,),
        ).fetchone()
    elif item_type == "Season":
        column = {"primary": "s.poster_image_id", "backdrop": "m.backdrop_image_id"}.get(image_type)
        if column is None:
            return None
        row = connection.execute(
            f"SELECT i.hash, i.kind FROM season s JOIN media_item m ON m.id = s.media_item_id "
            f"JOIN image i ON i.id = {column} WHERE s.id = ?",
            (item_id,),
        ).fetchone()
    else:
        column = {
            "primary": "m.poster_image_id",
            "backdrop": "m.backdrop_image_id",
            "thumb": "m.thumb_image_id",
            "logo": "m.logo_image_id",
        }.get(image_type)
        if column is None:
            return None
        row = connection.execute(
            f"SELECT i.hash, i.kind FROM media_item m JOIN image i ON i.id = {column} "
            f"WHERE m.id = ?",
            (item_id,),
        ).fetchone()
    if row is None:
        return None
    return str(row["hash"]), str(row["kind"])
