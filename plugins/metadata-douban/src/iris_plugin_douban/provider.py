"""豆瓣补缺 provider。

豆瓣没有开放 API，只提供条目页。插件按 IMDb ID 从搜索建议里找候选，再拉条目页核对
页面上的 IMDb，避免把同名的另一部片子当成补缺来源。所有请求都走宿主的 http 能力，
Cookie 可选；遇到 403 或反爬页面时提示管理员填写 Cookie。
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from iris_plugin_sdk import (
    HostCallError,
    MetadataCandidate,
    MetadataKind,
    MetadataNotFound,
    MetadataRecord,
    MetadataSearchRequest,
    MetadataSeason,
    MetadataUnavailable,
    PluginHttpClient,
    PluginHttpError,
    PluginHttpResponse,
    RateLimiter,
    SettingsEndpoint,
    raise_for_status,
)

from iris_plugin_douban.parse import is_blocked, parse_subject, parse_suggest

COOKIE_SECRET = "cookie"
USER_AGENT_FIELD = "user_agent"
INTERVAL_FIELD = "request_interval"
BASE_URL = "https://movie.douban.com"
SUGGEST_URL = f"{BASE_URL}/j/subject_suggest"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_INTERVAL = 3
# 搜索建议里最多核对前几个条目，避免为一部片子发太多请求
MAX_SUGGEST_CANDIDATES = 5
BLOCKED_MESSAGE = "豆瓣拒绝了这次请求，请在插件设置里填写 Cookie 后重试"
# 海报只允许从豆瓣自己的域名下载，否则 Cookie 会跟着页面给出的地址发出去
IMAGE_DOMAINS = ("doubanio.com", "douban.com")


class DoubanProvider:
    """只提供补缺能力：宿主按外部 ID 调用 find/details，不会调用 search。"""

    def __init__(
        self,
        http: PluginHttpClient,
        settings: SettingsEndpoint,
        plugin_id: str,
    ) -> None:
        self.id = plugin_id
        self._http = http
        self._settings = settings
        self._limiter_lock = threading.Lock()
        self._limiter = RateLimiter(DEFAULT_INTERVAL)
        self._limiter_interval = float(DEFAULT_INTERVAL)

    # ---- 宿主协议 ----

    def unavailable_reason(self) -> str | None:
        # Cookie 是可选配置，没有 Cookie 也能访问部分页面
        return None

    def search(self, request: MetadataSearchRequest) -> list[MetadataCandidate]:
        raise MetadataNotFound("豆瓣是补缺插件，不支持按片名搜索")

    def find(self, kind: MetadataKind, scheme: str, value: str) -> str | None:
        if scheme != "imdb":
            # 豆瓣没有按 TMDB ID 反查的公开接口
            return None
        imdb_id = value.strip()
        if not imdb_id.startswith("tt") or not imdb_id[2:].isdigit():
            return None
        payload = self._get_suggest(SUGGEST_URL, {"q": imdb_id})
        for remote_id in parse_suggest(payload)[:MAX_SUGGEST_CANDIDATES]:
            try:
                html = self._get_text(f"{BASE_URL}/subject/{remote_id}/")
            except MetadataNotFound:
                # 搜索建议里的条目可能已经下线，继续核对下一个
                continue
            if parse_subject(html).imdb_id == imdb_id:
                return remote_id
        return None

    def details(self, remote_id: str, *, kind: MetadataKind) -> MetadataRecord:
        if not remote_id.isdigit():
            raise MetadataNotFound("豆瓣条目 ID 不正确")
        html = self._get_text(f"{BASE_URL}/subject/{remote_id}/")
        subject = parse_subject(html)
        if not subject.title:
            raise MetadataUnavailable("豆瓣页面里没有解析出条目信息")
        external_ids = {"douban": remote_id}
        if subject.imdb_id is not None:
            external_ids["imdb"] = subject.imdb_id
        return MetadataRecord(
            provider_id=self.id,
            remote_id=remote_id,
            kind=kind,
            title=subject.title,
            overview=subject.overview,
            year=subject.year,
            release_date=subject.release_date,
            rating=subject.rating,
            genres=subject.genres,
            countries=subject.countries,
            runtime=subject.runtime,
            poster=subject.poster,
            external_ids=external_ids,
        )

    def season(self, remote_id: str, number: int) -> MetadataSeason:
        raise MetadataNotFound("豆瓣只提供条目信息，不提供季和集")

    def page_url(self, kind: MetadataKind, remote_id: str) -> str | None:
        return self._subject_url(remote_id)

    def image(self, source: str, width: int) -> bytes:
        url = _image_url(source)
        values = self._settings.values()
        response = self._download(url, values)
        if response.status_code == 403:
            raise MetadataUnavailable(BLOCKED_MESSAGE)
        if response.status_code == 404:
            raise MetadataNotFound("豆瓣图片不存在")
        raise_for_status(response, service="豆瓣")
        if not response.content:
            raise MetadataNotFound("豆瓣图片不存在")
        return bytes(response.content)

    def test_connection(self, values: Mapping[str, Any] | None = None) -> str | None:
        """设置页的测试连接：只请求一次豆瓣首页，判断 Key/代理/Cookie 是否可用。"""
        merged = {**self._settings.values(), **(values or {})}
        user_agent = _text(merged.get(USER_AGENT_FIELD))
        if user_agent is None:
            return "请填写 User-Agent"
        try:
            self._get_text(f"{BASE_URL}/", values=merged)
        except MetadataUnavailable as exc:
            return exc.message
        except MetadataNotFound:
            return "豆瓣没有返回正常页面"
        return None

    # ---- HTTP ----

    def _get_text(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
        *,
        values: Mapping[str, Any] | None = None,
    ) -> str:
        response = self._request(url, params=params, values=values)
        return response.text

    def _get_suggest(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        """搜索建议返回 JSON 数组，不是对象，因此单独解析。"""
        response = self._request(url, params=params)
        try:
            payload = response.json()
        except ValueError:
            raise MetadataUnavailable("豆瓣返回的数据无法解析") from None
        if not isinstance(payload, list):
            raise MetadataUnavailable("豆瓣返回的数据无法解析")
        return payload

    def _request(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        values: Mapping[str, Any] | None = None,
    ) -> PluginHttpResponse:
        current = self._settings.values() if values is None else values
        self._throttle(current)
        try:
            response = self._http.get(
                url,
                params=dict(params or {}),
                headers=self._headers(current, accept="text/html,application/json"),
            )
        except (PluginHttpError, HostCallError) as exc:
            raise MetadataUnavailable("连接豆瓣失败，请检查网络或代理") from exc
        if response.status_code == 403:
            raise MetadataUnavailable(BLOCKED_MESSAGE)
        if response.status_code == 404:
            raise MetadataNotFound("豆瓣上没有这个条目")
        raise_for_status(response, service="豆瓣")
        if is_blocked(response.text):
            raise MetadataUnavailable(BLOCKED_MESSAGE)
        return response

    def _download(self, url: str, values: Mapping[str, Any]) -> PluginHttpResponse:
        headers = self._headers(values, accept="image/*")
        headers["Referer"] = f"{BASE_URL}/"
        try:
            return self._http.get(url, headers=headers)
        except (PluginHttpError, HostCallError) as exc:
            raise MetadataUnavailable("下载豆瓣图片失败，请稍后再试") from exc

    def _headers(self, values: Mapping[str, Any], *, accept: str) -> dict[str, str]:
        user_agent = _text(values.get(USER_AGENT_FIELD)) or DEFAULT_USER_AGENT
        headers = {
            "User-Agent": user_agent,
            "Accept": accept,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        cookie = _text(values.get(COOKIE_SECRET))
        if cookie is not None:
            headers["Cookie"] = cookie
        return headers

    def _throttle(self, values: Mapping[str, Any]) -> None:
        interval = _interval(values)
        with self._limiter_lock:
            if interval != self._limiter_interval:
                self._limiter = RateLimiter(interval)
                self._limiter_interval = interval
            limiter = self._limiter
        limiter.acquire()

    def _subject_url(self, remote_id: str) -> str | None:
        return f"{BASE_URL}/subject/{remote_id}/" if remote_id.isdigit() else None


def _interval(values: Mapping[str, Any]) -> float:
    raw = values.get(INTERVAL_FIELD)
    interval = (
        float(raw)
        if isinstance(raw, int | float) and not isinstance(raw, bool)
        else float(DEFAULT_INTERVAL)
    )
    return max(0.0, interval)


def _image_url(source: str) -> str:
    """校验海报地址：只接受 https 和豆瓣域名，避免把 Cookie 发给任意主机。"""
    url = _large_image(source)
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme != "https" or not any(
        host == domain or host.endswith(f".{domain}") for domain in IMAGE_DOMAINS
    ):
        raise MetadataNotFound("豆瓣图片地址不在允许的域名内")
    return url


def _large_image(source: str) -> str:
    """豆瓣的海报缩略图是 s_ratio_poster，换成 l_ratio_poster 取大图。"""
    return source.replace("/s_ratio_poster/", "/l_ratio_poster/").replace(
        "/s_ratio_pic/", "/l_ratio_pic/"
    )


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None
