from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter

from app.api.admin.source_status import (
    SourceStatusOut,
    enabled_source_statuses,
    settings_path,
)
from app.api.schemas import Out
from app.clock import Clock
from app.deps import AdminUser, AppClock, MetadataPlugins, ReadDb, Resources, Settings
from app.library.metadata_providers import MetadataProblem
from app.library.repository import LibraryRepository
from app.settings import SettingsService
from app.sync.jobs import SyncJobRepository

router = APIRouter(prefix="/admin/setup", tags=["admin"])

# failed：做过但出了问题（来源不可用、同步失败），message 即原因
StepState = Literal["complete", "pending", "failed"]


class SetupStepOut(Out):
    key: str
    title: str
    state: StepState
    message: str
    action_path: str | None = None


class SetupOut(Out):
    complete: bool
    current_key: str | None
    steps: list[SetupStepOut]


@router.get("")
def setup_status(
    _: AdminUser,
    db: ReadDb,
    clock: AppClock,
    resources: Resources,
    providers: MetadataPlugins,
    settings: Settings,
) -> SetupOut:
    return setup_of(
        db, clock, enabled_source_statuses(db, clock, resources), providers.problem(), settings
    )


def setup_of(
    db: sqlite3.Connection,
    clock: Clock,
    sources: list[SourceStatusOut],
    metadata_problem: MetadataProblem | None,
    settings: SettingsService,
) -> SetupOut:
    """配置向导的各个步骤；管理后台入口也用它判断向导是否已完成。"""
    library_count = LibraryRepository(db, clock).count()
    sync_history = SyncJobRepository(db, clock).history()
    sync_ready = any(job.status == "succeeded" for job in sync_history)
    source_step = _source_step(sources)
    if metadata_problem is None:
        metadata_step = SetupStepOut(
            key="metadata", title="配置元数据插件", state="complete", message="元数据插件可用"
        )
    else:
        metadata_step = SetupStepOut(
            key="metadata",
            title="配置元数据插件",
            state="pending",
            message=f"{metadata_problem.message}，无法自动匹配和下载元数据",
            action_path=metadata_problem.action_path,
        )
    sync_state: StepState = "complete" if sync_ready else "pending"
    sync_message = "已有成功同步记录" if sync_ready else "运行首次同步以导入媒体"
    if not sync_ready and sync_history and sync_history[0].status == "failed":
        sync_state = "failed"
        sync_message = sync_history[0].error or "最近一次同步失败"
    steps = [
        source_step,
        metadata_step,
        SetupStepOut(
            key="public_url",
            title="设置对外地址",
            state="complete" if settings.public_url() else "pending",
            message="对外地址已设置" if settings.public_url() else "播放链接和分享地址需要对外地址",
            action_path=None if settings.public_url() else "/settings/server/general",
        ),
        SetupStepOut(
            key="library",
            title="创建资料库",
            state="complete" if library_count else "pending",
            message=f"已有 {library_count} 个资料库"
            if library_count
            else "至少创建一个电影或剧集资料库",
            action_path=None if library_count else "/settings/manage/libraries",
        ),
        SetupStepOut(
            key="sync",
            title="首次同步",
            state=sync_state,
            message=sync_message,
            action_path=None if sync_state == "complete" else "/settings/status/sync",
        ),
    ]
    current = next((step.key for step in steps if step.state != "complete"), None)
    return SetupOut(complete=current is None, current_key=current, steps=steps)


def _source_step(sources: list[SourceStatusOut]) -> SetupStepOut:
    """至少一个已启用的来源状态为 ok 时完成。"""
    action = "/settings/manage/plugins"
    if any(source.state == "ok" for source in sources):
        return SetupStepOut(
            key="source",
            title="添加资源来源",
            state="complete",
            message=f"已启用 {len(sources)} 个资源来源",
        )
    if not sources:
        return SetupStepOut(
            key="source",
            title="添加资源来源",
            state="pending",
            message="先添加并启用一个资源来源",
            action_path=action,
        )
    first = next((source for source in sources if source.state != "ok"), sources[0])
    state: StepState = "pending" if first.state == "pending" else "failed"
    return SetupStepOut(
        key="source",
        title="添加资源来源",
        state=state,
        message=first.message or f"资源来源「{first.source_name}」暂时不可用",
        action_path=settings_path(first.plugin_id, first.page),
    )
