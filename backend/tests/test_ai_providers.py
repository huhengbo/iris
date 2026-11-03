"""模型提供方、用途选择、用量和系统信息（M51，ADR 0030）。"""

from __future__ import annotations

from typing import Any, cast

from fastapi.testclient import TestClient

from app.ai.types import AiError, Finish, Message, TextDelta, UsageEvent
from app.container import AppContainer
from tests.conftest import FakeClock, bearer, create_user, login
from tests.fake_ai import FakeAi

KEY = "sk-secret-key-value"


def container(client: TestClient) -> AppContainer:
    return cast(AppContainer, client.app.state.container)  # type: ignore[attr-defined]


def add_provider(
    client: TestClient,
    admin_tokens: dict[str, str],
    name: str = "DeepSeek",
    kind: str = "openai",
    base_url: str = "https://api.deepseek.com/",
    api_key: str | None = KEY,
) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name, "kind": kind, "base_url": base_url}
    if api_key is not None:
        body["api_key"] = api_key
    response = client.post("/api/admin/ai/providers", json=body, headers=bearer(admin_tokens))
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def set_roles(client: TestClient, admin_tokens: dict[str, str], **roles: Any) -> dict[str, Any]:
    response = client.put("/api/admin/ai/roles", json=roles, headers=bearer(admin_tokens))
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def test_provider_crud_never_returns_the_key(
    client: TestClient, admin_tokens: dict[str, str]
) -> None:
    created = add_provider(client, admin_tokens)
    assert created["key_set"] is True
    assert created["base_url"] == "https://api.deepseek.com"
    assert KEY not in client.get("/api/admin/ai/providers", headers=bearer(admin_tokens)).text

    # 数据库里是密文
    with container(client).database.read_session() as connection:
        stored = connection.execute("SELECT api_key FROM ai_provider").fetchone()[0]
    assert stored and KEY not in stored

    # 不传 api_key 保持原 Key；传 null 清除
    headers = bearer(admin_tokens)
    url = f"/api/admin/ai/providers/{created['id']}"
    body = {"name": "DS", "kind": "openai", "base_url": "https://api.deepseek.com"}
    renamed = client.put(url, json=body, headers=headers).json()
    assert renamed["name"] == "DS" and renamed["key_set"] is True
    cleared = client.put(url, json={**body, "api_key": None}, headers=headers).json()
    assert cleared["key_set"] is False

    assert client.delete(url, headers=headers).status_code == 204
    assert client.get("/api/admin/ai/providers", headers=headers).json()["providers"] == []
    assert client.delete(url, headers=headers).status_code == 404


def test_provider_validation(client: TestClient, admin_tokens: dict[str, str]) -> None:
    headers = bearer(admin_tokens)
    add_provider(client, admin_tokens, name="A")
    base = {"name": "B", "kind": "openai", "base_url": "https://x.example/v1"}

    for bad in (
        {**base, "base_url": "ftp://x"},
        {**base, "base_url": "https://user:pw@x.example"},
        {**base, "name": "  "},
        {**base, "api_key": "has space"},
    ):
        response = client.post("/api/admin/ai/providers", json=bad, headers=headers)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_ai_provider"
    unknown_kind = client.post(
        "/api/admin/ai/providers", json={**base, "kind": "gemini"}, headers=headers
    )
    assert unknown_kind.status_code == 422

    duplicate = client.post("/api/admin/ai/providers", json={**base, "name": "A"}, headers=headers)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "ai_provider_name_taken"


def test_only_admins_manage_ai(client: TestClient, admin_tokens: dict[str, str]) -> None:
    create_user(client, admin_tokens)
    user = bearer(login(client, "alice", "alice-password"))
    for method, url in (
        ("get", "/api/admin/ai/providers"),
        ("get", "/api/admin/ai/roles"),
        ("get", "/api/admin/ai/usage"),
        ("get", "/api/admin/ai/presets"),
    ):
        assert client.request(method, url, headers=user).status_code == 403


def test_roles_and_provider_in_use(client: TestClient, admin_tokens: dict[str, str]) -> None:
    headers = bearer(admin_tokens)
    deepseek = add_provider(client, admin_tokens)
    claude = add_provider(
        client, admin_tokens, name="Claude", kind="anthropic", base_url="https://api.anthropic.com"
    )

    roles = set_roles(client, admin_tokens, chat={"provider_id": deepseek["id"], "model": "m1"})
    assert roles == {
        "chat": {"provider_id": deepseek["id"], "model": "m1"},
        "task": None,
        "embedding": None,
    }
    providers = client.get("/api/admin/ai/providers", headers=headers).json()["providers"]
    assert providers[0]["used_by"] == ["chat"]

    # 向量模型需要 OpenAI 兼容的提供方；提供方必须存在；模型名不能为空
    for roles_body in (
        {"embedding": {"provider_id": claude["id"], "model": "e"}},
        {"chat": {"provider_id": 999, "model": "m"}},
        {"chat": {"provider_id": deepseek["id"], "model": " "}},
    ):
        response = client.put("/api/admin/ai/roles", json=roles_body, headers=headers)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_ai_role"

    in_use = client.delete(f"/api/admin/ai/providers/{deepseek['id']}", headers=headers)
    assert in_use.status_code == 409
    assert in_use.json()["error"]["code"] == "ai_provider_in_use"

    # 不传的用途保持原值，传 null 清除
    set_roles(client, admin_tokens, task={"provider_id": claude["id"], "model": "t"})
    assert client.get("/api/admin/ai/roles", headers=headers).json()["chat"] is not None
    cleared = set_roles(client, admin_tokens, chat=None)
    assert cleared["chat"] is None and cleared["task"] is not None
    assert (
        client.delete(f"/api/admin/ai/providers/{deepseek['id']}", headers=headers).status_code
        == 204
    )


def test_system_info_hides_ai_until_the_chat_model_is_chosen(
    client: TestClient, admin_tokens: dict[str, str]
) -> None:
    def ai_flags() -> dict[str, bool]:
        return cast(dict[str, bool], client.get("/api/system/info").json()["ai"])

    assert ai_flags() == {"chat": False, "ask_search": False, "semantic": False}

    provider = add_provider(client, admin_tokens)
    set_roles(client, admin_tokens, task={"provider_id": provider["id"], "model": "t"})
    assert ai_flags()["chat"] is False

    set_roles(client, admin_tokens, chat={"provider_id": provider["id"], "model": "c"})
    assert ai_flags() == {"chat": True, "ask_search": True, "semantic": False}

    # 配了向量模型也要等语义索引可用（sqlite-vec 加载成功）才算
    set_roles(client, admin_tokens, embedding={"provider_id": provider["id"], "model": "e"})
    assert ai_flags()["semantic"] is False
    container(client).ai.vector_search_available = lambda: True
    assert ai_flags()["semantic"] is True


def test_connection_test_records_the_result(
    client: TestClient, admin_tokens: dict[str, str], fake_ai: FakeAi
) -> None:
    headers = bearer(admin_tokens)
    provider = add_provider(client, admin_tokens)
    url = f"/api/admin/ai/providers/{provider['id']}/test"

    ok = client.post(url, headers=headers).json()
    assert ok["ok"] is True and ok["error_kind"] is None
    assert fake_ai.pings == 1

    fake_ai.ping_error = AiError("auth", "Key 无效")
    failed = client.post(url, headers=headers).json()
    assert failed["ok"] is False
    assert failed["error_kind"] == "auth" and failed["message"] == "Key 无效"

    listed = client.get("/api/admin/ai/providers", headers=headers).json()["providers"][0]
    assert listed["last_test"]["ok"] is False
    assert listed["last_test"]["message"] == "Key 无效"
    assert KEY not in client.get("/api/admin/ai/providers", headers=headers).text

    # 改了地址或 Key 后，旧的测试结果作废
    body = {"name": "DeepSeek", "kind": "openai", "base_url": "https://other.example"}
    changed = client.put(f"/api/admin/ai/providers/{provider['id']}", json=body, headers=headers)
    assert changed.json()["last_test"] is None
    assert client.post("/api/admin/ai/providers/999/test", headers=headers).status_code == 404


def test_role_test_runs_a_minimal_call_with_the_chosen_model(
    client: TestClient, admin_tokens: dict[str, str], fake_ai: FakeAi
) -> None:
    headers = bearer(admin_tokens)
    provider = add_provider(client, admin_tokens)
    not_set = client.post("/api/admin/ai/roles/chat/test", headers=headers).json()
    assert not_set["ok"] is False and not_set["error_kind"] == "not_configured"

    set_roles(
        client,
        admin_tokens,
        chat={"provider_id": provider["id"], "model": "deepseek-chat"},
        embedding={"provider_id": provider["id"], "model": "bge"},
    )
    fake_ai.script([TextDelta("pong"), Finish("stop")])
    assert client.post("/api/admin/ai/roles/chat/test", headers=headers).json()["ok"] is True
    assert fake_ai.calls[0].model == "deepseek-chat"
    assert client.post("/api/admin/ai/roles/embedding/test", headers=headers).json()["ok"] is True
    assert fake_ai.embedded == [["ping"]]

    fake_ai.script(AiError("bad_request", "model not found"))
    failed = client.post("/api/admin/ai/roles/chat/test", headers=headers).json()
    assert failed["error_kind"] == "bad_request"
    assert client.post("/api/admin/ai/roles/other/test", headers=headers).status_code == 422
    # 测试不计入用量
    assert client.get("/api/admin/ai/usage", headers=headers).json()["rows"] == []


def test_calls_through_the_service_are_decrypted_and_metered(
    client: TestClient, admin_tokens: dict[str, str], fake_ai: FakeAi, clock: FakeClock
) -> None:
    headers = bearer(admin_tokens)
    provider = add_provider(client, admin_tokens)
    set_roles(
        client,
        admin_tokens,
        chat={"provider_id": provider["id"], "model": "c"},
        task={"provider_id": provider["id"], "model": "t"},
    )
    ai = container(client).ai

    fake_ai.script(
        [TextDelta("你好"), UsageEvent(100, 20), Finish("stop")],
        AiError("rate_limited", "太快了"),
        [TextDelta("x"), UsageEvent(5, 1), Finish("stop")],
    )
    events = list(ai.stream("chat", [Message("user", "hi")], [], user_id=None))
    assert [type(e).__name__ for e in events] == ["TextDelta", "UsageEvent", "Finish"]
    # 提供方拿到的是解密后的 Key
    assert fake_ai.calls[0].config.api_key == KEY
    assert fake_ai.calls[0].model == "c"

    try:
        list(ai.stream("chat", [Message("user", "hi")], [], user_id=None))
    except AiError as error:
        assert error.kind == "rate_limited"
    clock.advance(days=40)
    list(ai.stream("task", [Message("user", "hi")], [], user_id=None))

    headers = bearer(login(client, "admin", "admin-password-for-tests"))
    rows = client.get("/api/admin/ai/usage?months=6", headers=headers).json()["rows"]
    assert rows == [
        {
            "month": "2026-02",
            "role": "task",
            "calls": 1,
            "failures": 0,
            "input_tokens": 5,
            "output_tokens": 1,
        },
        {
            "month": "2026-01",
            "role": "chat",
            "calls": 2,
            "failures": 1,
            "input_tokens": 100,
            "output_tokens": 20,
        },
    ]
    # 只看最近一个月
    recent = client.get("/api/admin/ai/usage?months=1", headers=headers).json()["rows"]
    assert [r["month"] for r in recent] == ["2026-02"]
    assert client.get("/api/admin/ai/usage?months=0", headers=headers).status_code == 422


def test_unconfigured_role_raises_a_readable_error(client: TestClient) -> None:
    try:
        list(container(client).ai.stream("chat", [Message("user", "hi")], [], user_id=None))
    except AiError as error:
        assert error.kind == "not_configured"
    else:
        raise AssertionError("应当抛出 AiError")


def test_presets_list_the_known_providers(client: TestClient, admin_tokens: dict[str, str]) -> None:
    presets = client.get("/api/admin/ai/presets", headers=bearer(admin_tokens)).json()["presets"]
    by_id = {p["id"]: p for p in presets}
    assert by_id["deepseek"]["base_url"] == "https://api.deepseek.com"
    assert by_id["siliconflow"]["embedding_model"] == "BAAI/bge-m3"
    assert by_id["anthropic"]["kind"] == "anthropic"
    assert {"openai", "ollama"} <= by_id.keys()
