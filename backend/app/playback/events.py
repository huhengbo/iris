"""播放事件：进度上报时收尾事件，以及管理员请求停止播放的信号。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.clock import Clock, to_iso
from app.errors import AppError, NotFound

# 只有内置播放器会上报进度并读取停止信号，其他方式无法终止
STOPPABLE_MODES = frozenset({"builtin", "android", "ios"})
STOP_MESSAGE = "管理员已请求停止播放"


@dataclass(frozen=True)
class OpenPlayEvent:
    id: int
    version_id: int | None
    client: str
    mode: str


class PlayEvents:
    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        self._db = connection
        self._clock = clock

    def open_event(self, user_id: int, playable_id: int) -> OpenPlayEvent | None:
        """用户在这个播放项上最近一次还没结束的播放事件。"""
        row = self._db.execute(
            "SELECT id,version_id,client,mode FROM play_event "
            "WHERE user_id=? AND playable_id=? AND ended_at IS NULL "
            "ORDER BY id DESC LIMIT 1",
            (user_id, playable_id),
        ).fetchone()
        return None if row is None else OpenPlayEvent(*row)

    def record_progress(self, event_id: int, position: int, *, finished: bool) -> None:
        """记下已看秒数；看完时写入结束时间，否则保持事件未结束。"""
        self._db.execute(
            "UPDATE play_event SET watched_seconds=?, "
            "ended_at=CASE WHEN ? THEN ? ELSE ended_at END "
            "WHERE id=?",
            (position, int(finished), to_iso(self._clock.now()), event_id),
        )

    def finish_event(self, event_id: int, position: int) -> None:
        """Stopped 上报：不论看到多少都结束事件并记下已看秒数。"""
        self._db.execute(
            "UPDATE play_event SET watched_seconds=?, ended_at=? WHERE id=?",
            (position, to_iso(self._clock.now()), event_id),
        )

    def take_stop_request(self, user_id: int, playable_id: int) -> str | None:
        """取走管理员的停止请求并清除，保证只通知一次；没有请求时返回 None。"""
        row = self._db.execute(
            "SELECT stop_requested, stop_message FROM playback_state "
            "WHERE user_id = ? AND playable_id = ?",
            (user_id, playable_id),
        ).fetchone()
        if row is None or not row["stop_requested"]:
            return None
        self._db.execute(
            "UPDATE playback_state SET stop_requested = 0, stop_message = NULL "
            "WHERE user_id = ? AND playable_id = ?",
            (user_id, playable_id),
        )
        return str(row["stop_message"] or "")

    def request_stop(self, event_id: int) -> None:
        """管理员请求停止某次播放；播放器下一次上报进度时会收到。"""
        row = self._db.execute(
            "SELECT id,mode,playable_id FROM play_event WHERE id = ?", (event_id,)
        ).fetchone()
        if row is None:
            raise NotFound("播放会话不存在")
        if row["mode"] not in STOPPABLE_MODES:
            raise AppError(409, "session_not_stoppable", "外部播放器、投屏和 WebDAV 无法终止")
        self._db.execute(
            "UPDATE playback_state SET stop_requested = 1, stop_message = ? "
            "WHERE user_id = (SELECT user_id FROM play_event WHERE id = ?) AND playable_id = ?",
            (STOP_MESSAGE, event_id, row["playable_id"]),
        )
