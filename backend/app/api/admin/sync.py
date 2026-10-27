from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from iris_plugin_sdk.protocol import WaitReason
from pydantic import BaseModel, Field

from app.clock import to_iso
from app.deps import (
    AdminUser,
    AppClock,
    AppDatabase,
    AppMetadata,
    ReadDb,
    Schedule,
    Settings,
    Sync,
)
from app.library.repository import LibraryRepository
from app.sync.jobs import (
    Phase,
    Scope,
    SyncBusy,
    SyncJob,
    SyncJobRepository,
    Trigger,
)
from app.sync.purge import MissingFilePurger
from app.sync.schedule import MAX_TIMES, SyncSchedule

router = APIRouter(prefix="/admin/sync", tags=["admin"])


class StatsOut(BaseModel):
    added: int
    removed: int
    renamed: int
    restored: int
    failed: int
    purged: int
    paused: int
    matched: int
    unmatched: int
    ignored: int
    waiting: int
    problems: list[str]


class JobOut(BaseModel):
    id: int
    trigger: Trigger
    scope: Scope
    library_id: int | None
    library_name: str | None
    # 定向同步（scope 为 dir）的范围说明
    target: str | None
    status: Literal["running", "succeeded", "failed", "cancelled"]
    phase: Phase
    progress_done: int
    progress_total: int
    stats: StatsOut
    error: str | None
    waiting_until: str | None
    waiting_reason: WaitReason | None
    started_at: str
    finished_at: str | None
    task_id: int | None

    @classmethod
    def of(cls, job: SyncJob) -> JobOut:
        stats = job.stats
        return cls(
            id=job.id,
            trigger=job.trigger,
            scope=job.scope,
            library_id=job.library_id,
            library_name=job.library_name,
            target=job.target,
            status=job.status,
            phase=job.phase,
            progress_done=job.progress_done,
            progress_total=job.progress_total,
            stats=StatsOut(
                added=stats.added,
                removed=stats.removed,
                renamed=stats.renamed,
                restored=stats.restored,
                failed=stats.failed,
                purged=stats.purged,
                paused=stats.paused,
                matched=stats.matched,
                unmatched=stats.unmatched,
                ignored=stats.ignored,
                waiting=stats.waiting,
                problems=stats.problems,
            ),
            error=job.error,
            waiting_until=job.waiting_until,
            waiting_reason=job.waiting_reason,
            started_at=job.started_at,
            finished_at=job.finished_at,
            task_id=job.task_id,
        )


class StartRequest(BaseModel):
    # 不填表示同步全部资料库
    library_id: int | None = None


class CurrentOut(BaseModel):
    job: JobOut | None


class ScheduleRequest(BaseModel):
    times: list[str] = Field(max_length=MAX_TIMES)


class ScheduleOut(BaseModel):
    times: list[str]
    timezone: str
    next_run_at: str | None


@router.post("", status_code=202)
def start_sync(body: StartRequest, _: AdminUser, sync: Sync) -> JobOut:
    return JobOut.of(sync.start("manual", body.library_id))


@router.get("/current")
def current_job(_: AdminUser, db: ReadDb, clock: AppClock) -> CurrentOut:
    job = SyncJobRepository(db, clock).running()
    return CurrentOut(job=JobOut.of(job) if job else None)


@router.get("/jobs")
def list_jobs(_: AdminUser, db: ReadDb, clock: AppClock) -> list[JobOut]:
    return [JobOut.of(job) for job in SyncJobRepository(db, clock).history()]


@router.get("/schedule")
def get_schedule_config(_: AdminUser, schedule: Schedule) -> ScheduleOut:
    return _schedule_out(schedule, schedule.times())


@router.put("/schedule")
def save_schedule(body: ScheduleRequest, _: AdminUser, schedule: Schedule) -> ScheduleOut:
    return _schedule_out(schedule, schedule.save(body.times))


class HeldDeletionOut(BaseModel):
    library_id: int
    library_name: str
    # 失效超过保留期、等待确认删除的视频数，和资料库的视频总数
    due: int
    total: int


class PurgeOut(BaseModel):
    deleted: int


@router.get("/held-deletions")
def held_deletions(
    _: AdminUser, db: ReadDb, clock: AppClock, settings: Settings
) -> list[HeldDeletionOut]:
    """成批失效、同步时没有自动删除的资料库。"""
    plans = MissingFilePurger(db, clock).held(settings.missing_grace_days())
    return [
        HeldDeletionOut(
            library_id=p.library_id, library_name=p.library_name, due=p.due, total=p.total
        )
        for p in plans
    ]


@router.post("/held-deletions/{library_id}/confirm")
def confirm_deletions(
    library_id: int,
    _: AdminUser,
    database: AppDatabase,
    clock: AppClock,
    settings: Settings,
    metadata: AppMetadata,
) -> PurgeOut:
    """确认删除资料库里失效超过保留期的文件，立即执行，不访问 115。"""
    with database.session() as connection:
        LibraryRepository(connection, clock).require(library_id)
        if SyncJobRepository(connection, clock).running() is not None:
            raise SyncBusy()
        deleted = MissingFilePurger(connection, clock).purge(
            library_id, settings.missing_grace_days()
        )
    # 提交之后才能清理：条目删掉了，它们的图片不再被引用
    metadata.artwork.cleanup()
    return PurgeOut(deleted=deleted)


def _schedule_out(schedule: SyncSchedule, times: list[str]) -> ScheduleOut:
    next_run = schedule.next_run()
    return ScheduleOut(
        times=times,
        timezone=str(schedule.timezone),
        next_run_at=to_iso(next_run) if next_run else None,
    )
