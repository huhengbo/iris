"""两种提供方的真实 SDK 路径：用录制的响应体回放，检查事件翻译和错误映射（M51）。"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from app.ai.anthropic_client import AnthropicClient
from app.ai.openai_client import OpenAiClient
from app.ai.types import (
    AiError,
    Finish,
    Message,
    ProviderConfig,
    TextDelta,
    ThinkingDelta,
    ToolCall,
    ToolCallEvent,
    ToolSpec,
    UsageEvent,
)

Handler = Callable[[httpx2.Request], httpx2.Response]

TOOL = ToolSpec(
    "search",
    "搜索资料库",
    {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
)


def sse(*chunks: Any, event_names: list[str] | None = None) -> httpx2.Response:
    lines = []
    for index, chunk in enumerate(chunks):
        if event_names:
            lines.append(f"event: {event_names[index]}")
        lines.append(f"data: {chunk if isinstance(chunk, str) else json.dumps(chunk)}")
        lines.append("")
    return httpx2.Response(
        200, headers={"content-type": "text/event-stream"}, content="\n".join(lines) + "\n"
    )


def openai_client(handler: Handler) -> tuple[OpenAiClient, list[httpx2.Request]]:
    seen: list[httpx2.Request] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return handler(request)

    http = httpx2.Client(transport=httpx2.MockTransport(record))
    config = ProviderConfig(1, "openai", "https://api.example/v1", "sk-test")
    return OpenAiClient(config, http), seen


def anthropic_client(handler: Handler) -> tuple[AnthropicClient, list[httpx2.Request]]:
    seen: list[httpx2.Request] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return handler(request)

    http = httpx2.Client(transport=httpx2.MockTransport(record))
    config = ProviderConfig(1, "anthropic", "https://api.anthropic.example", "sk-ant-test")
    return AnthropicClient(config, http), seen


def delta(**fields: Any) -> dict[str, Any]:
    return {
        "id": "c",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "m",
        "choices": [{"index": 0, "delta": fields, "finish_reason": None}],
    }


def finish(reason: str) -> dict[str, Any]:
    return {
        "id": "c",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "m",
        "choices": [{"index": 0, "delta": {}, "finish_reason": reason}],
    }


USAGE_CHUNK = {
    "id": "c",
    "object": "chat.completion.chunk",
    "created": 1,
    "model": "m",
    "choices": [],
    "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
}


def test_openai_streams_thinking_and_text_with_usage() -> None:
    client, seen = openai_client(
        lambda _: sse(
            delta(role="assistant", reasoning_content="先想想"),
            delta(content="你"),
            delta(content="好"),
            finish("stop"),
            USAGE_CHUNK,
            "[DONE]",
        )
    )
    events = list(
        client.stream(model="m", messages=[Message("user", "hi")], tools=[], max_tokens=50)
    )
    assert events == [
        ThinkingDelta("先想想"),
        TextDelta("你"),
        TextDelta("好"),
        UsageEvent(12, 7),
        Finish("stop"),
    ]
    request = seen[0]
    assert request.headers["authorization"] == "Bearer sk-test"
    body = json.loads(request.content)
    assert body["stream"] is True and body["max_tokens"] == 50
    assert body["stream_options"] == {"include_usage": True}
    assert "tools" not in body


def test_openai_assembles_split_tool_calls_and_round_trips_messages() -> None:
    client, seen = openai_client(
        lambda _: sse(
            delta(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "search", "arguments": ""},
                    }
                ]
            ),
            delta(tool_calls=[{"index": 0, "function": {"arguments": '{"q": "沙'}}]),
            delta(tool_calls=[{"index": 0, "function": {"arguments": '丘"}'}}]),
            delta(
                tool_calls=[
                    {
                        "index": 1,
                        "id": "call_2",
                        "type": "function",
                        "function": {"name": "search", "arguments": "{bad"},
                    }
                ]
            ),
            finish("tool_calls"),
            "[DONE]",
        )
    )
    history = [
        Message("system", "你是助手"),
        Message("user", "找片"),
        Message(
            "assistant",
            tool_calls=(ToolCall("old", "search", {"q": "a"}),),
            reasoning="想过了",
        ),
        Message("tool", '{"items": []}', tool_call_id="old"),
    ]
    events = list(client.stream(model="m", messages=history, tools=[TOOL], max_tokens=50))

    assert events == [
        ToolCallEvent(ToolCall("call_1", "search", {"q": "沙丘"}, '{"q": "沙丘"}')),
        # 参数不是合法 JSON 时给空字典，原文保留供回给模型纠正
        ToolCallEvent(ToolCall("call_2", "search", {}, "{bad")),
        Finish("tool_calls"),
    ]
    body = json.loads(seen[0].content)
    assert body["tools"][0]["function"]["name"] == "search"
    assistant, tool = body["messages"][2], body["messages"][3]
    assert assistant["tool_calls"][0]["function"]["arguments"] == '{"q": "a"}'
    assert assistant["reasoning_content"] == "想过了"
    assert tool == {"role": "tool", "tool_call_id": "old", "content": '{"items": []}'}


def test_openai_embeddings_keep_input_order() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/embeddings"
        return httpx2.Response(
            200,
            json={
                "object": "list",
                "model": "bge",
                "data": [
                    {"object": "embedding", "index": 1, "embedding": [0.0, 1.0]},
                    {"object": "embedding", "index": 0, "embedding": [1.0, 0.0]},
                ],
                "usage": {"prompt_tokens": 9, "total_tokens": 9},
            },
        )

    client, _ = openai_client(handler)
    result = client.embed(model="bge", texts=["a", "b"])
    assert result.vectors == [[1.0, 0.0], [0.0, 1.0]]
    assert result.input_tokens == 9


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (401, "auth"),
        (403, "auth"),
        (429, "rate_limited"),
        (400, "bad_request"),
        (404, "bad_request"),
        (503, "server"),
    ],
)
def test_openai_maps_http_errors(status: int, kind: str) -> None:
    client, _ = openai_client(
        lambda _: httpx2.Response(status, json={"error": {"message": "模型不存在", "type": "x"}})
    )
    with pytest.raises(AiError) as caught:
        client.ping()
    assert caught.value.kind == kind
    with pytest.raises(AiError) as caught_stream:
        list(client.stream(model="m", messages=[Message("user", "x")], tools=[], max_tokens=5))
    assert caught_stream.value.kind == kind
    assert "sk-test" not in caught.value.message


def test_openai_maps_network_failures() -> None:
    def refuse(_: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused")

    def slow(_: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow")

    unreachable, _ = openai_client(refuse)
    with pytest.raises(AiError) as caught:
        unreachable.ping()
    assert caught.value.kind == "unreachable"
    timeout, _ = openai_client(slow)
    with pytest.raises(AiError) as caught:
        timeout.ping()
    assert caught.value.kind == "timeout"


def test_openai_ping_lists_models() -> None:
    client, seen = openai_client(
        lambda _: httpx2.Response(200, json={"object": "list", "data": []})
    )
    client.ping()
    assert seen[0].url.path == "/v1/models"


ANTHROPIC_NAMES = [
    "message_start",
    "content_block_start",
    "content_block_delta",
    "content_block_delta",
    "content_block_stop",
    "content_block_start",
    "content_block_delta",
    "content_block_delta",
    "content_block_stop",
    "message_delta",
    "message_stop",
]


def anthropic_events(stop_reason: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "message_start",
            "message": {
                "id": "m",
                "type": "message",
                "role": "assistant",
                "model": "c",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 30, "output_tokens": 1},
            },
        },
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "我来"},
        },
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "查"}},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "tool_use", "id": "tu_1", "name": "search", "input": {}},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"q": '},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '"沙丘"}'},
        },
        {"type": "content_block_stop", "index": 1},
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": 42},
        },
        {"type": "message_stop"},
    ]


def test_anthropic_streams_text_and_tool_use() -> None:
    client, seen = anthropic_client(
        lambda _: sse(*anthropic_events("tool_use"), event_names=ANTHROPIC_NAMES)
    )
    history = [
        Message("system", "你是助手"),
        Message("user", "找片"),
        Message(
            "assistant",
            "查",
            tool_calls=(ToolCall("a", "search", {"q": "x"}), ToolCall("b", "search", {"q": "y"})),
        ),
        Message("tool", "结果A", tool_call_id="a"),
        Message("tool", "结果B", tool_call_id="b"),
    ]
    events = list(client.stream(model="c", messages=history, tools=[TOOL], max_tokens=64))

    assert events == [
        TextDelta("我来"),
        TextDelta("查"),
        ToolCallEvent(ToolCall("tu_1", "search", {"q": "沙丘"}, '{"q": "沙丘"}')),
        UsageEvent(30, 42),
        Finish("tool_calls"),
    ]
    request = seen[0]
    assert "sk-ant-test" in request.headers["authorization"]
    body = json.loads(request.content)
    assert body["system"] == "你是助手" and body["max_tokens"] == 64
    assert body["tools"][0]["input_schema"]["required"] == ["q"]
    # 连续的工具结果合并成同一条 user 消息
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
    results = body["messages"][2]["content"]
    assert [r["tool_use_id"] for r in results] == ["a", "b"]
    assert body["messages"][1]["content"][1] == {
        "type": "tool_use",
        "id": "a",
        "name": "search",
        "input": {"q": "x"},
    }


def test_anthropic_reports_length_cutoff() -> None:
    client, _ = anthropic_client(
        lambda _: sse(*anthropic_events("max_tokens"), event_names=ANTHROPIC_NAMES)
    )
    events = list(client.stream(model="c", messages=[Message("user", "x")], tools=[], max_tokens=5))
    assert events[-1] == Finish("length")


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "auth"), (429, "rate_limited"), (400, "bad_request"), (529, "server")],
)
def test_anthropic_maps_http_errors(status: int, kind: str) -> None:
    client, _ = anthropic_client(
        lambda _: httpx2.Response(
            status, json={"type": "error", "error": {"type": "x", "message": "bad"}}
        )
    )
    with pytest.raises(AiError) as caught:
        client.ping()
    assert caught.value.kind == kind


def test_anthropic_has_no_embeddings() -> None:
    client, _ = anthropic_client(lambda _: httpx2.Response(500))
    with pytest.raises(AiError) as caught:
        client.embed(model="x", texts=["a"])
    assert caught.value.kind == "unsupported"
