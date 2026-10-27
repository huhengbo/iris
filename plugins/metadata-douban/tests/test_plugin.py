"""豆瓣补缺插件：suggest + IMDb 核对、页面解析、Cookie、反爬和设置页。全程假 HTTP。"""

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
    JsonSettingsStore,
    MetadataNotFound,
    MetadataUnavailable,
    PluginContext,
    PluginRequest,
    PluginRequestError,
    PluginResponse,
)
from iris_plugin_sdk.testing import FakeHttp, RecordedRequest, RecordingNotifier, response

from iris_plugin_douban.parse import is_blocked, parse_subject
from iris_plugin_douban.plugin import DoubanPlugin
from iris_plugin_douban.provider import DEFAULT_USER_AGENT, DoubanProvider

FIXTURES = Path(__file__).parent / "fixtures" / "douban"
SUBJECT = (FIXTURES / "subject.html").read_text(encoding="utf-8")
SUGGEST = json.loads((FIXTURES / "suggest.json").read_text(encoding="utf-8"))
BLOCKED = (FIXTURES / "blocked.html").read_text(encoding="utf-8")
POSTER = "https://img9.doubanio.com/view/photo/s_ratio_poster/public/p480747492.jpg"
COOKIE = "bid=abc123; dbcl2=xyz987"


def make_provider(
    handler: Callable[[RecordedRequest], Any],
    *,
    settings: Mapping[str, Any] | None = None,
    data_dir: Path | None = None,
) -> tuple[DoubanProvider, FakeHttp, DoubanPlugin]:
    http = FakeHttp(handler)
    directory = data_dir or Path(tempfile.mkdtemp(prefix="iris-douban-"))
    store = JsonSettingsStore(directory)
    values: dict[str, Any] = {"user_agent": "IrisTest/1.0", "request_interval": 0}
    values.update(settings or {})
    for name, value in values.items():
        store.put(name, value)
    plugin = DoubanPlugin()
    plugin.start(
        PluginContext(
            plugin_id="builtin.douban",
            data_dir=directory,
            host=HostInfo(version="test"),
            logger=logging.getLogger("test.douban"),
            media_dir=directory / "media",
            notify=RecordingNotifier(),
            http=http,
        )
    )
    return cast(DoubanProvider, plugin.providers()["metadata.details"]), http, plugin


def test_parse_subject_sample() -> None:
    subject = parse_subject(SUBJECT)

    assert subject.title == "肖申克的救赎"
    assert subject.release_date == "1994-09-10"
    assert subject.year == 1994
    assert subject.genres == ("剧情", "犯罪")
    assert subject.rating == 9.7
    assert (
        subject.overview == "一场谋杀案使银行家安迪蒙冤入狱，他用二十年的时间挖出一条通往自由的路。"
    )
    assert subject.poster == POSTER
    assert subject.imdb_id == "tt0111161"
    assert subject.countries == ("美国",)
    assert subject.runtime == 142


def test_parse_failure_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(self: object, html: str) -> None:
        raise ValueError("broken")

    monkeypatch.setattr("iris_plugin_douban.parse._SubjectParser.feed", fail)

    with pytest.raises(MetadataUnavailable, match="解析失败"):
        parse_subject("<html></html>")


def test_blocked_markers_are_specific() -> None:
    assert is_blocked(BLOCKED)
    assert not is_blocked("<html><body><p>异常请求的处理方式</p></body></html>")


def test_find_checks_imdb_on_subject_page() -> None:
    def handler(request: RecordedRequest) -> Any:
        if request.path == "/j/subject_suggest":
            assert request.params["q"] == "tt0111161"
            return response(json_body=SUGGEST)
        if request.path == "/subject/1292052/":
            return response(text=SUBJECT)
        return response(404, text="missing")

    provider, http, _ = make_provider(handler)

    assert provider.find("movie", "imdb", "tt0111161") == "1292052"
    assert [request.path for request in http.requests] == [
        "/j/subject_suggest",
        "/subject/1292052/",
    ]


def test_find_discards_page_with_another_imdb() -> None:
    wrong = SUBJECT.replace("tt0111161", "tt9999999")

    def handler(request: RecordedRequest) -> Any:
        if request.path == "/j/subject_suggest":
            return response(json_body=SUGGEST)
        return response(text=wrong)

    provider, _, _ = make_provider(handler)

    assert provider.find("movie", "imdb", "tt0111161") is None


def test_find_skips_missing_candidate_pages() -> None:
    def handler(request: RecordedRequest) -> Any:
        if request.path == "/j/subject_suggest":
            return response(json_body=SUGGEST)
        if request.path == "/subject/1292052/":
            return response(404, text="")
        return response(text=SUBJECT)

    provider, _, _ = make_provider(handler)

    assert provider.find("movie", "imdb", "tt0111161") == "1292720"


def test_find_does_not_support_tmdb_or_invalid_imdb() -> None:
    provider, http, _ = make_provider(lambda request: response(500))

    assert provider.find("movie", "tmdb", "157336") is None
    assert provider.find("movie", "imdb", "not-an-imdb") is None
    assert http.requests == []


def test_details_send_cookie_and_build_record() -> None:
    def handler(request: RecordedRequest) -> Any:
        assert request.path == "/subject/1292052/"
        assert request.headers["cookie"] == COOKIE
        assert request.headers["user-agent"] == "IrisTest/1.0"
        return response(text=SUBJECT)

    provider, _, _ = make_provider(handler, settings={"cookie": COOKIE})

    record = provider.details("1292052", kind="movie")

    assert record.provider_id == "builtin.douban"
    assert record.remote_id == "1292052"
    assert record.title == "肖申克的救赎"
    assert record.year == 1994
    assert record.release_date == "1994-09-10"
    assert record.rating == 9.7
    assert record.genres == ("剧情", "犯罪")
    assert record.countries == ("美国",)
    assert record.runtime == 142
    assert record.poster == POSTER
    assert record.external_ids == {"douban": "1292052", "imdb": "tt0111161"}


@pytest.mark.parametrize("status", [200, 403])
def test_anti_scrape_page_is_unavailable(status: int) -> None:
    provider, _, _ = make_provider(lambda request: response(status, text=BLOCKED))

    with pytest.raises(MetadataUnavailable) as error:
        provider.details("1292052", kind="movie")

    assert "Cookie" in error.value.message


def test_normal_page_with_blocked_word_is_not_rejected() -> None:
    page = SUBJECT.replace("</body>", "<p>异常请求</p></body>")
    provider, _, _ = make_provider(lambda request: response(text=page))

    record = provider.details("1292052", kind="movie")

    assert record.title == "肖申克的救赎"


def test_missing_subject_is_not_found() -> None:
    provider, _, _ = make_provider(lambda request: response(404, text=""))

    with pytest.raises(MetadataNotFound) as error:
        provider.details("404", kind="movie")

    assert "豆瓣上没有这个条目" in error.value.message


def test_rate_limit_uses_retry_after() -> None:
    provider, _, _ = make_provider(
        lambda request: response(429, text="", headers={"Retry-After": "7"})
    )

    with pytest.raises(MetadataUnavailable) as error:
        provider.details("1292052", kind="movie")

    assert "7" in error.value.message


def test_unexpected_http_error_bubbles() -> None:
    def handler(request: RecordedRequest) -> Any:
        raise RuntimeError("boom")

    provider, _, _ = make_provider(handler)

    with pytest.raises(RuntimeError, match="boom"):
        provider.details("1292052", kind="movie")


def test_suggest_invalid_json_is_unavailable() -> None:
    provider, _, _ = make_provider(lambda request: response(text="not json"))

    with pytest.raises(MetadataUnavailable, match="无法解析"):
        provider.find("movie", "imdb", "tt0111161")


def test_suggest_non_list_is_unavailable() -> None:
    provider, _, _ = make_provider(lambda request: response(json_body={"id": "1292052"}))

    with pytest.raises(MetadataUnavailable, match="无法解析"):
        provider.find("movie", "imdb", "tt0111161")


def test_image_uses_large_poster_and_referer() -> None:
    def handler(request: RecordedRequest) -> Any:
        assert "/l_ratio_poster/" in request.path
        assert request.headers["referer"] == "https://movie.douban.com/"
        assert request.headers["user-agent"] == "IrisTest/1.0"
        return response(content=b"JPEG")

    provider, http, _ = make_provider(handler)

    assert provider.image(POSTER, 780) == b"JPEG"
    assert len(http.requests) == 1


@pytest.mark.parametrize(
    "source",
    [
        "https://doubanio.com/view/photo/s_ratio_poster/public/p480747492.jpg",
        "https://img9.doubanio.com/view/photo/s_ratio_poster/public/p480747492.jpg",
        "https://movie.douban.com/view/photo/s_ratio_poster/public/p480747492.jpg",
    ],
)
def test_image_allows_douban_domains(source: str) -> None:
    provider, http, _ = make_provider(lambda request: response(content=b"JPEG"))

    assert provider.image(source, 780) == b"JPEG"
    assert len(http.requests) == 1


@pytest.mark.parametrize(
    "source",
    [
        "https://evil.com/view/photo/s_ratio_poster/public/p480747492.jpg",
        "https://evil-douban.com/view/photo/s_ratio_poster/public/p480747492.jpg",
        "http://img9.doubanio.com/view/photo/s_ratio_poster/public/p480747492.jpg",
        "https://doubanio.com.evil.com/view/photo/s_ratio_poster/public/p480747492.jpg",
        "https://evildoubanio.com/view/photo/s_ratio_poster/public/p480747492.jpg",
    ],
)
def test_image_rejects_other_domains_without_request(source: str) -> None:
    def handler(request: RecordedRequest) -> Any:
        raise AssertionError("不在豆瓣域名内时不应发出请求")

    provider, http, _ = make_provider(handler, settings={"cookie": COOKIE})

    with pytest.raises(MetadataNotFound):
        provider.image(source, 780)
    assert http.requests == []


def test_settings_page_declares_fields_and_status() -> None:
    provider, _, plugin = make_provider(lambda request: response(500))
    ui = cast(Any, plugin.providers()["admin.ui"])

    page = ui.handle(PluginRequest("GET", "settings", {}, None, 1))
    assert isinstance(page, PluginResponse)
    body = cast(dict[str, Any], page.body)
    fields = {field["name"]: field for field in body["fields"]}
    assert fields["cookie"]["kind"] == "secret"
    assert fields["cookie"]["required"] is False
    assert fields["user_agent"]["default"] == DEFAULT_USER_AGENT
    assert fields["request_interval"]["kind"] == "number"
    assert fields["request_interval"]["default"] == 3
    assert body["values"]["request_interval"] == 0

    status = ui.handle(PluginRequest("GET", "status", {}, None, 1))
    assert cast(dict[str, Any], status.body)["cookie_set"] is False
    assert provider.unavailable_reason() is None


def test_settings_can_store_cookie(tmp_path: Path) -> None:
    provider, _, plugin = make_provider(lambda request: response(500), data_dir=tmp_path)
    ui = cast(Any, plugin.providers()["admin.ui"])

    ui.handle(
        PluginRequest("PUT", "settings", {}, {"cookie": COOKIE, "user_agent": "NewAgent/2.0"}, 1)
    )

    assert provider.unavailable_reason() is None
    values = provider._settings.values()
    assert values["cookie"] == COOKIE
    assert values["user_agent"] == "NewAgent/2.0"
    assert json.loads((tmp_path / "state" / "settings.json").read_text())["cookie"] == COOKIE


def test_test_connection_can_report_success_and_failure() -> None:
    provider, http, _ = make_provider(lambda request: response(text="<html>ok"))
    assert provider.test_connection({"user_agent": "IrisTest/1.0"}) is None
    assert http.requests[0].path == "/"

    blocked, _, _ = make_provider(lambda request: response(text=BLOCKED))
    assert "Cookie" in (blocked.test_connection({"user_agent": "IrisTest/1.0"}) or "")


def test_settings_test_endpoint_returns_message() -> None:
    provider, _, plugin = make_provider(lambda request: response(403, text=""))
    ui = cast(Any, plugin.providers()["admin.ui"])

    result = ui.handle(PluginRequest("POST", "settings/test", {}, {"user_agent": "UA"}, 1))

    assert isinstance(result, PluginResponse)
    body = cast(dict[str, Any], result.body)
    assert body["ok"] is False
    assert "Cookie" in body["message"]
    assert provider.unavailable_reason() is None


def test_bad_settings_request_is_rejected() -> None:
    _, _, plugin = make_provider(lambda request: response(500))
    ui = cast(Any, plugin.providers()["admin.ui"])

    with pytest.raises(PluginRequestError):
        ui.handle(PluginRequest("PUT", "settings", {}, {"request_interval": -1}, 1))
