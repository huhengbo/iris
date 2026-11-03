from __future__ import annotations

import sqlite3
from datetime import timedelta

from app.clock import Clock, to_iso


class Cleanup:
    """计划任务里的清理动作，都返回删除的行数。"""

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        self._db = connection
        self._clock = clock

    def optimize_database(self) -> None:
        self._db.execute("PRAGMA optimize")

    def expired_sessions(self) -> int:
        return self._db.execute(
            "DELETE FROM auth_session WHERE expires_at <= ?", (to_iso(self._clock.now()),)
        ).rowcount

    def old_play_events(self) -> int:
        return self._db.execute(
            "DELETE FROM play_event WHERE created_at < datetime('now', '-365 days')"
        ).rowcount

    def old_ai_chats(self) -> int:
        """AI 对话保留 30 天（ADR 0030），按最后一次更新计；消息随对话级联删除。"""
        cutoff = to_iso(self._clock.now() - timedelta(days=30))
        return self._db.execute("DELETE FROM ai_chat WHERE updated_at < ?", (cutoff,)).rowcount

    def missing_files(self) -> int:
        return self._db.execute("DELETE FROM media_file WHERE state='missing'").rowcount
