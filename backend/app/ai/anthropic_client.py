"""Anthropic 提供方：官方 anthropic SDK。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any, cast

import anthropic
import httpx2
from anthropic.types import RawMessageStreamEvent

from app.ai.types import (
    AiError,
    Embeddings,
    ErrorKind,
    Finish,
    Message,
    ProviderConfig,
    StreamEvent,
    TextDelta,
    ThinkingDelta,
    ToolCall,
    ToolCallEvent,
    ToolSpec,
    UsageEvent,
)

TIMEOUT = httpx2.Timeout(60.0, connect=10.0)
MAX_ERROR_LENGTH = 200


def map_error(error: Exception) -> AiError:
    kind: ErrorKind
    if isinstance(error, anthropic.APITimeoutError):
        kind = "timeout"
    elif isinstance(error, anthropic.APIConnectionError):
        kind = "unreachable"
    elif isinstance(error, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        kind = "auth"
    elif isinstance(error, anthropic.RateLimitError):
        kind = "rate_limited"
    elif isinstance(error, anthropic.APIStatusError):
        kind = "server" if error.status_code >= 500 else "bad_request"
    else:
        kind = "server"
    message = getattr(error, "message", None) or str(error)
    return AiError(kind, str(message)[:MAX_ERROR_LENGTH])


def _to_wire(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
    """Anthropic 的 system 是独立参数；连续的工具结果要合并进同一条 user 消息。"""
    system = "\n\n".join(m.content for m in messages if m.role == "system")
    wire: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            continue
        if message.role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": message.tool_call_id,
                "content": message.content,
            }
            last = wire[-1] if wire else None
            if last and last["role"] == "user" and isinstance(last["content"], list):
                last["content"].append(block)
            else:
                wire.append({"role": "user", "content": [block]})
        elif message.role == "assistant" and message.tool_calls:
            content: list[dict[str, Any]] = []
            if message.content:
                content.append({"type": "text", "text": message.content})
            content.extend(
                {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
                for call in message.tool_calls
            )
            wire.append({"role": "assistant", "content": content})
        else:
            wire.append({"role": message.role, "content": message.content})
    return system, wire


class AnthropicClient:
    def __init__(self, config: ProviderConfig, http_client: httpx2.Client | None = None) -> None:
        self._client = anthropic.Anthropic(
            api_key=config.api_key or "none",
            base_url=config.base_url,
            timeout=TIMEOUT,
            max_retries=0,
            http_client=http_client,
        )

    def stream(
        self,
        *,
        model: str,
        messages: list[Message],
        tools: list[ToolSpec],
        max_tokens: int,
    ) -> Iterator[StreamEvent]:
        system, wire = _to_wire(messages)
        kwargs: dict[str, Any] = {}
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]
        # 按内容块序号拼接：文本块直接转发，工具块攒够参数后一次给出
        blocks: dict[int, dict[str, str]] = {}
        input_tokens = output_tokens = 0
        stop_reason: str | None = None
        try:
            params: dict[str, Any] = {
                "model": model,
                "messages": wire,
                "max_tokens": max_tokens,
                "stream": True,
                **kwargs,
            }
            stream = cast(
                anthropic.Stream[RawMessageStreamEvent],
                self._client.messages.create(**params),
            )
            for event in stream:
                if event.type == "message_start":
                    input_tokens = event.message.usage.input_tokens or 0
                    output_tokens = event.message.usage.output_tokens or 0
                elif event.type == "content_block_start":
                    block = event.content_block
                    if block.type == "tool_use":
                        blocks[event.index] = {"id": block.id, "name": block.name, "args": ""}
                elif event.type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        yield TextDelta(delta.text)
                    elif delta.type == "thinking_delta":
                        yield ThinkingDelta(delta.thinking)
                    elif delta.type == "input_json_delta" and event.index in blocks:
                        blocks[event.index]["args"] += delta.partial_json
                elif event.type == "content_block_stop" and event.index in blocks:
                    slot = blocks.pop(event.index)
                    yield ToolCallEvent(_tool_call(slot))
                elif event.type == "message_delta":
                    stop_reason = event.delta.stop_reason or stop_reason
                    output_tokens = event.usage.output_tokens or output_tokens
        except anthropic.AnthropicError as error:
            raise map_error(error) from error
        yield UsageEvent(input_tokens, output_tokens)
        if stop_reason == "tool_use":
            yield Finish("tool_calls")
        else:
            yield Finish("length" if stop_reason == "max_tokens" else "stop")

    def embed(self, *, model: str, texts: list[str]) -> Embeddings:
        raise AiError("unsupported", "Anthropic 不提供向量模型")

    def ping(self) -> None:
        try:
            self._client.models.list(limit=1)
        except anthropic.AnthropicError as error:
            raise map_error(error) from error


def _tool_call(slot: dict[str, str]) -> ToolCall:
    raw = slot["args"]
    try:
        value = json.loads(raw) if raw.strip() else {}
    except ValueError:
        value = {}
    arguments = value if isinstance(value, dict) else {}
    return ToolCall(id=slot["id"], name=slot["name"], arguments=arguments, raw_arguments=raw)
