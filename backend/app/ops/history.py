"""管理后台的播放历史、统计和播放动态，覆盖全部用户。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import timedelta

from app.clock import Clock, to_iso

# 内置播放器和投屏每 15 秒上报一次进度，这段时间内有上报就算还在播放。
# 外部播放器不上报，只在开始播放后的这段时间里出现。
NOW_PLAYING_WINDOW = timedelta(seconds=60)
RECENT_LIMIT = 20


@dataclass(frozen=True)
class HistoryFilter:
    user_id: int | None = None
    library_id: int | None = None
    item_id: int | None = None
    client: str | None = None
    start: str | None = None
    end: str | None = None


@dataclass(frozen=True)
class PlayRecord:
    id: int
    user_id: int
    playable_id: int
    version_id: int | None
    client: str
    created_at: str
    device_id: str | None
    mode: str
    ended_at: str | None
    watched_seconds: int | None
    username: str
    title: str
    library_id: int


@dataclass(frozen=True)
class PlayRecordPage:
    items: list[PlayRecord]
    total: int


@dataclass(frozen=True)
class ClientPlays:
    mode: str
    count: int


@dataclass(frozen=True)
class TopItem:
    id: int
    title: str
    count: int


@dataclass(frozen=True)
class PlayStats:
    plays: int
    watched_seconds: int
    by_client: list[ClientPlays]
    top_items: list[TopItem]


@dataclass(frozen=True)
class NowPlaying:
    username: str
    item_id: int
    title: str
    # 剧集的季号、集号和集名；电影为空
    season_number: int | None
    episode_number: int | None
    episode_name: str | None
    # 秒；内置播放器和投屏上报，开头不到 1 分钟或已看完时为空
    position: int | None
    duration: int | None
    at: str
    # 最近一次发起播放的客户端，例如 Infuse；没有播放事件时为空
    client: str | None


@dataclass(frozen=True)
class RecentPlay:
    username: str
    item_id: int
    title: str
    season_number: int | None
    episode_number: int | None
    episode_name: str | None
    # 发起播放的客户端：web、android、ios
    client: str
    at: str


class PlayHistory:
    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        self._db = connection
        self._clock = clock

    def search(self, criteria: HistoryFilter, *, offset: int, limit: int) -> PlayRecordPage:
        clauses: list[str] = []
        args: list[object] = []
        if criteria.user_id is not None:
            clauses.append("ev.user_id = ?")
            args.append(criteria.user_id)
        if criteria.library_id is not None:
            clauses.append("m.library_id = ?")
            args.append(criteria.library_id)
        if criteria.item_id is not None:
            clauses.append("m.id = ?")
            args.append(criteria.item_id)
        if criteria.client:
            clauses.append("ev.mode = ?")
            args.append(criteria.client)
        if criteria.start:
            clauses.append("ev.created_at >= ?")
            args.append(criteria.start)
        if criteria.end:
            clauses.append("ev.created_at < ?")
            args.append(criteria.end)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._db.execute(
            "SELECT ev.id,ev.user_id,ev.playable_id,ev.version_id,ev.client,ev.created_at,"
            "ev.device_id,ev.mode,ev.ended_at,ev.watched_seconds,u.username,m.title,m.library_id "
            "FROM play_event ev JOIN user u ON u.id=ev.user_id "
            "JOIN playable p ON p.id=ev.playable_id JOIN media_item m ON m.id=p.media_item_id "
            f"{where} ORDER BY ev.created_at DESC,ev.id DESC LIMIT ? OFFSET ?",
            (*args, limit, max(offset, 0)),
        ).fetchall()
        total = self._db.execute(
            "SELECT COUNT(*) FROM play_event ev JOIN playable p ON p.id=ev.playable_id "
            f"JOIN media_item m ON m.id=p.media_item_id {where}",
            tuple(args),
        ).fetchone()[0]
        return PlayRecordPage(items=[PlayRecord(*row) for row in rows], total=total)

    def stats(self) -> PlayStats:
        plays = self._db.execute("SELECT COUNT(*) FROM play_event").fetchone()[0]
        watched = self._db.execute(
            "SELECT COALESCE(SUM(watched_seconds),0) FROM play_event"
        ).fetchone()[0]
        by_client = self._db.execute(
            "SELECT mode,COUNT(*) AS count FROM play_event GROUP BY mode ORDER BY count DESC"
        ).fetchall()
        top_items = self._db.execute(
            "SELECT m.id,m.title,COUNT(*) AS count FROM play_event ev "
            "JOIN playable p ON p.id=ev.playable_id JOIN media_item m ON m.id=p.media_item_id "
            "GROUP BY m.id ORDER BY count DESC LIMIT 10"
        ).fetchall()
        return PlayStats(
            plays=plays,
            watched_seconds=watched,
            by_client=[ClientPlays(*row) for row in by_client],
            top_items=[TopItem(*row) for row in top_items],
        )

    def now_playing(self) -> list[NowPlaying]:
        since = to_iso(self._clock.now() - NOW_PLAYING_WINDOW)
        rows = self._db.execute(
            f"""
            SELECT {_TITLE_COLUMNS}, st.position, st.duration, st.last_played_at AS at,
                (SELECT ev.client FROM play_event ev
                    WHERE ev.user_id = st.user_id AND ev.playable_id = st.playable_id
                    ORDER BY ev.id DESC LIMIT 1) AS client
            FROM playback_state st {_TITLE_JOINS.format(alias="st")}
            WHERE st.last_played_at >= ?
            ORDER BY st.last_played_at DESC
            """,
            (since,),
        ).fetchall()
        return [NowPlaying(*row) for row in rows]

    def recent(self) -> list[RecentPlay]:
        rows = self._db.execute(
            f"""
            SELECT {_TITLE_COLUMNS}, ev.client, ev.created_at AS at
            FROM play_event ev {_TITLE_JOINS.format(alias="ev")}
            ORDER BY ev.created_at DESC, ev.id DESC LIMIT ?
            """,
            (RECENT_LIMIT,),
        ).fetchall()
        return [RecentPlay(*row) for row in rows]


_TITLE_JOINS = """
    JOIN user u ON u.id = {alias}.user_id
    JOIN playable p ON p.id = {alias}.playable_id
    JOIN media_item m ON m.id = p.media_item_id
    LEFT JOIN episode e ON e.id = p.episode_id
    LEFT JOIN season s ON s.id = e.season_id
"""
_TITLE_COLUMNS = (
    "u.username, m.id AS item_id, m.title, s.number AS season_number, "
    "e.number AS episode_number, e.name AS episode_name"
)
