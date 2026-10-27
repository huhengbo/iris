"""播放记录和已看标记。每个用户只能看到、改动自己的记录（ADR 0009）。"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Literal

from app.auth.users import User
from app.clock import Clock, to_iso
from app.errors import NotFound
from app.library.browse import visible_library_ids
from app.library.watchlist import drop_watched

# 发起播放的客户端类型，记在播放事件里
PlayClient = Literal["web", "android", "ios"]
# 内置播放器上报进度的规则（和 Emby 的默认值接近）：
# 看到 90% 算看完，标记已看并清掉位置；开头不到 1 分钟不值得续播，也不记位置
WATCHED_RATIO = 0.9
MIN_RESUME_SECONDS = 60


class PlaybackStates:
    def __init__(self, connection: sqlite3.Connection, clock: Clock, user: User) -> None:
        self._db = connection
        self._clock = clock
        self._user = user

    def record_play(
        self,
        playable_id: int,
        version_id: int,
        client: str,
        *,
        mode: str | None = None,
        device_id: str | None = None,
    ) -> None:
        """记一次「开始播放」，并更新最近播放时间和最近使用的版本。"""
        now = to_iso(self._clock.now())
        self._db.execute(
            "INSERT INTO play_event "
            "(user_id, playable_id, version_id, client, mode, device_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self._user.id, playable_id, version_id, client, mode or client, device_id, now),
        )
        self._db.execute(
            """
            INSERT INTO playback_state (user_id, playable_id, last_played_at, last_version_id,
                updated_at) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (user_id, playable_id) DO UPDATE SET
                last_played_at = excluded.last_played_at,
                last_version_id = excluded.last_version_id, updated_at = excluded.updated_at
            """,
            (self._user.id, playable_id, now, version_id, now),
        )

    def position(self, playable_id: int) -> int | None:
        """续播位置（秒）；没有记录时为 None。"""
        row = self._db.execute(
            "SELECT position FROM playback_state WHERE user_id = ? AND playable_id = ?",
            (self._user.id, playable_id),
        ).fetchone()
        return row["position"] if row else None

    def save_progress(self, playable_id: int, position: int, duration: int) -> None:
        """内置播放器和投屏上报的播放进度。看完标记已看，否则只记位置，不改已看。"""
        if not self._playables("p.id = ?", (playable_id,)):
            raise NotFound("这个条目不存在")
        position = min(position, duration)
        now = to_iso(self._clock.now())
        finished = position >= duration * WATCHED_RATIO
        resume = None if finished or position < MIN_RESUME_SECONDS else position
        self._db.execute(
            """
            INSERT INTO playback_state (user_id, playable_id, position, duration, watched,
                last_played_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (user_id, playable_id) DO UPDATE SET
                position = excluded.position, duration = excluded.duration,
                watched = MAX(watched, excluded.watched),
                last_played_at = excluded.last_played_at, updated_at = excluded.updated_at
            """,
            (self._user.id, playable_id, resume, duration, int(finished), now, now),
        )
        if finished:
            drop_watched(self._db, self._user.id, self._item_ids([playable_id]))

    def mark_item(self, item_id: int, watched: bool) -> None:
        """电影标记它自己；剧集标记全部可播放的集。"""
        self._mark(self._playables("m.id = ?", (item_id,)), watched, "条目不存在")

    def mark_season(self, season_id: int, watched: bool) -> None:
        playables = self._playables(
            "p.episode_id IN (SELECT id FROM episode WHERE season_id = ?)", (season_id,)
        )
        self._mark(playables, watched, "这一季不存在")

    def mark_episode(self, episode_id: int, watched: bool) -> None:
        self._mark(self._playables("p.episode_id = ?", (episode_id,)), watched, "这一集不存在")

    def _playables(self, condition: str, args: Sequence[object]) -> list[int]:
        """可见范围内、至少有一个可用文件的可播放条目。"""
        library_ids = visible_library_ids(self._db, self._user)
        scope = ", ".join("?" for _ in library_ids) or "NULL"
        rows = self._db.execute(
            f"""
            SELECT p.id FROM playable p JOIN media_item m ON m.id = p.media_item_id
            WHERE {condition} AND m.library_id IN ({scope})
                AND EXISTS (SELECT 1 FROM version v JOIN media_file c ON c.id = v.media_file_id
                    WHERE v.playable_id = p.id AND c.state = 'active')
            """,
            (*args, *library_ids),
        ).fetchall()
        return [row["id"] for row in rows]

    def _mark(self, playable_ids: list[int], watched: bool, missing: str) -> None:
        """手动标记已看或未看都从头开始，清掉续播位置。"""
        if not playable_ids:
            raise NotFound(missing)
        now = to_iso(self._clock.now())
        self._db.executemany(
            """
            INSERT INTO playback_state (user_id, playable_id, watched, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (user_id, playable_id) DO UPDATE SET watched = excluded.watched,
                position = NULL, updated_at = excluded.updated_at
            """,
            [(self._user.id, playable_id, int(watched), now) for playable_id in playable_ids],
        )
        if watched:
            drop_watched(self._db, self._user.id, self._item_ids(playable_ids))

    def _item_ids(self, playable_ids: Sequence[int]) -> list[int]:
        """可播放条目所属的作品；清稍后观看时用来判断整部作品是否已看完。"""
        if not playable_ids:
            return []
        marks = ", ".join("?" for _ in playable_ids)
        rows = self._db.execute(
            f"SELECT DISTINCT media_item_id FROM playable WHERE id IN ({marks})",
            tuple(playable_ids),
        ).fetchall()
        return [int(row[0]) for row in rows]
