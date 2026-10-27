"""资源来源状态：汇总、健康检查和配置向导共用的只读结构。

状态由资源插件的 ``status`` 能力提供；插件没有声明时视为正常，插件未运行时是 unavailable。
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Literal

from iris_plugin_sdk import ResourceProvider, ResourceUnavailable, SourceStatus

from app.api.schemas import Out
from app.clock import Clock
from app.resources.repository import ResourceRepository
from app.resources.service import ResourceSources

logger = logging.getLogger(__name__)

SourceState = Literal["ok", "pending", "failed", "limited", "unavailable"]


class SourceStatusOut(Out):
    source_id: int
    source_name: str
    plugin_id: str
    state: SourceState
    message: str | None = None
    resume_at: str | None = None
    # 插件设置页的页面 id；客户端据此跳到「插件 › 该插件 › 该页」
    page: str | None = None


def enabled_source_statuses(
    connection: sqlite3.Connection, clock: Clock, resources: ResourceSources
) -> list[SourceStatusOut]:
    """已启用资源来源的插件状态，按来源 ID 排序。"""
    statuses: list[SourceStatusOut] = []
    for source in ResourceRepository(connection, clock).sources():
        if not source.enabled:
            continue
        provider = resources.provider(source.plugin_id)
        if provider is None:
            statuses.append(
                SourceStatusOut(
                    source_id=source.id,
                    source_name=source.name,
                    plugin_id=source.plugin_id,
                    state="unavailable",
                )
            )
            continue
        if "status" not in provider.capabilities:
            statuses.append(_out(source.id, source.name, source.plugin_id, SourceStatus("ok")))
            continue
        statuses.append(
            _out(
                source.id,
                source.name,
                source.plugin_id,
                _call_status(provider, source.config, source.name),
            )
        )
    return statuses


def settings_path(plugin_id: str, page: str | None) -> str:
    """插件设置页的管理端地址；没有页面时不带查询参数。"""
    path = f"/settings/manage/plugins/{plugin_id}"
    return path if not page else f"{path}?page={page}"


def _call_status(provider: ResourceProvider, config: dict[str, object], name: str) -> SourceStatus:
    try:
        return provider.status(config)
    except ResourceUnavailable as exc:
        return SourceStatus("failed", message=exc.message)
    except Exception:
        logger.exception("资源来源 %s 读取状态失败", name)
        return SourceStatus("failed", message="读取来源状态失败，详见服务日志")


def _out(source_id: int, source_name: str, plugin_id: str, status: SourceStatus) -> SourceStatusOut:
    return SourceStatusOut(
        source_id=source_id,
        source_name=source_name,
        plugin_id=plugin_id,
        state=status.state,
        message=status.message,
        resume_at=status.resume_at,
        page=status.page,
    )
