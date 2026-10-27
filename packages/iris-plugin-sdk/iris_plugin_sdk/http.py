"""插件直连外部服务的能力：连接池、代理切换、限速、日志脱敏和状态码映射。

直连请求不经过宿主，因此宿主不审计这些请求；插件必须自己使用 ``context.proxy``，并且不能
把查询参数或认证头写进日志。``DirectHttpClient`` 返回和宿主 ``http`` 能力相同的
``PluginHttpResponse``，插件可以在两种来源之间复用解析代码。
"""

from __future__ import annotations

import heapq
import itertools
import logging
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx2

from iris_plugin_sdk.priority import current_priority
from iris_plugin_sdk.protocol import HTTP_ALLOWED_KWARGS, HTTP_METHODS
from iris_plugin_sdk.types import (
    MetadataNotFound,
    MetadataUnavailable,
    PluginHttpResponse,
    SubtitleUnavailable,
)

DEFAULT_TIMEOUT_SECONDS = 20.0
MAX_REDIRECTS = 5
# 状态码映射的调用族：元数据和图片共用 Metadata*，字幕用 SubtitleUnavailable。
HttpFamily = Literal["metadata", "image", "subtitle"]

logger = logging.getLogger(__name__)


class _RedactHttpLogFilter(logging.Filter):
    """去掉 httpx2 日志里 URL 的查询参数和片段，不改变日志级别。"""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not args:
            return True
        if isinstance(args, Mapping):
            record.args = {key: _redact(value) for key, value in args.items()}
            return True
        if isinstance(args, tuple):
            record.args = tuple(_redact(value) for value in args)
        return True


def _redact(value: Any) -> Any:
    text = value if isinstance(value, str) else str(value)
    if "://" not in text:
        return value
    try:
        parts = urlsplit(text)
    except ValueError:
        return value
    if not parts.query and not parts.fragment:
        return value
    return parts._replace(query="", fragment="").geturl()


_FILTER_INSTALLED = False
_FILTER_LOCK = threading.Lock()


def _install_log_filter() -> None:
    global _FILTER_INSTALLED
    with _FILTER_LOCK:
        if _FILTER_INSTALLED:
            return
        logging.getLogger("httpx2").addFilter(_RedactHttpLogFilter())
        _FILTER_INSTALLED = True


class DirectHttpClient:
    """插件直连 HTTP 客户端；每个代理配置复用一个连接池，代理变化时重建。"""

    def __init__(
        self,
        proxy: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx2.BaseTransport | None = None,
        log: logging.Logger = logger,
    ) -> None:
        _install_log_filter()
        self._timeout = timeout
        self._transport = transport
        self._log = log
        self._lock = threading.Lock()
        self._proxy = proxy
        self._client = self._build_client(proxy)

    def config_changed(self, *, proxy: str | None) -> None:
        """代理变化时换一个新的连接池；旧连接池在锁外关闭，避免阻塞请求线程。"""
        with self._lock:
            if proxy == self._proxy:
                return
            previous = self._client
            self._client = self._build_client(proxy)
            self._proxy = proxy
        previous.close()

    def close(self) -> None:
        with self._lock:
            client = self._client
        client.close()

    def request(self, method: str, url: str, **kwargs: Any) -> PluginHttpResponse:
        method = method.upper()
        if method not in HTTP_METHODS:
            raise ValueError(f"不支持的 HTTP 方法：{method}")
        unknown = sorted(set(kwargs) - HTTP_ALLOWED_KWARGS)
        if unknown:
            raise ValueError(f"不能设置参数：{', '.join(unknown)}")
        with self._lock:
            client = self._client
        started = time.monotonic()
        status: int | str = "error"
        try:
            response = client.request(method, url, **kwargs)
            status = response.status_code
        finally:
            # 只记录主机、状态和耗时；查询参数和认证头绝不进日志。同步时请求很多，只记 debug。
            self._log.debug(
                "插件直连请求 method=%s host=%s status=%s elapsed_ms=%d",
                method,
                _host(url),
                status,
                (time.monotonic() - started) * 1000,
            )
        return _response(response)

    def get(self, url: str, **kwargs: Any) -> PluginHttpResponse:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> PluginHttpResponse:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> PluginHttpResponse:
        return self.request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> PluginHttpResponse:
        return self.request("DELETE", url, **kwargs)

    def _build_client(self, proxy: str | None) -> httpx2.Client:
        try:
            return httpx2.Client(
                proxy=None if self._transport is not None else proxy,
                timeout=self._timeout,
                transport=self._transport,
                follow_redirects=True,
                max_redirects=MAX_REDIRECTS,
                trust_env=False,
            )
        except ValueError as exc:
            raise ValueError(f"代理地址格式不正确：{proxy}") from exc


class RateLimiter:
    """按优先级排队的最小间隔限速；交互请求优先于后台请求。"""

    def __init__(self, interval: float, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._interval = interval
        self._monotonic = monotonic
        self._condition = threading.Condition()
        self._queue: list[tuple[int, int]] = []
        self._sequence = itertools.count()
        self._next_slot = 0.0

    @property
    def waiting(self) -> int:
        with self._condition:
            return len(self._queue)

    def acquire(self) -> None:
        priority = 1 if current_priority() == "background" else 0
        with self._condition:
            ticket = (priority, next(self._sequence))
            heapq.heappush(self._queue, ticket)
            self._condition.notify_all()
            try:
                while True:
                    if self._queue[0] == ticket:
                        delay = self._next_slot - self._monotonic()
                        if delay <= 0:
                            break
                        self._condition.wait(delay)
                    else:
                        self._condition.wait()
                self._next_slot = max(self._monotonic(), self._next_slot) + self._interval
            finally:
                self._queue.remove(ticket)
                heapq.heapify(self._queue)
                self._condition.notify_all()


def raise_for_status(
    response: PluginHttpResponse,
    *,
    service: str,
    family: HttpFamily = "metadata",
) -> None:
    """把常见的 HTTP 错误映射成 SDK 异常；2xx/3xx 不处理。"""
    status = response.status_code
    if status < 400:
        return
    if status in (401, 403):
        raise _unavailable(family, f"{service} 认证失败（HTTP {status}），请检查凭据")
    if status == 404:
        if family == "subtitle":
            raise SubtitleUnavailable(f"{service} 上没有这个字幕")
        raise MetadataNotFound(f"{service} 上没有这个条目")
    if status == 429:
        retry_after = _retry_after(response.headers)
        message = f"{service} 请求过于频繁，请稍后再试"
        if retry_after is not None:
            message = f"{service} 请求过于频繁，请等待 {retry_after} 秒后重试"
        if family == "subtitle":
            raise SubtitleUnavailable(message, retry_after)
        raise MetadataUnavailable(message)
    if status >= 500:
        raise _unavailable(family, f"{service} 暂时不可用（HTTP {status}）")
    raise _unavailable(family, f"{service} 拒绝了请求（HTTP {status}）")


def json_object(
    response: PluginHttpResponse,
    *,
    service: str,
    family: HttpFamily = "metadata",
) -> dict[str, Any]:
    """把响应解析成 JSON 对象；解析失败或不是对象时按调用族抛不可用。"""
    try:
        data = response.json()
    except ValueError:
        raise _unavailable(family, f"{service} 返回的内容无法解析") from None
    if not isinstance(data, dict):
        raise _unavailable(family, f"{service} 返回的内容无法解析")
    return data


def _unavailable(family: HttpFamily, message: str) -> Exception:
    if family == "subtitle":
        return SubtitleUnavailable(message)
    return MetadataUnavailable(message)


def _retry_after(headers: Mapping[str, str]) -> int | None:
    value = next(
        (item for key, item in headers.items() if key.casefold() == "retry-after"),
        None,
    )
    if value is None:
        return None
    try:
        seconds = int(value.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def _host(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def _response(response: httpx2.Response) -> PluginHttpResponse:
    return PluginHttpResponse(
        status_code=response.status_code,
        headers=dict(response.headers),
        content=response.content,
        url=str(response.url),
        history=tuple(_response(item) for item in response.history),
    )
