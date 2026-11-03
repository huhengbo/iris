"""模型调用的统一类型：消息、工具定义、事件流和错误（ADR 0030）。

两种提供方（OpenAI 兼容、Anthropic）各自把自己的流式响应翻译成这里的事件，
上层的工具循环、对话和后台任务只认这套类型。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Role = Literal["chat", "task", "embedding"]
ROLES: tuple[Role, ...] = ("chat", "task", "embedding")
ProviderKind = Literal["openai", "anthropic"]
PROVIDER_KINDS: tuple[ProviderKind, ...] = ("openai", "anthropic")

# 错误类别：auth Key 无效或无权限；rate_limited 被限流；timeout 超时；unreachable 连不上；
# bad_request 请求被拒（模型名不存在等）；server 提供方 5xx；unsupported 提供方不支持该能力；
# not_configured 这个用途还没有选模型
ErrorKind = Literal[
    "auth",
    "rate_limited",
    "timeout",
    "unreachable",
    "bad_request",
    "server",
    "unsupported",
    "not_configured",
]


class AiError(Exception):
    """模型调用失败。kind 供上层决定是重试、提示还是进入警报；message 可以直接展示给管理员。"""

    def __init__(self, kind: ErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind: ErrorKind = kind
        self.message = message


@dataclass(frozen=True)
class ToolSpec:
    """给模型看的工具定义；parameters 是 JSON Schema。"""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    # 模型给出的参数；不是合法 JSON 对象时为空字典，原文留在 raw_arguments 里供回给模型纠正
    arguments: dict[str, Any]
    raw_arguments: str = ""


@dataclass(frozen=True)
class Message:
    """role 为 tool 时 tool_call_id 指向它回应的工具调用。"""

    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)
    tool_call_id: str | None = None
    # 模型的思考原文。DeepSeek 等要求工具调用的后续轮次把它原样带回，其他提供方忽略
    reasoning: str = ""


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ThinkingDelta:
    text: str


@dataclass(frozen=True)
class ToolCallEvent:
    """一次完整的工具调用（参数已拼接完）。"""

    call: ToolCall


@dataclass(frozen=True)
class UsageEvent:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class Finish:
    # stop 正常结束；tool_calls 等待工具结果；length 被长度截断
    reason: Literal["stop", "tool_calls", "length"]


StreamEvent = TextDelta | ThinkingDelta | ToolCallEvent | UsageEvent | Finish


@dataclass(frozen=True)
class Embeddings:
    vectors: list[list[float]]
    input_tokens: int = 0


class ModelClient(Protocol):
    """一个提供方连接。实现是同步的：接口和后台任务都跑在线程里。"""

    def stream(
        self,
        *,
        model: str,
        messages: list[Message],
        tools: list[ToolSpec],
        max_tokens: int,
    ) -> Iterator[StreamEvent]: ...

    def embed(self, *, model: str, texts: list[str]) -> Embeddings: ...

    def ping(self) -> None:
        """最小的连通性检查：能连上并且 Key 有效。失败抛出 AiError。"""
        ...


@dataclass(frozen=True)
class ProviderConfig:
    """创建 ModelClient 所需的配置，Key 已解密。"""

    id: int
    kind: ProviderKind
    base_url: str
    api_key: str | None
