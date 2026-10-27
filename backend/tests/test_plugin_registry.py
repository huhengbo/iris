from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import Database
from app.errors import AppError
from app.plugins.registry import PluginRegistry, PluginStartFailed
from app.plugins.supervisor import PluginSupervisor
from tests.conftest import FakeClock
from tests.plugin_env import FakePluginSpec, PluginEnvironment, install_fake_plugin

PLUGIN_ID = "example.resource"


@pytest.fixture
def database(tmp_path: Path, clock: FakeClock) -> Database:
    database = Database(tmp_path / "plugins.db")
    database.migrate(clock)
    return database


@pytest.fixture
def env(tmp_path: Path) -> PluginEnvironment:
    root = tmp_path / "plugins"
    root.mkdir()
    return PluginEnvironment(root)


@pytest.fixture
def registry(database: Database, clock: FakeClock, env: PluginEnvironment) -> PluginRegistry:
    install_fake_plugin(
        env.root,
        FakePluginSpec(PLUGIN_ID, "资源插件", ("resource.source",), capabilities=("logger",)),
    )
    supervisor = PluginSupervisor(
        env.root,
        env.root.parent / "data",
        media_dir=env.root.parent / "media",
        backoff_initial=0.01,
        backoff_max=0.02,
    )
    return PluginRegistry(database, clock, supervisor)


def state_of(registry: PluginRegistry, plugin_id: str) -> str:
    return str(next(item["state"] for item in registry.list() if item["id"] == plugin_id))


def insert_legacy_plugin(
    database: Database,
    plugin_id: str,
    *,
    enabled: bool = False,
    provides: tuple[str, ...] = (),
    proxy_mode: str = "inherit",
    proxy: str | None = None,
) -> None:
    with database.session() as connection:
        connection.execute(
            "INSERT INTO plugin (id, name, version, sdk_version, provides, requires, trust, "
            "built_in, enabled, updated_at, proxy_mode, proxy) "
            "VALUES (?, '旧插件', '1.0.0', '1', ?, '[]', 'external', 0, ?, 'x', ?, ?)",
            (
                plugin_id,
                json.dumps(provides),
                int(enabled),
                proxy_mode,
                proxy,
            ),
        )


def insert_source(database: Database, plugin_id: str) -> int:
    with database.session() as connection:
        cursor = connection.execute(
            "INSERT INTO resource_source "
            "(plugin_id, name, enabled, config, status, created_at, updated_at) "
            "VALUES (?, ?, 1, '{}', 'unknown', 'x', 'x')",
            (plugin_id, f"{plugin_id} 来源"),
        )
        assert cursor.lastrowid is not None
        return int(cursor.lastrowid)


def test_list_includes_description_category_and_proxy_fields(
    registry: PluginRegistry, database: Database, env: PluginEnvironment
) -> None:
    install_fake_plugin(
        env.root,
        FakePluginSpec(
            "example.metadata",
            "元数据插件",
            ("metadata.search", "subtitle.search"),
            capabilities=("logger",),
        ),
    )
    install_fake_plugin(
        env.root,
        FakePluginSpec("example.subtitle", "字幕插件", ("subtitle.search",)),
    )
    install_fake_plugin(env.root, FakePluginSpec("example.other", "其他插件", ()))
    # 注册中心在构造时发现插件；新建 supervisor 和注册中心加载新增目录
    supervisor = PluginSupervisor(
        env.root,
        env.root.parent / "data",
        media_dir=env.root.parent / "media",
        backoff_initial=0.01,
        backoff_max=0.02,
    )
    registry = PluginRegistry(database, registry._clock, supervisor)
    insert_legacy_plugin(database, "example.legacy", provides=("metadata.details",))

    items = {item["id"]: item for item in registry.list()}

    assert items["example.metadata"]["category"] == "metadata"
    assert items["example.subtitle"]["category"] == "subtitle"
    assert items["example.resource"]["category"] == "resource"
    assert items["example.other"]["category"] == "other"
    # 未安装的旧记录没有简介，分类从数据库里的 provides 推导
    assert items["example.legacy"]["description"] == ""
    assert items["example.legacy"]["category"] == "metadata"
    assert items["example.metadata"]["proxy_mode"] == "inherit"
    assert items["example.metadata"]["proxy"] is None


def test_proxy_modes_are_saved_and_custom_address_is_masked(registry: PluginRegistry) -> None:
    assert registry.proxy_config(PLUGIN_ID) == ("inherit", None)

    assert registry.set_proxy(PLUGIN_ID, "custom", "http://user:hunter2@127.0.0.1:7890")
    item = next(item for item in registry.list() if item["id"] == PLUGIN_ID)
    assert item["proxy_mode"] == "custom"
    assert item["proxy"] == "http://user:***@127.0.0.1:7890"
    assert "hunter2" not in json.dumps(item)
    assert registry.proxy_config(PLUGIN_ID) == (
        "custom",
        "http://user:hunter2@127.0.0.1:7890",
    )

    assert registry.set_proxy(PLUGIN_ID, "none", None)
    item = next(item for item in registry.list() if item["id"] == PLUGIN_ID)
    assert (item["proxy_mode"], item["proxy"]) == ("none", None)

    assert registry.set_proxy("example.missing", "inherit", None) is False


def test_failed_start_is_not_retried_until_enabled_again(
    registry: PluginRegistry, env: PluginEnvironment
) -> None:
    path = env.root / PLUGIN_ID / "mode.txt"
    path.write_text("crash_on_start", encoding="utf-8")

    registry.start_enabled()

    assert state_of(registry, PLUGIN_ID) == "failed"
    with pytest.raises(PluginStartFailed) as error:
        registry.provider(PLUGIN_ID, "resource.source")
    assert error.value.code == "plugin_start_failed"
    assert "插件进程已退出" in error.value.message

    # 管理员重新启用前不会重试
    with pytest.raises(PluginStartFailed):
        registry.provider(PLUGIN_ID, "resource.source")

    path.write_text("", encoding="utf-8")
    assert registry.set_enabled(PLUGIN_ID, True) is True
    assert state_of(registry, PLUGIN_ID) == "running"
    assert registry.provider(PLUGIN_ID, "resource.source") is not None


def test_invalid_manifest_is_listed_with_discovery_error(
    database: Database, clock: FakeClock, env: PluginEnvironment
) -> None:
    broken = env.root / "broken.plugin"
    broken.mkdir()
    (broken / "plugin.json").write_text("{ not json", encoding="utf-8")
    supervisor = PluginSupervisor(
        env.root,
        env.root.parent / "data",
        media_dir=env.root.parent / "media",
    )
    registry = PluginRegistry(database, clock, supervisor)

    item = next(entry for entry in registry.list() if entry["id"] == "broken.plugin")

    assert item["state"] == "failed"
    assert item["built_in"] is True
    assert "JSON" in item["error"]
    with pytest.raises(PluginStartFailed) as error:
        registry.set_enabled("broken.plugin", True)
    assert error.value.code == "plugin_manifest_invalid"


def test_provider_does_not_start_disabled_plugin(registry: PluginRegistry) -> None:
    assert registry.set_enabled(PLUGIN_ID, False) is True

    assert registry.provider(PLUGIN_ID, "resource.source") is None
    assert state_of(registry, PLUGIN_ID) == "stopped"


def test_provider_stops_plugin_disabled_by_database(
    registry: PluginRegistry, database: Database
) -> None:
    assert registry.provider(PLUGIN_ID, "resource.source") is not None

    # 模拟备份恢复直接把 enabled 改成 0
    with database.session() as connection:
        connection.execute("UPDATE plugin SET enabled = 0 WHERE id = ?", (PLUGIN_ID,))

    assert registry.provider(PLUGIN_ID, "resource.source") is None
    assert state_of(registry, PLUGIN_ID) == "stopped"


def test_not_installed_plugin_can_be_disabled_but_not_enabled(
    registry: PluginRegistry, database: Database
) -> None:
    plugin_id = "example.removed"
    insert_legacy_plugin(database, plugin_id)

    with pytest.raises(AppError) as error:
        registry.set_enabled(plugin_id, True)
    assert (error.value.status_code, error.value.code) == (409, "plugin_not_installed")
    with database.read_session() as connection:
        enabled = connection.execute(
            "SELECT enabled FROM plugin WHERE id = ?", (plugin_id,)
        ).fetchone()[0]
    assert enabled == 0
    assert state_of(registry, plugin_id) == "not_installed"

    assert registry.set_enabled(plugin_id, False) is True
    assert state_of(registry, plugin_id) == "not_installed"


def test_delete_only_removes_uninstalled_plugin(
    registry: PluginRegistry, database: Database
) -> None:
    with pytest.raises(AppError) as error:
        registry.delete(PLUGIN_ID)
    assert (error.value.status_code, error.value.code) == (409, "plugin_installed")

    in_use = "example.in-use"
    insert_legacy_plugin(database, in_use)
    with database.session() as connection:
        connection.execute(
            "INSERT INTO resource_source "
            "(plugin_id, name, enabled, config, status, created_at, updated_at) "
            "VALUES (?, '来源', 1, '{}', 'unknown', 'x', 'x')",
            (in_use,),
        )
    with pytest.raises(AppError) as error:
        registry.delete(in_use)
    assert (error.value.status_code, error.value.code) == (409, "plugin_in_use")

    removed = "example.removed"
    insert_legacy_plugin(database, removed)

    assert registry.delete(removed) is True
    with database.read_session() as connection:
        assert (
            connection.execute("SELECT 1 FROM plugin WHERE id = ?", (removed,)).fetchone() is None
        )
    assert registry.delete("example.unknown") is False


def test_running_provider_never_starts_plugin(registry: PluginRegistry) -> None:
    assert registry.running_provider(PLUGIN_ID, "resource.source") is None
    assert state_of(registry, PLUGIN_ID) == "stopped"

    assert registry.provider(PLUGIN_ID, "resource.source") is not None
    assert state_of(registry, PLUGIN_ID) == "running"
    assert registry.running_provider(PLUGIN_ID, "resource.source") is not None


def test_plugin_center_requires_admin(client: TestClient, admin_tokens: dict[str, str]) -> None:
    from tests.conftest import bearer, create_user, login

    user = create_user(client, admin_tokens)
    tokens = login(client, str(user["username"]), "alice-password")
    response = client.get("/api/admin/plugins", headers=bearer(tokens))
    assert response.status_code == 403


def test_slow_plugin_start_does_not_block_other_plugins(
    database: Database, clock: FakeClock, env: PluginEnvironment
) -> None:
    install_fake_plugin(env.root, FakePluginSpec("example.slow", "慢插件", ()))
    install_fake_plugin(env.root, FakePluginSpec("example.fast", "快插件", ()))
    supervisor = PluginSupervisor(
        env.root,
        env.root.parent / "data",
        media_dir=env.root.parent / "media",
        timeouts={"initialize": 10.0},
        backoff_initial=0.01,
        backoff_max=0.02,
    )
    supervisor.discover()
    started = threading.Event()

    def start_slow() -> None:
        started.set()
        supervisor.start("example.slow")

    thread = threading.Thread(target=start_slow, daemon=True)
    thread.start()
    assert started.wait(2)
    time.sleep(0.1)
    before = time.monotonic()
    supervisor.start("example.fast")
    assert time.monotonic() - before < 0.5
    thread.join(timeout=5)
    assert supervisor.is_running("example.slow")
    assert supervisor.is_running("example.fast")
    supervisor.shutdown()


def test_resource_changed_accepts_source_owned_by_the_plugin(
    registry: PluginRegistry, database: Database
) -> None:
    assert registry.provider(PLUGIN_ID, "resource.source") is not None
    source_id = insert_source(database, PLUGIN_ID)
    changed: list[tuple[int, list[str]]] = []
    registry.set_resource_changed_handler(
        lambda source, locators: changed.append((source, locators))
    )

    registry.resource_changed(PLUGIN_ID, source_id, ["a", "b"])

    assert changed == [(source_id, ["a", "b"])]


def test_resource_changed_rejects_source_of_another_plugin(
    registry: PluginRegistry,
    database: Database,
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert registry.provider(PLUGIN_ID, "resource.source") is not None
    insert_legacy_plugin(database, "example.other")
    source_id = insert_source(database, "example.other")
    changed: list[tuple[int, list[str]]] = []
    registry.set_resource_changed_handler(
        lambda source, locators: changed.append((source, locators))
    )
    caplog.set_level("WARNING", logger="app.plugins.registry")

    registry.resource_changed(PLUGIN_ID, source_id, ["a"])

    assert changed == []
    assert "属于插件 example.other" in caplog.text


def test_resource_changed_rejects_plugin_without_changes_capability(
    database: Database,
    clock: FakeClock,
    env: PluginEnvironment,
    caplog: pytest.LogCaptureFixture,
) -> None:
    plugin_id = "example.nochanges"
    install_fake_plugin(
        env.root,
        FakePluginSpec(
            plugin_id,
            "无变更插件",
            ("resource.source",),
            capabilities=("logger",),
            mode="no_changes",
        ),
    )
    supervisor = PluginSupervisor(
        env.root,
        env.root.parent / "data",
        media_dir=env.root.parent / "media",
        backoff_initial=0.01,
        backoff_max=0.02,
    )
    registry = PluginRegistry(database, clock, supervisor)
    assert registry.provider(plugin_id, "resource.source") is not None
    source_id = insert_source(database, plugin_id)
    changed: list[tuple[int, list[str]]] = []
    registry.set_resource_changed_handler(
        lambda source, locators: changed.append((source, locators))
    )
    caplog.set_level("WARNING", logger="app.plugins.registry")

    registry.resource_changed(plugin_id, source_id, ["a"])

    assert changed == []
    assert "没有声明 changes 能力" in caplog.text
    supervisor.shutdown()
