from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import bearer


def test_plugin_proxy_setting_is_masked_and_can_be_cleared(
    client: TestClient, admin_tokens: dict[str, str]
) -> None:
    headers = bearer(admin_tokens)
    original = "http://user:hunter2@127.0.0.1:7890"
    settings = client.app.state.container.settings  # type: ignore[attr-defined]

    response = client.put("/api/admin/settings/plugins", json={"proxy": original}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["plugins"]["proxy"] == "http://user:***@127.0.0.1:7890"
    assert "hunter2" not in response.text
    assert settings.plugin_proxy() == original

    # 客户端把打码地址原样传回时不修改地址
    response = client.put(
        "/api/admin/settings/plugins",
        json={"proxy": "http://user:***@127.0.0.1:7890"},
        headers=headers,
    )
    assert response.status_code == 200
    assert settings.plugin_proxy() == original

    # 不传字段时也保持原值
    response = client.put("/api/admin/settings/plugins", json={}, headers=headers)
    assert response.status_code == 200
    assert settings.plugin_proxy() == original

    response = client.put("/api/admin/settings/plugins", json={"proxy": ""}, headers=headers)
    assert response.status_code == 200
    assert response.json()["plugins"]["proxy"] is None
    assert settings.plugin_proxy() is None

    client.put("/api/admin/settings/plugins", json={"proxy": original}, headers=headers)
    response = client.put("/api/admin/settings/plugins", json={"proxy": None}, headers=headers)
    assert response.status_code == 200
    assert response.json()["plugins"]["proxy"] is None
    assert settings.plugin_proxy() is None


def test_plugin_proxy_setting_rejects_invalid_address(
    client: TestClient, admin_tokens: dict[str, str]
) -> None:
    response = client.put(
        "/api/admin/settings/plugins",
        json={"proxy": "127.0.0.1:7890"},
        headers=bearer(admin_tokens),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_proxy"


def test_settings_include_plugin_proxy(client: TestClient, admin_tokens: dict[str, str]) -> None:
    response = client.get("/api/admin/settings", headers=bearer(admin_tokens))

    assert response.status_code == 200
    assert response.json()["plugins"]["proxy"] is None


def test_tmdb_settings_are_not_part_of_global_settings(
    client: TestClient, admin_tokens: dict[str, str]
) -> None:
    headers = bearer(admin_tokens)

    response = client.get("/api/admin/settings", headers=headers)

    assert response.status_code == 200
    assert "tmdb" not in response.json()
    assert "tmdb" not in response.text
    removed = client.put("/api/admin/settings/tmdb", json={"key": "x"}, headers=headers)
    assert removed.status_code == 405
