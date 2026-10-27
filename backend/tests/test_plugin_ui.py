"""插件自定义页面：会话令牌、接口分发、静态页面和审计日志。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from typing import Any, cast

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.plugin_ui import PLUGIN_BODY_MAX_BYTES, PLUGIN_TOKEN_TTL
from app.clock import to_iso
from app.config import AppConfig
from tests.conftest import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    FakeClock,
    SyncRunner,
    bearer,
    create_user,
    login,
    make_app,
)
from tests.plugin_env import FakePluginSpec, install_fake_plugin

TEST_PLUGIN_ID = "test.ui"


def _ui_spec(plugin_id: str, name: str = "测试页面插件") -> FakePluginSpec:
    return FakePluginSpec(
        plugin_id,
        name,
        ("admin.ui",),
        pages=({"id": "settings", "title": "设置", "entry": "index.html"},),
        ui="ui",
    )


@pytest.fixture
def ui_client(
    config: AppConfig,
    clock: FakeClock,
    sync_runner: SyncRunner,
) -> Iterator[TestClient]:
    install_fake_plugin(config.plugins_dir, _ui_spec(TEST_PLUGIN_ID))
    install_fake_plugin(config.plugins_dir, _ui_spec("example.hello", "示例页面插件"))
    install_fake_plugin(
        config.plugins_dir,
        FakePluginSpec("builtin.local-subtitles", "本地字幕", ("metadata.details",)),
    )
    with TestClient(make_app(config, clock, sync_runner)) as client:
        from tests.plugin_env import bind_controls

        bind_controls(config.plugins_dir, client)
        yield client


@pytest.fixture
def hello_client(ui_client: TestClient) -> TestClient:
    return ui_client


@pytest.fixture
def ui_app(ui_client: TestClient) -> FastAPI:
    app: FastAPI = cast(FastAPI, ui_client.app)
    return app


@pytest.fixture
def hello_and_ui_client(ui_client: TestClient) -> TestClient:
    return ui_client


def _plugin_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _session(
    client: TestClient, headers: dict[str, str], plugin_id: str = TEST_PLUGIN_ID
) -> dict[str, Any]:
    response = client.post(f"/api/admin/plugins/{plugin_id}/session", headers=headers)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _plugin_token(
    client: TestClient, headers: dict[str, str], plugin_id: str = TEST_PLUGIN_ID
) -> str:
    return cast(str, _session(client, headers, plugin_id)["token"])


def test_admin_can_create_session_with_pages(hello_client: TestClient, clock: FakeClock) -> None:
    tokens = login(hello_client, ADMIN_USERNAME, ADMIN_PASSWORD)

    body = _session(hello_client, bearer(tokens), "example.hello")

    assert body["pages"] == [
        {
            "id": "settings",
            "title": "设置",
            "url": "/plugins/example.hello/ui/index.html",
        }
    ]
    assert body["expires_at"] == to_iso(clock.now() + PLUGIN_TOKEN_TTL)
    response = hello_client.get(
        "/api/plugins/example.hello/settings", headers=_plugin_headers(body["token"])
    )
    assert response.status_code == 200


def test_regular_user_cannot_create_session(
    hello_client: TestClient, admin_tokens: dict[str, str]
) -> None:
    user = create_user(hello_client, admin_tokens)
    tokens = login(hello_client, str(user["username"]), "alice-password")

    response = hello_client.post("/api/admin/plugins/example.hello/session", headers=bearer(tokens))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_session_requires_existing_plugin_with_pages(
    hello_client: TestClient, admin_tokens: dict[str, str]
) -> None:
    missing = hello_client.post(
        "/api/admin/plugins/not.installed/session", headers=bearer(admin_tokens)
    )
    no_pages = hello_client.post(
        "/api/admin/plugins/builtin.local-subtitles/session", headers=bearer(admin_tokens)
    )

    assert missing.status_code == 404
    assert no_pages.status_code == 404
    assert no_pages.json()["error"]["code"] == "not_found"


def test_session_for_disabled_plugin_returns_conflict(
    hello_client: TestClient, admin_tokens: dict[str, str]
) -> None:
    disabled = hello_client.put(
        "/api/admin/plugins/example.hello/enabled",
        json={"enabled": False},
        headers=bearer(admin_tokens),
    )
    assert disabled.status_code == 200

    response = hello_client.post(
        "/api/admin/plugins/example.hello/session", headers=bearer(admin_tokens)
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "plugin_disabled"


def test_plugin_endpoint_requires_token(ui_client: TestClient) -> None:
    response = ui_client.get("/api/plugins/test.ui/settings")

    assert response.status_code == 401


def test_plugin_token_is_bound_to_one_plugin(
    hello_and_ui_client: TestClient, admin_tokens: dict[str, str]
) -> None:
    token = _plugin_token(hello_and_ui_client, bearer(admin_tokens), "example.hello")

    response = hello_and_ui_client.get(
        "/api/plugins/test.ui/settings", headers=_plugin_headers(token)
    )

    assert response.status_code == 401


def test_login_token_cannot_be_used_as_plugin_token(
    ui_client: TestClient, admin_tokens: dict[str, str]
) -> None:
    response = ui_client.get(
        "/api/plugins/test.ui/settings", headers=_plugin_headers(admin_tokens["access_token"])
    )

    assert response.status_code == 401


def test_plugin_token_expires(
    ui_client: TestClient, admin_tokens: dict[str, str], clock: FakeClock
) -> None:
    token = _plugin_token(ui_client, bearer(admin_tokens))

    clock.advance(seconds=PLUGIN_TOKEN_TTL.total_seconds() + 1)
    response = ui_client.get("/api/plugins/test.ui/settings", headers=_plugin_headers(token))

    assert response.status_code == 401


def test_disabling_user_revokes_plugin_token(
    ui_client: TestClient, admin_tokens: dict[str, str]
) -> None:
    admin = create_user(ui_client, admin_tokens, "alice", "alice-password", role="admin")
    tokens = login(ui_client, "alice", "alice-password")
    token = _plugin_token(ui_client, bearer(tokens))

    disabled = ui_client.patch(
        f"/api/admin/users/{admin['id']}", json={"disabled": True}, headers=bearer(admin_tokens)
    )
    assert disabled.status_code == 200
    response = ui_client.get("/api/plugins/test.ui/settings", headers=_plugin_headers(token))

    assert response.status_code == 401


def test_demoting_user_revokes_plugin_token(
    ui_client: TestClient, admin_tokens: dict[str, str]
) -> None:
    admin = create_user(ui_client, admin_tokens, "alice", "alice-password", role="admin")
    tokens = login(ui_client, "alice", "alice-password")
    token = _plugin_token(ui_client, bearer(tokens))

    demoted = ui_client.patch(
        f"/api/admin/users/{admin['id']}", json={"role": "user"}, headers=bearer(admin_tokens)
    )
    assert demoted.status_code == 200
    response = ui_client.get("/api/plugins/test.ui/settings", headers=_plugin_headers(token))

    assert response.status_code == 401


def test_changing_password_revokes_plugin_token(
    ui_client: TestClient, admin_tokens: dict[str, str]
) -> None:
    create_user(ui_client, admin_tokens, "alice", "alice-password", role="admin")
    tokens = login(ui_client, "alice", "alice-password")
    token = _plugin_token(ui_client, bearer(tokens))

    changed = ui_client.post(
        "/api/auth/password",
        json={"current_password": "alice-password", "new_password": "alice-new-password"},
        headers=bearer(tokens),
    )
    assert changed.status_code == 200
    response = ui_client.get("/api/plugins/test.ui/settings", headers=_plugin_headers(token))

    assert response.status_code == 401


def test_dispatch_get_and_put_settings(ui_client: TestClient, admin_tokens: dict[str, str]) -> None:
    token = _plugin_token(ui_client, bearer(admin_tokens))
    headers = _plugin_headers(token)

    get = ui_client.get("/api/plugins/test.ui/settings", headers=headers)
    put = ui_client.put("/api/plugins/test.ui/settings", json={"greeting": "你好"}, headers=headers)

    assert get.status_code == 200
    assert get.json()["method"] == "GET"
    assert get.json()["body"] is None
    assert put.status_code == 200
    assert put.json()["method"] == "PUT"
    assert put.json()["body"] == {"greeting": "你好"}
    assert get.json()["user_id"] == put.json()["user_id"]


def test_plugin_request_error_maps_to_client_status(
    ui_client: TestClient, admin_tokens: dict[str, str]
) -> None:
    token = _plugin_token(ui_client, bearer(admin_tokens))
    headers = _plugin_headers(token)

    rejected = ui_client.get("/api/plugins/test.ui/reject", headers=headers)
    server_error = ui_client.get("/api/plugins/test.ui/reject-server", headers=headers)

    assert rejected.status_code == 422
    assert rejected.json()["error"] == {"code": "rejected", "message": "插件拒绝了请求"}
    # 非 4xx 的状态码不能借插件透传成真正的服务端错误
    assert server_error.status_code == 400
    assert server_error.json()["error"]["code"] == "unavailable"


def test_plugin_exception_returns_generic_failure(
    ui_client: TestClient, admin_tokens: dict[str, str]
) -> None:
    token = _plugin_token(ui_client, bearer(admin_tokens))

    response = ui_client.get("/api/plugins/test.ui/boom", headers=_plugin_headers(token))

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "plugin_failed"
    assert "SECRET-PLUGIN-DETAIL" not in response.text


@pytest.mark.parametrize("path", ["invalid", "invalid-status", "unserializable"])
def test_invalid_plugin_response_returns_failure(
    ui_client: TestClient, admin_tokens: dict[str, str], path: str
) -> None:
    token = _plugin_token(ui_client, bearer(admin_tokens))

    response = ui_client.get(f"/api/plugins/test.ui/{path}", headers=_plugin_headers(token))

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "plugin_failed"


def test_invalid_json_body_returns_400(ui_client: TestClient, admin_tokens: dict[str, str]) -> None:
    token = _plugin_token(ui_client, bearer(admin_tokens))

    response = ui_client.put(
        "/api/plugins/test.ui/settings",
        content=b"not json",
        headers={**_plugin_headers(token), "Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_json"


def test_no_content_response_has_empty_body(
    ui_client: TestClient, admin_tokens: dict[str, str]
) -> None:
    token = _plugin_token(ui_client, bearer(admin_tokens))

    response = ui_client.get("/api/plugins/test.ui/empty", headers=_plugin_headers(token))

    assert response.status_code == 204
    assert response.content == b""


def test_declared_body_over_limit_returns_413(
    ui_client: TestClient, admin_tokens: dict[str, str]
) -> None:
    token = _plugin_token(ui_client, bearer(admin_tokens))

    response = ui_client.post(
        "/api/plugins/test.ui/settings",
        content=b"x" * (PLUGIN_BODY_MAX_BYTES + 1),
        headers=_plugin_headers(token),
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


async def _stream_post(
    app: FastAPI, path: str, chunks: AsyncIterator[bytes], headers: dict[str, str]
) -> httpx2.Response:
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, content=chunks, headers=headers)


def test_chunked_body_over_limit_returns_413(
    ui_app: FastAPI, ui_client: TestClient, admin_tokens: dict[str, str]
) -> None:
    token = _plugin_token(ui_client, bearer(admin_tokens))
    consumed = 0

    async def chunks() -> AsyncIterator[bytes]:
        nonlocal consumed
        for _ in range(4):
            consumed += 1
            yield b"x" * (PLUGIN_BODY_MAX_BYTES // 2)

    response = asyncio.run(
        _stream_post(ui_app, "/api/plugins/test.ui/settings", chunks(), _plugin_headers(token))
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert consumed < 4


def test_unauthenticated_streaming_body_is_not_consumed(ui_app: FastAPI) -> None:
    consumed = 0

    async def chunks() -> AsyncIterator[bytes]:
        nonlocal consumed
        for _ in range(20):
            consumed += 1
            yield b"x" * (PLUGIN_BODY_MAX_BYTES // 2)

    response = asyncio.run(_stream_post(ui_app, "/api/plugins/test.ui/settings", chunks(), {}))

    assert response.status_code == 401
    assert consumed == 0


def test_page_and_sdk_files_are_served(ui_client: TestClient) -> None:
    page = ui_client.get("/plugins/test.ui/ui/index.html")
    script = ui_client.get("/plugins/test.ui/ui/assets/app.js")
    sdk = ui_client.get("/plugins/_sdk/iris-plugin.js")
    missing_sdk = ui_client.get("/plugins/_sdk/not-found.js")

    assert page.status_code == 200
    assert "测试页面" in page.text
    assert script.status_code == 200
    assert sdk.status_code == 200
    assert missing_sdk.status_code == 404


def test_plugin_page_has_csp_sandbox(ui_client: TestClient) -> None:
    response = ui_client.get("/plugins/test.ui/ui/index.html")

    policy = response.headers["content-security-policy"]
    assert "sandbox allow-scripts allow-forms" in policy
    assert "frame-ancestors 'self'" in policy


@pytest.mark.parametrize(
    "path",
    [
        "/plugins/test.ui/ui/..%2Fsecret.txt",
        "/plugins/test.ui/ui/%2Fetc%2Fpasswd",
        "/plugins/test.ui/ui/.hidden",
    ],
)
def test_plugin_page_rejects_unsafe_paths(ui_client: TestClient, path: str) -> None:
    response = ui_client.get(path)

    assert response.status_code == 404


def test_plugin_page_does_not_start_plugin(
    config: AppConfig, clock: FakeClock, sync_runner: SyncRunner
) -> None:
    install_fake_plugin(
        config.plugins_dir,
        FakePluginSpec(
            "test.broken",
            "启动失败的页面插件",
            ("admin.ui",),
            pages=({"id": "settings", "title": "设置", "entry": "index.html"},),
            ui="ui",
            mode="crash_on_start",
        ),
    )
    app = make_app(config, clock, sync_runner)
    with TestClient(app) as client:
        # 启动时已经失败一次；页面请求不能再触发按需启动
        response = client.get("/plugins/test.broken/ui/index.html")

    assert response.status_code == 404


def test_plugin_cors_preflight_and_response_headers(
    ui_client: TestClient, admin_tokens: dict[str, str]
) -> None:
    preflight = ui_client.options(
        "/api/plugins/test.ui/settings",
        headers={
            "Origin": "null",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
    )
    token = _plugin_token(ui_client, bearer(admin_tokens))
    response = ui_client.get("/api/plugins/test.ui/settings", headers=_plugin_headers(token))

    assert preflight.status_code == 204
    assert preflight.headers["access-control-allow-origin"] == "*"
    assert "GET" in preflight.headers["access-control-allow-methods"]
    assert preflight.headers["access-control-allow-headers"] == "Authorization, Content-Type"
    assert response.headers["access-control-allow-origin"] == "*"


def test_plugin_dispatch_writes_audit_log(
    ui_client: TestClient, admin_tokens: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    token = _plugin_token(ui_client, bearer(admin_tokens))
    caplog.set_level(logging.INFO, logger="app.api.plugin_ui")

    response = ui_client.get(
        "/api/plugins/test.ui/settings?token=super-secret", headers=_plugin_headers(token)
    )

    assert response.status_code == 200
    assert "plugin=test.ui" in caplog.text
    assert "status=200" in caplog.text
    assert "elapsed_ms=" in caplog.text
    assert "super-secret" not in caplog.text
