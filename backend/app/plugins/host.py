"""宿主向插件提供的受控能力：HTTP。

密钥、普通配置、本地资产和进程内上下文已经删除；插件把自己的数据写在
``data/plugins/<id>/state/`` 下。
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx2
from iris_plugin_sdk.protocol import HTTP_ALLOWED_KWARGS, HTTP_METHODS

from app.db import Database
from app.priority import Priority
from app.rate_limit import RateLimiter
from app.settings import PLUGIN_PROXY_SETTING, get_setting

HOST_VERSION = "0.1.0"
# 插件请求的默认超时和最小间隔；插件不能自行放宽
HTTP_TIMEOUT_SECONDS = 15.0
HTTP_INTERVAL_SECONDS = 0.5
# 重定向由宿主跟随，每一跳都要重新检查目标地址
HTTP_MAX_REDIRECTS = 5
# 插件拿到的响应是完整读入内存的，限制大小避免被远端拖垮进程
HTTP_MAX_BYTES = 20 * 1024 * 1024
# 解析函数可注入，测试里不访问真实 DNS
AddressResolver = Callable[[str, int], Iterable[Any]]

logger = logging.getLogger(__name__)


class PluginHostError(ValueError):
    """插件调用宿主能力时传入了不允许的参数。"""


def _no_proxy() -> str | None:
    return None


class PluginHttp:
    """每个插件独立的限速和连接池；审计日志只记录方法、主机、状态和耗时，不记录查询参数。"""

    def __init__(
        self,
        plugin_id: str,
        *,
        built_in: bool,
        limiter: RateLimiter | None = None,
        transport: httpx2.BaseTransport | None = None,
        resolver: AddressResolver = socket.getaddrinfo,
        proxy: Callable[[], str | None] = _no_proxy,
    ) -> None:
        self._plugin_id = plugin_id
        self._built_in = built_in
        self._limiter = limiter or RateLimiter(HTTP_INTERVAL_SECONDS)
        self._resolver = resolver
        self._transport = transport
        # 每次请求都重新解析当前有效代理；代理变了就换一个新的连接池
        self._proxy = proxy
        self._active_proxy: str | None = None
        self._client_lock = threading.Lock()
        self._client = self._build_client(None)

    def request(self, method: str, url: str, **kwargs: Any) -> httpx2.Response:
        method = method.upper()
        if method not in HTTP_METHODS:
            raise PluginHostError(f"插件不支持 HTTP 方法：{method}")
        unknown = sorted(set(kwargs) - HTTP_ALLOWED_KWARGS)
        if unknown:
            raise PluginHostError(f"插件不能设置参数：{', '.join(unknown)}")
        self._check_url(url)

        proxy = self._proxy() or None
        client = self._client_for(proxy)
        current_method = method
        current_url = url
        current_kwargs = dict(kwargs)
        history: list[httpx2.Response] = []
        redirects = 0
        while True:
            self._limiter.acquire(Priority.BACKGROUND)
            started = time.monotonic()
            status: int | str = "error"
            try:
                with client.stream(current_method, current_url, **current_kwargs) as response:
                    status = response.status_code
                    # 跳转响应不需要正文，交给下一跳重新读取
                    body = b"" if response.is_redirect else self._read_body(response)
            finally:
                logger.info(
                    "插件 HTTP 请求 plugin=%s method=%s host=%s status=%s proxy=%s elapsed_ms=%d",
                    self._plugin_id,
                    current_method,
                    urlsplit(current_url).hostname,
                    status,
                    "on" if proxy is not None else "off",
                    (time.monotonic() - started) * 1000,
                )

            if not response.is_redirect:
                return self._build_response(response, body, history)

            redirects += 1
            if redirects > HTTP_MAX_REDIRECTS:
                raise PluginHostError(f"插件请求重定向不能超过 {HTTP_MAX_REDIRECTS} 次")
            location = response.headers["location"]
            next_url = urljoin(str(response.url), location)
            self._check_url(next_url)
            history.append(self._build_response(response, b"", []))
            current_method, current_kwargs = self._redirect_request(
                response.status_code, current_method, current_kwargs
            )
            # params 已经体现在 response.url 里，跟随重定向时不能重复附加
            current_kwargs.pop("params", None)
            if _origin(next_url) != _origin(str(response.url)):
                current_kwargs = _strip_credentials(current_kwargs)
            current_url = next_url

    def close(self) -> None:
        with self._client_lock:
            self._client.close()

    def _client_for(self, proxy: str | None) -> httpx2.Client:
        """取当前代理对应的连接池；代理变了就关闭旧连接池再重建。"""
        with self._client_lock:
            if proxy != self._active_proxy:
                previous = self._client
                self._client = self._build_client(proxy)
                self._active_proxy = proxy
                previous.close()
            return self._client

    def _build_client(self, proxy: str | None) -> httpx2.Client:
        return httpx2.Client(
            # 测试注入 transport 时不真正走代理；代理只用管理员配置的，不读取环境变量
            proxy=None if self._transport is not None else proxy,
            timeout=HTTP_TIMEOUT_SECONDS,
            transport=self._transport,
            follow_redirects=False,
            trust_env=False,
        )

    def _check_url(self, url: str) -> None:
        try:
            parts = urlsplit(url)
        except ValueError as exc:
            raise PluginHostError("插件请求的地址无效") from exc
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise PluginHostError("插件只能请求 http 或 https 地址")
        # 内置插件可能要连用户的局域网服务，只限制外部插件
        if self._built_in:
            return
        try:
            port = parts.port or (443 if parts.scheme == "https" else 80)
        except ValueError as exc:
            raise PluginHostError("插件请求的地址无效") from exc
        self._check_public_host(parts.hostname, port)

    def _check_public_host(self, host: str, port: int) -> None:
        # 这里是「先解析检查、后交给 httpx 连接」，httpx 建连时会再解析一次，DNS 重绑定
        # 仍可能在这两次解析之间把公网地址换成内网地址。暂时接受：要彻底修复需要固定本次
        # 解析结果并让连接直接使用该 IP（自定义 transport），会牺牲连接池对正常域名的复用，
        # 留到真正加载第三方代码时再做。
        try:
            addresses = self._resolver(host, port)
        except OSError as exc:
            raise PluginHostError(f"无法解析插件请求的主机：{host}") from exc
        found = False
        for info in addresses:
            found = True
            try:
                address = ipaddress.ip_address(info[4][0])
            except (IndexError, TypeError, ValueError) as exc:
                raise PluginHostError(f"无法解析插件请求的主机：{host}") from exc
            if (
                address.is_loopback
                or address.is_private
                or address.is_link_local
                or address.is_reserved
                or address.is_multicast
                or address.is_unspecified
            ):
                raise PluginHostError(f"外部插件不能请求内网或保留地址：{host}")
        if not found:
            raise PluginHostError(f"无法解析插件请求的主机：{host}")

    def _read_body(self, response: httpx2.Response) -> bytes:
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > HTTP_MAX_BYTES:
                # 退出 with 会关闭连接，不再继续读取远端内容
                raise PluginHostError(f"插件请求的响应不能超过 {HTTP_MAX_BYTES} 字节")
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _build_response(
        response: httpx2.Response, body: bytes, history: list[httpx2.Response]
    ) -> httpx2.Response:
        # 正文已经解码，保留 content-encoding 会让 Response 再解码一次
        headers = response.headers.copy()
        for name in ("content-encoding", "content-length", "transfer-encoding"):
            headers.pop(name, None)
        return httpx2.Response(
            status_code=response.status_code,
            headers=headers,
            content=body,
            request=response.request,
            history=history,
        )

    @staticmethod
    def _redirect_request(
        status: int, method: str, kwargs: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        if (status == 303 and method != "HEAD") or (status in (301, 302) and method == "POST"):
            next_method = "GET"
        else:
            return method, kwargs
        next_kwargs = {
            key: value for key, value in kwargs.items() if key not in ("json", "data", "content")
        }
        headers = next_kwargs.get("headers")
        if headers:
            next_kwargs["headers"] = {
                key: value
                for key, value in dict(headers).items()
                if key.lower() not in ("content-type", "content-length")
            }
        return next_method, next_kwargs


def _origin(url: str) -> tuple[str, str | None, int | None]:
    parts = urlsplit(url)
    return parts.scheme, parts.hostname, parts.port


def _strip_credentials(kwargs: dict[str, Any]) -> dict[str, Any]:
    # 跨源跳转不能把插件带给原站的凭据转发给新主机（与 httpx 自动跟随的行为一致）
    headers = kwargs.get("headers")
    if not headers:
        return kwargs
    return {
        **kwargs,
        "headers": {
            key: value
            for key, value in dict(headers).items()
            if key.lower() not in ("authorization", "cookie", "proxy-authorization")
        },
    }


def effective_proxy(database: Database, plugin_id: str) -> str | None:
    """插件当前生效的代理：none 不走代理，custom 用插件自己的地址，inherit 用全局插件代理。

    PluginHttp 和 supervisor 都从这里解析，避免两处规则不一致。
    """
    with database.read_session() as connection:
        row = connection.execute(
            "SELECT proxy_mode, proxy FROM plugin WHERE id = ?", (plugin_id,)
        ).fetchone()
        if row is not None:
            if row["proxy_mode"] == "none":
                return None
            if row["proxy_mode"] == "custom":
                return str(row["proxy"]) if row["proxy"] else None
        return get_setting(connection, PLUGIN_PROXY_SETTING) or None
