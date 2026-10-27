from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tests.conftest import bearer, create_user, login


def test_admin_lists_builtin_plugins(client: TestClient, admin_tokens: dict[str, str]) -> None:
    response = client.get("/api/admin/plugins", headers=bearer(admin_tokens))
    assert response.status_code == 200
    plugins = response.json()["plugins"]
    assert {plugin["id"] for plugin in plugins} == {
        "builtin.assrt",
        "builtin.douban",
        "builtin.fanart",
        "builtin.tmdb",
        "source.115",
        "source.local",
    }
    tmdb = next(plugin for plugin in plugins if plugin["id"] == "builtin.tmdb")
    assert tmdb["provides"] == [
        "metadata.search",
        "metadata.details",
        "metadata.images",
        "metadata.people",
        "image.fetch",
        "admin.ui",
    ]
    assert tmdb["requires"] == ["logger"]
    assert tmdb["pages"] == [{"id": "settings", "title": "设置"}]
    assert tmdb["description"] == "从 TMDB 获取影片资料、演职员和图片。"
    assert tmdb["category"] == "metadata"
    cloud = next(plugin for plugin in plugins if plugin["id"] == "source.115")
    assert cloud["version"] == "1.2.0"
    assert cloud["provides"] == ["resource.source", "admin.ui"]
    assert cloud["pages"] == [
        {"id": "account", "title": "账号"},
        {"id": "settings", "title": "设置"},
    ]
    assert {plugin["id"]: plugin["category"] for plugin in plugins} == {
        "builtin.assrt": "subtitle",
        "builtin.douban": "metadata",
        "builtin.fanart": "metadata",
        "builtin.tmdb": "metadata",
        "source.115": "resource",
        "source.local": "resource",
    }
    assert all(plugin["description"] for plugin in plugins)
    assert all(plugin["proxy_mode"] == "inherit" and plugin["proxy"] is None for plugin in plugins)
    assert all(plugin["enabled"] for plugin in plugins)
    assert all(plugin["state"] == "running" for plugin in plugins)


def test_admin_can_set_plugin_proxy_mode(client: TestClient, admin_tokens: dict[str, str]) -> None:
    headers = bearer(admin_tokens)
    original = "http://user:hunter2@127.0.0.1:7890"

    response = client.put(
        "/api/admin/plugins/source.115/proxy",
        json={"mode": "custom", "proxy": original},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["proxy_mode"] == "custom"
    assert response.json()["proxy"] == "http://user:***@127.0.0.1:7890"
    assert "hunter2" not in response.text

    # 客户端把打码地址原样传回时不改地址
    response = client.put(
        "/api/admin/plugins/source.115/proxy",
        json={"mode": "custom", "proxy": "http://user:***@127.0.0.1:7890"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["proxy"] == "http://user:***@127.0.0.1:7890"
    plugins = client.app.state.container.plugins  # type: ignore[attr-defined]
    assert plugins.proxy_config("source.115") == ("custom", original)

    response = client.put(
        "/api/admin/plugins/source.115/proxy", json={"mode": "none"}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["proxy_mode"] == "none"
    assert response.json()["proxy"] is None
    assert plugins.proxy_config("source.115") == ("none", None)

    response = client.put(
        "/api/admin/plugins/source.115/proxy",
        json={"mode": "inherit", "proxy": original},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["proxy_mode"] == "inherit"
    assert response.json()["proxy"] is None
    # 非 custom 模式不能留下地址
    assert plugins.proxy_config("source.115") == ("inherit", None)


def test_plugin_custom_proxy_requires_valid_address(
    client: TestClient, admin_tokens: dict[str, str]
) -> None:
    headers = bearer(admin_tokens)
    for proxy in (None, "", "ftp://127.0.0.1:7890"):
        response = client.put(
            "/api/admin/plugins/source.local/proxy",
            json={"mode": "custom", "proxy": proxy},
            headers=headers,
        )
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == "invalid_proxy"


def test_setting_proxy_of_unknown_plugin_is_not_found(
    client: TestClient, admin_tokens: dict[str, str]
) -> None:
    response = client.put(
        "/api/admin/plugins/example.missing/proxy",
        json={"mode": "inherit", "proxy": None},
        headers=bearer(admin_tokens),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_removed_plugin_category_comes_from_stored_provides(
    client: TestClient, admin_tokens: dict[str, str]
) -> None:
    _insert_legacy_plugin(client, "example.legacy", enabled=False, provides=("subtitle.search",))

    plugins = client.get("/api/admin/plugins", headers=bearer(admin_tokens)).json()["plugins"]
    legacy = next(plugin for plugin in plugins if plugin["id"] == "example.legacy")
    assert legacy["description"] == ""
    assert legacy["category"] == "subtitle"
    assert (legacy["proxy_mode"], legacy["proxy"]) == ("inherit", None)


def test_admin_can_disable_builtin_plugin(client: TestClient, admin_tokens: dict[str, str]) -> None:
    response = client.put(
        "/api/admin/plugins/builtin.tmdb/enabled",
        json={"enabled": False},
        headers=bearer(admin_tokens),
    )
    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert response.json()["state"] == "stopped"

    response = client.get("/api/admin/plugins", headers=bearer(admin_tokens))
    assert (
        next(item for item in response.json()["plugins"] if item["id"] == "builtin.tmdb")["enabled"]
        is False
    )


def test_removed_plugin_is_not_installed_and_cannot_be_enabled(
    client: TestClient, admin_tokens: dict[str, str]
) -> None:
    _insert_legacy_plugin(client, "example.removed", enabled=True)
    headers = bearer(admin_tokens)

    plugins = client.get("/api/admin/plugins", headers=headers).json()["plugins"]
    removed = next(plugin for plugin in plugins if plugin["id"] == "example.removed")
    assert (removed["enabled"], removed["state"]) == (True, "not_installed")

    response = client.put(
        "/api/admin/plugins/example.removed/enabled", json={"enabled": True}, headers=headers
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "plugin_not_installed"

    response = client.put(
        "/api/admin/plugins/example.removed/enabled", json={"enabled": False}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["state"] == "not_installed"

    response = client.delete("/api/admin/plugins/example.removed", headers=headers)
    assert response.status_code == 204
    plugins = client.get("/api/admin/plugins", headers=headers).json()["plugins"]
    assert all(plugin["id"] != "example.removed" for plugin in plugins)


def test_installed_plugin_cannot_be_deleted(
    client: TestClient, admin_tokens: dict[str, str]
) -> None:
    response = client.delete("/api/admin/plugins/builtin.tmdb", headers=bearer(admin_tokens))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "plugin_installed"


def test_plugin_used_by_resource_source_cannot_be_deleted(
    client: TestClient, admin_tokens: dict[str, str]
) -> None:
    _insert_legacy_plugin(client, "example.in-use", enabled=False)
    with client.app.state.container.database.session() as connection:  # type: ignore[attr-defined]
        connection.execute(
            "INSERT INTO resource_source "
            "(plugin_id, name, enabled, config, status, created_at, updated_at) "
            "VALUES ('example.in-use', '来源', 1, '{}', 'unknown', 'x', 'x')"
        )

    response = client.delete("/api/admin/plugins/example.in-use", headers=bearer(admin_tokens))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "plugin_in_use"


def test_delete_unknown_plugin_is_not_found(
    client: TestClient, admin_tokens: dict[str, str]
) -> None:
    response = client.delete("/api/admin/plugins/example.missing", headers=bearer(admin_tokens))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def _insert_legacy_plugin(
    client: TestClient,
    plugin_id: str,
    *,
    enabled: bool,
    provides: tuple[str, ...] = (),
) -> None:
    with client.app.state.container.database.session() as connection:  # type: ignore[attr-defined]
        connection.execute(
            "INSERT INTO plugin (id, name, version, sdk_version, provides, requires, trust, "
            "built_in, enabled, updated_at) "
            "VALUES (?, '旧插件', '1.0.0', '1', ?, '[]', 'external', 0, ?, 'x')",
            (plugin_id, json.dumps(provides), int(enabled)),
        )


def test_plugin_center_requires_admin(client: TestClient, admin_tokens: dict[str, str]) -> None:
    user = create_user(client, admin_tokens)
    tokens = login(client, str(user["username"]), "alice-password")
    response = client.get("/api/admin/plugins", headers=bearer(tokens))
    assert response.status_code == 403


@pytest.fixture(autouse=True)
def _all_builtin_plugins(plugin_env: object) -> None:
    from tests.plugin_env import PluginEnvironment, install_builtin, install_source_local

    assert isinstance(plugin_env, PluginEnvironment)
    for plugin_id in ("builtin.douban", "builtin.fanart", "builtin.assrt"):
        install_builtin(plugin_env.root, plugin_id)
    install_source_local(plugin_env.root)
