"""插件宿主能力：HTTP 白名单、重定向、SSRF 和有效代理解析的单元测试。"""

from __future__ import annotations

import io
import ipaddress
import logging
import socket
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx2
import pytest

import app.plugins.host as host_module
from app.clock import SystemClock
from app.db import Database
from app.plugins.host import (
    HTTP_MAX_BYTES,
    HTTP_MAX_REDIRECTS,
    AddressResolver,
    PluginHostError,
    PluginHttp,
    effective_proxy,
)
from app.plugins.rpc import PluginRpcClient
from app.rate_limit import RateLimiter

PUBLIC_ADDRESS = "93.184.216.34"
HttpHandler = Callable[[httpx2.Request], httpx2.Response]


class _ChunkStream(httpx2.SyncByteStream):
    def __init__(self, chunks: Iterable[bytes]) -> None:
        self._chunks = chunks

    def __iter__(self) -> Iterator[bytes]:
        yield from self._chunks


def _resolver(mapping: Mapping[str, str] | None = None) -> AddressResolver:
    """只走测试给定的地址，绝不访问真实 DNS；IP 字面量按原值返回。"""
    addresses = mapping or {}

    def resolve(host: str, port: int) -> list[tuple[Any, ...]]:
        try:
            ipaddress.ip_address(host)
            address = host
        except ValueError:
            address = addresses.get(host, PUBLIC_ADDRESS)
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port))]

    return resolve


@contextmanager
def _http(
    built_in: bool,
    handler: HttpHandler,
    resolver: AddressResolver | None = None,
    proxy: Callable[[], str | None] | None = None,
) -> Iterator[PluginHttp]:
    http = PluginHttp(
        "org.example.test",
        built_in=built_in,
        limiter=RateLimiter(0),
        transport=httpx2.MockTransport(handler),
        resolver=resolver or _resolver(),
        proxy=proxy or (lambda: None),
    )
    try:
        yield http
    finally:
        http.close()


def _ok(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(200, text="ok")


def _insert_plugin(
    database: Database,
    plugin_id: str,
    *,
    proxy_mode: str = "inherit",
    proxy: str | None = None,
) -> None:
    with database.session() as connection:
        connection.execute(
            "INSERT INTO plugin (id, name, version, updated_at, proxy_mode, proxy) "
            "VALUES (?, ?, '1.0.0', '2026-01-01T00:00:00+00:00', ?, ?)",
            (plugin_id, plugin_id, proxy_mode, proxy),
        )


def _set_global_proxy(database: Database, proxy: str) -> None:
    with database.session() as connection:
        connection.execute(
            "INSERT INTO setting (key, value, is_secret, updated_at) VALUES (?, ?, 0, 'x')",
            ("plugin.proxy", proxy),
        )


@pytest.fixture
def database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "plugin-host.db")
    database.migrate(SystemClock())
    return database


# ---- HTTP 参数白名单 ----


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout": 1.0},
        {"follow_redirects": True},
        {"auth": None},
        {"extensions": {}},
        {"transport": object()},
    ],
)
def test_http_rejects_kwargs_outside_whitelist(kwargs: dict[str, object]) -> None:
    with _http(True, _ok) as http, pytest.raises(PluginHostError):
        http.request("GET", "https://public.test/x", **kwargs)


def test_http_accepts_content_kwargs_and_real_method() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"ok": True})

    with _http(True, handler) as http:
        response = http.request(
            "POST",
            "https://public.test/api",
            params={"q": "中文"},
            headers={"X-Test": "1"},
            json={"name": "值"},
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert b'"ok"' in response.content
    assert "ok" in response.text
    assert requests[0].method == "POST"
    assert requests[0].url.params["q"] == "中文"
    assert requests[0].headers["x-test"] == "1"
    assert b'"name"' in requests[0].content


# ---- 代理 ----


def test_http_rebuilds_client_when_proxy_changes(caplog: pytest.LogCaptureFixture) -> None:
    current: dict[str, str | None] = {"proxy": None}
    proxy = "http://user:hunter2@127.0.0.1:7890"

    with _http(True, _ok, proxy=lambda: current["proxy"]) as http:
        with caplog.at_level(logging.INFO, logger="app.plugins.host"):
            http.request("GET", "https://public.test/first")
            first = http._client
            assert "proxy=off" in caplog.text

            # 全局代理变了，下一次请求应该换一个新的连接池
            current["proxy"] = proxy
            http.request("GET", "https://public.test/second")
            second = http._client

        assert second is not first
        assert first.is_closed
        assert "proxy=on" in caplog.text
        # 审计日志只记录是否走代理，不记录代理地址
        assert "127.0.0.1" not in caplog.text
        assert "hunter2" not in caplog.text

        # 代理没变时复用连接池
        http.request("GET", "https://public.test/third")
        assert http._client is second


def test_http_without_proxy_uses_one_client() -> None:
    with _http(True, _ok) as http:
        client = http._client
        http.request("GET", "https://public.test/first")
        http.request("GET", "https://public.test/second")
        assert http._client is client


# ---- 重定向 ----


def test_redirect_to_private_address_rejected_for_external_plugin() -> None:
    requests: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(str(request.url))
        return httpx2.Response(302, headers={"location": "http://127.0.0.1/secret"})

    with _http(False, handler) as http, pytest.raises(PluginHostError, match="内网"):
        http.request("GET", "https://public.test/start")

    assert requests == ["https://public.test/start"]


def test_redirect_to_private_address_allowed_for_built_in_plugin() -> None:
    requests: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(str(request.url))
        if request.url.host == "public.test":
            return httpx2.Response(302, headers={"location": "http://127.0.0.1/ok"})
        return httpx2.Response(200, text="local")

    with _http(True, handler) as http:
        response = http.request("GET", "https://public.test/start")

    assert response.text == "local"
    assert requests == ["https://public.test/start", "http://127.0.0.1/ok"]


def test_redirect_to_non_http_scheme_rejected() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(302, headers={"location": "file:///etc/passwd"})

    with _http(True, handler) as http, pytest.raises(PluginHostError, match="http"):
        http.request("GET", "https://public.test/start")


def test_redirect_limit_is_five_hops() -> None:
    paths: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        paths.append(request.url.path)
        number = int(request.url.path.removeprefix("/r"))
        if number < HTTP_MAX_REDIRECTS + 1:
            return httpx2.Response(302, headers={"location": f"/r{number + 1}"})
        return httpx2.Response(200)

    with _http(True, handler) as http, pytest.raises(PluginHostError, match="重定向"):
        http.request("GET", "https://public.test/r0")

    assert paths == [f"/r{number}" for number in range(HTTP_MAX_REDIRECTS + 1)]


@pytest.mark.parametrize("status", [301, 302, 303])
def test_post_redirect_converts_to_get(status: int) -> None:
    methods: list[str] = []
    bodies: list[bytes] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        methods.append(request.method)
        if request.method == "POST":
            return httpx2.Response(status, headers={"location": "/done"})
        bodies.append(request.content)
        return httpx2.Response(200, text="done")

    with _http(True, handler) as http:
        response = http.request("POST", "https://public.test/start", json={"a": 1})

    assert response.text == "done"
    assert methods == ["POST", "GET"]
    assert bodies == [b""]


def test_307_preserves_method_and_body() -> None:
    methods: list[str] = []
    bodies: list[bytes] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        methods.append(request.method)
        bodies.append(request.content)
        if request.url.path == "/start":
            return httpx2.Response(307, headers={"location": "/done"})
        return httpx2.Response(200, text="done")

    with _http(True, handler) as http:
        http.request("POST", "https://public.test/start", json={"a": 1})

    assert methods == ["POST", "POST"]
    assert bodies[0] == bodies[1]
    assert bodies[1]


def test_cross_origin_redirect_drops_credentials() -> None:
    seen: list[tuple[str, str | None, str | None]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(
            (request.url.host, request.headers.get("authorization"), request.headers.get("x-id"))
        )
        if request.url.path == "/start":
            return httpx2.Response(302, headers={"location": "/same"})
        if request.url.path == "/same":
            return httpx2.Response(302, headers={"location": "https://cdn.test/file"})
        return httpx2.Response(200, text="done")

    with _http(True, handler) as http:
        http.request(
            "GET", "https://api.test/start", headers={"Authorization": "Bearer k", "X-Id": "1"}
        )

    assert seen == [
        ("api.test", "Bearer k", "1"),
        ("api.test", "Bearer k", "1"),
        ("cdn.test", None, "1"),
    ]


# ---- SSRF 防护 ----


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.1.1",
        "192.0.2.10",
        "224.0.0.1",
        "0.0.0.0",
        "::1",
        "fc00::1",
        "fe80::1",
    ],
)
def test_external_plugin_rejects_non_public_addresses(address: str) -> None:
    requests: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(str(request.url))
        return httpx2.Response(200)

    with (
        _http(False, handler, _resolver({"internal.test": address})) as http,
        pytest.raises(PluginHostError),
    ):
        http.request("GET", "https://internal.test/x")

    assert requests == []


@pytest.mark.parametrize("address", ["127.0.0.1", "10.1.2.3"])
def test_built_in_plugin_can_reach_private_addresses(address: str) -> None:
    requests: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(str(request.url))
        return httpx2.Response(200, text="ok")

    with _http(True, handler, _resolver({"internal.test": address})) as http:
        response = http.request("GET", "https://internal.test/x")

    assert response.text == "ok"
    assert requests == ["https://internal.test/x"]


def test_external_plugin_rejects_unresolvable_host() -> None:
    def resolver(host: str, port: int) -> list[tuple[Any, ...]]:
        raise OSError("dns unavailable")

    with _http(False, _ok, resolver) as http, pytest.raises(PluginHostError, match="解析"):
        http.request("GET", "https://public.test/x")


@pytest.mark.parametrize("url", ["ftp://public.test/x", "http://[::1/x"])
def test_external_plugin_rejects_invalid_url(url: str) -> None:
    with _http(False, _ok) as http, pytest.raises(PluginHostError):
        http.request("GET", url)


# ---- 响应大小和审计 ----


def test_response_size_limit_is_twenty_megabytes() -> None:
    assert HTTP_MAX_BYTES == 20 * 1024 * 1024


def test_oversized_response_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host_module, "HTTP_MAX_BYTES", 8)

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, stream=_ChunkStream([b"12345", b"6789"]))

    with _http(True, handler) as http, pytest.raises(PluginHostError, match="响应"):
        http.request("GET", "https://public.test/big")


def test_audit_log_omits_query_and_uses_real_method(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with _http(True, _ok) as http, caplog.at_level(logging.INFO, logger="app.plugins.host"):
        http.request("POST", "https://public.test/path?token=top-secret", content=b"body")

    assert "top-secret" not in caplog.text
    assert "method=POST" in caplog.text
    assert "host=public.test" in caplog.text


def test_audit_log_records_each_redirect_hop(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "public.test":
            return httpx2.Response(
                302, headers={"location": "https://cdn.test/final?token=top-secret"}
            )
        return httpx2.Response(200, text="done")

    with (
        _http(True, handler) as http,
        caplog.at_level(logging.INFO, logger="app.plugins.host"),
    ):
        http.request("GET", "https://public.test/start?token=top-secret")

    assert caplog.text.count("插件 HTTP 请求") == 2
    assert "host=cdn.test" in caplog.text
    assert "top-secret" not in caplog.text


# ---- 有效代理 ----


@pytest.mark.parametrize(
    ("mode", "plugin_proxy", "expected"),
    [
        ("inherit", None, "http://global-proxy:7890"),
        ("none", None, None),
        ("custom", "http://plugin-proxy:7890", "http://plugin-proxy:7890"),
    ],
)
def test_effective_proxy(
    database: Database,
    mode: str,
    plugin_proxy: str | None,
    expected: str | None,
) -> None:
    _insert_plugin(database, "org.example.test", proxy_mode=mode, proxy=plugin_proxy)
    _set_global_proxy(database, "http://global-proxy:7890")

    assert effective_proxy(database, "org.example.test") == expected


def test_effective_proxy_reads_changes_on_each_request(database: Database) -> None:
    _insert_plugin(database, "org.example.test")
    _set_global_proxy(database, "http://first-proxy:7890")
    assert effective_proxy(database, "org.example.test") == "http://first-proxy:7890"

    with database.session() as connection:
        connection.execute(
            "UPDATE plugin SET proxy_mode = 'custom', proxy = 'http://second-proxy:7890' "
            "WHERE id = 'org.example.test'"
        )
    assert effective_proxy(database, "org.example.test") == "http://second-proxy:7890"


def test_closing_plugin_client_closes_host_http_pool(tmp_path: Path) -> None:
    http = PluginHttp(
        "org.example.test",
        built_in=True,
        limiter=RateLimiter(0),
        transport=httpx2.MockTransport(_ok),
        resolver=_resolver(),
    )
    client = PluginRpcClient(
        "org.example.test",
        io.BytesIO(),
        io.BytesIO(),
        http=http,
        on_resource_changed=lambda source_id, locators: None,
    )

    client.close()

    assert http._client.is_closed
