"""豆瓣元数据补缺插件入口。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from iris_plugin_sdk import JsonSettingsStore, PluginContext, SettingsAdminUi

from iris_plugin_douban.admin import douban_settings, douban_status
from iris_plugin_douban.provider import DoubanProvider

PLUGIN_DIR = Path(__file__).resolve().parents[2]


class DoubanPlugin:
    def __init__(self) -> None:
        self._provider: DoubanProvider | None = None
        self._admin: SettingsAdminUi | None = None

    def start(self, context: PluginContext) -> None:
        if context.http is None:
            raise ValueError("豆瓣插件缺少 http 能力")
        settings = douban_settings(JsonSettingsStore(context.data_dir))
        provider = DoubanProvider(context.http, settings, context.plugin_id)
        settings.bind_tester(provider.test_connection)
        self._provider = provider
        self._admin = SettingsAdminUi(
            settings,
            PLUGIN_DIR / "ui",
            status=lambda: douban_status(settings),
        )
        context.logger.info("豆瓣插件已准备")

    def stop(self) -> None:
        self._provider = None
        self._admin = None

    def providers(self) -> Mapping[str, object]:
        if self._provider is None or self._admin is None:
            return {}
        return {
            "metadata.details": self._provider,
            "metadata.images": self._provider,
            "admin.ui": self._admin,
        }
