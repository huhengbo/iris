from __future__ import annotations

import dataclasses
import json
import shutil
import sqlite3
import subprocess
import tarfile
import threading
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.container import AppContainer
from app.db import apply_migrations, load_migrations
from app.errors import AppError
from app.main import create_app
from app.ops.maintenance import run_due
from app.plugins.installer import UvInstaller
from tests.conftest import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    FakeClock,
    SyncRunner,
    bearer,
    login,
    make_app,
)
from tests.plugin_env import PluginEnvironment, bind_controls
from tests.repository_server import (
    FakeInstaller,
    RepositoryHTTPServer,
    index_entry,
    make_plugin_zip,
    publish_index,
)

PLUGIN_ID = "org.example.fake"


@pytest.fixture
def installer() -> FakeInstaller:
    return FakeInstaller()


@pytest.fixture
def install_client(
    config: AppConfig,
    clock: FakeClock,
    sync_runner: SyncRunner,
    plugin_env: PluginEnvironment,
    installer: FakeInstaller,
) -> Iterator[TestClient]:
    with TestClient(
        make_app(config, clock, sync_runner, plugin_installer=installer)
    ) as test_client:
        bind_controls(plugin_env.root, test_client)
        yield test_client


def container_of(client: TestClient) -> AppContainer:
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    return container


def admin_headers(client: TestClient) -> dict[str, str]:
    return bearer(login(client, ADMIN_USERNAME, ADMIN_PASSWORD))


def wait_task(
    client: TestClient, headers: dict[str, str], task_id: int, *, timeout: float = 30.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/admin/tasks/{task_id}", headers=headers)
        assert response.status_code == 200, response.text
        task = response.json()
        if task["status"] not in ("running", "cancel_requested"):
            return dict(task)
        time.sleep(0.02)
    raise AssertionError(f"任务 {task_id} 超时未完成")


def wait_for(predicate: Any, *, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("等待条件超时")


def plugin_item(client: TestClient, headers: dict[str, str], plugin_id: str = PLUGIN_ID) -> Any:
    plugins = client.get("/api/admin/plugins", headers=headers).json()["plugins"]
    return next((item for item in plugins if item["id"] == plugin_id), None)


def publish(
    server: RepositoryHTTPServer,
    tmp_path: Path,
    *,
    version: str = "1.0.0",
    entry: str = "fake_plugin",
    plugin_id: str = PLUGIN_ID,
    sdk: str = ">=1.0,<2.0",
    provides: tuple[str, ...] | None = None,
    capabilities: tuple[str, ...] | None = None,
    path: str = "/index.json",
) -> dict[str, Any]:
    archive = make_plugin_zip(
        tmp_path / f"{plugin_id}-{version}-{entry}.zip",
        version=version,
        plugin_id=plugin_id,
        entry=entry,
        provides=provides,
        capabilities=capabilities,
    )
    asset = f"/{plugin_id}-{version}-{entry}.zip"
    server.add_file(asset, archive.read_bytes())
    indexed = index_entry(
        plugin_id=plugin_id,
        version=version,
        url=asset,
        archive=archive,
        sdk=sdk,
        provides=provides,
        capabilities=capabilities,
    )
    publish_index(server, [indexed], name="测试仓库", path=path)
    return indexed


def add_repository(
    client: TestClient, headers: dict[str, str], server: RepositoryHTTPServer
) -> dict[str, Any]:
    response = client.post(
        "/api/admin/plugin-repositories",
        json={"url": server.index_url(), "accept_risk": True},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def start_install(
    client: TestClient,
    headers: dict[str, str],
    repository_id: int,
    version: str = "1.0.0",
) -> dict[str, Any]:
    response = client.post(
        "/api/admin/plugins/install",
        json={
            "repository_id": repository_id,
            "plugin_id": PLUGIN_ID,
            "version": version,
            "accept_permissions": True,
        },
        headers=headers,
    )
    assert response.status_code == 202, response.text
    return dict(response.json())


def test_install_upgrade_uninstall_end_to_end(
    install_client: TestClient,
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
    config: AppConfig,
) -> None:
    headers = admin_headers(install_client)
    publish(repository_server, tmp_path, version="1.0.0")
    repository = add_repository(install_client, headers, repository_server)

    catalog = install_client.get("/api/admin/plugin-catalog", headers=headers).json()
    assert catalog["plugins"][0]["status"] == "available"
    assert catalog["plugins"][0]["version"] == "1.0.0"

    task = wait_task(
        install_client, headers, start_install(install_client, headers, repository["id"])["task_id"]
    )
    assert task["status"] == "succeeded", task
    assert task["phase"] == "done"
    assert task["result"]["plugin_id"] == PLUGIN_ID
    assert task["result"]["version"] == "1.0.0"
    assert task["result"]["error_code"] is None

    item = plugin_item(install_client, headers)
    assert item is not None
    assert item["source"] == "repository"
    assert item["repository_id"] == repository["id"]
    assert item["repository_name"] == "测试仓库"
    assert item["version"] == "1.0.0"
    assert item["enabled"] is True
    assert item["state"] == "running"
    assert item["package_status"] == "ok"
    assert item["package_error"] is None

    # 插件页面可以访问，状态目录落在 data/plugins/<id>/state
    session = install_client.post(f"/api/admin/plugins/{PLUGIN_ID}/session", headers=headers)
    assert session.status_code == 200, session.text
    page = install_client.get(f"/plugins/{PLUGIN_ID}/ui/index.html")
    assert page.status_code == 200
    assert "sandbox" in page.headers["content-security-policy"]
    assert (config.data_dir / "plugins" / PLUGIN_ID / "state").is_dir()

    same = install_client.post(
        f"/api/admin/plugins/{PLUGIN_ID}/upgrade",
        json={"version": "1.0.0", "accept_permissions": True},
        headers=headers,
    )
    assert same.status_code == 409
    assert same.json()["error"]["code"] == "plugin_no_update"

    # 发布 1.1.0 后目录显示有更新，插件列表也带 available_version
    publish(repository_server, tmp_path, version="1.1.0")
    assert (
        install_client.post("/api/admin/plugin-repositories/refresh", headers=headers).status_code
        == 200
    )
    catalog = install_client.get("/api/admin/plugin-catalog", headers=headers).json()
    assert catalog["plugins"][0]["status"] == "update_available"
    assert plugin_item(install_client, headers)["available_version"] == "1.1.0"

    upgrade = install_client.post(
        f"/api/admin/plugins/{PLUGIN_ID}/upgrade",
        json={"version": "1.1.0", "accept_permissions": True},
        headers=headers,
    )
    assert upgrade.status_code == 202, upgrade.text
    task = wait_task(install_client, headers, upgrade.json()["task_id"])
    assert task["status"] == "succeeded", task
    assert task["result"]["from_version"] == "1.0.0"
    item = plugin_item(install_client, headers)
    assert item["version"] == "1.1.0"
    assert item["state"] == "running"
    with container_of(install_client).database.read_session() as connection:
        row = connection.execute(
            "SELECT version, previous_version FROM plugin_install WHERE plugin_id = ?",
            (PLUGIN_ID,),
        ).fetchone()
    assert (row["version"], row["previous_version"]) == ("1.1.0", "1.0.0")
    packages = config.plugin_packages_dir / PLUGIN_ID
    assert (packages / "1.0.0").is_dir()
    assert (packages / "1.1.0").is_dir()

    uninstall = install_client.post(
        f"/api/admin/plugins/{PLUGIN_ID}/uninstall",
        json={"delete_data": False},
        headers=headers,
    )
    assert uninstall.status_code == 204, uninstall.text
    assert plugin_item(install_client, headers) is None
    assert not packages.exists()
    # 默认保留状态目录
    assert (config.data_dir / "plugins" / PLUGIN_ID / "state").is_dir()


def test_checksum_mismatch_fails_without_leftovers(
    install_client: TestClient,
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
    config: AppConfig,
) -> None:
    headers = admin_headers(install_client)
    archive = make_plugin_zip(tmp_path / "package.zip")
    repository_server.add_file("/package.zip", archive.read_bytes())
    indexed = index_entry(url="/package.zip", archive=archive, sha256="0" * 64)
    publish_index(repository_server, [indexed])
    repository = add_repository(install_client, headers, repository_server)

    task = wait_task(
        install_client, headers, start_install(install_client, headers, repository["id"])["task_id"]
    )
    assert task["status"] == "failed"
    assert task["result"]["error_code"] == "package_checksum_mismatch"
    assert not (config.plugin_packages_dir / PLUGIN_ID).exists()
    assert plugin_item(install_client, headers) is None


def test_upgrade_start_failure_rolls_back(
    install_client: TestClient,
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
    config: AppConfig,
) -> None:
    headers = admin_headers(install_client)
    publish(repository_server, tmp_path, version="1.0.0")
    repository = add_repository(install_client, headers, repository_server)
    first = wait_task(
        install_client, headers, start_install(install_client, headers, repository["id"])["task_id"]
    )
    assert first["status"] == "succeeded"

    publish(repository_server, tmp_path, version="1.1.0", entry="missing_entry_module")
    install_client.post("/api/admin/plugin-repositories/refresh", headers=headers)
    upgrade = install_client.post(
        f"/api/admin/plugins/{PLUGIN_ID}/upgrade",
        json={"version": "1.1.0", "accept_permissions": True},
        headers=headers,
    )
    assert upgrade.status_code == 202, upgrade.text
    task = wait_task(install_client, headers, upgrade.json()["task_id"])
    assert task["status"] == "failed"
    assert task["result"]["error_code"] == "plugin_start_failed"
    assert task["result"]["rolled_back"] is True
    item = plugin_item(install_client, headers)
    assert item["version"] == "1.0.0"
    assert item["state"] == "running"
    assert (config.plugin_packages_dir / PLUGIN_ID / "1.0.0").is_dir()
    assert not (config.plugin_packages_dir / PLUGIN_ID / "1.1.0").exists()


def test_install_synchronous_errors(
    install_client: TestClient,
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
) -> None:
    headers = admin_headers(install_client)
    publish(repository_server, tmp_path)
    repository = add_repository(install_client, headers, repository_server)

    refused = install_client.post(
        "/api/admin/plugins/install",
        json={
            "repository_id": repository["id"],
            "plugin_id": PLUGIN_ID,
            "version": "1.0.0",
            "accept_permissions": False,
        },
        headers=headers,
    )
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "permissions_not_accepted"

    missing = install_client.post(
        "/api/admin/plugins/install",
        json={
            "repository_id": repository["id"],
            "plugin_id": "org.example.missing",
            "version": "1.0.0",
            "accept_permissions": True,
        },
        headers=headers,
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "plugin_not_in_repository"

    changed = install_client.post(
        "/api/admin/plugins/install",
        json={
            "repository_id": repository["id"],
            "plugin_id": PLUGIN_ID,
            "version": "9.9.9",
            "accept_permissions": True,
        },
        headers=headers,
    )
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "catalog_changed"

    # 操作锁被占用时拒绝新操作
    packages = container_of(install_client).plugin_packages
    assert packages._lock.acquire(blocking=False)
    try:
        running = install_client.post(
            "/api/admin/plugins/install",
            json={
                "repository_id": repository["id"],
                "plugin_id": PLUGIN_ID,
                "version": "1.0.0",
                "accept_permissions": True,
            },
            headers=headers,
        )
    finally:
        packages._lock.release()
    assert running.status_code == 409
    assert running.json()["error"]["code"] == "plugin_operation_running"


def test_install_conflicts_and_incompatible(
    install_client: TestClient,
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
) -> None:
    headers = admin_headers(install_client)
    system_archive = make_plugin_zip(
        tmp_path / "system.zip", plugin_id="source.115", version="1.0.0"
    )
    incompatible_archive = make_plugin_zip(
        tmp_path / "incompat.zip", plugin_id="org.example.incompatible", version="1.0.0"
    )
    repository_server.add_file("/system.zip", system_archive.read_bytes())
    repository_server.add_file("/incompat.zip", incompatible_archive.read_bytes())
    entries = [
        index_entry(plugin_id="source.115", url="/system.zip", archive=system_archive),
        index_entry(
            plugin_id="org.example.incompatible",
            url="/incompat.zip",
            archive=incompatible_archive,
            sdk=">=2.0,<3.0",
        ),
    ]
    publish_index(repository_server, entries)
    repository = add_repository(install_client, headers, repository_server)

    for plugin_id, code, status in (
        ("source.115", "plugin_conflict", 409),
        ("org.example.incompatible", "plugin_incompatible", 409),
    ):
        response = install_client.post(
            "/api/admin/plugins/install",
            json={
                "repository_id": repository["id"],
                "plugin_id": plugin_id,
                "version": "1.0.0",
                "accept_permissions": True,
            },
            headers=headers,
        )
        assert response.status_code == status, response.text
        assert response.json()["error"]["code"] == code


def test_upload_install_upgrade_delete_and_conflicts(
    install_client: TestClient,
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
    config: AppConfig,
    clock: FakeClock,
) -> None:
    headers = admin_headers(install_client)
    publish(repository_server, tmp_path, version="1.0.0")
    repository = add_repository(install_client, headers, repository_server)
    wait_task(
        install_client, headers, start_install(install_client, headers, repository["id"])["task_id"]
    )

    # 已由仓库安装的 id 不允许本地上传
    archive = make_plugin_zip(tmp_path / "upload-11.zip", version="1.1.0")
    response = install_client.post(
        "/api/admin/plugins/uploads",
        content=archive.read_bytes(),
        headers={**headers, "Content-Type": "application/zip"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "plugin_conflict"

    # 卸载仓库插件后可以使用本地安装
    assert (
        install_client.post(
            f"/api/admin/plugins/{PLUGIN_ID}/uninstall", json={}, headers=headers
        ).status_code
        == 204
    )
    response = install_client.post(
        "/api/admin/plugins/uploads",
        content=archive.read_bytes(),
        headers={**headers, "Content-Type": "application/zip"},
    )
    assert response.status_code == 201, response.text
    upload = response.json()
    assert upload["action"] == "install"
    assert upload["manifest"]["id"] == PLUGIN_ID
    assert upload["manifest"]["version"] == "1.1.0"

    refused = install_client.post(
        f"/api/admin/plugins/uploads/{upload['upload_id']}/install",
        json={"accept_risk": False},
        headers=headers,
    )
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "risk_not_accepted"

    installed = install_client.post(
        f"/api/admin/plugins/uploads/{upload['upload_id']}/install",
        json={"accept_risk": True},
        headers=headers,
    )
    assert installed.status_code == 202, installed.text
    task = wait_task(install_client, headers, installed.json()["task_id"])
    assert task["status"] == "succeeded", task
    item = plugin_item(install_client, headers)
    assert item["source"] == "local"
    assert item["version"] == "1.1.0"
    assert item["state"] == "running"

    # 同版本再次上传被拒绝
    response = install_client.post(
        "/api/admin/plugins/uploads",
        content=archive.read_bytes(),
        headers={**headers, "Content-Type": "application/zip"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "plugin_version_not_newer"

    # 本地插件不能走仓库升级
    response = install_client.post(
        f"/api/admin/plugins/{PLUGIN_ID}/upgrade",
        json={"version": "9.9.9", "accept_permissions": True},
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "plugin_local_source"

    # 上传预览过期后安装返回 404
    archive_12 = make_plugin_zip(tmp_path / "upload-12.zip", version="1.2.0")
    response = install_client.post(
        "/api/admin/plugins/uploads",
        content=archive_12.read_bytes(),
        headers={**headers, "Content-Type": "application/zip"},
    )
    upload_12 = response.json()
    clock.advance(hours=2)
    headers = admin_headers(install_client)
    expired = install_client.post(
        f"/api/admin/plugins/uploads/{upload_12['upload_id']}/install",
        json={"accept_risk": True},
        headers=admin_headers(install_client),
    )
    assert expired.status_code == 404
    assert expired.json()["error"]["code"] == "upload_not_found"

    # 删除上传记录
    archive_13 = make_plugin_zip(tmp_path / "upload-13.zip", version="1.3.0")
    response = install_client.post(
        "/api/admin/plugins/uploads",
        content=archive_13.read_bytes(),
        headers={**headers, "Content-Type": "application/zip"},
    )
    upload_13 = response.json()
    assert (
        install_client.delete(
            f"/api/admin/plugins/uploads/{upload_13['upload_id']}", headers=headers
        ).status_code
        == 204
    )
    assert (
        install_client.delete(
            f"/api/admin/plugins/uploads/{upload_13['upload_id']}", headers=headers
        ).status_code
        == 404
    )

    # 只删除代码、保留 state；delete_data 才删除 state
    state = config.data_dir / "plugins" / PLUGIN_ID / "state"
    assert state.is_dir()


def test_upload_rejects_system_id_and_too_large(
    install_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = admin_headers(install_client)
    system_archive = make_plugin_zip(
        tmp_path / "builtin.zip", plugin_id="builtin.tmdb", version="1.0.0"
    )
    response = install_client.post(
        "/api/admin/plugins/uploads",
        content=system_archive.read_bytes(),
        headers={**headers, "Content-Type": "application/zip"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "plugin_conflict"

    monkeypatch.setattr("app.api.admin.plugins.MAX_PACKAGE_BYTES", 8)
    response = install_client.post(
        "/api/admin/plugins/uploads",
        content=b"x" * 9,
        headers={**headers, "Content-Type": "application/zip"},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "package_too_large"

    invalid = install_client.post(
        "/api/admin/plugins/uploads",
        content=b"bad",
        headers={**headers, "Content-Type": "application/zip"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "package_invalid"


def test_uninstall_in_use_and_delete_data(
    install_client: TestClient,
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
    config: AppConfig,
) -> None:
    headers = admin_headers(install_client)
    publish(repository_server, tmp_path)
    repository = add_repository(install_client, headers, repository_server)
    wait_task(
        install_client, headers, start_install(install_client, headers, repository["id"])["task_id"]
    )
    container = container_of(install_client)
    with container.database.session() as connection:
        connection.execute(
            "INSERT INTO resource_source "
            "(plugin_id, name, enabled, config, status, created_at, updated_at) "
            "VALUES (?, '来源', 1, '{}', 'unknown', 'x', 'x')",
            (PLUGIN_ID,),
        )
    response = install_client.post(
        f"/api/admin/plugins/{PLUGIN_ID}/uninstall", json={"delete_data": True}, headers=headers
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "plugin_in_use"

    with container.database.session() as connection:
        connection.execute("DELETE FROM resource_source WHERE plugin_id = ?", (PLUGIN_ID,))
    response = install_client.post(
        f"/api/admin/plugins/{PLUGIN_ID}/uninstall", json={"delete_data": True}, headers=headers
    )
    assert response.status_code == 204, response.text
    assert not (config.data_dir / "plugins" / PLUGIN_ID).exists()
    assert not (config.plugin_packages_dir / PLUGIN_ID).exists()
    assert plugin_item(install_client, headers) is None


def test_restore_missing_plugin_triggers_reinstall(
    install_client: TestClient,
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
    config: AppConfig,
) -> None:
    headers = admin_headers(install_client)
    publish(repository_server, tmp_path)
    repository = add_repository(install_client, headers, repository_server)
    wait_task(
        install_client, headers, start_install(install_client, headers, repository["id"])["task_id"]
    )

    backup = install_client.post("/api/admin/backups", headers=headers)
    assert backup.status_code == 201, backup.text
    shutil.rmtree(config.plugin_packages_dir / PLUGIN_ID)

    restored = install_client.post(
        f"/api/admin/backups/{backup.json()['name']}/restore?confirm=true", headers=headers
    )
    assert restored.status_code == 200, restored.text
    fresh = admin_headers(install_client)

    def recovered() -> bool:
        item = plugin_item(install_client, fresh)
        return item is not None and item["state"] == "running" and item["version"] == "1.0.0"

    wait_for(recovered, timeout=30.0)
    with container_of(install_client).database.read_session() as connection:
        kinds = [
            row[0]
            for row in connection.execute(
                "SELECT kind FROM background_task WHERE kind IN "
                "('plugin:reinstall', 'plugin:dependencies')"
            )
        ]
    assert "plugin:reinstall" in kinds


def test_check_plugin_updates_manual_and_automatic(
    install_client: TestClient,
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
    clock: FakeClock,
) -> None:
    headers = admin_headers(install_client)
    publish(repository_server, tmp_path)
    repository = add_repository(install_client, headers, repository_server)
    first = wait_task(
        install_client, headers, start_install(install_client, headers, repository["id"])["task_id"]
    )
    assert first["status"] == "succeeded"
    publish(repository_server, tmp_path, version="1.1.0")
    container = container_of(install_client)
    with container.database.session() as connection:
        connection.execute("UPDATE scheduled_task SET enabled = 0 WHERE key = 'download_subtitles'")

    manual = install_client.post(
        "/api/admin/scheduled-tasks/check_plugin_updates/run", headers=headers
    )
    assert manual.status_code == 202, manual.text
    task = wait_task(install_client, headers, manual.json()["task_id"])
    assert task["status"] == "succeeded", task
    assert task["result"]["repositories"] == 1
    assert task["result"]["failed"] == 0
    assert task["result"]["updates"] == 1

    clock.advance(days=2)
    run_due(
        container.database,
        container.backups,
        container.images,
        container.metadata,
        container.subtitle_auto,
        container.plugin_repositories,
        container.clock,
    )
    with container.database.read_session() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM background_task WHERE kind = 'scheduled:check_plugin_updates'"
        ).fetchone()[0]
    assert count == 2


def test_openapi_contains_plugin_repository_routes(install_client: TestClient) -> None:
    schema = install_client.get("/api/openapi.json").json()
    paths = schema["paths"]
    for path in (
        "/api/admin/plugin-repositories",
        "/api/admin/plugin-repositories/{repository_id}",
        "/api/admin/plugin-repositories/refresh",
        "/api/admin/plugin-catalog",
        "/api/admin/plugins/install",
        "/api/admin/plugins/{plugin_id}/upgrade",
        "/api/admin/plugins/{plugin_id}/uninstall",
        "/api/admin/plugins/uploads",
        "/api/admin/plugins/uploads/{upload_id}/install",
    ):
        assert path in paths, path


@pytest.mark.network
def test_real_uv_installer_exports_hashes_and_installs(tmp_path: Path) -> None:
    """真实 uv：uv lock 后走 export + require-hashes + 安装插件本身。"""
    package = tmp_path / "network-plugin"
    source = package / "src" / "iris_plugin_network"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text('__version__ = "1.0.0"\n', encoding="utf-8")
    (package / "plugin.json").write_text(
        json.dumps(
            {
                "id": "org.example.network",
                "name": "网络安装测试",
                "version": "1.0.0",
                "description": "真实 uv 用例",
                "sdk": ">=1.0,<2.0",
                "entry": "iris_plugin_network",
                "provides": ["metadata.search"],
                "capabilities": ["logger"],
            }
        ),
        encoding="utf-8",
    )
    (package / "pyproject.toml").write_text(
        """[project]
name = "iris-plugin-network"
version = "1.0.0"
requires-python = ">=3.13"
dependencies = ["iris-plugin-sdk>=1.0,<2"]

[build-system]
requires = ["setuptools>=80"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]
include = ["iris_plugin_network*"]
""",
        encoding="utf-8",
    )
    uv = shutil.which("uv") or "uv"
    locked = subprocess.run(
        [uv, "lock", "--project", str(package)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert locked.returncode == 0, locked.stdout + locked.stderr

    installer = UvInstaller(tmp_path / "uv-cache")
    installer.install(package, proxy=None)

    python = package / ".venv" / "bin" / "python"
    probe = subprocess.run(
        [
            str(python),
            "-c",
            "import iris_plugin_sdk, iris_plugin_network; "
            "print(iris_plugin_sdk.SDK_VERSION, iris_plugin_network.__version__)",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    sdk_version, plugin_version = probe.stdout.strip().split()
    assert sdk_version == "1.0.0"
    assert plugin_version == "1.0.0"


def test_system_plugin_list_fields(install_client: TestClient) -> None:
    headers = admin_headers(install_client)
    item = plugin_item(install_client, headers, "source.115")
    assert item is not None
    assert item["source"] == "system"
    assert item["built_in"] is True
    assert item["repository_id"] is None
    assert item["repository_name"] is None
    assert item["available_version"] is None
    assert item["package_status"] == "ok"

    upgrade = install_client.post(
        "/api/admin/plugins/source.115/upgrade",
        json={"version": "9.0", "accept_permissions": True},
        headers=headers,
    )
    assert upgrade.status_code == 409
    assert upgrade.json()["error"]["code"] == "plugin_built_in"
    uninstall = install_client.post(
        "/api/admin/plugins/source.115/uninstall", json={}, headers=headers
    )
    assert uninstall.status_code == 409
    assert uninstall.json()["error"]["code"] == "plugin_built_in"


def test_dependency_install_failure_is_reported(
    install_client: TestClient,
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
    config: AppConfig,
    installer: FakeInstaller,
) -> None:
    headers = admin_headers(install_client)
    publish(repository_server, tmp_path)
    repository = add_repository(install_client, headers, repository_server)
    installer.fail = RuntimeError("index 不可用")
    task = wait_task(
        install_client, headers, start_install(install_client, headers, repository["id"])["task_id"]
    )
    assert task["status"] == "failed"
    assert task["result"]["error_code"] == "dependency_install_failed"
    assert "index 不可用" in task["error"]
    assert not (config.plugin_packages_dir / PLUGIN_ID).exists()


def test_repository_and_package_redirects(
    install_client: TestClient,
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
) -> None:
    headers = admin_headers(install_client)
    archive = make_plugin_zip(tmp_path / "redirect.zip")
    repository_server.add_file("/files/fake.zip", archive.read_bytes())
    repository_server.redirects["/real/fake.zip"] = "/files/fake.zip"
    indexed = index_entry(url="/real/fake.zip", archive=archive)
    publish_index(repository_server, [indexed], path="/real/index.json")
    repository_server.redirects["/index.json"] = "/real/index.json"

    response = install_client.post(
        "/api/admin/plugin-repositories",
        json={"url": repository_server.index_url(), "accept_risk": True},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    repository = response.json()
    task = wait_task(
        install_client, headers, start_install(install_client, headers, repository["id"])["task_id"]
    )
    assert task["status"] == "succeeded", task
    assert plugin_item(install_client, headers)["state"] == "running"


def test_local_upload_upgrade_and_restart(
    config: AppConfig,
    clock: FakeClock,
    sync_runner: SyncRunner,
    plugin_env: PluginEnvironment,
    installer: FakeInstaller,
    tmp_path: Path,
) -> None:
    first_zip = make_plugin_zip(tmp_path / "local-10.zip", version="1.0.0")
    second_zip = make_plugin_zip(tmp_path / "local-11.zip", version="1.1.0")
    with TestClient(make_app(config, clock, sync_runner, plugin_installer=installer)) as first:
        bind_controls(plugin_env.root, first)
        headers = admin_headers(first)
        upload = first.post(
            "/api/admin/plugins/uploads",
            content=first_zip.read_bytes(),
            headers={**headers, "Content-Type": "application/zip"},
        ).json()
        task = wait_task(
            first,
            headers,
            first.post(
                f"/api/admin/plugins/uploads/{upload['upload_id']}/install",
                json={"accept_risk": True},
                headers=headers,
            ).json()["task_id"],
        )
        assert task["status"] == "succeeded"
        assert plugin_item(first, headers)["version"] == "1.0.0"

        upload = first.post(
            "/api/admin/plugins/uploads",
            content=second_zip.read_bytes(),
            headers={**headers, "Content-Type": "application/zip"},
        ).json()
        assert upload["action"] == "upgrade"
        task = wait_task(
            first,
            headers,
            first.post(
                f"/api/admin/plugins/uploads/{upload['upload_id']}/install",
                json={"accept_risk": True},
                headers=headers,
            ).json()["task_id"],
        )
        assert task["status"] == "succeeded", task
        item = plugin_item(first, headers)
        assert (item["version"], item["source"]) == ("1.1.0", "local")
        assert (config.plugin_packages_dir / PLUGIN_ID / "1.0.0").is_dir()

    # 重启宿主后按已安装清单加载，不重新下载或安装依赖
    with TestClient(make_app(config, clock, sync_runner, plugin_installer=installer)) as second:
        bind_controls(plugin_env.root, second)
        headers = admin_headers(second)
        item = plugin_item(second, headers)
        assert item is not None
        assert item["version"] == "1.1.0"
        assert item["state"] == "running"


def test_python_version_change_rebuilds_dependencies(
    config: AppConfig,
    clock: FakeClock,
    sync_runner: SyncRunner,
    plugin_env: PluginEnvironment,
    installer: FakeInstaller,
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
) -> None:
    publish(repository_server, tmp_path)
    with TestClient(make_app(config, clock, sync_runner, plugin_installer=installer)) as first:
        bind_controls(plugin_env.root, first)
        headers = admin_headers(first)
        repository = add_repository(first, headers, repository_server)
        task = wait_task(first, headers, start_install(first, headers, repository["id"])["task_id"])
        assert task["status"] == "succeeded"

    version_dir = config.plugin_packages_dir / PLUGIN_ID / "1.0.0"
    pyvenv = version_dir / ".venv" / "pyvenv.cfg"
    pyvenv.write_text("home = /\nversion_info = 0.0\n", encoding="utf-8")
    calls_before = len(installer.calls)

    with TestClient(make_app(config, clock, sync_runner, plugin_installer=installer)) as second:
        bind_controls(plugin_env.root, second)
        headers = admin_headers(second)
        container = container_of(second)
        container.plugin_packages.reinstall_missing()

        def rebuilt() -> bool:
            item = plugin_item(second, headers)
            return item is not None and item["state"] == "running"

        wait_for(rebuilt, timeout=30.0)
        with container.database.read_session() as connection:
            kinds = [
                row[0]
                for row in connection.execute(
                    "SELECT kind FROM background_task WHERE kind = 'plugin:dependencies'"
                )
            ]
        assert kinds == ["plugin:dependencies"]
        assert len(installer.calls) > calls_before


def test_restore_backup_created_before_plugin_repository_migration(
    install_client: TestClient,
    config: AppConfig,
    clock: FakeClock,
) -> None:
    """恢复 0004 之前的备份时，start_all 会先补迁移，新表不能消失。"""
    old_database = config.data_dir / "old.sqlite3"
    old_database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(old_database)
    try:
        apply_migrations(
            connection,
            [migration for migration in load_migrations() if migration.version < 4],
            clock,
        )
        connection.commit()
    finally:
        connection.close()

    backups = config.data_dir / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    name = "iris-20260101T000000Z.tar.gz"
    with tarfile.open(backups / name, "w:gz") as archive:
        archive.add(old_database, arcname="iris.sqlite3")

    container = container_of(install_client)
    container.backups.restore(name)

    with container.database.read_session() as check:
        tables = {
            row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert {"plugin_repository", "plugin_install"} <= tables
        assert (
            check.execute(
                "SELECT 1 FROM scheduled_task WHERE key = 'check_plugin_updates'"
            ).fetchone()
            is not None
        )


def test_upgrade_blocked_when_resource_source_is_in_use(
    install_client: TestClient,
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
) -> None:
    headers = admin_headers(install_client)
    publish(repository_server, tmp_path, version="1.0.0")
    repository = add_repository(install_client, headers, repository_server)
    wait_task(
        install_client, headers, start_install(install_client, headers, repository["id"])["task_id"]
    )
    with container_of(install_client).database.session() as connection:
        connection.execute(
            "INSERT INTO resource_source "
            "(plugin_id, name, enabled, config, status, created_at, updated_at) "
            "VALUES (?, '来源', 1, '{}', 'unknown', 'x', 'x')",
            (PLUGIN_ID,),
        )
    publish(
        repository_server,
        tmp_path,
        version="1.1.0",
        provides=("metadata.search",),
        capabilities=("logger",),
    )
    install_client.post("/api/admin/plugin-repositories/refresh", headers=headers)
    response = install_client.post(
        f"/api/admin/plugins/{PLUGIN_ID}/upgrade",
        json={"version": "1.1.0", "accept_permissions": True},
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "plugin_in_use"


def test_manifest_index_mismatch_is_rejected(
    install_client: TestClient,
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
    config: AppConfig,
) -> None:
    headers = admin_headers(install_client)
    # 索引写 1.0.0，压缩包里的 manifest 是 0.9.0
    archive = make_plugin_zip(tmp_path / "mismatch.zip", version="0.9.0")
    repository_server.add_file("/mismatch.zip", archive.read_bytes())
    indexed = index_entry(version="1.0.0", url="/mismatch.zip", archive=archive)
    publish_index(repository_server, [indexed])
    repository = add_repository(install_client, headers, repository_server)
    task = wait_task(
        install_client, headers, start_install(install_client, headers, repository["id"])["task_id"]
    )
    assert task["status"] == "failed"
    assert task["result"]["error_code"] == "package_mismatch"
    assert not (config.plugin_packages_dir / PLUGIN_ID).exists()


# ---- 审查后补充的回归测试 ----


def rewrite_manifest(archive: Path, **updates: Any) -> Path:
    """改写 zip 里的 plugin.json（其余文件原样复制）。"""
    rewritten = archive.with_name(f"{archive.stem}-rewritten.zip")
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(rewritten, "w") as target:
        for info in source.infolist():
            data = source.read(info)
            if info.filename == "plugin.json":
                manifest = json.loads(data)
                manifest.update(updates)
                data = json.dumps(manifest).encode("utf-8")
            target.writestr(info, data)
    return rewritten


def upload_zip(client: TestClient, headers: dict[str, str], archive: Path) -> Any:
    return client.post(
        "/api/admin/plugins/uploads",
        content=archive.read_bytes(),
        headers={**headers, "Content-Type": "application/zip"},
    )


def install_local(
    client: TestClient, headers: dict[str, str], archive: Path, *, version: str
) -> dict[str, Any]:
    response = upload_zip(client, headers, archive)
    assert response.status_code == 201, response.text
    started = client.post(
        f"/api/admin/plugins/uploads/{response.json()['upload_id']}/install",
        json={"accept_risk": True},
        headers=headers,
    )
    assert started.status_code == 202, started.text
    task = wait_task(client, headers, started.json()["task_id"])
    assert task["status"] == "succeeded", task
    assert task["result"]["version"] == version
    return task


def test_upload_with_incompatible_sdk_is_409_plugin_incompatible(
    install_client: TestClient, tmp_path: Path, config: AppConfig
) -> None:
    headers = admin_headers(install_client)
    archive = rewrite_manifest(make_plugin_zip(tmp_path / "old.zip"), sdk=">=9.0,<10.0")
    response = upload_zip(install_client, headers, archive)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "plugin_incompatible"
    assert not (config.plugin_packages_dir / PLUGIN_ID).exists()


def test_upload_same_version_rejected_when_installed_but_repairs_missing(
    install_client: TestClient, tmp_path: Path, config: AppConfig
) -> None:
    headers = admin_headers(install_client)
    archive = make_plugin_zip(tmp_path / "upload.zip", version="1.2.0")
    install_local(install_client, headers, archive, version="1.2.0")

    # 状态是 installed：同版本重传被拒
    rejected = upload_zip(install_client, headers, archive)
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "plugin_version_not_newer"

    # 低版本始终被拒，哪怕记录已经不是 installed
    container = container_of(install_client)
    packages = container.plugin_packages
    packages._detach(PLUGIN_ID)
    shutil.rmtree(config.plugin_packages_dir / PLUGIN_ID)
    packages._mark_missing(PLUGIN_ID, "插件代码目录不存在，等待重新安装")
    older = upload_zip(
        install_client, headers, make_plugin_zip(tmp_path / "older.zip", version="1.1.0")
    )
    assert older.status_code == 409
    assert older.json()["error"]["code"] == "plugin_version_not_newer"

    # 状态是 missing：同版本可以重传来修复
    install_local(install_client, headers, archive, version="1.2.0")
    item = plugin_item(install_client, headers)
    assert item is not None
    assert item["version"] == "1.2.0"
    assert item["state"] == "running"
    assert item["package_status"] == "ok"
    with container.database.read_session() as connection:
        row = connection.execute(
            "SELECT status, previous_version FROM plugin_install WHERE plugin_id = ?",
            (PLUGIN_ID,),
        ).fetchone()
    assert row["status"] == "installed"
    assert row["previous_version"] is None


def _make_pending_missing(
    container: AppContainer, config: AppConfig, repository_id: int, *, drop_cache: bool
) -> None:
    """模拟恢复备份后：运行时没有这个插件、目录没了、安装记录是 missing。"""
    packages = container.plugin_packages
    packages._detach(PLUGIN_ID)
    shutil.rmtree(config.plugin_packages_dir / PLUGIN_ID)
    packages._mark_missing(PLUGIN_ID, "插件代码目录不存在，等待重新安装")
    if drop_cache:
        with container.database.session() as connection:
            connection.execute(
                "UPDATE plugin_repository SET status = 'pending', index_json = NULL, "
                "etag = NULL, index_schema = NULL, last_success_at = NULL WHERE id = ?",
                (repository_id,),
            )


def supervisor_has(container: AppContainer) -> bool:
    return any(item.plugin_id == PLUGIN_ID for item in container.supervisor.discovered())


def _install_row_status(container: AppContainer) -> str:
    with container.database.read_session() as connection:
        return str(
            connection.execute(
                "SELECT status FROM plugin_install WHERE plugin_id = ?", (PLUGIN_ID,)
            ).fetchone()[0]
        )


def test_reinstall_missing_refreshes_empty_repository_cache_first(
    install_client: TestClient,
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
    config: AppConfig,
) -> None:
    headers = admin_headers(install_client)
    publish(repository_server, tmp_path)
    repository = add_repository(install_client, headers, repository_server)
    wait_task(
        install_client, headers, start_install(install_client, headers, repository["id"])["task_id"]
    )
    container = container_of(install_client)
    _make_pending_missing(container, config, repository["id"], drop_cache=True)
    before = len(repository_server.requests)

    container.plugin_packages.reinstall_missing()

    assert len(repository_server.requests) > before
    assert repository_server.requests[before][0] == "/index.json"
    assert _install_row_status(container) == "installed"
    item = plugin_item(install_client, headers)
    assert item is not None
    assert item["state"] == "running"


def test_reinstall_missing_stays_missing_when_repository_is_unreachable(
    install_client: TestClient,
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
    config: AppConfig,
) -> None:
    headers = admin_headers(install_client)
    publish(repository_server, tmp_path)
    repository = add_repository(install_client, headers, repository_server)
    wait_task(
        install_client, headers, start_install(install_client, headers, repository["id"])["task_id"]
    )
    container = container_of(install_client)
    _make_pending_missing(container, config, repository["id"], drop_cache=True)

    repository_server.fail = True
    container.plugin_packages.reinstall_missing()
    assert _install_row_status(container) == "missing"

    # 网络恢复后下一次补装成功，说明没有被永久标成 failed
    repository_server.fail = False
    container.plugin_packages.reinstall_missing()
    assert _install_row_status(container) == "installed"


def test_reinstall_missing_marks_failed_only_when_index_lacks_the_plugin(
    install_client: TestClient,
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
    config: AppConfig,
) -> None:
    headers = admin_headers(install_client)
    publish(repository_server, tmp_path)
    repository = add_repository(install_client, headers, repository_server)
    wait_task(
        install_client, headers, start_install(install_client, headers, repository["id"])["task_id"]
    )
    container = container_of(install_client)
    _make_pending_missing(container, config, repository["id"], drop_cache=True)
    publish_index(repository_server, [], name="测试仓库")

    container.plugin_packages.reinstall_missing()

    assert _install_row_status(container) == "failed"


class _GatedInstaller(FakeInstaller):
    """依赖安装阶段卡住，直到测试放行；用来让两个操作真正重叠。"""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def install(self, directory: Path, *, proxy: str | None) -> None:
        self.entered.set()
        assert self.release.wait(30.0)
        super().install(directory, proxy=proxy)


def test_concurrent_installs_of_same_plugin_only_one_wins(
    config: AppConfig,
    clock: FakeClock,
    sync_runner: SyncRunner,
    plugin_env: PluginEnvironment,
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
) -> None:
    gated = _GatedInstaller()
    with TestClient(make_app(config, clock, sync_runner, plugin_installer=gated)) as client:
        bind_controls(plugin_env.root, client)
        headers = admin_headers(client)
        publish(repository_server, tmp_path)
        repository = add_repository(client, headers, repository_server)
        packages = container_of(client).plugin_packages

        start = threading.Barrier(2)
        outcomes: list[int | AppError] = []
        guard = threading.Lock()

        def attempt() -> None:
            start.wait(10.0)
            try:
                outcome: int | AppError = packages.install(
                    repository["id"], PLUGIN_ID, "1.0.0", True
                )
            except AppError as exc:
                outcome = exc
            with guard:
                outcomes.append(outcome)

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads:
            thread.start()
        # 获胜的一方卡在依赖安装，输的一方已经被拒绝
        assert gated.entered.wait(30.0)
        wait_for(lambda: len(outcomes) == 2)
        winners = [item for item in outcomes if isinstance(item, int)]
        losers = [item for item in outcomes if isinstance(item, AppError)]
        assert len(winners) == 1
        assert len(losers) == 1
        assert losers[0].code == "plugin_operation_running"
        for thread in threads:
            thread.join(10.0)

        gated.release.set()
        task = wait_task(client, headers, winners[0])
        assert task["status"] == "succeeded", task

        directory = config.plugin_packages_dir / PLUGIN_ID / "1.0.0"
        assert (directory / "plugin.json").is_file()
        assert not (directory / ".installing").exists()
        assert len(gated.calls) == 1
        item = plugin_item(client, headers)
        assert item is not None
        assert item["state"] == "running"
        assert not packages._lock.locked()


def test_activation_failure_detaches_runtime_before_cleanup_and_releases_lock(
    install_client: TestClient,
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
    config: AppConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = admin_headers(install_client)
    publish(repository_server, tmp_path)
    repository = add_repository(install_client, headers, repository_server)
    container = container_of(install_client)
    packages = container.plugin_packages

    def broken(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(packages, "_write_install_row", broken)
    task = wait_task(
        install_client, headers, start_install(install_client, headers, repository["id"])["task_id"]
    )
    assert task["status"] == "failed"
    # 运行时不能指向被清掉的目录：插件已摘除，目录已清理，没有安装记录
    assert plugin_item(install_client, headers) is None
    assert not supervisor_has(container)
    assert not (config.plugin_packages_dir / PLUGIN_ID).exists()
    assert not packages._lock.locked()
    assert packages.package_status(PLUGIN_ID) is None

    # 锁已释放，状态干净，可以直接重试
    monkeypatch.undo()
    retry = wait_task(
        install_client, headers, start_install(install_client, headers, repository["id"])["task_id"]
    )
    assert retry["status"] == "succeeded", retry


def test_upgrade_record_write_failure_rolls_back_to_old_version(
    install_client: TestClient,
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
    config: AppConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = admin_headers(install_client)
    publish(repository_server, tmp_path, version="1.0.0")
    repository = add_repository(install_client, headers, repository_server)
    wait_task(
        install_client, headers, start_install(install_client, headers, repository["id"])["task_id"]
    )
    publish(repository_server, tmp_path, version="1.1.0")
    install_client.post("/api/admin/plugin-repositories/refresh", headers=headers)
    packages = container_of(install_client).plugin_packages
    original = packages._write_install_row

    def broken(package: Any, **kwargs: Any) -> None:
        if package.version == "1.1.0":
            raise sqlite3.OperationalError("database is locked")
        original(package, **kwargs)

    monkeypatch.setattr(packages, "_write_install_row", broken)
    response = install_client.post(
        f"/api/admin/plugins/{PLUGIN_ID}/upgrade",
        json={"version": "1.1.0", "accept_permissions": True},
        headers=headers,
    )
    assert response.status_code == 202, response.text
    task = wait_task(install_client, headers, response.json()["task_id"])
    assert task["status"] == "failed"
    assert task["result"]["rolled_back"] is True
    item = plugin_item(install_client, headers)
    assert item is not None
    assert item["version"] == "1.0.0"
    assert item["state"] == "running"
    assert (config.plugin_packages_dir / PLUGIN_ID / "1.0.0" / "plugin.json").is_file()
    assert not (config.plugin_packages_dir / PLUGIN_ID / "1.1.0").exists()
    assert not packages._lock.locked()


def test_finish_failure_still_releases_operation_lock(
    install_client: TestClient,
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = admin_headers(install_client)
    publish(repository_server, tmp_path)
    repository = add_repository(install_client, headers, repository_server)
    packages = container_of(install_client).plugin_packages

    def broken(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(packages, "_finish", broken)
    # worker 线程里的未捕获异常正是这个测试要制造的，自己接住，不交给 pytest 报警
    raised: list[threading.ExceptHookArgs] = []
    monkeypatch.setattr(threading, "excepthook", raised.append)
    start_install(install_client, headers, repository["id"])
    wait_for(lambda: len(raised) == 1)
    assert raised[0].exc_type is sqlite3.OperationalError
    assert not packages._lock.locked()
    assert packages.package_status(PLUGIN_ID) is None


def test_uninstall_in_use_keeps_runtime_attached(
    install_client: TestClient,
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
    config: AppConfig,
) -> None:
    headers = admin_headers(install_client)
    publish(repository_server, tmp_path)
    repository = add_repository(install_client, headers, repository_server)
    wait_task(
        install_client, headers, start_install(install_client, headers, repository["id"])["task_id"]
    )
    container = container_of(install_client)
    with container.database.session() as connection:
        connection.execute(
            "INSERT INTO resource_source "
            "(plugin_id, name, enabled, config, status, created_at, updated_at) "
            "VALUES (?, '来源', 1, '{}', 'unknown', 'x', 'x')",
            (PLUGIN_ID,),
        )
    response = install_client.post(
        f"/api/admin/plugins/{PLUGIN_ID}/uninstall", json={"delete_data": True}, headers=headers
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "plugin_in_use"
    # 被拒绝的卸载不能碰运行时、安装记录和目录
    item = plugin_item(install_client, headers)
    assert item is not None
    assert item["state"] == "running"
    assert supervisor_has(container)
    assert _install_row_status(container) == "installed"
    assert (config.plugin_packages_dir / PLUGIN_ID / "1.0.0" / "plugin.json").is_file()
    assert not container.plugin_packages._lock.locked()


def test_uninstall_database_failure_leaves_plugin_running(
    install_client: TestClient,
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
) -> None:
    """外键拒绝删除时，先删库的顺序保证插件没有被提前摘掉。"""
    headers = admin_headers(install_client)
    publish(repository_server, tmp_path)
    repository = add_repository(install_client, headers, repository_server)
    wait_task(
        install_client, headers, start_install(install_client, headers, repository["id"])["task_id"]
    )
    container = container_of(install_client)
    with container.database.session() as connection:
        connection.execute(
            "CREATE TRIGGER block_plugin_delete BEFORE DELETE ON plugin_install "
            "BEGIN SELECT RAISE(ABORT, 'blocked'); END"
        )
    response = install_client.post(
        f"/api/admin/plugins/{PLUGIN_ID}/uninstall", json={}, headers=headers
    )
    assert response.status_code >= 400
    item = plugin_item(install_client, headers)
    assert item is not None
    assert item["state"] == "running"
    assert _install_row_status(container) == "installed"
    assert not container.plugin_packages._lock.locked()


def test_default_repository_is_refreshed_in_background_after_startup(
    config: AppConfig,
    clock: FakeClock,
    plugin_env: PluginEnvironment,
    installer: FakeInstaller,
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
) -> None:
    publish(repository_server, tmp_path)
    fresh = dataclasses.replace(config, default_plugin_repos=(repository_server.index_url(),))
    app = create_app(fresh, clock, background_tasks=True, plugin_installer=installer)
    with TestClient(app) as client:
        headers = admin_headers(client)

        def repositories() -> list[dict[str, Any]]:
            response = client.get("/api/admin/plugin-repositories", headers=headers)
            assert response.status_code == 200, response.text
            return list(response.json()["repositories"])

        # 不手动刷新，后台线程自己把 pending 的官方仓库拉成 ok
        wait_for(lambda: [item["status"] for item in repositories()] == ["ok"], timeout=20.0)
        official = repositories()[0]
        assert official["official"] is True
        assert official["plugin_count"] == 1
        assert official["error"] is None
        catalog = client.get("/api/admin/plugin-catalog", headers=headers).json()
        assert [item["id"] for item in catalog["plugins"]] == [PLUGIN_ID]
        assert catalog["plugins"][0]["status"] == "available"


def test_default_repository_refresh_failure_is_recorded_not_raised(
    config: AppConfig,
    clock: FakeClock,
    plugin_env: PluginEnvironment,
    installer: FakeInstaller,
    repository_server: RepositoryHTTPServer,
) -> None:
    repository_server.fail = True
    fresh = dataclasses.replace(config, default_plugin_repos=(repository_server.index_url(),))
    app = create_app(fresh, clock, background_tasks=True, plugin_installer=installer)
    with TestClient(app) as client:
        headers = admin_headers(client)

        def status() -> str:
            response = client.get("/api/admin/plugin-repositories", headers=headers)
            return str(response.json()["repositories"][0]["status"])

        wait_for(lambda: status() == "error", timeout=20.0)


def test_never_run_update_check_is_due_on_first_maintenance_tick(
    install_client: TestClient,
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
) -> None:
    container = container_of(install_client)
    with container.database.session() as connection:
        connection.execute(
            "UPDATE scheduled_task SET enabled = 0 WHERE key != 'check_plugin_updates'"
        )
        row = connection.execute(
            "SELECT enabled, last_run_at FROM scheduled_task WHERE key = 'check_plugin_updates'"
        ).fetchone()
    assert row["enabled"] == 1
    assert row["last_run_at"] is None

    # 没有推进过时钟：从未运行过的检查在第一次维护 tick 就到期
    run_due(
        container.database,
        container.backups,
        container.images,
        container.metadata,
        container.subtitle_auto,
        container.plugin_repositories,
        container.clock,
    )
    with container.database.read_session() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM background_task WHERE kind = 'scheduled:check_plugin_updates'"
        ).fetchone()[0]
    assert count == 1
