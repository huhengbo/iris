"""Fanart.tv 图片插件入口。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from iris_plugin_sdk import JsonSettingsStore, PluginContext, SettingsAdminUi

from iris_plugin_fanart.admin import fanart_settings, fanart_status
from iris_plugin_fanart.provider import FanartProvider

PLUGIN_DIR = Path(__file__).resolve().parents[2]


class FanartPlugin:
    def __init__(self) -> None:
        self._provider: FanartProvider | None = None
        self._admin: SettingsAdminUi | None = None

    def start(self, context: PluginContext) -> None:
        if context.http is None:
            raise ValueError("Fanart.tv 插件缺少 http 能力")
        settings = fanart_settings(JsonSettingsStore(context.data_dir))
        provider = FanartProvider(context.http, settings, context.logger, context.plugin_id)
        settings.bind_tester(provider.test_connection)
        self._provider = provider
        self._admin = SettingsAdminUi(
            settings,
            PLUGIN_DIR / "ui",
            status=lambda: fanart_status(settings),
        )
        context.logger.info("Fanart.tv 插件已准备")

    def stop(self) -> None:
        self._provider = None
        self._admin = None

    def providers(self) -> Mapping[str, object]:
        if self._provider is None or self._admin is None:
            return {}
        return {"image.fetch": self._provider, "admin.ui": self._admin}
