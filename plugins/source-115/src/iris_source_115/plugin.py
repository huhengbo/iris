"""115 插件入口：在自己的进程里装配来源、账号页和生活事件监听。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from iris_plugin_sdk import PluginContext, load_manifest

from iris_source_115.admin import Cloud115AdminUi, cloud115_settings
from iris_source_115.clock import Clock, SystemClock
from iris_source_115.credential import CredentialStore
from iris_source_115.gateway import Cloud115Gateway
from iris_source_115.life import TICK_SECONDS, LifeMonitor
from iris_source_115.p115 import P115Gateway
from iris_source_115.provider import Cloud115ResourceProvider
from iris_source_115.scheduler import (
    DEFAULT_INTERVAL_SECONDS,
    ApiGroup,
    CircuitBreaker,
    RateLimiter,
    RequestScheduler,
)
from iris_source_115.service import Cloud115Service

PLUGIN_ROOT = Path(__file__).resolve().parents[2]


class Source115Plugin:
    """实现了 SDK Plugin 协议；所有状态都在 initialize 下发的 data_dir 里。"""

    def __init__(
        self,
        *,
        gateway: Cloud115Gateway | None = None,
        clock: Clock | None = None,
        sleeper: Callable[[float], None] | None = None,
        life_tick_seconds: float = TICK_SECONDS,
        request_interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        self._gateway = gateway
        self._clock = clock or SystemClock()
        self._sleeper = sleeper
        self._life_tick_seconds = life_tick_seconds
        self._request_interval_seconds = request_interval_seconds
        self._provider: Cloud115ResourceProvider | None = None
        self._admin: Cloud115AdminUi | None = None

    # ---- 生命周期 ----

    def start(self, context: PluginContext) -> None:
        manifest = load_manifest(PLUGIN_ROOT)
        if manifest.ui is None:
            raise ValueError("admin.ui 插件缺少页面目录")
        state = context.data_dir / "state"
        settings = cloud115_settings(context.data_dir)
        credentials = CredentialStore(state / "credential.json", self._clock)
        breakers = {
            group: CircuitBreaker(self._clock, state / f"breaker-{group}.json", group)
            for group in ApiGroup
        }
        scheduler = RequestScheduler(RateLimiter(self._request_interval_seconds), breakers)
        if self._gateway is None:
            self._gateway = P115Gateway(context.proxy)
        else:
            self._gateway.configure(context.proxy)
        service = Cloud115Service(self._gateway, scheduler, credentials, self._clock)
        provider = Cloud115ResourceProvider(
            service,
            self._clock,
            context.plugin_id,
            self._sleeper,
            progress=context.notify.progress,
            heartbeat=context.notify.heartbeat,
        )
        provider.use_same_playback(lambda: bool(settings.values()["same_playback"]))
        life = LifeMonitor(
            provider,
            state / "life.json",
            settings,
            self._clock,
            context.logger,
            tick_seconds=self._life_tick_seconds,
        )
        provider.set_open_hook(life.set_source_id)
        self._provider = provider
        self._admin = Cloud115AdminUi(
            service,
            settings,
            life,
            self._clock,
            ui_root=PLUGIN_ROOT / manifest.ui,
        )
        life.start(context.notify)

    def stop(self) -> None:
        admin = self._admin
        if admin is not None:
            admin.life.stop()
        if self._gateway is not None:
            self._gateway.close()
        self._admin = None
        self._provider = None

    def flush(self) -> None:
        admin = self._admin
        if admin is not None:
            admin.life.flush()

    def config_changed(self, *, proxy: str | None, host_url: str | None) -> None:
        """代理变化时重建 115 的直连连接池；本插件不反连宿主，忽略 host_url。"""
        if self._gateway is not None:
            self._gateway.configure(proxy)

    def providers(self) -> Mapping[str, object]:
        if self._provider is None or self._admin is None:
            return {}
        return {"resource.source": self._provider, "admin.ui": self._admin}
