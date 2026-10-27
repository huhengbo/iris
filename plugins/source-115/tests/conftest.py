"""插件测试的公共夹具：假时钟、假网关、假宿主通知和直接调用入口。"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from iris_plugin_sdk import (
    HostInfo,
    PluginContext,
    PluginRequest,
    PluginResponse,
)

from iris_source_115.admin import Cloud115AdminUi
from iris_source_115.credential import CredentialStore
from iris_source_115.life import LifeMonitor
from iris_source_115.p115 import P115Gateway
from iris_source_115.plugin import Source115Plugin
from iris_source_115.provider import Cloud115ResourceProvider
from iris_source_115.scheduler import (
    ApiGroup,
    CircuitBreaker,
    RateLimiter,
    RequestScheduler,
)
from iris_source_115.service import Cloud115Service
from tests.fake_gateway import FakeCloud115


class FakeClock:
    def __init__(self, current: datetime | None = None) -> None:
        self.current = current or datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, **kwargs: float) -> None:
        self.current += timedelta(**kwargs)


class FakeNotifier:
    """记录插件发给宿主的通知；对应 SDK 的 PluginNotifier。"""

    def __init__(self) -> None:
        self.changes: list[tuple[int, list[str]]] = []
        self.progress_events: list[tuple[Any, str]] = []
        self.heartbeats = 0

    def resource_changed(self, source_id: int, locators: Sequence[str]) -> None:
        self.changes.append((source_id, list(locators)))

    def progress(self, until: Any, reason: str) -> None:
        self.progress_events.append((until, reason))

    def heartbeat(self) -> None:
        self.heartbeats += 1


class FakeLogger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def debug(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.messages.append(("debug", message))

    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.messages.append(("info", message))

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.messages.append(("warning", message))

    def error(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.messages.append(("error", message))

    def exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.messages.append(("exception", message))


@dataclass
class Harness:
    plugin: Source115Plugin
    provider: Cloud115ResourceProvider
    admin: Cloud115AdminUi
    life: LifeMonitor
    gateway: FakeCloud115
    clock: FakeClock
    notifier: FakeNotifier
    tmp_path: Path
    data_dir: Path
    context: PluginContext
    logger: FakeLogger = field(default_factory=FakeLogger)

    @property
    def state_dir(self) -> Path:
        return self.data_dir / "state"

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        query: dict[str, str] | None = None,
    ) -> PluginResponse:
        return self.admin.handle(
            PluginRequest(
                method=method,
                path=path,
                query=query or {},
                body=body,
                user_id=1,
            )
        )

    def state(self, name: str) -> dict[str, Any]:
        import json

        value = json.loads((self.state_dir / name).read_text(encoding="utf-8"))
        assert isinstance(value, dict)
        return value


def make_harness(
    tmp_path: Path,
    *,
    gateway: FakeCloud115 | None = None,
    clock: FakeClock | None = None,
    data_dir: Path | None = None,
    life_tick_seconds: float = 3600,
    sleeper: Any = None,
) -> Harness:
    clock = clock or FakeClock()
    gateway = gateway or FakeCloud115(clock)
    data_dir = data_dir or tmp_path / "data"
    notifier = FakeNotifier()
    logger = FakeLogger()
    plugin = Source115Plugin(
        gateway=gateway,
        clock=clock,
        sleeper=sleeper or (lambda _seconds: None),
        life_tick_seconds=life_tick_seconds,
        request_interval_seconds=0,
    )
    context = PluginContext(
        plugin_id="source.115",
        logger=logger,
        data_dir=data_dir,
        host=HostInfo(version="test", url="http://host.test"),
        media_dir=tmp_path / "media",
        notify=notifier,
    )
    plugin.start(context)
    providers = plugin.providers()
    provider = providers["resource.source"]
    admin = providers["admin.ui"]
    assert isinstance(provider, Cloud115ResourceProvider)
    assert isinstance(admin, Cloud115AdminUi)
    return Harness(
        plugin,
        provider,
        admin,
        admin.life,
        gateway,
        clock,
        notifier,
        tmp_path,
        data_dir,
        context,
        logger,
    )


def restart(harness: Harness, gateway: FakeCloud115 | None = None) -> Harness:
    """用同一个 data_dir 重启插件，验证状态是否落盘。"""
    harness.plugin.stop()
    return make_harness(
        harness.tmp_path,
        gateway=gateway or FakeCloud115(harness.clock),
        clock=harness.clock,
        data_dir=harness.data_dir,
        life_tick_seconds=3600,
    )


@dataclass
class Stack:
    service: Cloud115Service
    provider: Cloud115ResourceProvider
    gateway: FakeCloud115
    clock: FakeClock
    credentials: CredentialStore
    sleeper: list[float] = field(default_factory=list)


def make_stack(
    tmp_path: Path,
    gateway: FakeCloud115 | None = None,
    clock: FakeClock | None = None,
    progress: Callable[[Any, str], None] | None = None,
    heartbeat: Callable[[], None] | None = None,
) -> Stack:
    """只装配来源层，不启动插件线程；供 provider 和契约测试直接调用。"""
    clock = clock or FakeClock()
    gateway = gateway or FakeCloud115(clock)
    state = tmp_path / "data" / "state"
    credentials = CredentialStore(state / "credential.json", clock)
    breakers = {
        group: CircuitBreaker(clock, state / f"breaker-{group}.json", group) for group in ApiGroup
    }
    scheduler = RequestScheduler(RateLimiter(0), breakers)
    service = Cloud115Service(gateway, scheduler, credentials, clock)
    sleeps: list[float] = []
    provider = Cloud115ResourceProvider(
        service,
        clock,
        "source.115",
        sleeper=sleeps.append,
        progress=progress,
        heartbeat=heartbeat,
    )
    return Stack(service, provider, gateway, clock, credentials, sleeps)


def save_cookie(harness: Harness, cookie: str | None = None) -> PluginResponse:
    from tests.fake_gateway import VALID_COOKIE

    return harness.request("PUT", "cookie", {"cookie": cookie or VALID_COOKIE})


def update_settings(harness: Harness, body: dict[str, Any]) -> PluginResponse:
    return harness.request("PUT", "settings", body)


def status_body(harness: Harness) -> dict[str, Any]:
    body = harness.request("GET", "status").body
    assert isinstance(body, dict)
    return body


@pytest.fixture
def deferred(monkeypatch: pytest.MonkeyPatch) -> list[tuple[float, Any]]:
    """副本的延迟删除先攒着，由测试手动执行。"""
    from iris_source_115 import copies

    actions: list[tuple[float, Any]] = []
    monkeypatch.setattr(copies, "_defer", lambda delay, action: actions.append((delay, action)))
    return actions


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def harness(tmp_path: Path, clock: FakeClock) -> Iterator[Harness]:
    value = make_harness(tmp_path, clock=clock)
    try:
        yield value
    finally:
        value.plugin.stop()


@pytest.fixture
def real_gateway() -> Iterator[Callable[..., P115Gateway]]:
    """创建真实网关，测试结束时关闭连接池，避免解释器退出时才被回收。"""
    gateways: list[P115Gateway] = []

    def make(proxy: str | None = None) -> P115Gateway:
        gateway = P115Gateway(proxy)
        gateways.append(gateway)
        return gateway

    yield make
    for gateway in gateways:
        gateway.close()
