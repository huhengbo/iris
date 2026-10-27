"""TMDB 请求的出口：读取插件自己的 Key、限速、记录 Key 的校验结果。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from iris_plugin_sdk import RateLimiter, SettingsEndpoint

from iris_plugin_tmdb.errors import (
    TmdbAuthFailed,
    TmdbNotConfigured,
    TmdbNotFound,
    TmdbUnavailable,
)
from iris_plugin_tmdb.gateway import (
    TmdbAccess,
    TmdbDetails,
    TmdbGateway,
    TmdbImage,
    TmdbKind,
    TmdbPerson,
    TmdbSearchResult,
    TmdbSeason,
)
from iris_plugin_tmdb.validation import (
    TmdbValidation,
    ValidationStore,
)

# TMDB 的上限大约每秒 40–50 次，这里留足余量
DEFAULT_INTERVAL_SECONDS = 0.1
# 插件 state 里保存 API Key 的名字，与设置页的字段一致
API_KEY_SECRET = "api_key"


@dataclass(frozen=True)
class TmdbStatus:
    """设置页和 provider 共用的当前状态；没有 Key 时不展示旧的校验结果。"""

    api_key_set: bool
    validation: TmdbValidation | None


class TmdbService:
    """所有 TMDB 请求的出口。

    Key 来自插件自己的 settings.json；HTTP 由 TmdbHttpGateway 发出，保留 0.1s 优先级
    限速和 429 重试。
    """

    def __init__(
        self,
        gateway: TmdbGateway,
        limiter: RateLimiter,
        settings: SettingsEndpoint,
        validation: ValidationStore,
    ) -> None:
        self._gateway = gateway
        self._limiter = limiter
        self._settings = settings
        self._validation = validation

    def status(self) -> TmdbStatus:
        if self._key() is None:
            return TmdbStatus(False, None)
        return TmdbStatus(True, self._validation.get())

    def validation(self) -> TmdbValidation | None:
        return self.status().validation

    def validate(self, key: str) -> None:
        """用还没保存的 Key 试连一次。"""
        self._limiter.acquire()
        self._gateway.validate(TmdbAccess(key))

    def clear_validation(self) -> None:
        """Key 变化后清掉旧结果，避免页面继续显示上一个 Key 的校验状态。"""
        self._validation.clear()

    def search(self, kind: TmdbKind, query: str) -> list[TmdbSearchResult]:
        return self._call(lambda access: self._gateway.search(access, kind, query))

    def find_imdb(self, kind: TmdbKind, imdb_id: str) -> int | None:
        return self._call(lambda access: self._gateway.find_imdb(access, kind, imdb_id))

    def details(self, kind: TmdbKind, tmdb_id: int) -> TmdbDetails:
        return self._call(lambda access: self._gateway.details(access, kind, tmdb_id))

    def images(self, kind: TmdbKind, tmdb_id: int) -> list[TmdbImage]:
        return self._call(lambda access: self._gateway.images(access, kind, tmdb_id))

    def season(self, tmdb_id: int, number: int) -> TmdbSeason:
        return self._call(lambda access: self._gateway.season(access, tmdb_id, number))

    def person(self, tmdb_id: int) -> TmdbPerson:
        return self._call(lambda access: self._gateway.person(access, tmdb_id))

    def image(self, path: str, width: int) -> bytes:
        return self._call(lambda access: self._gateway.image(access, path, width))

    def _call[T](self, call: Callable[[TmdbAccess], T]) -> T:
        key = self._key()
        if key is None:
            raise TmdbNotConfigured()
        self._limiter.acquire()
        # 请求的实际结果同步到 Key 的校验状态：401 记无效，连不上记不可达，其余说明 Key 可用
        try:
            result = call(TmdbAccess(key))
        except TmdbAuthFailed:
            self._validation.record("invalid")
            raise
        except TmdbUnavailable:
            self._validation.record("unreachable")
            raise
        except TmdbNotFound:
            self._validation.record("ok")
            raise
        self._validation.record("ok")
        return result

    def _key(self) -> str | None:
        value = self._settings.values().get(API_KEY_SECRET)
        return value.strip() if isinstance(value, str) and value.strip() else None


__all__ = ["API_KEY_SECRET", "DEFAULT_INTERVAL_SECONDS", "TmdbService", "TmdbStatus"]
