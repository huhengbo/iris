"""Fanart.tv 图片获取器：按 ID 取图、映射、语言、404/401 和设置页。全程假 HTTP。"""

from __future__ import annotations

import json
import logging
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

import pytest
from iris_plugin_sdk import (
    HostInfo,
    ImageLookupRequest,
    JsonSettingsStore,
    MetadataNotFound,
    MetadataUnavailable,
    PluginContext,
    PluginRequest,
    PluginResponse,
    load_manifest,
)
from iris_plugin_sdk.testing import FakeHttp, RecordedRequest, RecordingNotifier, response

from iris_plugin_fanart.plugin import FanartPlugin
from iris_plugin_fanart.provider import (
    API_KEY_SECRET,
    CLIENT_KEY_SECRET,
    FanartProvider,
)

FIXTURES = Path(__file__).parent / "fixtures" / "fanart"
MOVIE = json.loads((FIXTURES / "movie.json").read_text(encoding="utf-8"))
TV = json.loads((FIXTURES / "tv.json").read_text(encoding="utf-8"))
KEY = "fanart-api-key"
CLIENT_KEY = "fanart-client-key"
POSTER = "https://assets.fanart.tv/fanart/movies/550/movieposter/fight-club-1.jpg"


def make_provider(
    handler: Callable[[RecordedRequest], Any],
    *,
    settings: Mapping[str, Any] | None = None,
    data_dir: Path | None = None,
) -> tuple[FanartProvider, FakeHttp, FanartPlugin]:
    http = FakeHttp(handler)
    directory = data_dir or Path(tempfile.mkdtemp(prefix="iris-fanart-"))
    store = JsonSettingsStore(directory)
    values: dict[str, Any] = {API_KEY_SECRET: KEY}
    values.update(settings if settings is not None else {})
    for name, value in values.items():
        store.put(name, value)
    plugin = FanartPlugin()
    plugin.start(
        PluginContext(
            plugin_id="builtin.fanart",
            data_dir=directory,
            host=HostInfo(version="test"),
            logger=logging.getLogger("test.fanart"),
            media_dir=directory / "media",
            notify=RecordingNotifier(),
            http=http,
        )
    )
    return cast(FanartProvider, plugin.providers()["image.fetch"]), http, plugin


def test_movie_uses_tmdb_id_and_maps_all_image_types() -> None:
    def handler(request: RecordedRequest) -> Any:
        assert request.path == "/v3/movies/550"
        # Key 只走请求头，不出现在 URL 里
        assert "api_key" not in request.params
        assert request.headers["api-key"] == KEY
        return response(json_body=MOVIE)

    provider, _, _ = make_provider(handler)

    images = provider.images(
        ImageLookupRequest(kind="movie", external_ids={"tmdb": "550", "imdb": "tt0137523"})
    )

    assert [(image.type, image.language, image.score) for image in images] == [
        ("poster", "en", 8.0),
        ("poster", "zh", 2.0),
        ("backdrop", None, 9.0),
        ("thumb", "en", 4.0),
        ("logo", "en", 7.0),
        ("logo", None, 1.0),
    ]
    assert "00" not in {image.language for image in images if image.language is not None}


def test_movie_without_tmdb_uses_imdb() -> None:
    def handler(request: RecordedRequest) -> Any:
        assert request.path == "/v3/movies/tt0137523"
        return response(json_body=MOVIE)

    provider, _, _ = make_provider(handler)

    assert provider.images(ImageLookupRequest(kind="movie", external_ids={"imdb": "tt0137523"}))


def test_movie_rejects_invalid_imdb_without_request() -> None:
    def handler(request: RecordedRequest) -> Any:
        raise AssertionError("IMDb ID 不合法时不应发出请求")

    provider, http, _ = make_provider(handler)

    for imdb_id in ("0137523", "ttabc", "tt"):
        with pytest.raises(MetadataNotFound):
            provider.images(ImageLookupRequest(kind="movie", external_ids={"imdb": imdb_id}))
    assert http.requests == []


def test_series_without_tvdb_returns_empty_without_request() -> None:
    provider, http, _ = make_provider(lambda request: response(500))

    assert (
        provider.images(
            ImageLookupRequest(kind="series", external_ids={"imdb": "tt6468322", "tmdb": "1"})
        )
        == []
    )
    assert http.requests == []


def test_series_uses_tvdb_only() -> None:
    def handler(request: RecordedRequest) -> Any:
        assert request.path == "/v3/tv/328630"
        return response(json_body=TV)

    provider, _, _ = make_provider(handler)

    images = provider.images(ImageLookupRequest(kind="series", external_ids={"tvdb": "328630"}))

    assert [(image.type, image.language, image.score) for image in images] == [
        ("poster", "de", 6.0),
        ("backdrop", None, 5.0),
        ("thumb", "en", 3.0),
        ("logo", "en", 4.0),
        ("logo", None, 2.0),
    ]


def test_client_key_is_sent_as_header() -> None:
    def handler(request: RecordedRequest) -> Any:
        assert not request.params
        assert request.headers["api-key"] == KEY
        assert request.headers["client-key"] == CLIENT_KEY
        return response(json_body=MOVIE)

    provider, _, _ = make_provider(
        handler, settings={API_KEY_SECRET: KEY, CLIENT_KEY_SECRET: CLIENT_KEY}
    )

    assert provider.images(ImageLookupRequest(kind="movie", external_ids={"tmdb": "550"}))


def test_404_is_empty_and_401_is_unavailable() -> None:
    not_found, _, _ = make_provider(lambda request: response(404, json_body={}))
    assert not_found.images(ImageLookupRequest(kind="movie", external_ids={"tmdb": "550"})) == []

    rejected, _, _ = make_provider(lambda request: response(401, json_body={}))
    with pytest.raises(MetadataUnavailable) as error:
        rejected.images(ImageLookupRequest(kind="movie", external_ids={"tmdb": "550"}))
    assert "API Key" in error.value.message


def test_429_uses_retry_after() -> None:
    provider, _, _ = make_provider(
        lambda request: response(429, json_body={}, headers={"Retry-After": "12"})
    )

    with pytest.raises(MetadataUnavailable) as error:
        provider.images(ImageLookupRequest(kind="movie", external_ids={"tmdb": "550"}))

    assert "12" in error.value.message


def test_invalid_payload_is_unavailable() -> None:
    provider, _, _ = make_provider(lambda request: response(text="not json"))

    with pytest.raises(MetadataUnavailable, match="无法解析"):
        provider.images(ImageLookupRequest(kind="movie", external_ids={"tmdb": "550"}))


def test_unexpected_http_error_bubbles() -> None:
    def handler(request: RecordedRequest) -> Any:
        raise RuntimeError("boom")

    provider, _, _ = make_provider(handler)

    with pytest.raises(RuntimeError, match="boom"):
        provider.images(ImageLookupRequest(kind="movie", external_ids={"tmdb": "550"}))


def test_no_key_reports_reason_and_sends_no_request() -> None:
    provider, http, plugin = make_provider(lambda request: response(500), settings={})
    # 用一个没有 Key 的插件，避免默认 Key 干扰
    directory = Path(tempfile.mkdtemp(prefix="iris-fanart-empty-"))
    plugin = FanartPlugin()
    plugin.start(
        PluginContext(
            plugin_id="builtin.fanart",
            data_dir=directory,
            host=HostInfo(version="test"),
            logger=logging.getLogger("test.fanart"),
            media_dir=directory / "media",
            notify=RecordingNotifier(),
            http=http,
        )
    )
    provider = cast(FanartProvider, plugin.providers()["image.fetch"])

    assert provider.unavailable_reason() is not None
    assert "API Key" in (provider.unavailable_reason() or "")
    with pytest.raises(MetadataUnavailable):
        provider.images(ImageLookupRequest(kind="movie", external_ids={"tmdb": "550"}))
    assert http.requests == []


def test_image_downloads_full_url() -> None:
    def handler(request: RecordedRequest) -> Any:
        assert request.url == POSTER
        assert request.headers["accept"] == "image/*"
        return response(content=b"JPEG")

    provider, _, _ = make_provider(handler)

    assert provider.image(POSTER, 780) == b"JPEG"


def test_image_403_reports_invalid_key() -> None:
    provider, _, _ = make_provider(lambda request: response(403))

    with pytest.raises(MetadataUnavailable) as error:
        provider.image(POSTER, 780)

    assert "API Key" in error.value.message


def test_image_404_is_not_found() -> None:
    provider, _, _ = make_provider(lambda request: response(404))

    with pytest.raises(MetadataNotFound):
        provider.image(POSTER, 780)


def test_settings_page_fields_and_status() -> None:
    _, _, plugin = make_provider(lambda request: response(500), settings={API_KEY_SECRET: KEY})
    ui = cast(Any, plugin.providers()["admin.ui"])

    result = ui.handle(PluginRequest("GET", "settings", {}, None, 1))
    assert isinstance(result, PluginResponse)
    body = cast(dict[str, Any], result.body)
    fields = {field["name"]: field for field in body["fields"]}
    assert fields["api_key"]["kind"] == "secret"
    assert fields["api_key"]["required"] is True
    assert fields["client_key"]["kind"] == "secret"
    assert fields["client_key"]["required"] is False
    assert body["values"]["api_key"] == {"set": True}

    status = ui.handle(PluginRequest("GET", "status", {}, None, 1))
    assert cast(dict[str, Any], status.body)["api_key_set"] is True
    assert cast(dict[str, Any], status.body)["client_key_set"] is False


def test_test_connection_uses_provided_key() -> None:
    def handler(request: RecordedRequest) -> Any:
        assert request.path == "/v3/movies/550"
        assert request.headers["api-key"] == KEY
        return response(json_body={})

    provider, http, _ = make_provider(handler, settings={})

    assert provider.test_connection({API_KEY_SECRET: KEY}) is None
    assert len(http.requests) == 1

    rejected, _, _ = make_provider(lambda request: response(401), settings={})
    assert "API Key" in (rejected.test_connection({API_KEY_SECRET: KEY}) or "")

    limited, _, _ = make_provider(
        lambda request: response(429, headers={"Retry-After": "9"}), settings={}
    )
    assert "9" in (limited.test_connection({API_KEY_SECRET: KEY}) or "")


def test_manifest_only_declares_image_fetch() -> None:
    manifest = load_manifest(Path(__file__).resolve().parents[1])

    assert manifest.provides == ("image.fetch", "admin.ui")
