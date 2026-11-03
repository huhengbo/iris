"""待确认动作：需要管理员点头的写操作（ADR 0030）。"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from pydantic import ValidationError

from app.ai.settings import AiSettings
from app.ai.tools import Tool, ToolContext, ToolRegistry
from app.auth.users import User
from app.clock import Clock, to_iso
from app.db import Database
from app.errors import AppError, NotFound

logger = logging.getLogger(__name__)

ActionStatus = Literal["pending", "running", "executed", "failed", "rejected", "expired"]
# 超过这个时间没人确认就作废，避免很久以前的建议在状态变了之后被误执行
EXPIRES_AFTER = timedelta(days=7)
MAX_LIST = 200


@dataclass(frozen=True)
class PendingAction:
    id: int
    tool: str
    arguments: dict[str, Any]
    summary: str
    reason: str
    user_id: int | None
    status: ActionStatus
    created_at: str
    expires_at: str
    decided_at: str | None
    decided_by: int | None
    result: str | None
    error: str | None


class PendingActions:
    def __init__(
        self, database: Database, clock: Clock, registry: ToolRegistry, settings: AiSettings
    ) -> None:
        self._database = database
        self._clock = clock
        self._registry = registry
        self._settings = settings

    # 审批策略 -------------------------------------------------------------

    def needs_confirmation(self, tool: Tool) -> bool:
        if tool.risk == "read":
            return False
        if tool.risk == "destructive":
            return True
        return self._settings.get().reversible_write == "confirm"

    # 生命周期 -------------------------------------------------------------

    def queue(self, tool: Tool, arguments: dict[str, Any], user: User, reason: str = "") -> int:
        now = self._clock.now()
        summary = (
            tool.describe(tool.params.model_validate(arguments)) if tool.describe else tool.name
        )
        with self._database.session() as connection:
            cursor = connection.execute(
                "INSERT INTO ai_pending_action (tool, arguments, summary, reason, user_id, "
                "created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    tool.name,
                    json.dumps(arguments, ensure_ascii=False),
                    summary,
                    reason,
                    user.id,
                    to_iso(now),
                    to_iso(now + EXPIRES_AFTER),
                ),
            )
            return int(cursor.lastrowid or 0)

    def list(self, status: ActionStatus | None = "pending") -> list[PendingAction]:
        self._expire_overdue()
        sql = "SELECT * FROM ai_pending_action"
        params: tuple[object, ...] = ()
        if status is not None:
            sql += " WHERE status = ?"
            params = (status,)
        sql += " ORDER BY id DESC LIMIT ?"
        with self._database.read_session() as connection:
            return [_action(r) for r in connection.execute(sql, (*params, MAX_LIST))]

    def get(self, action_id: int) -> PendingAction:
        with self._database.read_session() as connection:
            return _get(connection, action_id)

    def confirm(self, action_id: int, context: ToolContext) -> PendingAction:
        """用确认人的身份执行动作。执行失败不抛出，记为失败并带上原因，让管理员看到。"""
        self._expire_overdue()
        action = self._claim(action_id, context.user)
        tool = self._registry.get(action.tool)
        result: str | None = None
        error: str | None = None
        if tool is None or context.user.role not in tool.roles:
            error = "这个动作对应的工具已不可用"
        else:
            try:
                parsed = tool.params.model_validate(action.arguments)
                outcome = tool.run(context, parsed)
                result = outcome.summary
            except ValidationError:
                error = "动作参数已不合法"
            except AppError as exc:
                error = exc.message
            except Exception:
                logger.exception("pending action %s failed", action_id)
                error = "执行失败，详情见服务日志"
        with self._database.session() as connection:
            connection.execute(
                "UPDATE ai_pending_action SET status = ?, result = ?, error = ? WHERE id = ?",
                ("failed" if error else "executed", result, error, action_id),
            )
        return self.get(action_id)

    def reject(self, action_id: int, user: User) -> PendingAction:
        now = to_iso(self._clock.now())
        with self._database.session() as connection:
            action = _get(connection, action_id)
            if action.status != "pending":
                raise AppError(409, "ai_action_not_pending", "这个动作已经处理过了")
            connection.execute(
                "UPDATE ai_pending_action SET status = 'rejected', decided_at = ?, decided_by = ? "
                "WHERE id = ?",
                (now, user.id, action_id),
            )
        return self.get(action_id)

    def _claim(self, action_id: int, user: User) -> PendingAction:
        """把待确认的动作原子地标成执行中，防止两个管理员同时确认而执行两次。"""
        with self._database.session() as connection:
            action = _get(connection, action_id)
            claimed = connection.execute(
                "UPDATE ai_pending_action SET status = 'running', decided_at = ?, decided_by = ? "
                "WHERE id = ? AND status = 'pending'",
                (to_iso(self._clock.now()), user.id, action_id),
            ).rowcount
        if not claimed:
            raise AppError(409, "ai_action_not_pending", "这个动作已经处理过了")
        return action

    def _expire_overdue(self) -> None:
        with self._database.session() as connection:
            connection.execute(
                "UPDATE ai_pending_action SET status = 'expired' "
                "WHERE status = 'pending' AND expires_at <= ?",
                (to_iso(self._clock.now()),),
            )


def _get(connection: sqlite3.Connection, action_id: int) -> PendingAction:
    row = connection.execute(
        "SELECT * FROM ai_pending_action WHERE id = ?", (action_id,)
    ).fetchone()
    if row is None:
        raise NotFound("待确认动作不存在")
    return _action(row)


def _action(row: sqlite3.Row) -> PendingAction:
    return PendingAction(
        id=row["id"],
        tool=row["tool"],
        arguments=json.loads(row["arguments"]),
        summary=row["summary"],
        reason=row["reason"],
        user_id=row["user_id"],
        status=row["status"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        decided_at=row["decided_at"],
        decided_by=row["decided_by"],
        result=row["result"],
        error=row["error"],
    )
