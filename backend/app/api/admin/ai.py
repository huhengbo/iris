"""AI 助手的模型接入：提供方、用途选择、用量（ADR 0030）。"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from app.ai.actions import ActionStatus, PendingAction
from app.ai.presets import PRESETS
from app.ai.service import (
    MAX_KEY_LENGTH,
    MAX_MODEL_LENGTH,
    MAX_NAME_LENGTH,
    MAX_URL_LENGTH,
    Provider,
    RoleBinding,
    TestResult,
)
from app.ai.tools import ToolContext
from app.ai.types import ROLES, ProviderKind, Role
from app.api.schemas import Out
from app.deps import Actions, AdminUser, Ai, AiConfig, Container, Semantic

router = APIRouter(prefix="/admin/ai", tags=["admin"])

MAX_USAGE_MONTHS = 24


class PresetOut(Out):
    id: str
    name: str
    kind: Literal["openai", "anthropic"]
    base_url: str
    # 预填的模型名，没有则为空字符串
    chat_model: str
    embedding_model: str


class PresetsOut(Out):
    presets: list[PresetOut]


class ProviderTestOut(Out):
    ok: bool
    checked_at: str
    # auth、rate_limited、timeout、unreachable、bad_request、server、unsupported、not_configured
    error_kind: str | None
    message: str | None
    duration_ms: int


class LastTestOut(Out):
    ok: bool
    checked_at: str
    message: str | None


class AiProviderOut(Out):
    id: int
    name: str
    kind: Literal["openai", "anthropic"]
    base_url: str
    # Key 只返回是否已设置，不回显
    key_set: bool
    last_test: LastTestOut | None
    # 正在使用这个提供方的用途
    used_by: list[Literal["chat", "task", "embedding"]]


class AiProvidersOut(Out):
    providers: list[AiProviderOut]


class ProviderRequest(BaseModel):
    name: str = Field(max_length=MAX_NAME_LENGTH * 2)
    kind: Literal["openai", "anthropic"]
    base_url: str = Field(max_length=MAX_URL_LENGTH * 2)
    # 创建时不传表示没有 Key（本地 Ollama）；更新时不传保持原值，传空字符串或 null 表示清除
    api_key: str | None = Field(default=None, max_length=MAX_KEY_LENGTH * 2)


class RoleBindingBody(BaseModel):
    provider_id: int
    model: str = Field(max_length=MAX_MODEL_LENGTH * 2)


class RoleBindingOut(Out):
    provider_id: int
    model: str


class RolesOut(Out):
    chat: RoleBindingOut | None
    task: RoleBindingOut | None
    embedding: RoleBindingOut | None


class RolesRequest(BaseModel):
    """不传的用途保持原值，传 null 表示清除。"""

    chat: RoleBindingBody | None = None
    task: RoleBindingBody | None = None
    embedding: RoleBindingBody | None = None


class AiSettingsOut(Out):
    # 可逆写工具的审批：confirm 先生成待确认动作；auto 直接执行
    reversible_write: Literal["confirm", "auto"]
    # 同步后自动为未匹配的作品生成匹配建议（需要配置后台任务模型）
    suggest_matches: bool
    # 高置信度且通过硬校验的建议自动写入匹配
    auto_match: bool
    auto_match_threshold: float


class AiSettingsRequest(BaseModel):
    """不传的项保持原值。"""

    reversible_write: Literal["confirm", "auto"] | None = None
    suggest_matches: bool | None = None
    auto_match: bool | None = None
    auto_match_threshold: float | None = None


class PendingActionOut(Out):
    id: int
    tool: str
    summary: str
    reason: str
    # 发起者；用户已删除时为 null
    user_id: int | None
    status: Literal["pending", "running", "executed", "failed", "rejected", "expired"]
    created_at: str
    expires_at: str
    decided_at: str | None
    decided_by: int | None
    result: str | None
    error: str | None


class PendingActionsOut(Out):
    actions: list[PendingActionOut]


class SemanticStatusOut(Out):
    # off 没有配置向量模型；unavailable sqlite-vec 加载失败；building 正在建索引；
    # ready 已是最新；error 向量模型调用失败
    state: Literal["off", "unavailable", "building", "ready", "error"]
    model: str | None
    total: int
    indexed: int
    error: str | None


class UsageRowOut(Out):
    month: str
    role: Literal["chat", "task", "embedding"]
    calls: int
    failures: int
    input_tokens: int
    output_tokens: int


class UsageOut(Out):
    rows: list[UsageRowOut]


@router.get("/presets")
def list_presets(_: AdminUser) -> PresetsOut:
    return PresetsOut(presets=[PresetOut.model_validate(p) for p in PRESETS])


@router.get("/providers")
def list_providers(_: AdminUser, ai: Ai) -> AiProvidersOut:
    return AiProvidersOut(providers=[_provider_out(p) for p in ai.list_providers()])


@router.post("/providers", status_code=201)
def create_provider(body: ProviderRequest, _: AdminUser, ai: Ai) -> AiProviderOut:
    return _provider_out(ai.create_provider(body.name, body.kind, body.base_url, body.api_key))


@router.put("/providers/{provider_id}")
def update_provider(provider_id: int, body: ProviderRequest, _: AdminUser, ai: Ai) -> AiProviderOut:
    kind: ProviderKind = body.kind
    return _provider_out(
        ai.update_provider(
            provider_id,
            name=body.name,
            kind=kind,
            base_url=body.base_url,
            api_key=body.api_key,
            change_key="api_key" in body.model_fields_set,
        )
    )


@router.delete("/providers/{provider_id}", status_code=204)
def delete_provider(provider_id: int, _: AdminUser, ai: Ai) -> Response:
    ai.delete_provider(provider_id)
    return Response(status_code=204)


@router.post("/providers/{provider_id}/test")
def test_provider(provider_id: int, _: AdminUser, ai: Ai) -> ProviderTestOut:
    """发一次最小请求（列出模型），验证地址和 Key；结果同时记在提供方上。"""
    ai.get_provider(provider_id)
    return _test_out(ai.test_provider(provider_id))


@router.get("/roles")
def get_roles(_: AdminUser, ai: Ai) -> RolesOut:
    return _roles_out(ai.roles())


@router.put("/roles")
def save_roles(body: RolesRequest, _: AdminUser, ai: Ai) -> RolesOut:
    for role in ROLES:
        if role in body.model_fields_set:
            value: RoleBindingBody | None = getattr(body, role)
            ai.set_role(role, RoleBinding(value.provider_id, value.model) if value else None)
    return _roles_out(ai.roles())


@router.post("/roles/{role}/test")
def test_role(role: Role, _: AdminUser, ai: Ai) -> ProviderTestOut:
    """用这个用途选的模型发一次最小请求，确认模型名可用；不计入用量。"""
    return _test_out(ai.test_role(role))


@router.get("/settings")
def get_ai_settings(_: AdminUser, config: AiConfig) -> AiSettingsOut:
    return AiSettingsOut.model_validate(config.get())


@router.put("/settings")
def save_ai_settings(body: AiSettingsRequest, _: AdminUser, config: AiConfig) -> AiSettingsOut:
    return AiSettingsOut.model_validate(config.save(**body.model_dump(exclude_none=True)))


@router.get("/actions")
def list_actions(
    _: AdminUser,
    actions: Actions,
    status: Annotated[ActionStatus, Query()] = "pending",
) -> PendingActionsOut:
    """按状态列出待确认动作，默认只列出还没处理的。"""
    return PendingActionsOut(actions=[_action_out(a) for a in actions.list(status)])


@router.post("/actions/{action_id}/confirm")
def confirm_action(
    action_id: int, admin: AdminUser, actions: Actions, container: Container
) -> PendingActionOut:
    """用当前管理员的身份执行动作；执行失败时返回 status 为 failed 并带上原因。"""
    return _action_out(actions.confirm(action_id, ToolContext(admin, container)))


@router.post("/actions/{action_id}/reject")
def reject_action(action_id: int, admin: AdminUser, actions: Actions) -> PendingActionOut:
    return _action_out(actions.reject(action_id, admin))


@router.get("/semantic")
def get_semantic_status(_: AdminUser, semantic: Semantic) -> SemanticStatusOut:
    """语义索引的进度。换向量模型后会自动重建，这里能看到进度。"""
    return SemanticStatusOut.model_validate(semantic.status())


@router.post("/semantic/rebuild", status_code=204)
def rebuild_semantic(_: AdminUser, semantic: Semantic) -> Response:
    """清空语义索引，由后台任务重新生成。"""
    semantic.clear()
    return Response(status_code=204)


@router.get("/usage")
def get_usage(
    _: AdminUser,
    ai: Ai,
    months: Annotated[int, Query(ge=1, le=MAX_USAGE_MONTHS)] = 6,
) -> UsageOut:
    return UsageOut(rows=[UsageRowOut.model_validate(r) for r in ai.usage(months)])


def _provider_out(provider: Provider) -> AiProviderOut:
    return AiProviderOut(
        id=provider.id,
        name=provider.name,
        kind=provider.kind,
        base_url=provider.base_url,
        key_set=provider.key_set,
        last_test=(
            LastTestOut(
                ok=provider.last_test.ok,
                checked_at=provider.last_test.checked_at,
                message=provider.last_test.message,
            )
            if provider.last_test
            else None
        ),
        used_by=list(provider.used_by),
    )


def _test_out(result: TestResult) -> ProviderTestOut:
    return ProviderTestOut(
        ok=result.ok,
        checked_at=result.checked_at,
        error_kind=result.error_kind,
        message=result.message,
        duration_ms=result.duration_ms,
    )


def _roles_out(roles: dict[Role, RoleBinding | None]) -> RolesOut:
    def one(role: Role) -> RoleBindingOut | None:
        binding = roles[role]
        return (
            RoleBindingOut(provider_id=binding.provider_id, model=binding.model)
            if binding
            else None
        )

    return RolesOut(chat=one("chat"), task=one("task"), embedding=one("embedding"))


def _action_out(action: PendingAction) -> PendingActionOut:
    return PendingActionOut.model_validate(action)
