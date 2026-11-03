"""AI 对话：按用户保存的会话，以及流式回答的事件序列（ADR 0030）。

回答过程用事件流描述，API 层把事件编码成 SSE。事件：
start 开始；thinking 思考的增量和用时；tool_start / tool_end 一次工具调用的开始和结果
（结果带条目卡片）；text 回答的增量；suggestions 建议追问；done 结束；error 出错。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from app.ai import chat_tools
from app.ai.actions import PendingActions
from app.ai.chat_tools import ChatScope
from app.ai.loop import (
    END_MESSAGES,
    LoopEnd,
    ToolEnd,
    ToolStart,
    run_tool_loop,
)
from app.ai.service import AiService
from app.ai.tools import ToolContext, ToolRegistry
from app.ai.types import AiError, Message, TextDelta, ThinkingDelta, UsageEvent
from app.auth.users import User
from app.clock import Clock, to_iso
from app.db import Database
from app.errors import AppError, NotFound
from app.library.browse import BrowseQueries
from app.library.browse_models import ItemCard

if TYPE_CHECKING:
    from app.container import AppContainer

logger = logging.getLogger(__name__)

MAX_CONTENT_LENGTH = 2000
MAX_CHATS_PER_USER = 50
MAX_MESSAGES_PER_CHAT = 200
# 带给模型的历史：最近这么多条，且总字数不超过上限
HISTORY_MESSAGES = 12
HISTORY_CHARS = 6000
MAX_ITEMS_PER_MESSAGE = 12
MAX_STEPS = 8
MAX_TOOL_CALLS = 10
TITLE_LENGTH = 30

SYSTEM_PROMPT = """你是「光圈」里的影视助手。光圈是用户家里的私人影视库，你帮用户在里面找片、\
了解影片、回顾观影记录。

做法：
- 只推荐库里有的作品。先调用工具查询，不要凭记忆编造库里没有的片名，\
也不要提到工具没有返回过的作品。
- 找片时尽量一次调用就把条件组合好；难以用条件表达的偏好（心情、氛围、题材）写进 semantic_query。
- 回答用中文，简短自然，一般推荐 3 到 6 部，每部一句话说明理由。\
界面会自动显示作品卡片，不要重复罗列年份、评分、类型等信息。
- 用户问库外的事（新闻、影评、新片上映），你不能联网，如实说明。
- 工具返回没有结果时，直接告诉用户，并建议放宽条件。
- 用户的观影记录只用来回答他自己的问题。
- 回答完后调用 suggest_followups，给出 2 到 3 个用户可能接着问的短问题。"""


@dataclass(frozen=True)
class Chat:
    id: int
    title: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ChatStep:
    name: str
    label: str
    summary: str
    ok: bool
    item_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class ChatMessage:
    id: int
    role: Literal["user", "assistant"]
    content: str
    steps: tuple[ChatStep, ...]
    item_ids: tuple[int, ...]
    suggestions: tuple[str, ...]
    thinking_seconds: float | None
    status: Literal["done", "stopped", "error"]
    error: str | None
    created_at: str


# 事件：名称和载荷
ChatEvent = tuple[str, dict[str, Any]]


class ChatService:
    def __init__(
        self,
        database: Database,
        clock: Clock,
        ai: AiService,
        registry: ToolRegistry,
        actions: PendingActions,
    ) -> None:
        self._database = database
        self._clock = clock
        self._ai = ai
        self._registry = registry
        self._actions = actions
        chat_tools.register(registry)

    # 会话 -----------------------------------------------------------------

    def chats(self, user: User) -> list[Chat]:
        with self._database.read_session() as connection:
            rows = connection.execute(
                "SELECT * FROM ai_chat WHERE user_id = ? ORDER BY updated_at DESC, id DESC LIMIT ?",
                (user.id, MAX_CHATS_PER_USER),
            ).fetchall()
        return [_chat(r) for r in rows]

    def create(self, user: User) -> Chat:
        now = to_iso(self._clock.now())
        with self._database.session() as connection:
            cursor = connection.execute(
                "INSERT INTO ai_chat (user_id, title, created_at, updated_at) "
                "VALUES (?, '新对话', ?, ?)",
                (user.id, now, now),
            )
            chat_id = int(cursor.lastrowid or 0)
            # 只保留最近的若干个会话
            connection.execute(
                "DELETE FROM ai_chat WHERE user_id = ? AND id NOT IN ("
                "SELECT id FROM ai_chat WHERE user_id = ? ORDER BY updated_at DESC, id DESC "
                "LIMIT ?)",
                (user.id, user.id, MAX_CHATS_PER_USER),
            )
        return self.get(user, chat_id)

    def get(self, user: User, chat_id: int) -> Chat:
        with self._database.read_session() as connection:
            return _chat(_owned(connection, user, chat_id))

    def messages(self, user: User, chat_id: int) -> list[ChatMessage]:
        with self._database.read_session() as connection:
            _owned(connection, user, chat_id)
            rows = connection.execute(
                "SELECT * FROM ai_chat_message WHERE chat_id = ? ORDER BY id", (chat_id,)
            ).fetchall()
        return [_message(r) for r in rows]

    def delete(self, user: User, chat_id: int) -> None:
        with self._database.session() as connection:
            _owned(connection, user, chat_id)
            connection.execute("DELETE FROM ai_chat WHERE id = ?", (chat_id,))

    def cards(self, user: User, item_ids: list[int]) -> dict[int, ItemCard]:
        """条目卡片，按用户当前的可见范围解析；范围外的不在结果里。"""
        with self._database.read_session() as connection:
            return {c.id: c for c in BrowseQueries(connection, user).cards(item_ids)}

    # 回答 -----------------------------------------------------------------

    def start(self, user: User, chat_id: int, content: str) -> ChatTurn:
        """记下用户的提问并准备回答。会话不属于该用户、内容为空或过长时抛出业务错误。"""
        content = content.strip()
        if not content or len(content) > MAX_CONTENT_LENGTH:
            raise AppError(
                400, "invalid_chat_message", f"消息不能为空，最多 {MAX_CONTENT_LENGTH} 个字"
            )
        now = to_iso(self._clock.now())
        with self._database.session() as connection:
            chat = _owned(connection, user, chat_id)
            count = connection.execute(
                "SELECT COUNT(*) FROM ai_chat_message WHERE chat_id = ?", (chat_id,)
            ).fetchone()[0]
            if count >= MAX_MESSAGES_PER_CHAT:
                raise AppError(409, "chat_full", "这个对话太长了，请新建一个对话")
            history = _history(connection, chat_id)
            cursor = connection.execute(
                "INSERT INTO ai_chat_message (chat_id, role, content, created_at) "
                "VALUES (?, 'user', ?, ?)",
                (chat_id, content, now),
            )
            message_id = int(cursor.lastrowid or 0)
            title = content[:TITLE_LENGTH] if count == 0 else chat["title"]
            connection.execute(
                "UPDATE ai_chat SET title = ?, updated_at = ? WHERE id = ?", (title, now, chat_id)
            )
        return ChatTurn(self, user, chat_id, message_id, content, history)

    def _save_reply(self, chat_id: int, reply: _Reply, status: str, error: str | None) -> int:
        now = to_iso(self._clock.now())
        with self._database.session() as connection:
            cursor = connection.execute(
                "INSERT INTO ai_chat_message (chat_id, role, content, steps, item_ids, "
                "suggestions, thinking_seconds, status, error, created_at) "
                "VALUES (?, 'assistant', ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    chat_id,
                    reply.text,
                    json.dumps([s.__dict__ for s in reply.steps], ensure_ascii=False),
                    json.dumps(reply.item_ids()),
                    json.dumps(reply.suggestions, ensure_ascii=False),
                    reply.thinking_seconds,
                    status,
                    error,
                    now,
                ),
            )
            connection.execute("UPDATE ai_chat SET updated_at = ? WHERE id = ?", (now, chat_id))
            return int(cursor.lastrowid or 0)


@dataclass
class _Reply:
    """一次回答攒起来的内容，结束（或中断）时一次性保存。"""

    text: str = ""
    steps: list[ChatStep] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    # 没有思考过程时为 None
    thinking_seconds: float | None = None

    def item_ids(self) -> list[int]:
        ids: list[int] = []
        for step in self.steps:
            ids.extend(step.item_ids)
        return list(dict.fromkeys(ids))[:MAX_ITEMS_PER_MESSAGE]


class ChatTurn:
    """一轮问答。events() 是个生成器：调用方迭代它并把事件发给客户端；
    客户端断开时生成器被关闭，已经生成的部分会保存并标记为已停止。"""

    def __init__(
        self,
        service: ChatService,
        user: User,
        chat_id: int,
        message_id: int,
        content: str,
        history: list[Message],
    ) -> None:
        self._service = service
        self._user = user
        self.chat_id = chat_id
        self.message_id = message_id
        self._content = content
        self._history = history
        self._reply = _Reply()
        # 回答只保存一次：正常结束、出错和被停止三条路径谁先到谁保存
        self._lock = threading.Lock()
        self._saved = False

    def stop(self) -> None:
        """客户端断开：立刻保存已经生成的部分。回答已经保存过则什么也不做。

        不等模型：生成器可能正卡在一次模型调用里，要等到它吐出下一个事件才会返回。"""
        self._finish("stopped", None)

    def _finish(self, status: str, error: str | None) -> int | None:
        with self._lock:
            if self._saved:
                return None
            self._saved = True
        return self._service._save_reply(self.chat_id, self._reply, status, error)

    def events(self, container: AppContainer) -> Generator[ChatEvent]:
        service = self._service
        reply = self._reply
        scope = ChatScope()
        input_tokens = output_tokens = 0
        thinking_since: float | None = None
        labels: dict[str, str] = {}
        yield "start", {"chat_id": self.chat_id, "message_id": self.message_id}
        messages = [
            Message("system", f"{SYSTEM_PROMPT}\n\n今天是 {service._clock.now():%Y-%m-%d}。"),
            *self._history,
            Message("user", self._content),
        ]
        try:
            for event in run_tool_loop(
                ai=service._ai,
                registry=service._registry,
                actions=service._actions,
                context=ToolContext(self._user, container, scope),
                role="chat",
                messages=messages,
                tool_names=set(chat_tools.CHAT_TOOLS),
                max_steps=MAX_STEPS,
                max_tool_calls=MAX_TOOL_CALLS,
                stop_after=frozenset({chat_tools.FOLLOW_UPS}),
            ):
                if isinstance(event, ThinkingDelta):
                    if thinking_since is None:
                        thinking_since = time.monotonic()
                    yield "thinking", {"delta": event.text, "seconds": None}
                    continue
                if thinking_since is not None:
                    seconds = round(time.monotonic() - thinking_since, 1)
                    reply.thinking_seconds = (reply.thinking_seconds or 0.0) + seconds
                    thinking_since = None
                    yield "thinking", {"delta": "", "seconds": seconds}
                if isinstance(event, TextDelta):
                    reply.text += event.text
                    yield "text", {"delta": event.text}
                elif isinstance(event, UsageEvent):
                    input_tokens += event.input_tokens
                    output_tokens += event.output_tokens
                elif isinstance(event, ToolStart):
                    if event.name == chat_tools.FOLLOW_UPS:
                        continue
                    # tool_start 之前的文字是过渡语（「我来查一下」），不属于最终回答
                    reply.text = ""
                    labels[event.call_id] = event.label
                    yield (
                        "tool_start",
                        {"id": event.call_id, "name": event.name, "label": event.label},
                    )
                elif isinstance(event, ToolEnd):
                    if event.name == chat_tools.FOLLOW_UPS:
                        continue
                    reply.steps.append(
                        ChatStep(
                            event.name,
                            labels.get(event.call_id, ""),
                            event.summary,
                            event.ok,
                            event.item_ids,
                        )
                    )
                    cards = service.cards(self._user, list(event.item_ids))
                    yield (
                        "tool_end",
                        {
                            "id": event.call_id,
                            "name": event.name,
                            "summary": event.summary,
                            "ok": event.ok,
                            "items": [cards[i] for i in event.item_ids if i in cards],
                        },
                    )
                elif isinstance(event, LoopEnd) and END_MESSAGES[event.reason]:
                    note = END_MESSAGES[event.reason]
                    reply.text += ("\n\n" if reply.text else "") + note
                    yield "text", {"delta": note}
            if scope.suggestions:
                reply.suggestions = scope.suggestions
                yield "suggestions", {"items": scope.suggestions}
            message_id = self._finish("done", None)
            if message_id is None:
                return  # 客户端已经断开，停止时保存过了
            yield (
                "done",
                {
                    "message_id": message_id,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
            )
        except AiError as error:
            message = f"AI 暂不可用：{error.message}"
            self._finish("error", message)
            yield "error", {"code": "ai_unavailable", "message": message}
        except GeneratorExit:
            # 生成器被关闭（客户端断开）：保存已生成的部分
            self.stop()
            raise
        except Exception:
            logger.exception("AI 对话出错")
            message = "出了点问题，请稍后重试"
            self._finish("error", message)
            yield "error", {"code": "internal", "message": message}


def _owned(connection: sqlite3.Connection, user: User, chat_id: int) -> sqlite3.Row:
    row: sqlite3.Row | None = connection.execute(
        "SELECT * FROM ai_chat WHERE id = ? AND user_id = ?", (chat_id, user.id)
    ).fetchone()
    if row is None:
        raise NotFound("对话不存在")
    return row


def _chat(row: sqlite3.Row) -> Chat:
    return Chat(row["id"], row["title"], row["created_at"], row["updated_at"])


def _message(row: sqlite3.Row) -> ChatMessage:
    return ChatMessage(
        id=row["id"],
        role=row["role"],
        content=row["content"],
        steps=tuple(
            ChatStep(
                s["name"], s.get("label", ""), s["summary"], s["ok"], tuple(s.get("item_ids", ()))
            )
            for s in json.loads(row["steps"])
        ),
        item_ids=tuple(json.loads(row["item_ids"])),
        suggestions=tuple(json.loads(row["suggestions"])),
        thinking_seconds=row["thinking_seconds"],
        status=row["status"],
        error=row["error"],
        created_at=row["created_at"],
    )


def _history(connection: sqlite3.Connection, chat_id: int) -> list[Message]:
    """带给模型的历史：只有提问和最终回答，不含工具调用的细节；被中断的回答也保留已有的部分。"""
    rows = connection.execute(
        "SELECT role, content FROM ai_chat_message WHERE chat_id = ? AND content != '' "
        "ORDER BY id DESC LIMIT ?",
        (chat_id, HISTORY_MESSAGES),
    ).fetchall()
    kept: list[Message] = []
    total = 0
    for row in rows:
        total += len(row["content"])
        if kept and total > HISTORY_CHARS:
            break
        kept.append(Message(row["role"], row["content"]))
    kept.reverse()
    return kept
