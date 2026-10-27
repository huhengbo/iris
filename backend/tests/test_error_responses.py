"""错误响应：统一结构，不回显输入、堆栈、内部路径和播放令牌。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import ADMIN_USERNAME


def test_errors_share_one_shape(client: TestClient) -> None:
    for response in (
        client.get("/api/admin/users"),
        client.get("/api/no-such-endpoint"),
        client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": "wrong"}),
        client.post("/api/auth/login", json={}),
    ):
        assert response.status_code >= 400
        error = response.json()["error"]
        assert set(error) <= {"code", "message", "details"}
        assert error["code"] and error["message"]


def test_validation_error_does_not_echo_input(client: TestClient) -> None:
    secret = "hunter2-secret-value"
    response = client.post(
        "/api/auth/login", json={"username": ADMIN_USERNAME, "password": {"nested": secret}}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert secret not in response.text


def test_unexpected_exception_hides_internals(client: TestClient) -> None:
    def boom() -> None:
        raise RuntimeError("leak /srv/iris/data/secret.db cookie=UID=abc123")

    app: FastAPI = client.app  # type: ignore[assignment]
    app.add_api_route("/api/_test/boom", boom)
    # 前面已有兜底路由，测试路由要排到最前
    app.router.routes.insert(0, app.router.routes.pop())
    response = TestClient(app, raise_server_exceptions=False).get("/api/_test/boom")
    assert response.status_code == 500
    for fragment in ("Traceback", "RuntimeError", "/srv/iris", "UID=abc123", "boom"):
        assert fragment not in response.text


def test_play_errors_do_not_echo_the_token(client: TestClient) -> None:
    token = "not-a-real-token.deadbeef"
    response = client.get(f"/play/{token}/movie.mkv", follow_redirects=False)
    assert response.status_code in (401, 404)
    assert token not in response.text
    assert token not in str(response.headers)
