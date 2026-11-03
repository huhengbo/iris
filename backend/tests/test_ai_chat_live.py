"""AI 对话的流式输出在真实的 HTTP 服务器上的表现（M57 验收）：事件逐个送达，断开后保存已有部分。"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import httpx2
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai.types import Finish, TextDelta
from app.container import AppContainer
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME
from tests.fake_ai import FakeAi
from tests.test_ai_providers import add_provider, container


@contextmanager
def live_server(app: FastAPI) -> Iterator[str]:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "服务器没有启动"
        time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(10)


@pytest.fixture
def ready(client: TestClient, admin_tokens: dict[str, str], fake_ai: FakeAi) -> AppContainer:
    provider = add_provider(client, admin_tokens)
    headers = {"Authorization": f"Bearer {admin_tokens['access_token']}"}
    client.put(
        "/api/admin/ai/roles",
        json={"chat": {"provider_id": provider["id"], "model": "c"}},
        headers=headers,
    )
    return container(client)


def login(base: str) -> dict[str, str]:
    response = httpx2.post(
        f"{base}/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def read_event(lines: Iterator[str]) -> tuple[str, dict[str, object]]:
    name = ""
    for line in lines:
        if line.startswith("event: "):
            name = line[7:]
        elif line.startswith("data: "):
            return name, json.loads(line[6:])
    raise AssertionError("事件流提前结束")


def test_events_reach_the_client_one_by_one(
    client: TestClient, ready: AppContainer, fake_ai: FakeAi
) -> None:
    fake_ai.script([TextDelta("你"), TextDelta("好"), Finish("stop")])
    fake_ai.hold_after = 1  # 第一个文字增量之后，模型停住
    with live_server(client.app) as base:  # type: ignore[arg-type]
        headers = login(base)
        chat_id = httpx2.post(f"{base}/api/ai/chats", headers=headers).json()["id"]
        started = time.monotonic()
        with httpx2.stream(
            "POST",
            f"{base}/api/ai/chats/{chat_id}/messages",
            json={"content": "你好"},
            headers=headers,
            timeout=15,
        ) as response:
            lines = response.iter_lines()
            assert read_event(lines)[0] == "start"
            assert read_event(lines) == ("text", {"delta": "你"})
            # 模型还停在那里，第一个增量已经到了客户端：没有任何中间层把输出攒起来
            assert time.monotonic() - started < 5
            fake_ai.release.set()
            assert read_event(lines) == ("text", {"delta": "好"})
            assert read_event(lines)[0] == "done"


def test_disconnecting_stops_the_answer_and_keeps_the_generated_part(
    client: TestClient, ready: AppContainer, fake_ai: FakeAi
) -> None:
    fake_ai.script([TextDelta("你"), TextDelta("好"), Finish("stop")])
    fake_ai.hold_after = 1
    with live_server(client.app) as base:  # type: ignore[arg-type]
        headers = login(base)
        chat_id = httpx2.post(f"{base}/api/ai/chats", headers=headers).json()["id"]
        with httpx2.stream(
            "POST",
            f"{base}/api/ai/chats/{chat_id}/messages",
            json={"content": "你好"},
            headers=headers,
            timeout=15,
        ) as response:
            lines = response.iter_lines()
            read_event(lines)  # start
            assert read_event(lines) == ("text", {"delta": "你"})
        # 退出 with 就是关闭连接。模型还停在那里（没有放行），已生成的部分也应当马上保存：
        # 不能等模型吐出下一个事件
        deadline = time.monotonic() + 5
        reply = None
        while time.monotonic() < deadline:
            messages = httpx2.get(f"{base}/api/ai/chats/{chat_id}", headers=headers).json()[
                "messages"
            ]
            if len(messages) == 2:
                reply = messages[1]
                break
            time.sleep(0.1)
        fake_ai.release.set()

    assert reply is not None, "断开后没有保存已生成的部分"
    assert reply["status"] == "stopped"
    assert reply["content"] == "你"
