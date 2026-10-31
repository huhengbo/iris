"""宿主切换为进程外插件后的端到端行为。"""

from __future__ import annotations

import os
import shutil
import signal
from typing import Any

from fastapi.testclient import TestClient

from app.config import AppConfig
from tests.conftest import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    FakeClock,
    SyncRunner,
    bearer,
    ensure_cloud115_source,
    login,
    make_app,
)
from tests.fake_cloud115 import FakeCloud115
from tests.fake_tmdb import FakeTmdb
from tests.plugin_env import bind_controls, control_for, install_source_local
from tests.plugin_runtime_support import wait_until
from tests.test_browse import build_catalog
from tests.test_sync import sync


def test_killed_plugin_restarts_without_affecting_others(
    client: TestClient,
    admin_tokens: dict[str, str],
    config: AppConfig,
) -> None:
    supervisor = client.app.state.container.supervisor  # type: ignore[attr-defined]
    source_pid = supervisor.pid("source.115")
    metadata_pid = supervisor.pid("builtin.tmdb")
    assert source_pid is not None and metadata_pid is not None

    os.kill(source_pid, signal.SIGKILL)
    # 其他插件和宿主请求完全不受影响
    assert supervisor.pid("builtin.tmdb") == metadata_pid
    control_for(config.plugins_dir, "builtin.tmdb").call(
        "metadata", "add_movie", {"tmdb_id": 1, "title": "仍然可用", "year": 2024}
    )

    wait_until(lambda: supervisor.pid("source.115") not in (None, source_pid), timeout=15.0)
    headers = bearer(admin_tokens)
    source_id = ensure_cloud115_source(client, headers)
    assert (
        client.get(f"/api/admin/resources/{source_id}/browse", headers=headers).status_code == 200
    )
    plugins = client.get("/api/admin/plugins", headers=headers).json()["plugins"]
    assert next(item for item in plugins if item["id"] == "source.115")["state"] == "running"


def test_removed_115_plugin_does_not_block_startup_or_local_source(
    config: AppConfig,
    clock: FakeClock,
    sync_runner: SyncRunner,
    plugin_env: Any,
) -> None:
    install_source_local(plugin_env.root)
    with TestClient(make_app(config, clock, sync_runner)) as first:
        bind_controls(plugin_env.root, first)
        headers = bearer(login(first, ADMIN_USERNAME, ADMIN_PASSWORD))
        ensure_cloud115_source(first, headers)
        assert (
            first.put(
                "/api/admin/plugins/source.115/enabled",
                json={"enabled": False},
                headers=headers,
            ).status_code
            == 200
        )

    # 管理员把 115 插件从插件目录整体移除后重启
    shutil.rmtree(config.plugins_dir / "source.115")
    with TestClient(make_app(config, clock, sync_runner)) as restarted:
        bind_controls(plugin_env.root, restarted)
        headers = bearer(login(restarted, ADMIN_USERNAME, ADMIN_PASSWORD))
        plugins = restarted.get("/api/admin/plugins", headers=headers).json()["plugins"]
        removed = next(item for item in plugins if item["id"] == "source.115")
        assert (removed["enabled"], removed["state"]) == (False, "not_installed")

        sources = restarted.get("/api/admin/resources", headers=headers).json()
        cloud_source = next(item for item in sources if item["plugin_id"] == "source.115")
        assert (
            restarted.delete(
                f"/api/admin/resources/{cloud_source['id']}", headers=headers
            ).status_code
            == 204
        )
        assert restarted.delete("/api/admin/plugins/source.115", headers=headers).status_code == 204

        # 本地来源：建来源、建库、同步、播放、健康检查全部走通
        movies = config.media_dir / "movies" / "Film (2024)"
        movies.mkdir(parents=True)
        (movies / "Film.2024.1080p.mkv").write_bytes(b"local-video")
        restarted.put("/api/admin/settings/library", json={"extras_min_mb": 0}, headers=headers)
        restarted.put(
            "/api/admin/settings/server",
            json={"name": "光圈", "public_url": "https://movie.example.com"},
            headers=headers,
        )
        source = restarted.post(
            "/api/admin/resources",
            json={"plugin_id": "source.local", "name": "本地", "config": {"root": "movies"}},
            headers=headers,
        )
        assert source.status_code == 201, source.text
        source_id = source.json()["id"]
        library = restarted.post(
            "/api/admin/libraries",
            json={
                "name": "电影",
                "kind": "movie",
                "roots": [{"source_id": source_id, "locator": None}],
            },
            headers=headers,
        )
        assert library.status_code == 201, library.text

        control_for(config.plugins_dir, "builtin.tmdb").call(
            "metadata", "add_movie", {"tmdb_id": 42, "title": "Film", "year": 2024}
        )
        from tests.conftest import save_tmdb_key

        save_tmdb_key(restarted, headers)
        body = sync(restarted, headers)
        assert body["status"] == "succeeded", body
        assert body["stats"]["added"] == 1
        item = restarted.get("/api/admin/items", headers=headers).json()["items"][0]
        ticket = restarted.post("/api/play", json={"item_id": item["id"]}, headers=headers)
        assert ticket.status_code == 200, ticket.text
        played = restarted.get(ticket.json()["path"], headers={"User-Agent": "Infuse/7.8"})
        assert played.status_code == 200
        assert played.content == b"local-video"

        health = restarted.get("/api/admin/health", headers=headers).json()["checks"]
        local = next(check for check in health if check["key"] == f"source:{source_id}")
        assert local["state"] == "ok"
        setup = restarted.get("/api/admin/setup", headers=headers).json()
        assert (
            next(step for step in setup["steps"] if step["key"] == "source")["state"] == "complete"
        )
        assert restarted.get("/api/admin/plugins", headers=headers).status_code == 200


def test_resource_changed_triggers_targeted_sync(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115, tmdb: FakeTmdb
) -> None:
    build_catalog(client, admin, cloud115, tmdb)
    tmdb.add_movie(9100, "事件新片", 2026)
    root = cloud115.make_path("影视/电影")
    cloud115.make_path("影视/电影/事件新片 (2026)/事件新片.2026.1080p.mkv")
    source_id = ensure_cloud115_source(client, admin)
    cloud115.calls.clear()

    cloud115.control.call("source", "changed", {"source_id": source_id, "locators": [root]})

    def latest_event() -> dict[str, Any] | None:
        jobs = client.get("/api/admin/sync/jobs", headers=admin).json()
        return next((job for job in jobs if job["trigger"] == "event"), None)

    wait_until(lambda: (latest_event() or {}).get("status") == "succeeded", timeout=10.0)
    assert any(
        item["title"] == "事件新片"
        for item in client.get("/api/admin/items", headers=admin).json()["items"]
    )


def test_public_url_change_updates_plugin_context(
    client: TestClient, admin_tokens: dict[str, str], config: AppConfig
) -> None:
    headers = bearer(admin_tokens)
    current = client.get("/api/admin/settings", headers=headers).json()["server"]["name"]

    response = client.put(
        "/api/admin/settings/server",
        json={"name": current, "public_url": "https://iris.example.com"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    supervisor = client.app.state.container.supervisor  # type: ignore[attr-defined]
    wait_until(
        lambda: (
            supervisor.client("builtin.tmdb").call("test.context", {}, timeout=5.0)["host_url"]
            == "https://iris.example.com"
        )
    )


def test_global_and_plugin_proxy_changes_notify_running_plugins(
    client: TestClient, admin_tokens: dict[str, str], config: AppConfig
) -> None:
    headers = bearer(admin_tokens)
    control = control_for(config.plugins_dir, "builtin.tmdb")

    response = client.put(
        "/api/admin/settings/plugins",
        json={"proxy": "http://127.0.0.1:7890"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    wait_until(
        lambda: control.call("metadata", "calls") is not None and _config_count(control) >= 1
    )
    assert _config_proxy(control) == "http://127.0.0.1:7890"

    response = client.put(
        "/api/admin/plugins/builtin.tmdb/proxy",
        json={"mode": "custom", "proxy": "http://127.0.0.1:7891"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    wait_until(lambda: _config_count(control) >= 2)
    assert _config_proxy(control) == "http://127.0.0.1:7891"


def _config_state(control: Any) -> dict[str, Any]:
    value: dict[str, Any] = control.call("metadata", "config_changes")
    return value


def _config_count(control: Any) -> int:
    return int(_config_state(control)["count"])


def _config_proxy(control: Any) -> str | None:
    value = _config_state(control)["proxy"]
    return None if value is None else str(value)


def test_container_wires_backup_and_media_dir(client: TestClient, config: AppConfig) -> None:
    container = client.app.state.container  # type: ignore[attr-defined]
    assert container.backups._plugin_maintenance is container.plugin_packages
    context = container.supervisor.client("builtin.tmdb").call("test.context", {}, timeout=5.0)
    assert context["media_dir"] == str(config.media_dir)
