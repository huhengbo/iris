from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

from app.clock import Clock, to_iso
from app.db import Database
from app.errors import NotFound

TaskStatus = Literal["running", "succeeded", "failed", "cancel_requested", "cancelled"]


@dataclass(frozen=True)
class BackgroundTask:
    id: int
    kind: str
    phase: str
    status: TaskStatus
    progress_done: int
    progress_total: int
    retry_count: int
    cancel_requested: bool
    error: str | None
    result: dict[str, Any]
    created_by: int | None
    idempotency_key: str | None
    created_at: str
    started_at: str
    finished_at: str | None


class TaskRepository:
    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        self._db = connection
        self._clock = clock

    def create(
        self,
        kind: str,
        *,
        total: int = 0,
        created_by: int | None = None,
        idempotency_key: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> BackgroundTask:
        if idempotency_key is not None:
            existing = self._db.execute(
                f"{_SELECT} WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing is not None:
                return _to_task(existing)
        now = to_iso(self._clock.now())
        try:
            cursor = self._db.execute(
                "INSERT INTO background_task "
                "(kind, phase, status, progress_total, created_by, idempotency_key, "
                "created_at, started_at, result) "
                "VALUES (?, 'starting', 'running', ?, ?, ?, ?, ?, ?)",
                (
                    kind,
                    total,
                    created_by,
                    idempotency_key,
                    now,
                    now,
                    json.dumps(result or {}, ensure_ascii=False),
                ),
            )
        except sqlite3.IntegrityError:
            if idempotency_key is None:
                raise
            row = self._db.execute(
                f"{_SELECT} WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            assert row is not None
            return _to_task(row)
        assert cursor.lastrowid is not None
        return self.require(cursor.lastrowid)

    def get(self, task_id: int) -> BackgroundTask | None:
        row = self._db.execute(f"{_SELECT} WHERE id = ?", (task_id,)).fetchone()
        return None if row is None else _to_task(row)

    def by_idempotency_key(self, key: str) -> BackgroundTask | None:
        row = self._db.execute(f"{_SELECT} WHERE idempotency_key = ?", (key,)).fetchone()
        return None if row is None else _to_task(row)

    def require(self, task_id: int) -> BackgroundTask:
        task = self.get(task_id)
        if task is None:
            raise NotFound("任务不存在")
        return task

    def list(
        self,
        *,
        kind: str | None = None,
        status: tuple[TaskStatus, ...] | None = None,
        limit: int = 50,
    ) -> list[BackgroundTask]:
        if status is not None:
            marks = ", ".join("?" for _ in status)
            rows = self._db.execute(
                f"{_SELECT} WHERE status IN ({marks}) ORDER BY id DESC LIMIT ?", (*status, limit)
            ).fetchall()
        elif kind is None:
            rows = self._db.execute(f"{_SELECT} ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        else:
            rows = self._db.execute(
                f"{_SELECT} WHERE kind = ? ORDER BY id DESC LIMIT ?", (kind, limit)
            ).fetchall()
        return [_to_task(row) for row in rows]

    def progress(self, task_id: int, phase: str, done: int, total: int | None = None) -> None:
        if total is None:
            self._db.execute(
                "UPDATE background_task SET phase = ?, progress_done = ? WHERE id = ?",
                (phase, done, task_id),
            )
        else:
            self._db.execute(
                "UPDATE background_task SET phase = ?, progress_done = ?, progress_total = ? "
                "WHERE id = ?",
                (phase, done, total, task_id),
            )

    def finish(
        self,
        task_id: int,
        *,
        error: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        task = self.require(task_id)
        if task.status in ("succeeded", "failed", "cancelled"):
            return
        self._db.execute(
            "UPDATE background_task SET status = ?, phase = 'done', error = ?, result = ?, "
            "finished_at = ? WHERE id = ?",
            (
                (
                    "cancelled"
                    if self._cancel_requested(task_id)
                    else "failed"
                    if error
                    else "succeeded"
                ),
                error,
                json.dumps({**task.result, **(result or {})}, ensure_ascii=False),
                to_iso(self._clock.now()),
                task_id,
            ),
        )

    def _cancel_requested(self, task_id: int) -> bool:
        return self.cancel_requested(task_id)

    def cancel_requested(self, task_id: int) -> bool:
        row = self._db.execute(
            "SELECT cancel_requested FROM background_task WHERE id = ?", (task_id,)
        ).fetchone()
        return row is not None and bool(row["cancel_requested"])

    def request_cancel(self, task_id: int) -> BackgroundTask:
        self.require(task_id)
        self._db.execute(
            "UPDATE background_task SET cancel_requested = 1, status = 'cancel_requested' "
            "WHERE id = ? AND status IN ('running', 'cancel_requested')",
            (task_id,),
        )
        return self.require(task_id)

    def fail_interrupted(self) -> None:
        """服务重启后收束不会再推进的任务，保留输入以便管理员重试。"""
        now = to_iso(self._clock.now())
        self._db.execute(
            "UPDATE background_task SET status = CASE status "
            "WHEN 'cancel_requested' THEN 'cancelled' ELSE 'failed' END, "
            "phase = 'done', error = '服务重启，任务中断', finished_at = ? "
            "WHERE status IN ('running', 'cancel_requested')",
            (now,),
        )


_SELECT = (
    "SELECT id, kind, phase, status, progress_done, progress_total, retry_count, "
    "cancel_requested, error, result, created_by, idempotency_key, created_at, started_at, "
    "finished_at FROM background_task"
)


def _to_task(row: sqlite3.Row) -> BackgroundTask:
    raw_result: dict[str, Any] = json.loads(row["result"] or "{}")
    return BackgroundTask(
        id=row["id"],
        kind=row["kind"],
        phase=row["phase"],
        status=row["status"],
        progress_done=row["progress_done"],
        progress_total=row["progress_total"],
        retry_count=row["retry_count"],
        cancel_requested=bool(row["cancel_requested"]),
        error=row["error"],
        result=raw_result,
        created_by=row["created_by"],
        idempotency_key=row["idempotency_key"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


@dataclass
class TrackedTask:
    """tracked_task 交给调用方的句柄：报告进度，并在结束时把 result 写入任务记录。"""

    id: int
    database: Database
    clock: Clock
    result: dict[str, Any] = field(default_factory=dict)

    def progress(self, phase: str, done: int, total: int | None = None) -> None:
        with self.database.session() as connection:
            TaskRepository(connection, self.clock).progress(self.id, phase, done, total)


@contextmanager
def tracked_task(
    database: Database,
    clock: Clock,
    kind: str,
    *,
    created_by: int | None = None,
    total: int = 0,
) -> Iterator[TrackedTask]:
    """创建后台任务，块正常结束记为成功，抛出异常时记下错误并原样重新抛出。

    每一步都用短事务落盘：任务进度要让别的请求立刻看到，失败记录也不能随块内回滚丢失。
    """
    with database.session() as connection:
        task = TaskRepository(connection, clock).create(kind, total=total, created_by=created_by)
    handle = TrackedTask(task.id, database, clock)
    try:
        yield handle
    except Exception as error:
        with database.session() as connection:
            TaskRepository(connection, clock).finish(
                task.id, error=str(error), result=handle.result
            )
        raise
    with database.session() as connection:
        TaskRepository(connection, clock).finish(task.id, result=handle.result)
