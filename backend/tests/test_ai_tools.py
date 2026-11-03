"""工具注册表、工具循环、审批策略和待确认动作（M52，ADR 0030）。"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.ai.loop import LoopEnd, LoopEvent, ToolEnd, ToolStart, run_tool_loop
from app.ai.tools import ADMIN_ONLY, ALL_ROLES, Tool, ToolContext, ToolResult
from app.ai.types import Finish, Message, StreamEvent, TextDelta, ToolCall, ToolCallEvent
from app.auth.users import User
from app.container import AppContainer
from app.errors import AppError
from tests.conftest import FakeClock, bearer, create_user, login
from tests.fake_ai import FakeAi
from tests.test_ai_providers import add_provider, container, set_roles


class SearchParams(BaseModel):
    q: str
    limit: int = 10


class MatchParams(BaseModel):
    work_id: int


def _mine(call: Any) -> list[str]:
    """本测试注册的工具；应用自带的工具（AI 找片、对话等）不在比较范围内。"""
    return [t.name for t in call.tools if t.name in ("search", "apply_match")]


def make_user(role: str, user_id: int) -> User:
    return User(user_id, role, role, False, 0, "x")  # type: ignore[arg-type]


class Probe:
    """记录工具被谁、以什么参数执行过。"""

    def __init__(self) -> None:
        self.runs: list[tuple[str, Any]] = []


def search_tool(probe: Probe, roles: frozenset[Any] = ALL_ROLES) -> Tool:
    def run(ctx: ToolContext, args: SearchParams) -> ToolResult:
        probe.runs.append((ctx.user.username, args))
        if args.q == "boom":
            raise RuntimeError("secret internals")
        if args.q == "denied":
            raise AppError(404, "not_found", "资源不存在")
        return ToolResult({"items": [1, 2]}, f"找到 2 部（{args.q}）", (1, 2))

    return Tool(
        "search",
        "搜索资料库",
        SearchParams,
        "read",
        roles,
        run,
        running=lambda a: f"正在搜索「{a.q}」…",
    )


def match_tool(probe: Probe) -> Tool:
    def run(ctx: ToolContext, args: MatchParams) -> ToolResult:
        probe.runs.append((ctx.user.username, args))
        if args.work_id == 0:
            raise AppError(404, "not_found", "作品不存在")
        return ToolResult({"ok": True}, f"已匹配作品 {args.work_id}")

    return Tool(
        "apply_match",
        "把作品匹配到候选",
        MatchParams,
        "reversible_write",
        ADMIN_ONLY,
        run,
        running=lambda a: "正在匹配…",
        describe=lambda a: f"匹配作品 {a.work_id}",
    )


def call(name: str, arguments: dict[str, Any], call_id: str = "c1") -> ToolCallEvent:
    import json

    return ToolCallEvent(ToolCall(call_id, name, arguments, json.dumps(arguments)))


@pytest.fixture
def setup(client: TestClient, admin_tokens: dict[str, str]) -> AppContainer:
    provider = add_provider(client, admin_tokens)
    set_roles(
        client,
        admin_tokens,
        chat={"provider_id": provider["id"], "model": "c"},
        task={"provider_id": provider["id"], "model": "t"},
    )
    return container(client)


def run(
    app: AppContainer,
    user: User,
    messages: list[Message] | None = None,
    **kwargs: Any,
) -> tuple[list[LoopEvent], list[Message]]:
    history = messages if messages is not None else [Message("user", "找片")]
    events = list(
        run_tool_loop(
            ai=app.ai,
            registry=app.tools,
            actions=app.actions,
            context=ToolContext(user, app),
            role="chat",
            messages=history,
            **kwargs,
        )
    )
    return events, history


def test_registry_rules() -> None:
    from app.ai.tools import ToolRegistry

    registry = ToolRegistry()
    probe = Probe()
    registry.register(search_tool(probe))
    with pytest.raises(ValueError, match="重名"):
        registry.register(search_tool(probe))

    def noop(ctx: ToolContext, args: MatchParams) -> ToolResult:
        return ToolResult({}, "")

    for risk, message in (("destructive", "破坏性"), ("reversible_write", "描述")):
        with pytest.raises(ValueError, match=message):
            registry.register(
                Tool("x", "x", MatchParams, risk, ADMIN_ONLY, noop, running=lambda a: "")  # type: ignore[arg-type]
            )
    spec = registry.get("search").spec()  # type: ignore[union-attr]
    assert spec.parameters["required"] == ["q"] and "title" not in spec.parameters


def test_loop_runs_a_tool_and_feeds_the_result_back(setup: AppContainer, fake_ai: FakeAi) -> None:
    probe = Probe()
    setup.tools.register(search_tool(probe))
    fake_ai.script(
        [TextDelta("我来搜"), call("search", {"q": "沙丘"}), Finish("tool_calls")],
        [TextDelta("找到了"), Finish("stop")],
    )
    events, messages = run(setup, make_user("user", 1))

    assert events == [
        TextDelta("我来搜"),
        ToolStart("c1", "search", "正在搜索「沙丘」…"),
        ToolEnd("c1", "search", "找到 2 部（沙丘）", ok=True, item_ids=(1, 2)),
        TextDelta("找到了"),
        LoopEnd("stop"),
    ]
    assert [m.role for m in messages] == ["user", "assistant", "tool", "assistant"]
    assert messages[2].tool_call_id == "c1" and '"items": [1, 2]' in messages[2].content
    # 第二轮请求带着工具结果
    assert fake_ai.calls[1].messages[-1].role == "tool"
    assert _mine(fake_ai.calls[0]) == ["search"]
    assert probe.runs[0][1].q == "沙丘"


def test_loop_returns_tool_problems_to_the_model(setup: AppContainer, fake_ai: FakeAi) -> None:
    probe = Probe()
    setup.tools.register(search_tool(probe))
    fake_ai.script(
        [
            call("missing", {}, "a"),
            call("search", {"limit": 3}, "b"),
            call("search", {"q": "denied"}, "c"),
            call("search", {"q": "boom"}, "d"),
            Finish("tool_calls"),
        ],
        [TextDelta("抱歉"), Finish("stop")],
    )
    events, messages = run(setup, make_user("user", 1))

    ends = [e for e in events if isinstance(e, ToolEnd)]
    assert [e.ok for e in ends] == [False, False, False, False]
    assert ends[0].summary == "没有这个工具"
    assert ends[1].summary == "参数不合法"
    assert ends[2].summary == "资源不存在"
    assert ends[3].summary == "工具执行失败"
    tool_messages = {m.tool_call_id: m.content for m in messages if m.role == "tool"}
    assert "没有名为 missing" in tool_messages["a"]
    assert "q" in tool_messages["b"]
    # 内部异常的细节不会回给模型
    assert "secret internals" not in tool_messages["d"]
    assert events[-1] == LoopEnd("stop")


def test_loop_rejects_arguments_that_are_not_json(setup: AppContainer, fake_ai: FakeAi) -> None:
    setup.tools.register(search_tool(Probe()))
    broken = ToolCallEvent(ToolCall("x", "search", {}, "{oops"))
    fake_ai.script([broken, Finish("tool_calls")], [TextDelta("好"), Finish("stop")])
    events, messages = run(setup, make_user("user", 1))
    assert [e.summary for e in events if isinstance(e, ToolEnd)] == ["参数不合法"]
    assert "JSON" in messages[2].content


def test_loop_stops_at_the_step_and_call_limits(setup: AppContainer, fake_ai: FakeAi) -> None:
    probe = Probe()
    setup.tools.register(search_tool(probe))
    again: list[StreamEvent] = [call("search", {"q": "a"}), Finish("tool_calls")]
    fake_ai.script(again, again)
    events, _ = run(setup, make_user("user", 1), max_steps=2)
    assert events[-1] == LoopEnd("max_steps")
    assert len(probe.runs) == 2

    probe.runs.clear()
    fake_ai.script(
        [call("search", {"q": "a"}, "1"), call("search", {"q": "b"}, "2"), Finish("tool_calls")]
    )
    events, messages = run(setup, make_user("user", 1), max_tool_calls=1)
    assert events[-1] == LoopEnd("max_tool_calls")
    assert len(probe.runs) == 1
    # 超出的调用也有对应的结果，对话仍然合法
    assert [m.tool_call_id for m in messages if m.role == "tool"] == ["1", "2"]

    # 恰好用完额度后模型仍可以回答
    fake_ai.script(
        [call("search", {"q": "a"}), Finish("tool_calls")], [TextDelta("done"), Finish("stop")]
    )
    events, _ = run(setup, make_user("user", 1), max_tool_calls=1)
    assert events[-1] == LoopEnd("stop")


def test_loop_reports_a_truncated_answer(setup: AppContainer, fake_ai: FakeAi) -> None:
    fake_ai.script([TextDelta("半截"), Finish("length")])
    events, _ = run(setup, make_user("user", 1))
    assert events[-1] == LoopEnd("length")


def test_tools_are_filtered_by_role_and_scene(setup: AppContainer, fake_ai: FakeAi) -> None:
    probe = Probe()
    setup.tools.register(search_tool(probe))
    setup.tools.register(match_tool(probe))
    fake_ai.script([Finish("stop")], [Finish("stop")], [Finish("stop")])

    run(setup, make_user("user", 1))
    run(setup, make_user("admin", 1))
    run(setup, make_user("admin", 1), tool_names={"search"})
    assert _mine(fake_ai.calls[0]) == ["search"]
    assert _mine(fake_ai.calls[1]) == ["search", "apply_match"]
    assert _mine(fake_ai.calls[2]) == ["search"]

    # 模型编造了没提供给它的工具：按不存在处理，不会执行
    fake_ai.script([call("apply_match", {"work_id": 1}), Finish("tool_calls")], [Finish("stop")])
    events, _ = run(setup, make_user("user", 1))
    assert [e.summary for e in events if isinstance(e, ToolEnd)] == ["没有这个工具"]
    assert probe.runs == []


def test_reversible_writes_wait_for_confirmation_by_default(
    client: TestClient, setup: AppContainer, fake_ai: FakeAi, admin_tokens: dict[str, str]
) -> None:
    probe = Probe()
    setup.tools.register(match_tool(probe))
    headers = bearer(admin_tokens)
    assert (
        client.get("/api/admin/ai/settings", headers=headers).json()["reversible_write"]
        == "confirm"
    )

    fake_ai.script(
        [call("apply_match", {"work_id": 7}), Finish("tool_calls")],
        [TextDelta("已提交"), Finish("stop")],
    )
    events, messages = run(setup, make_user("admin", 1))
    end = next(e for e in events if isinstance(e, ToolEnd))
    assert end.ok and end.summary == "已提交，等待管理员确认" and end.action_id is not None
    assert probe.runs == []
    assert "pending_confirmation" in messages[2].content

    pending = client.get("/api/admin/ai/actions", headers=headers).json()["actions"]
    assert [(a["id"], a["tool"], a["summary"], a["status"]) for a in pending] == [
        (end.action_id, "apply_match", "匹配作品 7", "pending")
    ]

    confirmed = client.post(f"/api/admin/ai/actions/{end.action_id}/confirm", headers=headers)
    body = confirmed.json()
    assert body["status"] == "executed" and body["result"] == "已匹配作品 7"
    assert body["decided_by"] is not None and body["decided_at"] is not None
    # 确认人的身份执行
    assert probe.runs[0][0] == "admin"

    again = client.post(f"/api/admin/ai/actions/{end.action_id}/confirm", headers=headers)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "ai_action_not_pending"
    assert len(probe.runs) == 1
    assert client.get("/api/admin/ai/actions", headers=headers).json()["actions"] == []
    done = client.get("/api/admin/ai/actions?status=executed", headers=headers).json()["actions"]
    assert len(done) == 1


def test_auto_mode_runs_reversible_writes_directly(
    client: TestClient, setup: AppContainer, fake_ai: FakeAi, admin_tokens: dict[str, str]
) -> None:
    probe = Probe()
    setup.tools.register(match_tool(probe))
    saved = client.put(
        "/api/admin/ai/settings", json={"reversible_write": "auto"}, headers=bearer(admin_tokens)
    )
    assert saved.json()["reversible_write"] == "auto"
    fake_ai.script([call("apply_match", {"work_id": 3}), Finish("tool_calls")], [Finish("stop")])
    events, _ = run(setup, make_user("admin", 1))
    end = next(e for e in events if isinstance(e, ToolEnd))
    assert end.summary == "已匹配作品 3" and end.action_id is None
    assert len(probe.runs) == 1
    assert (
        client.put(
            "/api/admin/ai/settings",
            json={"reversible_write": "maybe"},
            headers=bearer(admin_tokens),
        ).status_code
        == 422
    )


def test_reject_expire_and_failed_actions(
    client: TestClient,
    setup: AppContainer,
    admin_tokens: dict[str, str],
    clock: FakeClock,
) -> None:
    probe = Probe()
    tool = match_tool(probe)
    setup.tools.register(tool)
    admin = make_user("admin", 1)
    rejected = setup.actions.queue(tool, {"work_id": 1}, admin)
    failing = setup.actions.queue(tool, {"work_id": 0}, admin)
    stale = setup.actions.queue(tool, {"work_id": 2}, admin)
    headers = bearer(admin_tokens)

    assert (
        client.post(f"/api/admin/ai/actions/{rejected}/reject", headers=headers).json()["status"]
        == "rejected"
    )
    assert (
        client.post(f"/api/admin/ai/actions/{rejected}/confirm", headers=headers).status_code == 409
    )

    # 执行失败不抛出，记下原因
    failed = client.post(f"/api/admin/ai/actions/{failing}/confirm", headers=headers).json()
    assert failed["status"] == "failed" and failed["error"] == "作品不存在"

    clock.advance(days=8)
    headers = bearer(login(client, "admin", "admin-password-for-tests"))
    assert client.post(f"/api/admin/ai/actions/{stale}/confirm", headers=headers).status_code == 409
    expired = client.get("/api/admin/ai/actions?status=expired", headers=headers).json()["actions"]
    assert [a["id"] for a in expired] == [stale]
    assert probe.runs == [("admin", MatchParams(work_id=0))]
    assert client.post("/api/admin/ai/actions/999/confirm", headers=headers).status_code == 404


def test_actions_are_admin_only(client: TestClient, admin_tokens: dict[str, str]) -> None:
    create_user(client, admin_tokens)
    user = bearer(login(client, "alice", "alice-password"))
    assert client.get("/api/admin/ai/actions", headers=user).status_code == 403
    assert client.post("/api/admin/ai/actions/1/confirm", headers=user).status_code == 403
    assert client.get("/api/admin/ai/settings", headers=user).status_code == 403
