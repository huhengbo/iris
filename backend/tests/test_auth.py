from __future__ import annotations

import json
import sqlite3
from collections.abc import MutableMapping
from contextlib import closing
from typing import Any

import anyio
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.main import create_app
from tests.conftest import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    FakeClock,
    bearer,
    create_user,
    login,
)


def test_bootstrap_admin_can_log_in_and_read_profile(client: TestClient) -> None:
    tokens = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)

    me = client.get("/api/auth/me", headers=bearer(tokens))

    assert me.status_code == 200
    assert me.json()["username"] == ADMIN_USERNAME
    assert me.json()["role"] == "admin"
    assert "password" not in me.text


def test_bootstrap_admin_is_created_only_once(config: AppConfig, clock: FakeClock) -> None:
    with TestClient(create_app(config, clock)) as client:
        tokens = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
        client.post(
            "/api/auth/password",
            json={"current_password": ADMIN_PASSWORD, "new_password": "changed-password"},
            headers=bearer(tokens),
        )

    # 重启后环境变量里的初始密码不会覆盖已修改的密码
    with TestClient(create_app(config, clock)) as client:
        failed = client.post(
            "/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
        )
        assert failed.status_code == 401
        login(client, ADMIN_USERNAME, "changed-password")


def test_wrong_password_and_unknown_user_get_the_same_error(client: TestClient) -> None:
    wrong = client.post("/api/auth/login", json={"username": "admin", "password": "nope-nope"})
    unknown = client.post("/api/auth/login", json={"username": "ghost", "password": "nope-nope"})

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_requests_without_valid_token_are_rejected(client: TestClient) -> None:
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_tampered_access_token_is_rejected(
    client: TestClient, admin_tokens: dict[str, str]
) -> None:
    body, _, signature = admin_tokens["access_token"].partition(".")
    tampered = {"access_token": f"{body}x.{signature}"}

    assert client.get("/api/auth/me", headers=bearer(tampered)).status_code == 401


def test_access_token_expires_and_refresh_rotates(
    client: TestClient, admin_tokens: dict[str, str], clock: FakeClock
) -> None:
    clock.advance(minutes=16)
    assert client.get("/api/auth/me", headers=bearer(admin_tokens)).status_code == 401

    refreshed = client.post(
        "/api/auth/refresh", json={"refresh_token": admin_tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200
    new_tokens = refreshed.json()
    assert client.get("/api/auth/me", headers=bearer(new_tokens)).status_code == 200

    # 刷新后旧的 refresh token 立即作废
    reused = client.post("/api/auth/refresh", json={"refresh_token": admin_tokens["refresh_token"]})
    assert reused.status_code == 401


def test_refresh_token_expires_after_thirty_days(
    client: TestClient, admin_tokens: dict[str, str], clock: FakeClock
) -> None:
    clock.advance(days=31)

    response = client.post(
        "/api/auth/refresh", json={"refresh_token": admin_tokens["refresh_token"]}
    )

    assert response.status_code == 401


def test_logout_revokes_refresh_token(client: TestClient, admin_tokens: dict[str, str]) -> None:
    assert (
        client.post(
            "/api/auth/logout", json={"refresh_token": admin_tokens["refresh_token"]}
        ).status_code
        == 204
    )

    response = client.post(
        "/api/auth/refresh", json={"refresh_token": admin_tokens["refresh_token"]}
    )
    assert response.status_code == 401


def test_regular_user_cannot_use_admin_api(
    client: TestClient, admin_tokens: dict[str, str]
) -> None:
    create_user(client, admin_tokens)
    user_tokens = login(client, "alice", "alice-password")

    for method, path in [
        ("GET", "/api/admin/users"),
        ("POST", "/api/admin/users"),
        ("PATCH", "/api/admin/users/1"),
        ("POST", "/api/admin/users/1/password"),
        ("DELETE", "/api/admin/users/1"),
    ]:
        response = client.request(method, path, headers=bearer(user_tokens), json={})
        assert response.status_code == 403, (method, path)


def test_disabling_user_revokes_existing_tokens_immediately(
    client: TestClient, admin_tokens: dict[str, str]
) -> None:
    alice = create_user(client, admin_tokens)
    user_tokens = login(client, "alice", "alice-password")

    client.patch(
        f"/api/admin/users/{alice['id']}", json={"disabled": True}, headers=bearer(admin_tokens)
    )

    assert client.get("/api/auth/me", headers=bearer(user_tokens)).status_code == 401
    refresh = client.post("/api/auth/refresh", json={"refresh_token": user_tokens["refresh_token"]})
    assert refresh.status_code == 401
    relogin = client.post(
        "/api/auth/login", json={"username": "alice", "password": "alice-password"}
    )
    assert relogin.status_code == 401


def test_admin_password_reset_revokes_existing_tokens(
    client: TestClient, admin_tokens: dict[str, str]
) -> None:
    alice = create_user(client, admin_tokens)
    user_tokens = login(client, "alice", "alice-password")

    reset = client.post(
        f"/api/admin/users/{alice['id']}/password",
        json={"new_password": "brand-new-password"},
        headers=bearer(admin_tokens),
    )

    assert reset.status_code == 200
    assert client.get("/api/auth/me", headers=bearer(user_tokens)).status_code == 401
    refresh = client.post("/api/auth/refresh", json={"refresh_token": user_tokens["refresh_token"]})
    assert refresh.status_code == 401
    login(client, "alice", "brand-new-password")


def test_changing_own_password_keeps_this_device_and_logs_out_others(
    client: TestClient, admin_tokens: dict[str, str]
) -> None:
    create_user(client, admin_tokens)
    phone = login(client, "alice", "alice-password")
    laptop = login(client, "alice", "alice-password")

    response = client.post(
        "/api/auth/password",
        json={"current_password": "alice-password", "new_password": "alice-new-password"},
        headers=bearer(phone),
    )

    assert response.status_code == 200
    assert client.get("/api/auth/me", headers=bearer(response.json())).status_code == 200
    assert client.get("/api/auth/me", headers=bearer(laptop)).status_code == 401
    refresh = client.post("/api/auth/refresh", json={"refresh_token": laptop["refresh_token"]})
    assert refresh.status_code == 401


def test_writes_are_committed_before_the_response_is_sent(
    client: TestClient, config: AppConfig
) -> None:
    # 客户端收到响应后马上发下一个请求，此时上一个请求的写入必须已经落盘
    body = json.dumps({"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}).encode()
    sessions_at_response: list[int] = []

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: MutableMapping[str, Any]) -> None:
        if message["type"] == "http.response.start":
            with closing(sqlite3.connect(config.database_path)) as other:
                (count,) = other.execute("SELECT COUNT(*) FROM auth_session").fetchone()
            sessions_at_response.append(count)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/auth/login",
        "raw_path": b"/api/auth/login",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
    }
    anyio.run(client.app, scope, receive, send)

    assert sessions_at_response == [1]


def test_changing_password_requires_current_password(
    client: TestClient, admin_tokens: dict[str, str]
) -> None:
    response = client.post(
        "/api/auth/password",
        json={"current_password": "wrong-password", "new_password": "whatever-password"},
        headers=bearer(admin_tokens),
    )

    assert response.status_code == 400
    assert client.get("/api/auth/me", headers=bearer(admin_tokens)).status_code == 200


def test_login_is_throttled_after_repeated_failures(client: TestClient, clock: FakeClock) -> None:
    for _ in range(5):
        response = client.post("/api/auth/login", json={"username": "admin", "password": "bad"})
        assert response.status_code == 401

    blocked = client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "too_many_attempts"

    clock.advance(minutes=15)
    login(client, ADMIN_USERNAME, ADMIN_PASSWORD)


def test_padded_usernames_share_one_throttle(client: TestClient) -> None:
    # 首尾空格登录的是同一个账号，失败次数也要算在一起
    for username in ("admin", " admin", "admin ", "  admin", "ADMIN  "):
        client.post("/api/auth/login", json={"username": username, "password": "bad"})

    blocked = client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    assert blocked.status_code == 429


def test_throttle_also_limits_one_client_spraying_many_usernames(client: TestClient) -> None:
    for i in range(20):
        client.post("/api/auth/login", json={"username": f"user{i}", "password": "bad-password"})

    blocked = client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    assert blocked.status_code == 429


def test_admin_manages_users(client: TestClient, admin_tokens: dict[str, str]) -> None:
    alice = create_user(client, admin_tokens)

    duplicate = client.post(
        "/api/admin/users",
        json={"username": "ALICE", "password": "another-password"},
        headers=bearer(admin_tokens),
    )
    assert duplicate.status_code == 409

    weak = client.post(
        "/api/admin/users",
        json={"username": "bob", "password": "short"},
        headers=bearer(admin_tokens),
    )
    assert weak.status_code == 400

    promoted = client.patch(
        f"/api/admin/users/{alice['id']}", json={"role": "admin"}, headers=bearer(admin_tokens)
    )
    assert promoted.json()["role"] == "admin"

    listed = client.get("/api/admin/users", headers=bearer(admin_tokens)).json()
    assert [u["username"] for u in listed] == ["admin", "alice"]

    deleted = client.delete(f"/api/admin/users/{alice['id']}", headers=bearer(admin_tokens))
    assert deleted.status_code == 204
    assert len(client.get("/api/admin/users", headers=bearer(admin_tokens)).json()) == 1


def test_admin_cannot_lock_themselves_out(client: TestClient, admin_tokens: dict[str, str]) -> None:
    me = client.get("/api/auth/me", headers=bearer(admin_tokens)).json()

    for payload in ({"disabled": True}, {"role": "user"}):
        response = client.patch(
            f"/api/admin/users/{me['id']}", json=payload, headers=bearer(admin_tokens)
        )
        assert response.status_code == 400
    assert (
        client.delete(f"/api/admin/users/{me['id']}", headers=bearer(admin_tokens)).status_code
        == 400
    )


def test_admin_can_disable_another_admin(client: TestClient, admin_tokens: dict[str, str]) -> None:
    create_user(client, admin_tokens, "root2", "root2-password", role="admin")
    root2 = login(client, "root2", "root2-password")
    admin_id = client.get("/api/auth/me", headers=bearer(admin_tokens)).json()["id"]

    disabled = client.patch(
        f"/api/admin/users/{admin_id}", json={"disabled": True}, headers=bearer(root2)
    )

    assert disabled.status_code == 200
    assert client.get("/api/auth/me", headers=bearer(admin_tokens)).status_code == 401


def test_unknown_user_returns_404(client: TestClient, admin_tokens: dict[str, str]) -> None:
    response = client.patch(
        "/api/admin/users/999", json={"disabled": True}, headers=bearer(admin_tokens)
    )
    assert response.status_code == 404


def _session_names(client: TestClient, tokens: dict[str, str]) -> list[str | None]:
    response = client.get("/api/me/sessions", headers=bearer(tokens))
    assert response.status_code == 200
    return [session["device_name"] for session in response.json()]


def test_logging_in_again_on_the_same_device_replaces_its_session(
    client: TestClient, admin_tokens: dict[str, str]
) -> None:
    create_user(client, admin_tokens)
    device = {"device_id": "install-1", "device_name": "MacBook", "platform": "macOS"}
    first = client.post(
        "/api/auth/login", json={"username": "alice", "password": "alice-password", **device}
    ).json()
    other = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "alice-password", "device_id": "install-2"},
    ).json()

    again = client.post(
        "/api/auth/login", json={"username": "alice", "password": "alice-password", **device}
    ).json()

    assert sorted(_session_names(client, again), key=str) == ["MacBook", None]
    refresh = client.post("/api/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert refresh.status_code == 401
    assert client.get("/api/auth/me", headers=bearer(other)).status_code == 200


def test_same_device_id_is_not_shared_between_users(
    client: TestClient, admin_tokens: dict[str, str]
) -> None:
    create_user(client, admin_tokens)
    body = {"device_id": "shared-install"}
    alice = client.post(
        "/api/auth/login", json={"username": "alice", "password": "alice-password", **body}
    ).json()

    client.post(
        "/api/auth/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD, **body},
    )

    assert client.get("/api/auth/me", headers=bearer(alice)).status_code == 200


def test_refresh_records_device_info_for_sessions_from_before_the_upgrade(
    client: TestClient, admin_tokens: dict[str, str]
) -> None:
    create_user(client, admin_tokens)
    old = login(client, "alice", "alice-password")
    refreshed = client.post(
        "/api/auth/refresh",
        json={
            "refresh_token": old["refresh_token"],
            "device_id": "install-1",
            "device_name": "Pixel",
            "platform": "Android",
        },
    ).json()
    assert _session_names(client, refreshed) == ["Pixel"]

    again = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "alice-password", "device_id": "install-1"},
    ).json()

    assert len(_session_names(client, again)) == 1


def test_changing_password_drops_sessions_that_can_no_longer_be_used(
    client: TestClient, admin_tokens: dict[str, str]
) -> None:
    create_user(client, admin_tokens)
    phone = login(client, "alice", "alice-password")
    login(client, "alice", "alice-password")

    response = client.post(
        "/api/auth/password",
        json={"current_password": "alice-password", "new_password": "alice-new-password"},
        headers=bearer(phone),
    )

    assert len(_session_names(client, response.json())) == 1
