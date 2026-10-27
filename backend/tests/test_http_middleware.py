"""Server-Timing、慢请求日志和 ETag 的边界（M30）。"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from itertools import count
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import http_middleware
from tests.fake_cloud115 import FakeCloud115
from tests.test_sync import create_library, fresh_admin

SERVER_TIMING = re.compile(r"^app;dur=\d+\.\d$")


def test_api_responses_carry_server_timing_but_other_paths_do_not(client: TestClient) -> None:
    for path in ("/api/health", "/api/home", "/api/no-such-endpoint"):
        assert SERVER_TIMING.match(client.get(path).headers["Server-Timing"]), path
    assert "Server-Timing" not in client.get("/webdav").headers


def fake_clock_steps(step: float) -> SimpleNamespace:
    """每次读取前进 step 秒，一次请求读两次，所以耗时就是 step。"""
    ticks: Iterator[int] = count()
    return SimpleNamespace(perf_counter=lambda: next(ticks) * step)


def test_slow_requests_are_logged_with_path_and_status(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(http_middleware, "time", fake_clock_steps(0.3))
    with caplog.at_level(logging.WARNING, logger="app.http"):
        client.get("/api/health")
    messages = [r.getMessage() for r in caplog.records if r.name == "app.http"]
    assert any(
        m.startswith("slow request GET /api/health status=200 duration_ms=") for m in messages
    ), messages


def test_fast_requests_are_not_logged(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(http_middleware, "time", fake_clock_steps(0.01))
    with caplog.at_level(logging.WARNING, logger="app.http"):
        client.get("/api/health")
    assert not [r for r in caplog.records if r.name == "app.http"]


def test_etag_changes_with_the_body_and_is_only_for_successful_reads(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115
) -> None:
    headers = fresh_admin(client)
    before = client.get("/api/libraries", headers=headers)
    assert before.status_code == 200
    etag = before.headers["ETag"]

    # 内容变了，旧的 ETag 不再命中
    create_library(client, admin, "新库", "movie", cloud115.make_path("影视/新库"))
    after = client.get("/api/libraries", headers={**headers, "If-None-Match": etag})
    assert after.status_code == 200
    assert after.headers["ETag"] != etag

    # 错误响应不带 ETag，带着它再请求也不会变成 304
    denied = client.get("/api/home")
    assert denied.status_code == 401
    assert "ETag" not in denied.headers
    assert client.get("/api/home", headers={"If-None-Match": '"anything"'}).status_code == 401
