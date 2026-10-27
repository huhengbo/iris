from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.clock import Clock, to_iso
from app.errors import AppError, NotFound


@dataclass(frozen=True)
class Alert:
    id: int
    kind: str
    level: str
    message: str
    fingerprint: str | None
    created_at: str
    read_at: str | None
    resolved_at: str | None


class AlertRepository:
    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        self._db = connection
        self._clock = clock

    def create(self, kind: str, level: str, message: str, fingerprint: str | None) -> Alert:
        """同一个指纹在未解决期间只保留一条，重复上报视为冲突。"""
        cursor = self._db.execute(
            "INSERT INTO alert(kind,level,message,fingerprint,created_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(fingerprint) WHERE resolved_at IS NULL AND fingerprint IS NOT NULL "
            "DO NOTHING",
            (kind, level, message, fingerprint, to_iso(self._clock.now())),
        )
        if cursor.rowcount == 0:
            raise AppError(409, "alert_already_open", "相同警报已经存在")
        assert cursor.lastrowid is not None
        return self.require(cursor.lastrowid)

    def recent(self, *, active_only: bool, limit: int) -> list[Alert]:
        where = "WHERE resolved_at IS NULL" if active_only else ""
        rows = self._db.execute(f"{_SELECT} {where} ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [Alert(*row) for row in rows]

    def require(self, alert_id: int) -> Alert:
        row = self._db.execute(f"{_SELECT} WHERE id = ?", (alert_id,)).fetchone()
        if row is None:
            raise NotFound("警报不存在")
        return Alert(*row)

    def update(self, alert_id: int, *, read: bool | None, resolved: bool | None) -> Alert:
        """None 表示不改；True 记下首次时间；False 清除标记。"""
        self.require(alert_id)
        now = to_iso(self._clock.now())
        self._db.execute(
            "UPDATE alert SET "
            "read_at = CASE WHEN ? IS NULL THEN read_at "
            "WHEN ? THEN COALESCE(read_at, ?) ELSE NULL END, "
            "resolved_at = CASE WHEN ? IS NULL THEN resolved_at "
            "WHEN ? THEN COALESCE(resolved_at, ?) ELSE NULL END WHERE id = ?",
            (read, read, now, resolved, resolved, now, alert_id),
        )
        return self.require(alert_id)


_SELECT = "SELECT id,kind,level,message,fingerprint,created_at,read_at,resolved_at FROM alert"
