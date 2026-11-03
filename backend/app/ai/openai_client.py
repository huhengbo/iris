"""OpenAI 兼容提供方：官方 openai SDK，覆盖 DeepSeek、硅基流动、通义、OpenRouter、Ollama 等。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any, cast

import httpx2
import openai
from openai.types.chat import ChatCompletionChunk

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
    """SDK 异常归到统一类别。消息只取提供方的说明，不含请求头，不会带出 Key。"""
    kind: ErrorKind
    if isinstance(error, openai.APITimeoutError):
        kind = "timeout"
    elif isinstance(error, openai.APIConnectionError):
        kind = "unreachable"
    elif isinstance(error, openai.AuthenticationError | openai.PermissionDeniedError):
        kind = "auth"
    elif isinstance(error, openai.RateLimitError):
        kind = "rate_limited"
    elif isinstance(error, openai.APIStatusError):
        kind = "server" if error.status_code >= 500 else "bad_request"
    else:
        kind = "server"
    message = getattr(error, "message", None) or str(error)
    return AiError(kind, str(message)[:MAX_ERROR_LENGTH])


def _to_wire(message: Message) -> dict[str, Any]:
    if message.role == "tool":
        return {"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content}
    if message.role == "assistant" and message.tool_calls:
        wire: dict[str, Any] = {
            "role": "assistant",
            "content": message.content or None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": call.raw_arguments or json.dumps(call.arguments),
                    },
                }
                for call in message.tool_calls
            ],
        }
        if message.reasoning:
            wire["reasoning_content"] = message.reasoning
        return wire
    return {"role": message.role, "content": message.content}


def _parse_arguments(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


class OpenAiClient:
    def __init__(self, config: ProviderConfig, http_client: httpx2.Client | None = None) -> None:
        self._client = openai.OpenAI(
            # Ollama 等本地服务不需要 Key，SDK 要求非空
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
        kwargs: dict[str, Any] = {}
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in tools
            ]
        pending: dict[int, dict[str, str]] = {}
        finish: str | None = None
        usage: UsageEvent | None = None
        try:
            params: dict[str, Any] = {
                "model": model,
                "messages": [_to_wire(m) for m in messages],
                "max_tokens": max_tokens,
                "stream": True,
                "stream_options": {"include_usage": True},
                **kwargs,
            }
            stream = cast(
                openai.Stream[ChatCompletionChunk],
                self._client.chat.completions.create(**params),
            )
            for chunk in stream:
                if chunk.usage is not None:
                    usage = UsageEvent(
                        chunk.usage.prompt_tokens or 0, chunk.usage.completion_tokens or 0
                    )
                for choice in chunk.choices:
                    delta = choice.delta
                    reasoning = getattr(delta, "reasoning_content", None)
                    if reasoning:
                        yield ThinkingDelta(str(reasoning))
                    if delta.content:
                        yield TextDelta(delta.content)
                    for part in delta.tool_calls or []:
                        slot = pending.setdefault(part.index, {"id": "", "name": "", "args": ""})
                        slot["id"] = part.id or slot["id"]
                        if part.function is not None:
                            slot["name"] += part.function.name or ""
                            slot["args"] += part.function.arguments or ""
                    if choice.finish_reason:
                        finish = choice.finish_reason
        except openai.OpenAIError as error:
            raise map_error(error) from error
        for index in sorted(pending):
            slot = pending[index]
            yield ToolCallEvent(
                ToolCall(
                    id=slot["id"] or f"call_{index}",
                    name=slot["name"],
                    arguments=_parse_arguments(slot["args"]),
                    raw_arguments=slot["args"],
                )
            )
        if usage is not None:
            yield usage
        if pending or finish in ("tool_calls", "function_call"):
            yield Finish("tool_calls")
        else:
            yield Finish("length" if finish == "length" else "stop")

    def embed(self, *, model: str, texts: list[str]) -> Embeddings:
        try:
            response = self._client.embeddings.create(model=model, input=texts)
        except openai.OpenAIError as error:
            raise map_error(error) from error
        vectors = [item.embedding for item in sorted(response.data, key=lambda d: d.index)]
        return Embeddings(vectors, response.usage.prompt_tokens if response.usage else 0)

    def ping(self) -> None:
        try:
            self._client.models.list()
        except openai.OpenAIError as error:
            raise map_error(error) from error
