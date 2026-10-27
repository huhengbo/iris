"""Fanart.tv 图片获取器。

只按外部 ID 取图：电影用 TMDB 或 IMDb，剧集只认 TVDB（Fanart 的剧集接口没有别的入口）。
没有 TVDB 的剧集直接返回空列表，不发请求。所有请求都走宿主的 http 能力。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from iris_plugin_sdk import (
    HostCallError,
    ImageLookupRequest,
    ImageType,
    MetadataNotFound,
    MetadataUnavailable,
    PluginHttpClient,
    PluginHttpError,
    PluginLogger,
    RemoteImage,
    SettingsEndpoint,
    json_object,
    raise_for_status,
)

API_KEY_SECRET = "api_key"
CLIENT_KEY_SECRET = "client_key"
BASE_URL = "https://webservice.fanart.tv/v3"
# 测试连接用一个固定的电影，Fanart 对有效 Key 的未知条目也会返回 404 或空结果
TEST_MOVIE = "550"
NO_KEY_MESSAGE = "还没有填写 Fanart.tv API Key，在插件 › Fanart.tv 里填写"
_API_KEY_INVALID = "Fanart.tv API Key 无效，请重新填写"
_IMDB_ID = re.compile(r"tt\d+")

_MOVIE_KEYS: dict[str, ImageType] = {
    "movieposter": "poster",
    "moviebackground": "backdrop",
    "moviethumb": "thumb",
    "hdmovielogo": "logo",
    "movielogo": "logo",
}
_TV_KEYS: dict[str, ImageType] = {
    "tvposter": "poster",
    "showbackground": "backdrop",
    "tvthumb": "thumb",
    "hdtvlogo": "logo",
    "clearlogo": "logo",
}


class FanartProvider:
    def __init__(
        self,
        http: PluginHttpClient,
        settings: SettingsEndpoint,
        logger: PluginLogger,
        plugin_id: str,
    ) -> None:
        self.id = plugin_id
        self._http = http
        self._settings = settings
        self._logger = logger

    # ---- 宿主协议 ----

    def unavailable_reason(self) -> str | None:
        return None if self._api_key() is not None else NO_KEY_MESSAGE

    def images(self, request: ImageLookupRequest) -> list[RemoteImage]:
        api_key = self._api_key()
        if api_key is None:
            raise MetadataUnavailable(NO_KEY_MESSAGE)
        external_ids = {scheme.lower(): value for scheme, value in request.external_ids.items()}
        if request.kind == "series":
            tvdb_id = external_ids.get("tvdb")
            if not tvdb_id or not tvdb_id.isdigit():
                # 剧集只接受 TVDB ID；没有时不能按片名猜
                return []
            path = f"/tv/{tvdb_id}"
            keys = _TV_KEYS
        else:
            tmdb_id = external_ids.get("tmdb")
            if tmdb_id and tmdb_id.isdigit():
                path = f"/movies/{tmdb_id}"
            else:
                imdb_id = external_ids.get("imdb")
                if not imdb_id:
                    return []
                if _IMDB_ID.fullmatch(imdb_id) is None:
                    raise MetadataNotFound("Fanart.tv 上没有这个条目")
                path = f"/movies/{imdb_id}"
            keys = _MOVIE_KEYS
        payload = self._get(path, api_key)
        if payload is None:
            return []
        return _images(payload, keys)

    def image(self, source: str, width: int) -> bytes:
        try:
            response = self._http.get(source, headers={"Accept": "image/*"})
        except (PluginHttpError, HostCallError) as exc:
            raise MetadataUnavailable("下载 Fanart.tv 图片失败，请稍后再试") from exc
        if response.status_code == 404:
            raise MetadataNotFound("Fanart.tv 上没有这张图")
        if response.status_code in (401, 403):
            raise MetadataUnavailable(_API_KEY_INVALID)
        raise_for_status(response, service="Fanart.tv")
        if not response.content:
            raise MetadataNotFound("Fanart.tv 上没有这张图")
        return bytes(response.content)

    def test_connection(self, values: Mapping[str, Any] | None = None) -> str | None:
        merged = {**self._settings.values(), **(values or {})}
        api_key = _text(merged.get(API_KEY_SECRET))
        if api_key is None:
            return "请填写 API Key"
        try:
            response = self._http.get(
                f"{BASE_URL}/movies/{TEST_MOVIE}",
                headers=_headers(api_key, _text(merged.get(CLIENT_KEY_SECRET))),
            )
            if response.status_code in (401, 403):
                return "API Key 无效，请重新填写"
            # 404 说明 Key 有效，只是这个条目没有图
            if response.status_code == 404:
                return None
            raise_for_status(response, service="Fanart.tv")
        except MetadataUnavailable as exc:
            return exc.message
        except (PluginHttpError, HostCallError) as exc:
            self._logger.warning("Fanart.tv 测试连接失败：%s", type(exc).__name__)
            return "连接 Fanart.tv 失败，请稍后再试"
        return None

    # ---- HTTP ----

    def _get(self, path: str, api_key: str) -> dict[str, Any] | None:
        try:
            response = self._http.get(
                f"{BASE_URL}{path}", headers=_headers(api_key, self._client_key())
            )
        except (PluginHttpError, HostCallError) as exc:
            raise MetadataUnavailable("连接 Fanart.tv 失败，请检查网络或代理") from exc
        # 404 表示这个条目没有图，是正常结果
        if response.status_code == 404:
            return None
        if response.status_code in (401, 403):
            raise MetadataUnavailable(_API_KEY_INVALID)
        raise_for_status(response, service="Fanart.tv")
        return json_object(response, service="Fanart.tv")

    def _api_key(self) -> str | None:
        return _text(self._settings.values().get(API_KEY_SECRET))

    def _client_key(self) -> str | None:
        return _text(self._settings.values().get(CLIENT_KEY_SECRET))


def _images(payload: Mapping[str, Any], keys: Mapping[str, ImageType]) -> list[RemoteImage]:
    result: list[RemoteImage] = []
    seen: set[tuple[str, str]] = set()
    for key, image_type in keys.items():
        items = payload.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            url = _text(item.get("url"))
            if url is None or (image_type, url) in seen:
                continue
            seen.add((image_type, url))
            result.append(
                RemoteImage(
                    type=image_type,
                    source=url,
                    language=_language(item.get("lang")),
                    score=_score(item.get("likes")),
                    width=_integer(item.get("width")),
                    height=_integer(item.get("height")),
                )
            )
    return result


def _language(value: Any) -> str | None:
    lang = _text(value)
    if lang is None or lang == "00":
        return None
    return lang.lower()


def _score(value: Any) -> float | None:
    likes = _integer(value)
    return None if likes is None else float(likes)


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _headers(api_key: str, client_key: str | None) -> dict[str, str]:
    # Key 只放请求头：查询参数会随完整 URL 出现在日志里
    headers = {"Accept": "application/json", "api-key": api_key}
    if client_key is not None:
        headers["client-key"] = client_key
    return headers
