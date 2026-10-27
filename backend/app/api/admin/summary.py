from __future__ import annotations

from fastapi import APIRouter

from app.api.admin.setup import setup_of
from app.api.admin.source_status import SourceStatusOut, enabled_source_statuses
from app.api.admin.sync import HeldDeletionOut, JobOut
from app.api.schemas import Out
from app.clock import to_iso
from app.deps import (
    AdminUser,
    AppClock,
    MetadataPlugins,
    ReadDb,
    Resources,
    Schedule,
    Settings,
)
from app.library.review import ReviewQueries
from app.sync.jobs import SyncJobRepository
from app.sync.purge import MissingFilePurger

router = APIRouter(prefix="/admin/summary", tags=["admin"])


class PendingCountOut(Out):
    # 没匹配上的作品、没归入条目的视频
    unmatched: int
    unplaced: int


class SummaryOut(Out):
    # 每个已启用资源来源的状态
    sources: list[SourceStatusOut]
    sync: JobOut | None
    held_deletions: list[HeldDeletionOut]
    # 配置向导的步骤都完成了
    setup_complete: bool
    pending: PendingCountOut
    # 下一次定时同步；没设置定时同步时为空
    next_sync_at: str | None


@router.get("")
def summary(
    admin: AdminUser,
    db: ReadDb,
    clock: AppClock,
    settings: Settings,
    resources: Resources,
    providers: MetadataPlugins,
    schedule: Schedule,
) -> SummaryOut:
    latest = SyncJobRepository(db, clock).history(limit=1)
    plans = MissingFilePurger(db, clock).held(settings.missing_grace_days())
    pending = ReviewQueries(db).summary(None)
    next_sync = schedule.next_run()
    sources = enabled_source_statuses(db, clock, resources)
    return SummaryOut(
        sources=sources,
        sync=JobOut.of(latest[0]) if latest else None,
        held_deletions=[
            HeldDeletionOut(
                library_id=plan.library_id,
                library_name=plan.library_name,
                due=plan.due,
                total=plan.total,
            )
            for plan in plans
        ],
        setup_complete=setup_of(db, clock, sources, providers.problem(), settings).complete,
        pending=PendingCountOut(unmatched=pending.unmatched, unplaced=pending.unplaced),
        next_sync_at=to_iso(next_sync) if next_sync else None,
    )
