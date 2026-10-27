from __future__ import annotations

import sqlite3

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

    def missing_files(self) -> int:
        return self._db.execute("DELETE FROM media_file WHERE state='missing'").rowcount
