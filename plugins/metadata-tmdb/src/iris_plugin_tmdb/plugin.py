"""TMDB 插件入口。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from iris_plugin_sdk import (
    DirectHttpClient,
    JsonSettingsStore,
    MetadataUnavailable,
    PluginContext,
    RateLimiter,
)

from iris_plugin_tmdb.admin import TmdbAdminUi, tmdb_settings
from iris_plugin_tmdb.errors import TmdbAuthFailed
from iris_plugin_tmdb.gateway import TmdbGateway
from iris_plugin_tmdb.http import TIMEOUT_SECONDS, TmdbHttpGateway
from iris_plugin_tmdb.provider import METADATA_CAPABILITIES, TmdbMetadataProvider
from iris_plugin_tmdb.service import API_KEY_SECRET, DEFAULT_INTERVAL_SECONDS, TmdbService
from iris_plugin_tmdb.validation import ValidationStore


class TmdbPlugin:
    def __init__(self, gateway: TmdbGateway | None = None) -> None:
        self._gateway = gateway
        self._http: DirectHttpClient | None = None
        self._provider: TmdbMetadataProvider | None = None
        self._admin: TmdbAdminUi | None = None

    def start(self, context: PluginContext) -> None:
        gateway = self._gateway
        if gateway is None:
            self._http = DirectHttpClient(context.proxy, timeout=TIMEOUT_SECONDS)
            gateway = TmdbHttpGateway(self._http)
        settings = tmdb_settings(JsonSettingsStore(context.data_dir))
        service = TmdbService(
            gateway,
            RateLimiter(DEFAULT_INTERVAL_SECONDS),
            settings,
            ValidationStore(context.data_dir),
        )
        settings.bind_tester(lambda values: _test_connection(service, values))
        self._provider = TmdbMetadataProvider(service, context.plugin_id)
        self._admin = TmdbAdminUi(service, settings)
        context.logger.info("TMDB 插件已准备")

    def stop(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None
        self._provider = None
        self._admin = None

    def config_changed(self, *, proxy: str | None, host_url: str | None) -> None:
        if self._http is not None:
            self._http.config_changed(proxy=proxy)

    def providers(self) -> Mapping[str, object]:
        if self._provider is None or self._admin is None:
            return {}
        providers: dict[str, object] = dict.fromkeys(
            (*METADATA_CAPABILITIES, "image.fetch"), self._provider
        )
        providers["admin.ui"] = self._admin
        return providers


def _test_connection(service: TmdbService, values: dict[str, Any]) -> str | None:
    key = values.get(API_KEY_SECRET)
    if not isinstance(key, str) or not key.strip():
        return "请填写 API Key"
    try:
        service.validate(key.strip())
    except TmdbAuthFailed:
        return "API Key 无效或已被吊销，请重新填写"
    except MetadataUnavailable as exc:
        return exc.message
    return None
