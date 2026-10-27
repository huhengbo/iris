"""Fanart.tv 插件设置页字段和状态。"""

from __future__ import annotations

from typing import Any

from iris_plugin_sdk import SettingField, SettingsEndpoint, SettingsStore

from iris_plugin_fanart.provider import API_KEY_SECRET, CLIENT_KEY_SECRET


def fanart_settings(store: SettingsStore) -> SettingsEndpoint:
    return SettingsEndpoint(
        (
            SettingField(
                API_KEY_SECRET,
                "API Key",
                "secret",
                help="在 Fanart.tv 个人页面申请；保存在插件自己的数据目录，不会回显。",
                required=True,
                max_length=1024,
            ),
            SettingField(
                CLIENT_KEY_SECRET,
                "个人 Key",
                "secret",
                help="可选。Fanart.tv 的个人 API Key，部分接口需要它。",
                max_length=1024,
            ),
        ),
        store=store,
    )


def fanart_status(settings: SettingsEndpoint) -> dict[str, Any]:
    values = settings.values()
    return {
        "api_key_set": bool(values.get(API_KEY_SECRET)),
        "client_key_set": bool(values.get(CLIENT_KEY_SECRET)),
    }
