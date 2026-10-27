"""豆瓣插件设置页字段和状态。"""

from __future__ import annotations

from typing import Any

from iris_plugin_sdk import SettingField, SettingsEndpoint, SettingsStore

from iris_plugin_douban.provider import (
    COOKIE_SECRET,
    DEFAULT_INTERVAL,
    DEFAULT_USER_AGENT,
    INTERVAL_FIELD,
    USER_AGENT_FIELD,
)


def douban_settings(store: SettingsStore) -> SettingsEndpoint:
    return SettingsEndpoint(
        (
            SettingField(
                COOKIE_SECRET,
                "Cookie",
                "secret",
                help="可选。遇到 403 或「检测到有异常请求」时，粘贴浏览器里"
                " movie.douban.com 的 Cookie。",
                max_length=4096,
            ),
            SettingField(
                USER_AGENT_FIELD,
                "User-Agent",
                "text",
                help="豆瓣会拒绝默认的脚本 User-Agent，一般保持默认即可。",
                required=True,
                default=DEFAULT_USER_AGENT,
                max_length=512,
            ),
            SettingField(
                INTERVAL_FIELD,
                "最小请求间隔（秒）",
                "number",
                help="两次豆瓣页面请求之间至少等待的时间；交互请求会优先于后台同步。",
                required=True,
                default=DEFAULT_INTERVAL,
                min=0,
                max=60,
            ),
        ),
        store=store,
    )


def douban_status(settings: SettingsEndpoint) -> dict[str, Any]:
    values = settings.values()
    return {
        "cookie_set": bool(values.get(COOKIE_SECRET)),
        "user_agent": values.get(USER_AGENT_FIELD),
        "request_interval": values.get(INTERVAL_FIELD),
    }
