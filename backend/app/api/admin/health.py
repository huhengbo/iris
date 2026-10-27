from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter

from app.api.admin.source_status import SourceStatusOut, enabled_source_statuses, settings_path
from app.api.schemas import Out
from app.clock import to_iso
from app.db import integrity_check, schema_version
from app.deps import AdminUser, AppClock, Images, MetadataPlugins, ReadDb, Resources, Settings
from app.library.images import WIDTHS, ImageStore, file_name
from app.library.review import ReviewQueries
from app.sync.jobs import SyncJobRepository

router = APIRouter(prefix="/admin/health", tags=["admin"])

HealthState = Literal["ok", "warning", "error"]

# 图片缓存只抽查最近登记的这么多张，避免健康检查变成全盘扫描
IMAGE_SAMPLE = 200
# 失败原因只展示开头，避免把冗长的底层报错原样带出来
ERROR_PREVIEW = 80
# 来源状态到健康检查状态；unavailable 表示插件未运行
_SOURCE_STATES: dict[str, HealthState] = {
    "ok": "ok",
    "pending": "warning",
    "limited": "warning",
    "failed": "error",
    "unavailable": "error",
}
_SOURCE_MESSAGES = {
    "ok": "连接正常",
    "pending": "还需要完成插件设置",
    "limited": "请求暂时受限",
    "failed": "来源不可用",
    "unavailable": "插件未运行",
}


class HealthCheckOut(Out):
    key: str
    title: str
    state: HealthState
    message: str
    action_path: str | None = None


class HealthOut(Out):
    checked_at: str
    schema_version: int
    checks: list[HealthCheckOut]


@router.get("")
def health(
    _: AdminUser,
    db: ReadDb,
    clock: AppClock,
    resources: Resources,
    providers: MetadataPlugins,
    settings: Settings,
    images: Images,
) -> HealthOut:
    checks: list[HealthCheckOut] = [
        _source_check(status) for status in enabled_source_statuses(db, clock, resources)
    ]

    # 各资料库选用的元数据插件里第一个不能用的
    problem = providers.problem()
    if problem is None:
        metadata_check = HealthCheckOut(
            key="metadata", title="元数据插件", state="ok", message="可用"
        )
    else:
        metadata_check = HealthCheckOut(
            key="metadata",
            title="元数据插件",
            state="warning",
            message=f"{problem.message}，无法自动匹配和下载元数据",
            action_path=problem.action_path,
        )
    checks.append(metadata_check)

    jobs = SyncJobRepository(db, clock).history(limit=1)
    latest = jobs[0] if jobs else None
    if latest is None:
        sync_state: HealthState = "warning"
        sync_message = "还没有同步记录"
    elif latest.status == "failed":
        sync_state = "error"
        sync_message = latest.error or "最近一次同步失败"
    elif latest.status == "running":
        sync_state = "warning"
        sync_message = "同步进行中"
    else:
        sync_state = "ok"
        sync_message = f"最近一次同步于 {latest.finished_at or latest.started_at} 完成"
    checks.append(
        HealthCheckOut(
            key="sync",
            title="同步",
            state=sync_state,
            message=sync_message,
            action_path="/settings/status/sync",
        )
    )

    success = db.execute(
        "SELECT COALESCE(finished_at, started_at) AS at FROM sync_job "
        "WHERE status = 'succeeded' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    checks.append(
        HealthCheckOut(
            key="last_sync_success",
            title="最近成功同步",
            state="ok" if success else "warning",
            message=f"完成于 {success['at']}" if success else "还没有成功完成过同步",
            action_path=None if success else "/settings/status/sync",
        )
    )

    checks.append(_failed_task(db))

    summary = ReviewQueries(db).summary(None)
    # 已忽略的是管理员处理过的，不算待处理
    pending = summary.unmatched + summary.unplaced
    checks.append(
        HealthCheckOut(
            key="pending",
            title="待处理内容",
            state="warning" if pending else "ok",
            message=f"{pending} 项待处理" if pending else "没有待处理内容",
            action_path="/settings/manage/pending" if pending else None,
        )
    )

    missing = db.execute(
        "SELECT COUNT(*) FROM media_file WHERE kind = 'video' AND state = 'missing'"
    ).fetchone()[0]
    checks.append(
        HealthCheckOut(
            key="missing_files",
            title="失效文件",
            state="warning" if missing else "ok",
            message=f"{missing} 个视频文件在资源来源上已找不到" if missing else "没有失效文件",
            action_path="/settings/status/sync" if missing else None,
        )
    )

    checks.append(_image_cache(db, images))

    integrity = integrity_check(db)
    checks.append(
        HealthCheckOut(
            key="database",
            title="数据库",
            state="ok" if integrity == "ok" else "error",
            message="完整性检查通过" if integrity == "ok" else "完整性检查失败",
            action_path=None,
        )
    )

    public_url = settings.public_url()
    checks.append(
        HealthCheckOut(
            key="public_url",
            title="对外地址",
            state="ok" if public_url else "warning",
            message="已配置" if public_url else "未配置，外部分享链接可能不可用",
            action_path=None if public_url else "/settings/server/general",
        )
    )
    return HealthOut(
        checked_at=to_iso(clock.now()),
        schema_version=schema_version(db),
        checks=checks,
    )


def _source_check(status: SourceStatusOut) -> HealthCheckOut:
    """每个已启用来源一条；key 带来源 ID，客户端据此区分。"""
    state: HealthState = _SOURCE_STATES[status.state]
    message = status.message or _SOURCE_MESSAGES[status.state]
    if status.state == "limited" and status.resume_at:
        message = f"{message}，预计 {status.resume_at} 恢复"
    return HealthCheckOut(
        key=f"source:{status.source_id}",
        title=status.source_name,
        state=state,
        message=message,
        action_path=(
            None if status.state == "ok" else settings_path(status.plugin_id, status.page)
        ),
    )


def _failed_task(db: sqlite3.Connection) -> HealthCheckOut:
    # 只看还没被同类任务后来的成功覆盖的失败，已经恢复的不再提醒
    row = db.execute(
        "SELECT kind, error, COALESCE(finished_at, started_at) AS at FROM background_task f "
        "WHERE status = 'failed' AND NOT EXISTS ("
        "SELECT 1 FROM background_task s WHERE s.kind = f.kind AND s.status = 'succeeded' "
        "AND s.id > f.id) ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return HealthCheckOut(
            key="failed_task", title="后台任务", state="ok", message="没有未恢复的失败任务"
        )
    reason = (row["error"] or "未知原因")[:ERROR_PREVIEW]
    return HealthCheckOut(
        key="failed_task",
        title="后台任务",
        state="warning",
        message=f"{row['kind']} 于 {row['at']} 失败：{reason}",
        action_path="/settings/status/tasks",
    )


def _image_cache(db: sqlite3.Connection, images: ImageStore) -> HealthCheckOut:
    total = db.execute("SELECT COUNT(*) FROM image").fetchone()[0]
    rows = db.execute(
        "SELECT hash, kind FROM image ORDER BY id DESC LIMIT ?", (IMAGE_SAMPLE,)
    ).fetchall()
    lost = sum(
        1
        for row in rows
        if images.path(file_name(row["hash"], WIDTHS[row["kind"]][0], row["kind"])) is None
    )
    if lost:
        return HealthCheckOut(
            key="image_cache",
            title="图片缓存",
            state="warning",
            message=f"最近 {len(rows)} 张图片中有 {lost} 张缺少本地文件",
        )
    return HealthCheckOut(
        key="image_cache", title="图片缓存", state="ok", message=f"已缓存 {total} 张图片"
    )
