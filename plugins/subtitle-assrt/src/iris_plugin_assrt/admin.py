"""射手网 assrt 插件设置页字段和状态。"""

from __future__ import annotations

from typing import Any

from iris_plugin_sdk import SettingField, SettingsEndpoint, SettingsStore

from iris_plugin_assrt.provider import (
    DEFAULT_HOST,
    DEFAULT_RATE,
    HOST_FIELD,
    HOSTS,
    RATE_FIELD,
    TOKEN_SECRET,
)


def assrt_settings(store: SettingsStore) -> SettingsEndpoint:
    return SettingsEndpoint(
        (
            SettingField(
                TOKEN_SECRET,
                "API Token",
                "secret",
                help="在射手网个人中心获取；保存在插件自己的数据目录，不会回显。",
                required=True,
                max_length=1024,
            ),
            SettingField(
                HOST_FIELD,
                "API 地址",
                "select",
                help="官方地址连不上时可以换用备用地址。",
                required=True,
                default=DEFAULT_HOST,
                options=HOSTS,
            ),
            SettingField(
                RATE_FIELD,
                "每分钟请求数",
                "number",
                help="插件自己的限流；官方配额按 Token 和 IP 共享，建议按需设置。",
                required=True,
                default=DEFAULT_RATE,
                min=1,
                max=60,
            ),
        ),
        store=store,
    )


def assrt_status(settings: SettingsEndpoint) -> dict[str, Any]:
    values = settings.values()
    return {
        "token_set": bool(values.get(TOKEN_SECRET)),
        "host": values.get(HOST_FIELD),
        "requests_per_minute": values.get(RATE_FIELD),
    }
