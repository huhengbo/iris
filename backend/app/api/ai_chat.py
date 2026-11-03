"""AI 对话接口：会话的增删查，以及用 SSE 流式返回回答（ADR 0030）。

POST /ai/chats/{id}/messages 返回 text/event-stream。每个事件由 `event:` 行和 `data:` 行
（一行 JSON）组成，事件的结构见 ChatStreamEvent。客户端断开连接就是「停止生成」：
已经生成的部分会保存，消息状态为 stopped。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from anyio import CancelScope, to_thread
from fastapi import APIRouter, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.ai.chat import Chat, ChatMessage, ChatStep, ChatTurn
from app.api.browse import ItemCardOut
from app.api.schemas import Out
from app.container import AppContainer
from app.deps import Chats, Container, CurrentUser
from app.errors import AppError
from app.library.browse_models import ItemCard

router = APIRouter(prefix="/ai/chats", tags=["ai"])

MAX_CONTENT_LENGTH = 2000


class ChatOut(Out):
    id: int
    title: str
    created_at: str
    updated_at: str


class ChatListOut(Out):
    chats: list[ChatOut]


class ChatStepOut(Out):
    # 工具名：search_library、item_details、person_works、my_history
    name: str
    # 进行中的文案，例如「正在搜索资料库…」
    label: str
    # 完成后的一句话结果，例如「找到 12 部」
    summary: str
    ok: bool
    item_ids: list[int]


class ChatMessageOut(Out):
    id: int
    role: Literal["user", "assistant"]
    content: str
    steps: list[ChatStepOut]
    # 回答里的条目卡片，按当前可见范围解析；范围外的会被省略
    items: list[ItemCardOut]
    suggestions: list[str]
    # 思考用了多少秒；没有思考过程时为 null
    thinking_seconds: float | None
    # done 正常结束；stopped 被停止；error 出错（error 字段有原因）
    status: Literal["done", "stopped", "error"]
    error: str | None
    created_at: str


class ChatDetailOut(ChatOut):
    messages: list[ChatMessageOut]


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_CONTENT_LENGTH)


# ---- SSE 事件（仅用于文档：线上格式是 `event: <名称>\ndata: <data 的 JSON>\n\n`）----


class StartData(BaseModel):
    chat_id: int
    # 用户这条消息的编号
    message_id: int


class ThinkingData(BaseModel):
    # 思考的增量文本，可以不显示；思考结束时 delta 为空、seconds 有值
    delta: str
    seconds: float | None


class ToolStartData(BaseModel):
    id: str
    name: str
    # 进行中的文案，例如「正在搜索资料库…」
    label: str


class ToolEndData(BaseModel):
    id: str
    name: str
    # 完成后的一句话结果；失败时是原因
    summary: str
    ok: bool
    # 这次调用涉及的条目卡片，应当先于回答文字展示
    items: list[ItemCardOut]


class TextData(BaseModel):
    # 回答的增量文本。tool_start 之前收到的文字是过渡语，可以丢弃
    delta: str


class SuggestionsData(BaseModel):
    items: list[str]


class DoneData(BaseModel):
    # 助手这条消息的编号
    message_id: int
    input_tokens: int
    output_tokens: int


class ErrorData(BaseModel):
    code: str
    message: str


class StartEvent(BaseModel):
    event: Literal["start"]
    data: StartData


class ThinkingEvent(BaseModel):
    event: Literal["thinking"]
    data: ThinkingData


class ToolStartEvent(BaseModel):
    event: Literal["tool_start"]
    data: ToolStartData


class ToolEndEvent(BaseModel):
    event: Literal["tool_end"]
    data: ToolEndData


class TextEvent(BaseModel):
    event: Literal["text"]
    data: TextData


class SuggestionsEvent(BaseModel):
    event: Literal["suggestions"]
    data: SuggestionsData


class DoneEvent(BaseModel):
    event: Literal["done"]
    data: DoneData


class ErrorEvent(BaseModel):
    event: Literal["error"]
    data: ErrorData


ChatStreamEvent = Annotated[
    StartEvent
    | ThinkingEvent
    | ToolStartEvent
    | ToolEndEvent
    | TextEvent
    | SuggestionsEvent
    | DoneEvent
    | ErrorEvent,
    Field(discriminator="event"),
]


class ChatStream(BaseModel):
    """事件流的整体说明：按出现顺序，最后一个事件是 done 或 error。"""

    events: list[ChatStreamEvent]


@router.get("")
def list_chats(user: CurrentUser, chats: Chats) -> ChatListOut:
    return ChatListOut(chats=[_chat_out(c) for c in chats.chats(user)])


@router.post("", status_code=201)
def create_chat(user: CurrentUser, chats: Chats, container: Container) -> ChatOut:
    _require_available(container)
    return _chat_out(chats.create(user))


@router.get("/{chat_id}")
def get_chat(chat_id: int, user: CurrentUser, chats: Chats) -> ChatDetailOut:
    chat = chats.get(user, chat_id)
    messages = chats.messages(user, chat_id)
    wanted = [i for m in messages for i in m.item_ids]
    cards = chats.cards(user, list(dict.fromkeys(wanted)))
    return ChatDetailOut(
        **_chat_out(chat).model_dump(),
        messages=[_message_out(m, cards) for m in messages],
    )


@router.delete("/{chat_id}", status_code=204)
def delete_chat(chat_id: int, user: CurrentUser, chats: Chats) -> Response:
    chats.delete(user, chat_id)
    return Response(status_code=204)


@router.post(
    "/{chat_id}/messages",
    response_model=None,
    openapi_extra={"x-sse": True},
    responses={
        200: {
            "description": "SSE 事件流（text/event-stream），事件结构见 ChatStream.events",
            "model": ChatStream,
        }
    },
)
def send_message(
    chat_id: int,
    body: SendMessageRequest,
    user: CurrentUser,
    chats: Chats,
    container: Container,
) -> StreamingResponse:
    """提问并流式接收回答。断开连接即停止生成，已生成的部分会保存。"""
    _require_available(container)
    turn = chats.start(user, chat_id, body.content)
    return StreamingResponse(
        _stream(turn, container),
        media_type="text/event-stream",
        # 告诉反向代理不要缓冲，否则回答会攒成一整段才到
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _stream(turn: ChatTurn, container: AppContainer) -> AsyncIterator[str]:
    """把回答的事件逐个发给客户端。事件在工作线程里生成（模型调用是阻塞的）。

    客户端断开时 Starlette 取消这个协程：不等工作线程里正在进行的那一步，立刻让回答
    保存为「已停止」；还没跑完的那一步结果直接丢弃。"""
    events = turn.events(container)
    end: object = object()
    try:
        while True:
            item = await to_thread.run_sync(next, events, end, abandon_on_cancel=True)
            if isinstance(item, tuple):
                name, data = item
            else:
                return
            yield f"event: {name}\ndata: {json.dumps(_plain(data), ensure_ascii=False)}\n\n"
    finally:
        with CancelScope(shield=True):
            await to_thread.run_sync(turn.stop)


def _plain(data: dict[str, Any]) -> dict[str, Any]:
    if "items" in data and data["items"] and isinstance(data["items"][0], ItemCard):
        return {
            **data,
            "items": [ItemCardOut.model_validate(c).model_dump(mode="json") for c in data["items"]],
        }
    return data


def _require_available(container: Container) -> None:
    if not container.ai.availability().chat:
        raise AppError(409, "ai_not_configured", "管理员还没有配置 AI 助手")


def _chat_out(chat: Chat) -> ChatOut:
    return ChatOut(
        id=chat.id, title=chat.title, created_at=chat.created_at, updated_at=chat.updated_at
    )


def _step_out(step: ChatStep) -> ChatStepOut:
    return ChatStepOut(
        name=step.name,
        label=step.label,
        summary=step.summary,
        ok=step.ok,
        item_ids=list(step.item_ids),
    )


def _message_out(message: ChatMessage, cards: dict[int, ItemCard]) -> ChatMessageOut:
    return ChatMessageOut(
        id=message.id,
        role=message.role,
        content=message.content,
        steps=[_step_out(s) for s in message.steps],
        items=[ItemCardOut.model_validate(cards[i]) for i in message.item_ids if i in cards],
        suggestions=list(message.suggestions),
        thinking_seconds=message.thinking_seconds,
        status=message.status,
        error=message.error,
        created_at=message.created_at,
    )
