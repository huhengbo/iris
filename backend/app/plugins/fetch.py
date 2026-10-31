"""插件仓库索引和插件包的受限下载。

只允许 HTTPS（本机开发可用 ``localhost``/``127.0.0.1``/``::1`` 的 HTTP），手动跟随
重定向并且每一跳都重新检查协议，限制下载大小，流式写文件时同时计算 sha256。
起始地址不是本机时，重定向目标必须是 HTTPS 且不能指向本机，防止公网仓库把请求引到
宿主所在网络的 HTTP 服务。每次下载有总墙钟截止时间，慢速滴灌不能长期占用安装锁。
代理只使用管理员配置的插件代理，不读取进程环境里的代理变量。
"""

from __future__ import annotations

import hashlib
import ipaddress
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx2

CONNECT_TIMEOUT_SECONDS = 10.0
READ_TIMEOUT_SECONDS = 60.0
MAX_REDIRECTS = 5
INDEX_DEADLINE_SECONDS = 60.0
PACKAGE_DEADLINE_SECONDS = 600.0
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_CHUNK_SIZE = 64 * 1024


class FetchError(Exception):
    """下载失败；调用方按仓库状态或任务错误处理。"""


class FetchTooLarge(FetchError):
    pass


class FetchTimeout(FetchError):
    """超过整体截止时间。"""


@dataclass(frozen=True)
class FetchResult:
    """一次下载的结果；304 时 not_modified 为真，content 为 None。"""

    url: str
    not_modified: bool
    content: bytes | None
    size: int
    sha256: str
    etag: str | None


def check_url(url: str, *, allow_local: bool = True) -> None:
    """仓库地址和插件包地址的统一规则；allow_local 为假时不允许任何指向本机的地址。"""
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url):
        raise FetchError(f"地址无效：{url!r}")
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError as exc:
        raise FetchError(f"地址无效：{url}") from exc
    if not host:
        raise FetchError(f"地址无效：{url}")
    if not allow_local and _is_loopback(host):
        raise FetchError(f"重定向不能指向本机：{url}")
    if parts.scheme == "https":
        return
    if parts.scheme == "http" and host in LOCAL_HOSTS and allow_local:
        return
    raise FetchError(f"只允许 HTTPS 地址（本机开发可用 localhost）：{url}")


def _is_loopback(host: str) -> bool:
    name = host.rstrip(".").lower()
    if name == "localhost" or name.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def resolve_url(base: str, value: str) -> str:
    """相对地址按索引地址解析，然后套用 HTTPS 规则。"""
    resolved = urljoin(base, value)
    check_url(resolved)
    return resolved


class SafeFetcher:
    """按代理缓存连接池的下载器；索引和插件包共用。"""

    def __init__(
        self,
        transport: httpx2.BaseTransport | None = None,
        *,
        connect_timeout: float = CONNECT_TIMEOUT_SECONDS,
        read_timeout: float = READ_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport = transport
        self._clock = clock
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._lock = threading.Lock()
        self._clients: dict[str | None, httpx2.Client] = {}

    def close(self) -> None:
        with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            client.close()

    def fetch(
        self,
        url: str,
        *,
        proxy: str | None = None,
        etag: str | None = None,
        max_bytes: int,
        timeout: float | None = None,
        deadline: float = INDEX_DEADLINE_SECONDS,
    ) -> FetchResult:
        """读取到内存；带上 If-None-Match，304 时返回 not_modified。

        deadline 是整次下载的总秒数（含重定向），不是单次读取超时。
        """
        headers = {"Accept": "application/json"}
        if etag:
            headers["If-None-Match"] = etag
        expires = self._clock() + deadline
        response, final_url = self._open(
            url, proxy=proxy, headers=headers, timeout=timeout, expires=expires
        )
        try:
            if response.status_code == 304:
                return FetchResult(
                    url=final_url,
                    not_modified=True,
                    content=None,
                    size=0,
                    sha256="",
                    etag=response.headers.get("etag") or etag,
                )
            self._check_status(response, final_url)
            _check_content_length(response, max_bytes)
            content = bytearray()
            try:
                for chunk in response.iter_bytes(_CHUNK_SIZE):
                    self._check_deadline(expires, final_url)
                    content.extend(chunk)
                    if len(content) > max_bytes:
                        raise FetchTooLarge(f"下载内容超过上限：{final_url}")
            except httpx2.HTTPError as exc:
                raise FetchError(f"读取失败：{final_url}：{exc}") from exc
            data = bytes(content)
            return FetchResult(
                url=final_url,
                not_modified=False,
                content=data,
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                etag=response.headers.get("etag"),
            )
        finally:
            response.close()

    def download(
        self,
        url: str,
        destination: Path,
        *,
        proxy: str | None = None,
        max_bytes: int,
        timeout: float | None = None,
        deadline: float = PACKAGE_DEADLINE_SECONDS,
    ) -> FetchResult:
        """流式写文件；返回最终地址、大小和 sha256。deadline 同 :meth:`fetch`。"""
        expires = self._clock() + deadline
        response, final_url = self._open(
            url, proxy=proxy, headers=None, timeout=timeout, expires=expires
        )
        digest = hashlib.sha256()
        size = 0
        try:
            self._check_status(response, final_url)
            _check_content_length(response, max_bytes)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("wb") as file:
                for chunk in response.iter_bytes(_CHUNK_SIZE):
                    self._check_deadline(expires, final_url)
                    size += len(chunk)
                    if size > max_bytes:
                        raise FetchTooLarge(f"下载内容超过上限：{final_url}")
                    digest.update(chunk)
                    file.write(chunk)
            return FetchResult(
                url=final_url,
                not_modified=False,
                content=None,
                size=size,
                sha256=digest.hexdigest(),
                etag=response.headers.get("etag"),
            )
        except httpx2.HTTPError as exc:
            destination.unlink(missing_ok=True)
            raise FetchError(f"读取失败：{final_url}：{exc}") from exc
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        finally:
            response.close()

    def _open(
        self,
        url: str,
        *,
        proxy: str | None,
        headers: dict[str, str] | None,
        timeout: float | None,
        expires: float,
    ) -> tuple[httpx2.Response, str]:
        client = self._client_for(proxy)
        # 起始地址不是本机时，重定向不能降级到 HTTP，也不能指向本机
        allow_local = _is_local_host(url)
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            check_url(current)
            remaining = self._remaining(expires, current)
            try:
                request = client.build_request(
                    "GET",
                    current,
                    headers=headers,
                    timeout=self._timeout(timeout, limit=remaining),
                )
                response = client.send(request, stream=True)
            except (httpx2.HTTPError, httpx2.InvalidURL) as exc:
                raise FetchError(f"请求失败：{current}：{exc}") from exc
            # httpx2 把 304 也算作 redirect；304 是有条件请求的成功结果，不是重定向
            if response.status_code == 304:
                return response, current
            if response.is_redirect:
                location = response.headers.get("location")
                response.close()
                if not location:
                    raise FetchError(f"重定向缺少 Location：{current}")
                current = urljoin(current, location)
                check_url(current, allow_local=allow_local)
                continue
            return response, current
        raise FetchError(f"重定向超过 {MAX_REDIRECTS} 次：{url}")

    def _remaining(self, expires: float, url: str) -> float:
        remaining = expires - self._clock()
        if remaining <= 0:
            raise FetchTimeout(f"下载超时：{url}")
        return remaining

    def _check_deadline(self, expires: float, url: str) -> None:
        self._remaining(expires, url)

    def _client_for(self, proxy: str | None) -> httpx2.Client:
        with self._lock:
            client = self._clients.get(proxy)
            if client is None:
                client = httpx2.Client(
                    # 注入 transport 的测试不真正走代理；代理只用管理员配置的
                    proxy=None if self._transport is not None else proxy,
                    timeout=self._timeout(None),
                    transport=self._transport,
                    follow_redirects=False,
                    trust_env=False,
                )
                self._clients[proxy] = client
            return client

    def _timeout(self, read: float | None, *, limit: float | None = None) -> httpx2.Timeout:
        """单次操作的超时；limit 是整体截止时间剩余的秒数，单次不会超过它。"""
        values = {
            "connect": self._connect_timeout,
            "read": read if read is not None else self._read_timeout,
            "write": self._read_timeout,
            "pool": self._connect_timeout,
        }
        if limit is not None:
            values = {key: min(value, limit) for key, value in values.items()}
        return httpx2.Timeout(**values)

    @staticmethod
    def _check_status(response: httpx2.Response, url: str) -> None:
        if response.status_code == 200:
            return
        raise FetchError(f"下载失败（HTTP {response.status_code}）：{url}")


def _is_local_host(url: str) -> bool:
    try:
        return urlsplit(url).hostname in LOCAL_HOSTS
    except ValueError:
        return False


def _check_content_length(response: httpx2.Response, max_bytes: int) -> None:
    raw = response.headers.get("content-length")
    if raw is None or not raw.isdigit():
        return
    if int(raw) > max_bytes:
        raise FetchTooLarge(f"下载内容超过上限：{response.request.url}")
