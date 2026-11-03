"""模型提供方的测试替身：按脚本回放事件，记录收到的调用。"""

from __future__ import annotations

import threading
from collections.abc import Iterator

from app.ai.types import (
    AiError,
    Embeddings,
    Message,
    ModelClient,
    ProviderConfig,
    StreamEvent,
    ToolSpec,
)

Turn = list[StreamEvent] | AiError


class StreamCall:
    def __init__(
        self, model: str, messages: list[Message], tools: list[ToolSpec], config: ProviderConfig
    ) -> None:
        self.model = model
        self.messages = messages
        self.tools = tools
        self.config = config


class FakeAi:
    """每次 stream 调用消耗 turns 里的下一项：事件列表照常回放，AiError 则抛出。"""

    def __init__(self) -> None:
        self.turns: list[Turn] = []
        self.calls: list[StreamCall] = []
        self.ping_error: AiError | None = None
        self.embed_error: AiError | None = None
        # 向量按文本里出现的关键词计数生成，方便在测试里编排「语义相近」
        self.keywords = ("太空", "爱情", "战争", "喜剧")
        self.embedded: list[list[str]] = []
        self.pings = 0
        # 在第 hold_after 个事件之后停住，直到 release 被设置：用来在真实服务器上观察
        # 事件是否一个一个送到客户端、以及客户端中途断开时服务端的处理
        self.hold_after: int | None = None
        self.release = threading.Event()

    def factory(self, config: ProviderConfig) -> ModelClient:
        return _Client(self, config)

    def vector(self, text: str) -> list[float]:
        return [text.count(word) + 0.01 for word in self.keywords]

    def script(self, *turns: Turn) -> None:
        self.turns.extend(turns)


class _Client:
    def __init__(self, ai: FakeAi, config: ProviderConfig) -> None:
        self._ai = ai
        self._config = config

    def stream(
        self,
        *,
        model: str,
        messages: list[Message],
        tools: list[ToolSpec],
        max_tokens: int,
    ) -> Iterator[StreamEvent]:
        self._ai.calls.append(StreamCall(model, list(messages), list(tools), self._config))
        if not self._ai.turns:
            raise AssertionError("FakeAi 没有剩余的脚本回合")
        turn = self._ai.turns.pop(0)
        if isinstance(turn, AiError):
            raise turn
        for index, event in enumerate(turn, 1):
            yield event
            if self._ai.hold_after == index:
                self._ai.release.wait(10)

    def embed(self, *, model: str, texts: list[str]) -> Embeddings:
        if self._ai.embed_error is not None:
            raise self._ai.embed_error
        self._ai.embedded.append(list(texts))
        return Embeddings([self._ai.vector(text) for text in texts], len(texts))

    def ping(self) -> None:
        self._ai.pings += 1
        if self._ai.ping_error is not None:
            raise self._ai.ping_error
