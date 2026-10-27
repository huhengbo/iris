from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.main import create_app
from tests.fake_cloud115 import FakeCloud115
from tests.fake_tmdb import FakeTmdb
from tests.plugin_env import (
    BUILTIN_SPECS,
    PluginEnvironment,
    bind_controls,
    control_for,
)

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin-password-for-tests"

DEFAULT_PLUGINS = ("source.115", "builtin.tmdb")


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.current = start or datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, **kwargs: float) -> None:
        self.current += timedelta(**kwargs)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def plugin_env(tmp_path: Path) -> PluginEnvironment:
    root = tmp_path / "plugins"
    root.mkdir()
    environment = PluginEnvironment(root)
    for plugin_id in DEFAULT_PLUGINS:
        environment.install(BUILTIN_SPECS[plugin_id])
    return environment


@pytest.fixture
def config(tmp_path: Path, plugin_env: PluginEnvironment) -> AppConfig:
    return AppConfig(
        data_dir=tmp_path / "data",
        secret_key="x" * 48,
        admin_username=ADMIN_USERNAME,
        admin_password=ADMIN_PASSWORD,
        static_dir=tmp_path / "static",
        plugins_dir=plugin_env.root,
    )


class SyncRunner:
    """代替后台线程执行同步任务，并让任务里的等待只推进假时钟。

    默认在触发同步的请求里同步执行完；deferred 为 True 时先攒着，由测试调用 run_pending。
    on_sleep 在每次等待时调用，可以在任务暂停的时刻检查状态。
    """

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.deferred = False
        self.sleeps: list[float] = []
        self.on_sleep: Callable[[float], None] | None = None
        self._pending: list[Callable[[], None]] = []

    def execute(self, job: Callable[[], None]) -> None:
        if self.deferred:
            self._pending.append(job)
        else:
            job()

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if self.on_sleep is not None:
            self.on_sleep(seconds)
        self.clock.advance(seconds=seconds)

    def run_pending(self) -> None:
        while self._pending:
            self._pending.pop(0)()


@pytest.fixture
def sync_runner(clock: FakeClock) -> SyncRunner:
    return SyncRunner(clock)


def make_app(
    config: AppConfig,
    clock: FakeClock,
    runner: SyncRunner,
) -> FastAPI:
    """测试不启动后台线程；插件仍然从 config.plugins_dir 以真实子进程运行。"""
    return create_app(
        config,
        clock,
        sync_sleeper=runner.sleep,
        sync_executor=runner.execute,
        delete_executor=runner.execute,
        background_tasks=False,
    )


@pytest.fixture
def client(
    config: AppConfig,
    clock: FakeClock,
    sync_runner: SyncRunner,
    plugin_env: PluginEnvironment,
) -> Iterator[TestClient]:
    with TestClient(make_app(config, clock, sync_runner)) as test_client:
        bind_controls(plugin_env.root, test_client)
        yield test_client


@pytest.fixture
def cloud115(client: TestClient, config: AppConfig) -> FakeCloud115:
    return FakeCloud115(control_for(config.plugins_dir, "source.115"))


@pytest.fixture
def tmdb(client: TestClient, config: AppConfig) -> FakeTmdb:
    return FakeTmdb(control_for(config.plugins_dir, "builtin.tmdb"))


def login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    body: dict[str, str] = response.json()
    return body


def bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.fixture
def admin_tokens(client: TestClient) -> dict[str, str]:
    return login(client, ADMIN_USERNAME, ADMIN_PASSWORD)


def create_user(
    client: TestClient,
    admin_tokens: dict[str, str],
    username: str = "alice",
    password: str = "alice-password",
    role: str = "user",
) -> dict[str, object]:
    response = client.post(
        "/api/admin/users",
        json={"username": username, "password": password, "role": role},
        headers=bearer(admin_tokens),
    )
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


def cloud115_plugin_headers(client: TestClient, headers: dict[str, str]) -> dict[str, str]:
    """申请 source.115 的插件作用域令牌。"""
    response = client.post("/api/admin/plugins/source.115/session", headers=headers)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def save_cloud115_cookie(
    client: TestClient, headers: dict[str, str], cookie: str = "UID=test"
) -> Any:
    from tests.fake_cloud115 import VALID_COOKIE

    plugin = cloud115_plugin_headers(client, headers)
    return client.put(
        "/api/plugins/source.115/cookie",
        json={"cookie": cookie or VALID_COOKIE},
        headers=plugin,
    )


@pytest.fixture
def admin(client: TestClient, admin_tokens: dict[str, str]) -> dict[str, str]:
    """管理员请求头；来源账号由 fake 插件自己维护，不需要宿主设置。"""
    return bearer(admin_tokens)


def tmdb_plugin_headers(client: TestClient, admin_headers: dict[str, str]) -> dict[str, str]:
    """打开 builtin.tmdb 插件页的会话，返回调用插件接口的 Bearer 头。"""
    response = client.post("/api/admin/plugins/builtin.tmdb/session", headers=admin_headers)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def save_tmdb_key(client: TestClient, admin_headers: dict[str, str], key: str = "k") -> Any:
    """通过插件设置页保存 TMDB Key；fake 插件接受任意值并清除未配置状态。"""
    response = client.put(
        "/api/plugins/builtin.tmdb/settings",
        json={"api_key": key},
        headers=tmdb_plugin_headers(client, admin_headers),
    )
    assert response.status_code == 200, response.text
    config = client.app.state.container.config  # type: ignore[attr-defined]
    control_for(config.plugins_dir, "builtin.tmdb").call("metadata", "set_reason", {"reason": None})
    return response.json()


def ensure_cloud115_source(client: TestClient, headers: dict[str, str]) -> int:
    """按需创建 source.115 的来源实例。"""
    sources = client.get("/api/admin/resources", headers=headers).json()
    for source in sources:
        if source["plugin_id"] == "source.115":
            return int(source["id"])
    response = client.post(
        "/api/admin/resources",
        json={"plugin_id": "source.115", "name": "115 网盘"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def cloud115_roots(client: TestClient, *cids: str) -> list[dict[str, Any]]:
    """把来源节点 ID 写成建库、改库请求里的 roots。"""
    headers = bearer(login(client, ADMIN_USERNAME, ADMIN_PASSWORD))
    source_id = ensure_cloud115_source(client, headers)
    return [{"source_id": source_id, "locator": cid} for cid in cids]


def fake_control(config: AppConfig, plugin_id: str) -> Any:
    return control_for(config.plugins_dir, plugin_id)
