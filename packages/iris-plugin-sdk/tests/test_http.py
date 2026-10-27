from __future__ import annotations

import logging

import httpx2
import pytest

from iris_plugin_sdk import (
    DirectHttpClient,
    MetadataNotFound,
    MetadataUnavailable,
    PluginHttpResponse,
    SubtitleUnavailable,
    json_object,
    priority_scope,
    raise_for_status,
)


def _client(handler: object, *, proxy: str | None = None) -> DirectHttpClient:
    return DirectHttpClient(
        proxy,
        transport=httpx2.MockTransport(handler),  # type: ignore[arg-type]
    )


def test_request_returns_plugin_response_and_redacts_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.params["api_key"] == "secret-value"
        return httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            json={"ok": True},
            request=request,
        )

    client = _client(handler)
    try:
        with caplog.at_level(logging.INFO, logger="httpx2"):
            response = client.get(
                "https://api.example.test/search",
                params={"api_key": "secret-value"},
                headers={"Authorization": "Bearer secret-token"},
            )
    finally:
        client.close()

    assert isinstance(response, PluginHttpResponse)
    assert response.json() == {"ok": True}
    assert "secret-value" not in caplog.text
    assert "secret-token" not in caplog.text
    assert "api.example.test" in caplog.text


def test_config_changed_rebuilds_pool() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=b"ok", request=request)

    client = _client(handler, proxy="http://proxy-a.test:1080")
    first = client._client
    client.config_changed(proxy="http://proxy-a.test:1080")
    assert client._client is first
    client.config_changed(proxy="http://proxy-b.test:1080")
    assert client._client is not first
    client.close()


def test_rejects_unknown_method_and_kwargs() -> None:
    client = _client(lambda request: httpx2.Response(200, request=request))
    try:
        with pytest.raises(ValueError, match="方法"):
            client.request("TRACE", "https://example.test/")
        with pytest.raises(ValueError, match="不能设置"):
            client.get("https://example.test/", timeout=1)
    finally:
        client.close()


def test_bad_proxy_is_reported() -> None:
    with pytest.raises(ValueError, match="代理"):
        DirectHttpClient("not-a-proxy")


def _response(status: int, *, headers: dict[str, str] | None = None) -> PluginHttpResponse:
    return PluginHttpResponse(
        status_code=status,
        headers=headers or {},
        content=b"{}",
        url="https://example.test/",
    )


def test_raise_for_status_maps_metadata_errors() -> None:
    with pytest.raises(MetadataNotFound, match="TMDB 上没有这个条目"):
        raise_for_status(_response(404), service="TMDB")
    with pytest.raises(MetadataUnavailable, match="认证失败"):
        raise_for_status(_response(401), service="TMDB")
    with pytest.raises(MetadataUnavailable, match="请求过于频繁"):
        raise_for_status(_response(429, headers={"Retry-After": "30"}), service="TMDB")
    with pytest.raises(MetadataUnavailable, match="暂时不可用"):
        raise_for_status(_response(503), service="TMDB")
    with pytest.raises(MetadataUnavailable, match="拒绝"):
        raise_for_status(_response(418), service="TMDB")


def test_raise_for_status_maps_subtitle_errors_with_retry_after() -> None:
    with pytest.raises(SubtitleUnavailable) as error:
        raise_for_status(
            _response(429, headers={"retry-after": "12"}),
            service="射手网",
            family="subtitle",
        )
    assert error.value.retry_after == 12
    with pytest.raises(SubtitleUnavailable, match="没有这个字幕"):
        raise_for_status(_response(404), service="射手网", family="subtitle")


def test_json_object_rejects_invalid_payloads() -> None:
    bad = PluginHttpResponse(200, {}, b"not json", "https://example.test/")
    with pytest.raises(MetadataUnavailable, match="无法解析"):
        json_object(bad, service="TMDB")
    assert json_object(
        PluginHttpResponse(200, {}, b'{"id": 1}', "https://example.test/"), service="TMDB"
    ) == {"id": 1}
    with pytest.raises(SubtitleUnavailable, match="无法解析"):
        json_object(
            PluginHttpResponse(200, {}, b"[]", "https://x/"), service="射手网", family="subtitle"
        )


def test_rate_limiter_enforces_interval() -> None:
    from iris_plugin_sdk import RateLimiter

    limiter = RateLimiter(0.02)
    limiter.acquire()
    limiter.acquire()
    assert limiter.waiting == 0


def test_rate_limiter_reads_priority() -> None:
    from iris_plugin_sdk import RateLimiter

    limiter = RateLimiter(0)
    with priority_scope("background"):
        limiter.acquire()
    limiter.acquire()
