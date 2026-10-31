from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import httpx2
import pytest

from app.plugins.fetch import (
    MAX_REDIRECTS,
    FetchError,
    FetchTimeout,
    FetchTooLarge,
    SafeFetcher,
    check_url,
)
from app.plugins.index_schema import MAX_INDEX_BYTES, MAX_PACKAGE_BYTES
from tests.repository_server import RepositoryHTTPServer

PUBLIC = "https://plugins.example.com"


def _fetcher(handler: Callable[[httpx2.Request], httpx2.Response], **kwargs: object) -> SafeFetcher:
    return SafeFetcher(transport=httpx2.MockTransport(handler), **kwargs)  # type: ignore[arg-type]


def _redirect_to(location: str) -> Callable[[httpx2.Request], httpx2.Response]:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "plugins.example.com":
            return httpx2.Response(302, headers={"Location": location})
        return httpx2.Response(200, content=b"{}")

    return handler


@pytest.mark.parametrize(
    "location",
    [
        "http://plugins.example.org/index.json",
        "http://127.0.0.1:9/index.json",
        "http://localhost/index.json",
        "https://127.0.0.1/index.json",
        "https://127.0.0.2/index.json",
        "https://localhost/index.json",
        "https://[::1]/index.json",
        "ftp://plugins.example.org/index.json",
    ],
)
def test_public_redirect_cannot_downgrade_or_reach_localhost(location: str) -> None:
    fetcher = _fetcher(_redirect_to(location))
    with pytest.raises(FetchError):
        fetcher.fetch(f"{PUBLIC}/index.json", max_bytes=MAX_INDEX_BYTES)


def test_public_redirect_to_other_https_host_is_followed() -> None:
    fetcher = _fetcher(_redirect_to("https://cdn.example.net/index.json"))
    result = fetcher.fetch(f"{PUBLIC}/index.json", max_bytes=MAX_INDEX_BYTES)
    assert result.url == "https://cdn.example.net/index.json"
    assert result.content == b"{}"


def test_local_start_may_redirect_within_localhost(
    repository_server: RepositoryHTTPServer,
) -> None:
    repository_server.add_file("/real.json", b'{"ok": true}')
    repository_server.redirects["/index.json"] = "/real.json"
    result = SafeFetcher().fetch(repository_server.index_url(), max_bytes=MAX_INDEX_BYTES)
    assert result.content == b'{"ok": true}'
    assert result.url.endswith("/real.json")


def test_local_start_redirect_to_plain_http_public_host_is_still_rejected(
    repository_server: RepositoryHTTPServer,
) -> None:
    repository_server.redirects["/index.json"] = "http://plugins.example.org/index.json"
    with pytest.raises(FetchError):
        SafeFetcher().fetch(repository_server.index_url(), max_bytes=MAX_INDEX_BYTES)


def test_redirect_limit(repository_server: RepositoryHTTPServer) -> None:
    repository_server.redirects["/index.json"] = "/index.json"
    with pytest.raises(FetchError, match=str(MAX_REDIRECTS)):
        SafeFetcher().fetch(repository_server.index_url(), max_bytes=MAX_INDEX_BYTES)
    # 每一跳都真的请求过
    assert len(repository_server.requests) == MAX_REDIRECTS + 1


def test_index_over_two_mib_is_rejected_by_content_length(
    repository_server: RepositoryHTTPServer,
) -> None:
    assert MAX_INDEX_BYTES == 2 * 1024 * 1024
    repository_server.add_file("/index.json", b"x" * (MAX_INDEX_BYTES + 1))
    with pytest.raises(FetchTooLarge):
        SafeFetcher().fetch(repository_server.index_url(), max_bytes=MAX_INDEX_BYTES)


def test_index_over_limit_without_content_length_is_cut_off() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        def body() -> Iterator[bytes]:
            for _ in range(40):
                yield b"x" * 64 * 1024

        return httpx2.Response(200, content=body())

    fetcher = _fetcher(handler)
    with pytest.raises(FetchTooLarge):
        fetcher.fetch(f"{PUBLIC}/index.json", max_bytes=MAX_INDEX_BYTES)


def test_package_content_length_over_fifty_mib_is_rejected_before_writing(tmp_path: Path) -> None:
    assert MAX_PACKAGE_BYTES == 50 * 1024 * 1024

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, headers={"Content-Length": str(MAX_PACKAGE_BYTES + 1)})

    destination = tmp_path / "package.zip"
    with pytest.raises(FetchTooLarge):
        _fetcher(handler).download(
            f"{PUBLIC}/package.zip", destination, max_bytes=MAX_PACKAGE_BYTES
        )
    assert not destination.exists()


def test_package_stream_over_limit_removes_partial_file(tmp_path: Path) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        def body() -> Iterator[bytes]:
            for _ in range(8):
                yield b"x" * 1024

        return httpx2.Response(200, content=body())

    destination = tmp_path / "package.zip"
    with pytest.raises(FetchTooLarge):
        _fetcher(handler).download(f"{PUBLIC}/package.zip", destination, max_bytes=2048)
    assert not destination.exists()


def test_download_reports_size_and_sha256(
    repository_server: RepositoryHTTPServer, tmp_path: Path
) -> None:
    repository_server.add_file("/package.zip", b"hello")
    destination = tmp_path / "package.zip"
    result = SafeFetcher().download(
        f"{repository_server.base_url}/package.zip", destination, max_bytes=1024
    )
    assert result.size == 5
    assert result.sha256 == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    assert destination.read_bytes() == b"hello"


@pytest.mark.parametrize(
    "url",
    [
        "https://exa mple.com/index.json",
        "https://example.com/in dex.json",
        "https://example.com/index.json\n",
        "https://example.com/\tindex.json",
        "https://",
        "ftp://example.com/index.json",
        "http://example.com/index.json",
        "http://[::1/index.json",
    ],
)
def test_check_url_rejects_invalid_addresses(url: str) -> None:
    with pytest.raises(FetchError):
        check_url(url)


def test_invalid_url_from_http_client_becomes_fetch_error() -> None:
    # 端口不是数字：urlsplit 不报错，httpx2 构造请求时抛 InvalidURL
    fetcher = _fetcher(lambda request: httpx2.Response(200, content=b"{}"))
    with pytest.raises(FetchError):
        fetcher.fetch("https://plugins.example.com:abc/index.json", max_bytes=MAX_INDEX_BYTES)


def test_read_errors_during_streaming_become_fetch_error(tmp_path: Path) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        def body() -> Iterator[bytes]:
            yield b"partial"
            raise httpx2.ReadTimeout("读取超时")

        return httpx2.Response(200, content=body())

    fetcher = _fetcher(handler)
    with pytest.raises(FetchError):
        fetcher.fetch(f"{PUBLIC}/index.json", max_bytes=MAX_INDEX_BYTES)
    destination = tmp_path / "package.zip"
    with pytest.raises(FetchError):
        fetcher.download(f"{PUBLIC}/package.zip", destination, max_bytes=MAX_PACKAGE_BYTES)
    assert not destination.exists()


class _Clock:
    """可手动推进的单调时钟。"""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _slow_handler(clock: _Clock, step: float) -> Callable[[httpx2.Request], httpx2.Response]:
    def handler(request: httpx2.Request) -> httpx2.Response:
        def body() -> Iterator[bytes]:
            for _ in range(10):
                clock.now += step
                yield b"x" * 1024

        return httpx2.Response(200, content=body())

    return handler


def test_overall_deadline_cuts_off_slow_index_download() -> None:
    clock = _Clock()
    fetcher = _fetcher(_slow_handler(clock, 25.0), clock=clock)
    # 每块用掉 25 秒，总截止 60 秒：第三块到达时已经超时
    with pytest.raises(FetchTimeout):
        fetcher.fetch(f"{PUBLIC}/index.json", max_bytes=MAX_INDEX_BYTES, deadline=60.0)


def test_overall_deadline_cuts_off_slow_package_download(tmp_path: Path) -> None:
    clock = _Clock()
    fetcher = _fetcher(_slow_handler(clock, 100.0), clock=clock)
    destination = tmp_path / "package.zip"
    with pytest.raises(FetchTimeout):
        fetcher.download(
            f"{PUBLIC}/package.zip", destination, max_bytes=MAX_PACKAGE_BYTES, deadline=250.0
        )
    assert not destination.exists()


def test_fast_download_within_deadline_succeeds() -> None:
    clock = _Clock()
    fetcher = _fetcher(_slow_handler(clock, 1.0), clock=clock)
    result = fetcher.fetch(f"{PUBLIC}/index.json", max_bytes=MAX_INDEX_BYTES, deadline=60.0)
    assert result.size == 10 * 1024


def test_deadline_is_checked_across_redirects() -> None:
    clock = _Clock()

    def handler(request: httpx2.Request) -> httpx2.Response:
        clock.now += 40.0
        return httpx2.Response(302, headers={"Location": "/next"})

    fetcher = _fetcher(handler, clock=clock)
    with pytest.raises(FetchTimeout):
        fetcher.fetch(f"{PUBLIC}/index.json", max_bytes=MAX_INDEX_BYTES, deadline=60.0)


def test_zero_deadline_fails_before_any_request() -> None:
    calls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(str(request.url))
        return httpx2.Response(200, content=b"{}")

    fetcher = _fetcher(handler, clock=_Clock())
    with pytest.raises(FetchTimeout):
        fetcher.fetch(f"{PUBLIC}/index.json", max_bytes=MAX_INDEX_BYTES, deadline=0.0)
    assert calls == []
