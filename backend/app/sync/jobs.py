from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from iris_plugin_sdk.protocol import WaitReason

from app.clock import Clock, to_iso
from app.errors import AppError
from app.ops.tasks import TaskRepository

# event：来源插件报告目录变动触发的定向同步
Trigger = Literal["manual", "scheduled", "event"]
# all：全部资料库；library：一个资料库；dir：指定目录（定向同步）
Scope = Literal["all", "library", "dir"]
JobStatus = Literal["running", "succeeded", "failed", "cancelled"]
# preparing：准备专用目录；exporting：逐个资料库目录导出并比对；listing：定向同步时逐个列出
# 指定目录并比对；enriching：按目录补全文件信息；
# recognizing：识别视频、按作品分组、读取 NFO；matching：作品匹配 TMDB；
# filling：用后面的元数据下载器补字段和图片；scraping：拉取季信息；images：下载图片
Phase = Literal[
    "preparing",
    "exporting",
    "listing",
    "enriching",
    "recognizing",
    "matching",
    "filling",
    "scraping",
    "images",
    "done",
]

# 任务里最多记录多少条问题，避免大面积失败时撑大记录
MAX_PROBLEMS = 20
HISTORY_LIMIT = 30


@dataclass
class SyncStats:
    added: int = 0
    removed: int = 0
    renamed: int = 0
    restored: int = 0
    failed: int = 0
    # 失效超过保留期而删除的视频
    purged: int = 0
    # 因风控暂停的次数
    paused: int = 0
    # 这次新匹配上的作品、没匹配上（进入待处理）的作品、新识别为附加内容而忽略的视频，
    # 以及同步结束时仍在等待匹配的作品（没配置 TMDB 或请求失败）
    matched: int = 0
    unmatched: int = 0
    ignored: int = 0
    waiting: int = 0
    problems: list[str] = field(default_factory=list)

    def problem(self, message: str, count: int = 1) -> None:
        """记一条问题。count 是受影响的文件数，例如一个目录补全失败时是该目录下的待补全文件数。"""
        self.failed += count
        if len(self.problems) < MAX_PROBLEMS:
            self.problems.append(message)

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False)

    @classmethod
    def from_json(cls, value: str) -> SyncStats:
        data: dict[str, Any] = json.loads(value)
        known = {key: data[key] for key in cls.__dataclass_fields__ if key in data}
        return cls(**known)


@dataclass(frozen=True)
class SyncJob:
    id: int
    trigger: Trigger
    scope: Scope
    library_id: int | None
    library_name: str | None
    # 定向同步的范围说明，例如「流浪地球」或「115 文件变动（3 个目录）」
    target: str | None
    status: JobStatus
    phase: Phase
    progress_done: int
    progress_total: int
    stats: SyncStats
    error: str | None
    waiting_until: str | None
    waiting_reason: WaitReason | None
    started_at: str
    finished_at: str | None
    task_id: int | None


class SyncBusy(AppError):
    def __init__(self) -> None:
        super().__init__(409, "sync_running", "已有同步任务在运行，请等它结束")


class SyncCancelled(Exception):
    """统一任务收到取消请求后让同步阶段尽快退出。"""


class SyncJobRepository:
    """同步任务记录。数据库的部分唯一索引保证同一时间只有一个运行中的任务。"""

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        self._db = connection
        self._clock = clock

    def create(
        self,
        trigger: Trigger,
        library_id: int | None,
        library_name: str | None,
        target: str | None = None,
    ) -> SyncJob:
        """target 不为空时是定向同步。"""
        if target is not None:
            scope: Scope = "dir"
        else:
            scope = "all" if library_id is None else "library"
        try:
            task = TaskRepository(self._db, self._clock).create("sync")
            cursor = self._db.execute(
                "INSERT INTO sync_job (trigger, scope, library_id, library_name, target, task_id, "
                "status, phase, started_at) VALUES (?, ?, ?, ?, ?, ?, 'running', 'preparing', ?)",
                (
                    trigger,
                    scope,
                    library_id,
                    library_name,
                    target,
                    task.id,
                    to_iso(self._clock.now()),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise SyncBusy() from exc
        assert cursor.lastrowid is not None
        job = self.get(cursor.lastrowid)
        assert job is not None
        return job

    def get(self, job_id: int) -> SyncJob | None:
        row = self._db.execute(f"{_SELECT} WHERE id = ?", (job_id,)).fetchone()
        return None if row is None else _to_job(row)

    def running(self) -> SyncJob | None:
        row = self._db.execute(f"{_SELECT} WHERE status = 'running'").fetchone()
        return None if row is None else _to_job(row)

    def history(self, limit: int = HISTORY_LIMIT) -> list[SyncJob]:
        rows = self._db.execute(f"{_SELECT} ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [_to_job(row) for row in rows]

    def progress(self, job_id: int, phase: Phase, done: int, total: int, stats: SyncStats) -> None:
        self._db.execute(
            "UPDATE sync_job SET phase = ?, progress_done = ?, progress_total = ?, stats = ? "
            "WHERE id = ?",
            (phase, done, total, stats.to_json(), job_id),
        )
        task_id = self._task_id(job_id)
        if task_id is not None:
            TaskRepository(self._db, self._clock).progress(task_id, phase, done, total)

    def waiting(
        self, job_id: int, until: datetime | None, reason: WaitReason | None, stats: SyncStats
    ) -> None:
        self._db.execute(
            "UPDATE sync_job SET waiting_until = ?, waiting_reason = ?, stats = ? WHERE id = ?",
            (to_iso(until) if until else None, reason, stats.to_json(), job_id),
        )
        task_id = self._task_id(job_id)
        if task_id is not None:
            progress = self._db.execute(
                "SELECT progress_done, progress_total FROM sync_job WHERE id = ?", (job_id,)
            ).fetchone()
            assert progress is not None
            TaskRepository(self._db, self._clock).progress(
                task_id,
                "waiting" if until else "resuming",
                progress["progress_done"],
                progress["progress_total"],
            )

    def finish(
        self,
        job_id: int,
        stats: SyncStats,
        error: str | None = None,
        *,
        cancelled: bool = False,
    ) -> None:
        self._db.execute(
            "UPDATE sync_job SET status = ?, phase = 'done', stats = ?, error = ?, "
            "waiting_until = NULL, waiting_reason = NULL, finished_at = ? WHERE id = ?",
            (
                "cancelled" if cancelled else "failed" if error else "succeeded",
                stats.to_json(),
                error,
                to_iso(self._clock.now()),
                job_id,
            ),
        )
        task_id = self._task_id(job_id)
        if task_id is not None:
            TaskRepository(self._db, self._clock).finish(
                task_id,
                error=error or ("同步已取消" if cancelled else None),
                result=stats.__dict__,
            )

    def fail_interrupted(self) -> None:
        """服务启动时调用：上次进程退出时还在运行的任务已经不会再推进了。"""
        rows = self._db.execute(
            "SELECT task_id FROM sync_job WHERE status = 'running' AND task_id IS NOT NULL"
        ).fetchall()
        self._db.execute(
            "UPDATE sync_job SET status = 'failed', error = '服务重启，任务中断', "
            "waiting_until = NULL, waiting_reason = NULL, finished_at = ? "
            "WHERE status = 'running'",
            (to_iso(self._clock.now()),),
        )
        for row in rows:
            TaskRepository(self._db, self._clock).finish(row["task_id"], error="服务重启，任务中断")

    def cancelled(self, job_id: int) -> bool:
        task_id = self._task_id(job_id)
        return task_id is not None and TaskRepository(self._db, self._clock).cancel_requested(
            task_id
        )

    def _task_id(self, job_id: int) -> int | None:
        row = self._db.execute("SELECT task_id FROM sync_job WHERE id = ?", (job_id,)).fetchone()
        return None if row is None else row["task_id"]


_SELECT = (
    "SELECT id, trigger, scope, library_id, library_name, target, status, phase, progress_done, "
    "progress_total, stats, error, waiting_until, waiting_reason, started_at, finished_at, task_id "
    "FROM sync_job"
)


def _to_job(row: sqlite3.Row) -> SyncJob:
    return SyncJob(
        id=row["id"],
        trigger=row["trigger"],
        scope=row["scope"],
        library_id=row["library_id"],
        library_name=row["library_name"],
        target=row["target"],
        status=row["status"],
        phase=row["phase"],
        progress_done=row["progress_done"],
        progress_total=row["progress_total"],
        stats=SyncStats.from_json(row["stats"]),
        error=row["error"],
        waiting_until=row["waiting_until"],
        waiting_reason=row["waiting_reason"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        task_id=row["task_id"],
    )
