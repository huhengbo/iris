"""工具循环：模型提出工具调用，执行后把结果回给模型，直到给出最终回答（ADR 0030）。

循环不抛出业务错误：未知工具、参数不合法、没有权限、工具执行失败都作为工具结果回给模型，
让它自己纠正或向用户解释。只有模型调用本身失败（AiError）才向上抛出，由调用方决定如何展示。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from app.ai.actions import PendingActions
from app.ai.service import AiService
from app.ai.tools import Tool, ToolContext, ToolError, ToolRegistry
from app.ai.types import (
    Finish,
    Message,
    Role,
    TextDelta,
    ThinkingDelta,
    ToolCall,
    ToolCallEvent,
    UsageEvent,
)
from app.errors import AppError

logger = logging.getLogger(__name__)

DEFAULT_MAX_STEPS = 8
# 单个工具结果回给模型的字符上限，防止一次查询撑爆上下文
MAX_RESULT_CHARS = 12_000

EndReason = Literal["stop", "max_steps", "max_tool_calls", "length"]
END_MESSAGES: dict[EndReason, str] = {
    "stop": "",
    "max_steps": "调用模型的次数达到上限，已停止。",
    "max_tool_calls": "调用工具的次数达到上限，已停止。",
    "length": "回答被长度限制截断。",
}


@dataclass(frozen=True)
class ToolStart:
    call_id: str
    name: str
    label: str


@dataclass(frozen=True)
class ToolEnd:
    call_id: str
    name: str
    summary: str
    ok: bool
    item_ids: tuple[int, ...] = ()
    # 写操作进入了待确认队列时的动作编号
    action_id: int | None = None


@dataclass(frozen=True)
class LoopEnd:
    reason: EndReason


LoopEvent = TextDelta | ThinkingDelta | UsageEvent | ToolStart | ToolEnd | LoopEnd


def run_tool_loop(
    *,
    ai: AiService,
    registry: ToolRegistry,
    actions: PendingActions,
    context: ToolContext,
    role: Role,
    messages: list[Message],
    tool_names: set[str] | None = None,
    max_steps: int = DEFAULT_MAX_STEPS,
    max_tool_calls: int | None = None,
    stop_after: frozenset[str] = frozenset(),
    max_tokens: int = 4096,
) -> Iterator[LoopEvent]:
    """messages 会被原地追加模型回复和工具结果，调用方据此保存对话。

    stop_after 里的工具成功执行后立即结束循环（用于「提交结果」这类终结工具）。
    """
    tools = {t.name: t for t in registry.available(context.user, tool_names)}
    specs = [t.spec() for t in tools.values()]
    used_calls = 0
    for _ in range(max_steps):
        text = ""
        reasoning = ""
        calls: list[ToolCall] = []
        reason: Literal["stop", "tool_calls", "length"] = "stop"
        for event in ai.stream(
            role, messages, specs, user_id=context.user.id or None, max_tokens=max_tokens
        ):
            if isinstance(event, TextDelta):
                text += event.text
                yield event
            elif isinstance(event, ThinkingDelta):
                reasoning += event.text
                yield event
            elif isinstance(event, ToolCallEvent):
                calls.append(event.call)
            elif isinstance(event, UsageEvent):
                yield event
            elif isinstance(event, Finish):
                reason = event.reason
        messages.append(Message("assistant", text, tuple(calls), reasoning=reasoning))
        if not calls:
            yield LoopEnd("length" if reason == "length" else "stop")
            return
        limit_hit = False
        finished = False
        for call in calls:
            if max_tool_calls is not None and used_calls >= max_tool_calls:
                # 每个调用都必须有对应的结果，否则下一轮请求会被提供方拒绝
                messages.append(
                    Message("tool", _error("工具调用次数已达上限"), tool_call_id=call.id)
                )
                limit_hit = True
                continue
            used_calls += 1
            for outcome in _execute(call, tools, actions, context, messages):
                yield outcome
                if isinstance(outcome, ToolEnd) and outcome.ok and call.name in stop_after:
                    finished = True
        if finished:
            yield LoopEnd("stop")
            return
        if limit_hit:
            yield LoopEnd("max_tool_calls")
            return
    yield LoopEnd("max_steps")


def _execute(
    call: ToolCall,
    tools: dict[str, Tool],
    actions: PendingActions,
    context: ToolContext,
    messages: list[Message],
) -> Iterator[ToolStart | ToolEnd]:
    def reply(content: str) -> None:
        messages.append(Message("tool", content[:MAX_RESULT_CHARS], tool_call_id=call.id))

    tool = tools.get(call.name)
    if tool is None:
        yield ToolEnd(call.id, call.name, "没有这个工具", ok=False)
        reply(_error(f"没有名为 {call.name} 的工具，请只使用提供的工具"))
        return
    try:
        if not call.arguments and call.raw_arguments.strip() not in ("", "{}"):
            raise ValueError("参数不是合法的 JSON 对象")
        params = tool.params.model_validate(call.arguments)
    except (ValidationError, ValueError) as exc:
        yield ToolEnd(call.id, call.name, "参数不合法", ok=False)
        reply(_error(f"参数不合法：{_describe(exc)}"))
        return
    yield ToolStart(call.id, call.name, tool.running(params))
    if actions.needs_confirmation(tool):
        action_id = actions.queue(tool, params.model_dump(mode="json"), context.user)
        summary = "已提交，等待管理员确认"
        yield ToolEnd(call.id, call.name, summary, ok=True, action_id=action_id)
        reply(
            json.dumps({"status": "pending_confirmation", "message": summary}, ensure_ascii=False)
        )
        return
    try:
        result = tool.run(context, params)
    except (AppError, ToolError) as exc:
        yield ToolEnd(call.id, call.name, exc.message, ok=False)
        reply(_error(exc.message))
        return
    except Exception:
        logger.exception("tool %s failed", tool.name)
        yield ToolEnd(call.id, call.name, "工具执行失败", ok=False)
        reply(_error("工具执行失败"))
        return
    yield ToolEnd(call.id, call.name, result.summary, ok=True, item_ids=result.item_ids)
    reply(json.dumps(result.content, ensure_ascii=False, default=str))


def _error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


def _describe(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "；".join(
            f"{'.'.join(str(p) for p in e['loc']) or '参数'}：{e['msg']}" for e in exc.errors()
        )
    return str(exc)
