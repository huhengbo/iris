"""计划任务的配置，以及管理员手动触发时的执行。自动执行见 maintenance。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from app.clock import Clock, to_iso
from app.library.images import ImageStore
from app.ops.backup import BackupService
from app.ops.cleanup import Cleanup
from app.ops.tasks import TaskRepository


@dataclass(frozen=True)
class ScheduledTask:
    key: str
    enabled: bool
    interval: int
    last_run_at: str | None
    last_task_id: int | None


class ScheduledTaskRepository:
    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        self._db = connection
        self._clock = clock

    def all(self) -> list[ScheduledTask]:
        rows = self._db.execute(f"{_SELECT} ORDER BY key").fetchall()
        return [_to_task(row) for row in rows]

    def get(self, key: str) -> ScheduledTask | None:
        row = self._db.execute(f"{_SELECT} WHERE key = ?", (key,)).fetchone()
        return None if row is None else _to_task(row)

    def ensure(self, key: str, *, enabled: bool = False) -> None:
        """计划项按需创建：迁移里只预置了内置任务，其余首次用到时补上。"""
        if self.get(key) is None:
            self._db.execute(
                "INSERT INTO scheduled_task(key, enabled) VALUES (?,?)", (key, int(enabled))
            )

    def patch(self, key: str, *, enabled: bool | None, interval: int | None) -> ScheduledTask:
        self.ensure(key)
        fields: list[str] = []
        args: list[Any] = []
        if enabled is not None:
            fields.append("enabled = ?")
            args.append(int(enabled))
        if interval is not None:
            fields.append("interval = ?")
            args.append(interval)
        if fields:
            self._db.execute(
                f"UPDATE scheduled_task SET {', '.join(fields)} WHERE key = ?", (*args, key)
            )
        task = self.get(key)
        assert task is not None
        return task

    def mark_run(self, key: str, task_id: int) -> None:
        self._db.execute(
            "UPDATE scheduled_task SET last_run_at = ?, last_task_id = ? WHERE key = ?",
            (to_iso(self._clock.now()), task_id, key),
        )


class ScheduledTaskRunner:
    """管理员手动执行计划项：和自动执行共用任务语义，失败只记录在任务里。"""

    def __init__(
        self,
        connection: sqlite3.Connection,
        clock: Clock,
        backups: BackupService,
        images: ImageStore,
    ) -> None:
        self._db = connection
        self._clock = clock
        self._backups = backups
        self._images = images

    def run_now(self, key: str, *, created_by: int) -> int:
        """返回后台任务 ID；整个过程在调用方的事务里完成。"""
        schedule = ScheduledTaskRepository(self._db, self._clock)
        schedule.ensure(key, enabled=True)
        tasks = TaskRepository(self._db, self._clock)
        task = tasks.create(f"scheduled:{key}", created_by=created_by)
        result: dict[str, Any] = {"scheduled_key": key, "manual": True}
        try:
            self._execute(key, result)
            tasks.finish(task.id, result=result)
        except Exception as exc:
            tasks.finish(task.id, error=str(exc), result=result)
            result["error"] = str(exc)
        schedule.mark_run(key, task.id)
        return task.id

    def _execute(self, key: str, result: dict[str, Any]) -> None:
        cleanup = Cleanup(self._db, self._clock)
        if key == "backup":
            backup = self._backups.create()
            result["backup"] = backup.name
            result["deleted_backups"] = self._backups.prune()
        elif key == "optimize_database":
            cleanup.optimize_database()
        elif key == "cleanup_images":
            result["deleted_images"] = self._images.cleanup(self._db)
        elif key == "cleanup_sessions":
            result["deleted_sessions"] = cleanup.expired_sessions()
        elif key == "cleanup_play_events":
            result["deleted_events"] = cleanup.old_play_events()
        elif key == "empty_recycle_bin":
            result["deleted_files"] = cleanup.missing_files()
        else:
            # 元数据刷新会通过 Metadata 服务自行开启短事务，交给下一次调度循环执行，
            # 避免在当前请求的 Db 事务内嵌套写事务。
            result["status"] = "queued"


_SELECT = "SELECT key,enabled,interval,last_run_at,last_task_id FROM scheduled_task"


def _to_task(row: sqlite3.Row) -> ScheduledTask:
    return ScheduledTask(
        key=row["key"],
        enabled=bool(row["enabled"]),
        interval=row["interval"],
        last_run_at=row["last_run_at"],
        last_task_id=row["last_task_id"],
    )
