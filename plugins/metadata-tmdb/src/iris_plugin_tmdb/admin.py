"""TMDB 插件设置页：API Key 存插件 state，连接状态来自最近的请求结果。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from iris_plugin_sdk import (
    SettingField,
    SettingsAdminUi,
    SettingsEndpoint,
    SettingsStore,
)

from iris_plugin_tmdb.service import API_KEY_SECRET, TmdbService
from iris_plugin_tmdb.validation import validation_problem

PLUGIN_DIR = Path(__file__).resolve().parents[2]


def tmdb_settings(store: SettingsStore) -> SettingsEndpoint:
    return SettingsEndpoint(
        (
            SettingField(
                API_KEY_SECRET,
                "API Key",
                "secret",
                help="TMDB v3 的 API Key 或 v4 的读取令牌都可以；"
                "保存在插件自己的数据目录，不会回显。",
                max_length=1024,
            ),
        ),
        store=store,
    )


class TmdbAdminUi(SettingsAdminUi):
    """设置型管理页；status 只展示当前 Key 的校验结果。"""

    def __init__(self, tmdb: TmdbService, settings: SettingsEndpoint) -> None:
        super().__init__(settings, PLUGIN_DIR / "ui", status=self._status)
        self._tmdb = tmdb
        self._endpoint = settings

    def settings_updated(self, before: Mapping[str, Any]) -> None:
        if before.get(API_KEY_SECRET) != self._endpoint.values().get(API_KEY_SECRET):
            # 旧 Key 的校验结果不能代表新 Key
            self._tmdb.clear_validation()

    def _status(self) -> dict[str, Any]:
        status = self._tmdb.status()
        validation = status.validation
        return {
            "api_key_set": status.api_key_set,
            "validation": (
                None
                if validation is None
                else {
                    "status": validation.status,
                    "checked_at": validation.checked_at,
                    "message": validation_problem(validation) or "连接正常",
                }
            ),
        }
