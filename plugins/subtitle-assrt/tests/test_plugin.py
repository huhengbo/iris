"""射手网插件：官方字段、详情下载、压缩包、限流和设置页。全程假 HTTP。"""

from __future__ import annotations

import io
import logging
import tempfile
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import pytest
from iris_plugin_sdk import (
    HostInfo,
    JsonSettingsStore,
    MediaContext,
    PluginContext,
    PluginRequest,
    PluginResponse,
    SubtitleCandidate,
    SubtitleSearchRequest,
    SubtitleUnavailable,
)
from iris_plugin_sdk.testing import FakeHttp, RecordedRequest, RecordingNotifier, response

from iris_plugin_assrt.plugin import AssrtPlugin
from iris_plugin_assrt.provider import (
    DEFAULT_HOST,
    DEFAULT_RATE,
    HOST_FIELD,
    MAX_UNCOMPRESSED,
    RATE_FIELD,
    TOKEN_SECRET,
    AssrtProvider,
)

TOKEN = "secret-assrt-token"
SRT = b"1\n00:00:01,000 --> 00:00:02,000\nHi\n"
ASS = b"[Script Info]\nScriptType: v4.00+\nDialogue: 0,0:00:01.00\n"

SEARCH = {
    "status": 0,
    "sub": {
        "subs": [
            {
                "id": 11,
                "native_name": "流浪地球 / The Wandering Earth",
                "videoname": "The.Wandering.Earth.2019.1080p.BluRay",
                "upload_time": "2024-01-02 03:04:05",
                "down_count": 120,
                "vote_score": 12,
                "lang": {"langlist": {"langchs": True}, "desc": "简体"},
                # 官方搜索接口的 filelist 只有文件名和大小，不返回下载地址
                "filelist": [{"f": "流浪地球.2019.1080p.chs.srt", "s": "52KB"}],
            }
        ]
    },
}

DETAIL = {
    "status": 0,
    "sub": {
        "subs": [
            {
                "id": 11,
                "native_name": "流浪地球 / The Wandering Earth",
                "videoname": "The.Wandering.Earth.2019.1080p.BluRay",
                "filename": "流浪地球.2019.1080p.chs.rar",
                "size": 20180,
                "url": "http://file0.assrt.net/download/11/package.rar",
                "upload_time": "2024-01-02 03:04:05",
                "down_count": 120,
                "lang": {"langlist": {"langchs": True}, "desc": "简体"},
                "filelist": [
                    {
                        "url": "http://file0.assrt.net/onthefly/11/movie.srt",
                        "f": "流浪地球.2019.1080p.chs.srt",
                        "s": "52KB",
                    }
                ],
            }
        ]
    },
}

SERIES_SEARCH = {
    "status": 0,
    "sub": {
        "subs": [
            {
                "id": 22,
                "native_name": "三体",
                "videoname": "Three-Body.S01E01.2160p",
                "upload_time": "2024-02-03 04:05:06",
                "down_count": 30,
                "lang": {"langlist": {"langchs": True}, "desc": "简体"},
                "filelist": [{"f": "三体.S01E01.chs.ass", "s": "30KB"}],
            },
            {
                "id": 23,
                "native_name": "三体",
                "videoname": "Three-Body.S01E02.2160p",
                "upload_time": "2024-02-03 04:05:06",
                "down_count": 20,
                "lang": {"langlist": {"langchs": True}, "desc": "简体"},
                "filelist": [{"f": "三体.S01E02.chs.ass", "s": "30KB"}],
            },
        ]
    },
}

SERIES_DETAIL = {
    "status": 0,
    "sub": {
        "subs": [
            {
                "id": 22,
                "filename": "三体.S01E01.chs.ass",
                "url": "http://file0.assrt.net/onthefly/22/three-body.ass",
                "filelist": [
                    {
                        "url": "http://file0.assrt.net/onthefly/22/three-body.ass",
                        "f": "三体.S01E01.chs.ass",
                        "s": "30KB",
                    }
                ],
            }
        ]
    },
}


def make_provider(
    handler: Callable[[RecordedRequest], Any],
    *,
    rate: int = 5,
    settings: Mapping[str, Any] | None = None,
    data_dir: Path | None = None,
) -> tuple[AssrtProvider, FakeHttp]:
    http = FakeHttp(handler)
    directory = data_dir or Path(tempfile.mkdtemp(prefix="iris-assrt-"))
    store = JsonSettingsStore(directory)
    values: dict[str, Any] = {
        TOKEN_SECRET: TOKEN,
        HOST_FIELD: DEFAULT_HOST,
        RATE_FIELD: rate,
    }
    values.update(settings or {})
    for name, value in values.items():
        store.put(name, value)
    plugin = AssrtPlugin()
    plugin.start(
        PluginContext(
            plugin_id="builtin.assrt",
            data_dir=directory,
            host=HostInfo(version="test"),
            logger=logging.getLogger("test.assrt"),
            media_dir=directory / "media",
            notify=RecordingNotifier(),
            http=http,
        )
    )
    return cast(AssrtProvider, plugin.providers()["subtitle.download"]), http


def media(**kwargs: Any) -> SubtitleSearchRequest:
    filename = kwargs.pop("filename", "流浪地球.2019.1080p.BluRay.mkv")
    context = MediaContext(
        title="流浪地球",
        original_title="The Wandering Earth",
        year=2019,
        filename=filename,
        kind="movie",
        **kwargs,
    )
    return SubtitleSearchRequest(media=context, language="zh-Hans")


def series_request() -> SubtitleSearchRequest:
    return SubtitleSearchRequest(
        media=MediaContext(
            title="三体",
            season=1,
            episode=1,
            filename="三体.S01E01.2160p.mkv",
            kind="series",
        ),
        language="zh-Hans",
    )


def test_search_download_and_episode_filter() -> None:
    def handler(request: RecordedRequest) -> Any:
        if request.path == "/v1/sub/search":
            if request.params["q"] == "流浪地球.2019.1080p.BluRay":
                assert request.params["is_file"] == "1"
                assert request.params["filelist"] == "1"
                return response(json_body=SEARCH)
            if request.params["q"] == "三体.S01E01.2160p":
                return response(json_body=SERIES_SEARCH)
            return response(json_body={"status": 0, "sub": {"subs": []}})
        if request.path == "/v1/sub/detail":
            if request.params["id"] == "11":
                return response(json_body=DETAIL)
            if request.params["id"] == "22":
                return response(json_body=SERIES_DETAIL)
            return response(404)
        if request.path == "/onthefly/11/movie.srt":
            return response(content=SRT)
        if request.path == "/onthefly/22/three-body.ass":
            return response(content=ASS)
        return response(404)

    provider, http = make_provider(handler)

    [candidate] = provider.search(media())
    assert candidate.provider_id == "builtin.assrt"
    assert candidate.remote_id == "11/流浪地球.2019.1080p.chs.srt"
    assert candidate.language == "zh-Hans"
    assert candidate.format == "srt"
    assert candidate.release == "The.Wandering.Earth.2019.1080p.BluRay"
    assert candidate.downloads == 120
    assert candidate.score == 12.0
    assert candidate.uploaded_at == "2024-01-02 03:04:05"
    assert "简体中文" in candidate.label

    download = provider.download(candidate)
    assert download.content == SRT
    assert (download.language, download.format) == ("zh-Hans", "srt")

    [episode] = provider.search(series_request())
    assert episode.remote_id == "22/三体.S01E01.chs.ass"
    series_download = provider.download(episode)
    assert series_download.format == "ass"
    assert series_download.content == ASS

    # Token 只在 Authorization 头里，不放到 URL 查询参数
    api_requests = [r for r in http.requests if urlsplit(r.url).hostname == "api.assrt.net"]
    assert api_requests and all(
        request.headers["authorization"] == f"Bearer {TOKEN}" for request in api_requests
    )
    assert all(TOKEN not in str(request.url) for request in http.requests)


def zip_bytes(*names: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, SRT)
    return buffer.getvalue()


def test_zip_download_unpacks_and_rejects_unsafe_entries() -> None:
    payload = zip_bytes("subs/movie.chs.srt")

    def handler(request: RecordedRequest) -> Any:
        if request.path == "/v1/sub/detail":
            return response(
                json_body={
                    "status": 0,
                    "sub": {
                        "subs": [
                            {
                                "id": 33,
                                "filelist": [
                                    {"url": "http://file0.assrt.net/33.zip", "f": "33.zip"}
                                ],
                            }
                        ]
                    },
                }
            )
        if request.path == "/33.zip":
            return response(content=payload)
        return response(404)

    provider, _ = make_provider(handler)
    candidate = SubtitleCandidate("builtin.assrt", "33/33.zip", "字幕包", "zh-Hans", "zip")
    download = provider.download(candidate)
    assert download.format == "srt"
    assert download.content == SRT

    unsafe = zip_bytes("../evil.srt")
    provider, _ = make_provider(
        lambda request: (
            response(
                json_body={
                    "status": 0,
                    "sub": {
                        "subs": [
                            {
                                "id": 34,
                                "filelist": [
                                    {"url": "http://file0.assrt.net/34.zip", "f": "34.zip"}
                                ],
                            }
                        ]
                    },
                }
            )
            if request.path == "/v1/sub/detail"
            else response(content=unsafe)
        )
    )
    with pytest.raises(SubtitleUnavailable, match="不安全"):
        provider.download(
            SubtitleCandidate("builtin.assrt", "34/34.zip", "字幕包", "zh-Hans", "zip")
        )


def test_rar_and_local_rate_limit() -> None:
    def rar_handler(request: RecordedRequest) -> Any:
        if request.path == "/v1/sub/detail":
            return response(
                json_body={
                    "status": 0,
                    "sub": {
                        "subs": [
                            {
                                "id": 44,
                                "filelist": [
                                    {"url": "http://file0.assrt.net/44.rar", "f": "44.rar"}
                                ],
                            }
                        ]
                    },
                }
            )
        return response(content=b"Rar!\x1a\x07\x00")

    provider, _ = make_provider(rar_handler)
    with pytest.raises(SubtitleUnavailable, match="暂不支持"):
        provider.download(
            SubtitleCandidate("builtin.assrt", "44/44.rar", "字幕包", "zh-Hans", "rar")
        )

    def search_handler(request: RecordedRequest) -> Any:
        return response(json_body=SEARCH)

    provider, _ = make_provider(search_handler, rate=1)
    provider.search(media())
    with pytest.raises(SubtitleUnavailable) as limited:
        provider.search(media(filename="流浪地球.2019.2160p.mkv"))
    assert limited.value.retry_after == 60


def test_quota_test_connection_and_status_errors() -> None:
    def handler(request: RecordedRequest) -> Any:
        if request.path == "/v1/user/quota":
            assert request.headers["authorization"] == f"Bearer {TOKEN}"
            return response(json_body={"status": 0, "quota": 100})
        return response(json_body={"status": 1, "errmsg": "invalid token: abc"})

    provider, http = make_provider(handler)
    assert provider.test_connection() is None
    # 业务错误的 errmsg 即使包含 token 字样也不能原样给用户
    with pytest.raises(SubtitleUnavailable) as wrong:
        provider.search(media())
    assert "abc" not in wrong.value.message
    assert all(TOKEN not in str(request.url) for request in http.requests)


@pytest.mark.parametrize("url", ["https://evil.com/55.srt", "http://assrt.net.evil.com/55.srt"])
def test_download_rejects_non_assrt_url_without_request(url: str) -> None:
    def handler(request: RecordedRequest) -> Any:
        if request.path == "/v1/sub/detail":
            return response(
                json_body={
                    "status": 0,
                    "sub": {
                        "subs": [
                            {
                                "id": 55,
                                "filelist": [{"url": url, "f": "55.srt", "s": "1KB"}],
                            }
                        ]
                    },
                }
            )
        raise AssertionError("不应请求非 assrt.net 的下载地址")

    provider, _ = make_provider(handler)

    with pytest.raises(SubtitleUnavailable, match="地址"):
        provider.download(SubtitleCandidate("builtin.assrt", "55/55.srt", "字幕", "zh-Hans", "srt"))


def test_download_size_limit() -> None:
    def handler(request: RecordedRequest) -> Any:
        if request.path == "/v1/sub/detail":
            return response(
                json_body={
                    "status": 0,
                    "sub": {
                        "subs": [
                            {
                                "id": 66,
                                "filelist": [
                                    {"url": "http://file0.assrt.net/66.srt", "f": "66.srt"}
                                ],
                            }
                        ]
                    },
                }
            )
        return response(content=b"x", headers={"content-length": str(MAX_UNCOMPRESSED + 1)})

    provider, _ = make_provider(handler)

    with pytest.raises(SubtitleUnavailable, match="5 MiB"):
        provider.download(SubtitleCandidate("builtin.assrt", "66/66.srt", "字幕", "zh-Hans", "srt"))


@pytest.mark.parametrize(("status", "retry_after"), [(429, 30), (509, 60)])
def test_rate_limit_statuses(status: int, retry_after: int) -> None:
    headers = {"Retry-After": str(retry_after)} if status == 429 else {}
    provider, _ = make_provider(lambda request: response(status, headers=headers))

    with pytest.raises(SubtitleUnavailable) as error:
        provider.search(media())

    assert error.value.retry_after == retry_after


def test_alias_fields_are_not_accepted() -> None:
    def handler(request: RecordedRequest) -> Any:
        return response(
            json_body={
                "status": 0,
                "sub": {
                    "subs": [
                        {
                            "fileid": 77,
                            "sub_name": "alias.srt",
                            "m_langn": "简体",
                            "url": "http://file0.assrt.net/77.srt",
                        }
                    ]
                },
            }
        )

    provider, _ = make_provider(handler)

    assert provider.search(media()) == []


def test_top_level_filename_and_url_are_used_without_filelist() -> None:
    def handler(request: RecordedRequest) -> Any:
        if request.path == "/v1/sub/detail":
            return response(
                json_body={
                    "status": 0,
                    "sub": {
                        "subs": [
                            {
                                "id": 88,
                                "filename": "88.srt",
                                "url": "http://file0.assrt.net/88.srt",
                            }
                        ]
                    },
                }
            )
        return response(content=SRT)

    provider, _ = make_provider(handler)

    download = provider.download(
        SubtitleCandidate("builtin.assrt", "88/88.srt", "字幕", "zh-Hans", "srt")
    )
    assert download.content == SRT


def test_unexpected_http_error_bubbles() -> None:
    def handler(request: RecordedRequest) -> Any:
        raise RuntimeError("boom")

    provider, _ = make_provider(handler)

    with pytest.raises(RuntimeError, match="boom"):
        provider.search(media())


def test_missing_token_is_reported_without_network() -> None:
    def handler(request: RecordedRequest) -> Any:
        raise AssertionError("没有 Token 时不应该发请求")

    http = FakeHttp(handler)
    directory = Path(tempfile.mkdtemp(prefix="iris-assrt-empty-"))
    plugin = AssrtPlugin()
    plugin.start(
        PluginContext(
            plugin_id="builtin.assrt",
            data_dir=directory,
            host=HostInfo(version="test"),
            logger=logging.getLogger("test.assrt"),
            media_dir=directory / "media",
            notify=RecordingNotifier(),
            http=http,
        )
    )
    provider = cast(AssrtProvider, plugin.providers()["subtitle.search"])
    assert provider.unavailable_reason() == "还没有填写 API Token"
    with pytest.raises(SubtitleUnavailable):
        provider.search(media())
    assert http.requests == []


def test_settings_page_fields_and_status() -> None:
    http = FakeHttp(lambda request: response(500))
    directory = Path(tempfile.mkdtemp(prefix="iris-assrt-settings-"))
    plugin = AssrtPlugin()
    plugin.start(
        PluginContext(
            plugin_id="builtin.assrt",
            data_dir=directory,
            host=HostInfo(version="test"),
            logger=logging.getLogger("test.assrt"),
            media_dir=directory / "media",
            notify=RecordingNotifier(),
            http=http,
        )
    )
    ui = cast(Any, plugin.providers()["admin.ui"])

    page = ui.handle(PluginRequest("GET", "settings", {}, None, 1))
    assert isinstance(page, PluginResponse)
    body = cast(dict[str, Any], page.body)
    fields = {field["name"]: field for field in body["fields"]}
    assert fields["api_token"]["kind"] == "secret"
    assert fields["api_token"]["required"] is True
    assert fields["api_host"]["options"] == ["api.assrt.net", "api.makedie.me"]
    assert fields["requests_per_minute"]["default"] == DEFAULT_RATE
    assert body["values"]["api_host"] == DEFAULT_HOST

    status = ui.handle(PluginRequest("GET", "status", {}, None, 1))
    assert cast(dict[str, Any], status.body)["token_set"] is False
