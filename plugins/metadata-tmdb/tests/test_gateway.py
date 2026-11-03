"""TMDB 网关：外部 ID、fallback_fields、图片映射和 429 重试。全程假 HTTP。"""

from __future__ import annotations

import logging
from typing import Any

import httpx2
import pytest
from iris_plugin_sdk import DirectHttpClient, PluginHttpResponse
from iris_plugin_sdk.testing import FakeHttp, RecordedRequest, response

from iris_plugin_tmdb.errors import TmdbAuthFailed, TmdbNotFound, TmdbUnavailable
from iris_plugin_tmdb.gateway import TmdbAccess
from iris_plugin_tmdb.http import TmdbHttpGateway

V3_KEY = "0123456789abcdef0123456789abcdef"
V4_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJ4In0.c2lnbmF0dXJl"


class Recorder:
    def __init__(self) -> None:
        self.requests: list[RecordedRequest] = []
        self.sleeps: list[float] = []
        self.routes: dict[str, list[PluginHttpResponse]] = {}
        self.http = FakeHttp(self.handle)

    def route(self, path: str, *responses: PluginHttpResponse) -> None:
        self.routes[path] = list(responses)

    def json(self, path: str, body: dict[str, Any], status: int = 200) -> None:
        self.route(path, response(status, json_body=body))

    def handle(self, request: RecordedRequest) -> PluginHttpResponse:
        self.requests.append(request)
        key = request.path
        if request.params.get("language") == "en-US":
            key += "?en"
        responses = self.routes.get(key)
        if not responses:
            return response(404, json_body={})
        return responses.pop(0) if len(responses) > 1 else responses[0]


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def gateway(recorder: Recorder) -> TmdbHttpGateway:
    return TmdbHttpGateway(recorder.http, sleep=recorder.sleeps.append)


def test_movie_details_collect_imdb_and_keep_chinese(
    gateway: TmdbHttpGateway, recorder: Recorder
) -> None:
    recorder.json(
        "/3/movie/157336",
        {
            "id": 157336,
            "title": "星际穿越",
            "original_title": "Interstellar",
            "original_language": "en",
            "imdb_id": "tt0816692",
            "release_date": "2014-11-05",
            "overview": "中文简介",
            "vote_average": 8.4,
            "tagline": "中文标语",
            "translations": {
                "translations": [
                    {
                        "iso_639_1": "zh",
                        "iso_3166_1": "CN",
                        "data": {
                            "title": "星际穿越",
                            "overview": "中文简介",
                            "tagline": "中文标语",
                        },
                    }
                ]
            },
        },
    )

    details = gateway.details(TmdbAccess(V3_KEY), "movie", 157336)

    assert details.external_ids == {"imdb": "tt0816692"}
    assert details.fallback_fields == frozenset()
    # 电影的 IMDb 在详情顶层，不需要额外的 external_ids
    assert "external_ids" not in recorder.requests[0].params["append_to_response"]


def test_movie_details_carry_the_series_name_and_images(
    gateway: TmdbHttpGateway, recorder: Recorder
) -> None:
    recorder.json(
        "/3/movie/157336",
        {
            "id": 157336,
            "title": "星际穿越",
            "original_title": "Interstellar",
            "original_language": "en",
            "release_date": "2014-11-05",
            "belongs_to_collection": {
                "id": 99,
                "name": " 星际系列 ",
                "poster_path": "/cp.jpg",
                "backdrop_path": None,
            },
        },
    )

    details = gateway.details(TmdbAccess(V3_KEY), "movie", 157336)

    assert (details.tmdb_collection_id, details.tmdb_collection_name) == (99, "星际系列")
    assert (details.tmdb_collection_poster, details.tmdb_collection_backdrop) == ("/cp.jpg", None)


def test_series_details_collect_imdb_and_tvdb(gateway: TmdbHttpGateway, recorder: Recorder) -> None:
    recorder.json(
        "/3/tv/108545",
        {
            "id": 108545,
            "name": "三体",
            "original_name": "三体",
            "original_language": "zh",
            "first_air_date": "2023-01-15",
            "external_ids": {"imdb_id": "tt20244034", "tvdb_id": 421183},
        },
    )

    details = gateway.details(TmdbAccess(V3_KEY), "series", 108545)

    assert details.external_ids == {"imdb": "tt20244034", "tvdb": "421183"}
    # 华语片的原名就是中文，不算拿别的语言顶上
    assert details.fallback_fields == frozenset()
    assert "external_ids" in recorder.requests[0].params["append_to_response"]


def test_missing_chinese_text_is_recorded_as_fallback(
    gateway: TmdbHttpGateway, recorder: Recorder
) -> None:
    recorder.json(
        "/3/movie/1",
        {
            "id": 1,
            "title": "",
            "original_title": "Original",
            "original_language": "ja",
            "release_date": "2001-01-01",
            "overview": "",
            "tagline": "",
            "translations": {
                "translations": [
                    {
                        "iso_639_1": "en",
                        "iso_3166_1": "US",
                        "data": {
                            "title": "English Title",
                            "overview": "English overview",
                            "tagline": "English tagline",
                        },
                    }
                ]
            },
        },
    )

    details = gateway.details(TmdbAccess(V3_KEY), "movie", 1)

    assert details.title == "English Title"
    assert details.overview == "English overview"
    assert details.tagline == "English tagline"
    assert details.fallback_fields == frozenset({"title", "overview", "tagline"})


def test_images_map_types_language_and_score(gateway: TmdbHttpGateway, recorder: Recorder) -> None:
    recorder.json(
        "/3/movie/157336/images",
        {
            "posters": [
                {
                    "file_path": "/p.jpg",
                    "iso_639_1": "en",
                    "vote_average": 5.5,
                    "width": 1000,
                    "height": 1500,
                }
            ],
            "backdrops": [
                {"file_path": "/plain.jpg", "iso_639_1": None, "vote_average": 6.0},
                {"file_path": "/zh.jpg", "iso_639_1": "zh", "vote_average": 4.0},
            ],
            "logos": [{"file_path": "/logo.png", "iso_639_1": "en", "vote_average": 3.0}],
        },
    )

    images = gateway.images(TmdbAccess(V3_KEY), "movie", 157336)

    assert [(image.type, image.path, image.language, image.score) for image in images] == [
        ("poster", "/p.jpg", "en", 5.5),
        ("backdrop", "/plain.jpg", None, 6.0),
        ("thumb", "/zh.jpg", "zh", 4.0),
        ("logo", "/logo.png", "en", 3.0),
    ]
    assert images[0].width == 1000 and images[0].height == 1500
    # 取全部图片，不加语言过滤
    assert "include_image_language" not in recorder.requests[0].params
    assert "language" not in recorder.requests[0].params


def test_429_is_retried_once_with_capped_wait() -> None:
    calls: list[str] = []
    sleeps: list[float] = []

    def handler(request: RecordedRequest) -> PluginHttpResponse:
        calls.append(request.path)
        if len(calls) == 1:
            return response(429, headers={"Retry-After": "60"}, json_body={})
        return response(json_body={})

    gateway = TmdbHttpGateway(FakeHttp(handler), sleep=sleeps.append)

    gateway.validate(TmdbAccess(V3_KEY))

    assert len(calls) == 2
    # Retry-After 封顶 10 秒，避免一个请求卡住整个同步
    assert sleeps == [10.0]


def test_429_after_retry_is_unavailable() -> None:
    def handler(request: RecordedRequest) -> PluginHttpResponse:
        return response(429, headers={"Retry-After": "1"}, json_body={})

    gateway = TmdbHttpGateway(FakeHttp(handler), sleep=lambda seconds: None)

    with pytest.raises(TmdbUnavailable):
        gateway.validate(TmdbAccess(V3_KEY))


def test_bearer_token_goes_to_authorization_header() -> None:
    requests: list[RecordedRequest] = []

    def handler(request: RecordedRequest) -> PluginHttpResponse:
        requests.append(request)
        return response(json_body={})

    gateway = TmdbHttpGateway(FakeHttp(handler))
    gateway.validate(TmdbAccess("a.b.c"))

    assert requests[0].headers["authorization"] == "Bearer a.b.c"
    assert "api_key" not in requests[0].params


def test_v3_key_goes_in_query_and_v4_token_in_header(
    gateway: TmdbHttpGateway, recorder: Recorder
) -> None:
    recorder.json("/3/configuration", {})

    gateway.validate(TmdbAccess(V3_KEY))
    gateway.validate(TmdbAccess(V4_TOKEN))

    v3, v4 = recorder.requests
    assert v3.params["api_key"] == V3_KEY and "authorization" not in v3.headers
    assert v4.headers["authorization"] == f"Bearer {V4_TOKEN}"
    assert "api_key" not in v4.params


@pytest.mark.parametrize(
    ("status", "error"),
    [(401, TmdbAuthFailed), (404, TmdbNotFound), (500, TmdbUnavailable)],
)
def test_http_errors_are_mapped(
    gateway: TmdbHttpGateway, recorder: Recorder, status: int, error: type[Exception]
) -> None:
    recorder.json("/3/configuration", {}, status)
    with pytest.raises(error):
        gateway.validate(TmdbAccess(V3_KEY))


def test_v3_key_never_appears_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.params["api_key"] == V3_KEY
        return httpx2.Response(200, json={}, request=request)

    http = DirectHttpClient(transport=httpx2.MockTransport(handler))
    gateway = TmdbHttpGateway(http)
    try:
        with caplog.at_level(logging.DEBUG):
            gateway.validate(TmdbAccess(V3_KEY))
    finally:
        http.close()

    assert "api.themoviedb.org" in caplog.text
    assert any(record.name == "httpx2" for record in caplog.records)
    assert V3_KEY not in caplog.text
    assert "api_key" not in caplog.text


def test_rate_limit_is_retried_once(gateway: TmdbHttpGateway, recorder: Recorder) -> None:
    recorder.route(
        "/3/configuration",
        response(429, headers={"Retry-After": "3"}),
        response(json_body={}),
    )
    gateway.validate(TmdbAccess(V3_KEY))
    assert recorder.sleeps == [3.0]


def test_network_errors_are_unavailable(recorder: Recorder) -> None:
    def fail(request: RecordedRequest) -> PluginHttpResponse:
        raise httpx2.ConnectError("refused")

    gateway = TmdbHttpGateway(FakeHttp(fail))
    with pytest.raises(TmdbUnavailable):
        gateway.validate(TmdbAccess(V3_KEY))


def test_movie_details_prefer_chinese_and_pick_images(
    gateway: TmdbHttpGateway, recorder: Recorder
) -> None:
    recorder.json(
        "/3/movie/157336",
        {
            "id": 157336,
            "title": "Interstellar",
            "original_title": "Interstellar",
            "original_language": "en",
            "release_date": "2014-11-05",
            "overview": "",
            "vote_average": 8.4,
            "runtime": 169,
            "genres": [{"name": "科幻"}],
            "poster_path": "/p.jpg",
            "backdrop_path": "/b.jpg",
            # 不属于任何系列的电影，TMDB 返回 null 而不是缺省
            "belongs_to_collection": None,
            "release_dates": {
                "results": [
                    {"iso_3166_1": "DE", "release_dates": [{"certification": "12"}]},
                    {
                        "iso_3166_1": "US",
                        "release_dates": [{"certification": ""}, {"certification": "PG-13"}],
                    },
                ]
            },
            "credits": {
                "cast": [{"id": i, "name": f"Actor {i}", "character": "Cooper"} for i in range(30)],
                "crew": [
                    {"id": 525, "name": "Christopher Nolan", "job": "Director"},
                    {"id": 525, "name": "Christopher Nolan", "job": "Director"},
                    {"id": 9, "name": "Writer", "job": "Screenplay"},
                    {"id": 9, "name": "Writer", "job": "Story"},
                    {"id": 10, "name": "Novelist", "job": "Writer"},
                    {"id": 11, "name": "Producer", "job": "Producer"},
                ],
            },
            "translations": {
                "translations": [
                    {"iso_639_1": "zh", "iso_3166_1": "CN", "data": {"title": "星际穿越"}},
                    {
                        "iso_639_1": "en",
                        "iso_3166_1": "US",
                        "data": {"title": "Interstellar", "overview": "A team of explorers"},
                    },
                ]
            },
            "images": {
                "backdrops": [
                    {"iso_639_1": None, "file_path": "/plain.jpg", "vote_average": 9},
                    {"iso_639_1": "en", "file_path": "/en.jpg", "vote_average": 8},
                    {"iso_639_1": "zh", "file_path": "/zh.jpg", "vote_average": 1},
                ],
                "logos": [
                    {"iso_639_1": "zh", "file_path": "/zh.svg", "vote_average": 9},
                    {"iso_639_1": "en", "file_path": "/en.png", "vote_average": 5},
                ],
            },
        },
    )

    details = gateway.details(TmdbAccess(V3_KEY), "movie", 157336)

    assert details.title == "星际穿越"
    assert details.en_title == "Interstellar"
    assert details.year == 2014
    assert details.overview == "A team of explorers"
    assert details.rating == 8.4
    assert details.thumb_path == "/zh.jpg"
    assert details.logo_path == "/en.png"
    assert details.tmdb_collection_id is None
    assert len(details.cast) == 20
    assert [d.name for d in details.directors] == ["Christopher Nolan"]
    assert [w.name for w in details.writers] == ["Writer", "Novelist"]
    assert details.content_rating == "PG-13"
    params = recorder.requests[0].params
    assert params["language"] == "zh-CN"
    assert "credits" in params["append_to_response"]
    assert "release_dates" in params["append_to_response"]


def test_series_use_aggregate_credits_and_chinese_original_title(
    gateway: TmdbHttpGateway, recorder: Recorder
) -> None:
    recorder.json(
        "/3/tv/108545",
        {
            "id": 108545,
            "name": "三体",
            "original_name": "三体",
            "original_language": "zh",
            "first_air_date": "2023-01-15",
            "episode_run_time": [],
            "created_by": [{"id": 7, "name": "杨磊"}],
            "aggregate_credits": {
                "cast": [{"id": 1, "name": "张鲁一", "roles": [{"character": "史强"}]}],
                "crew": [
                    {"id": 5, "name": "刘慈欣", "jobs": [{"job": "Novel"}, {"job": "Story"}]},
                    {"id": 6, "name": "摄影", "jobs": [{"job": "Director of Photography"}]},
                ],
            },
            "content_ratings": {
                "results": [
                    {"iso_3166_1": "KR", "rating": "15"},
                    {"iso_3166_1": "HK", "rating": "IIB"},
                ]
            },
        },
    )

    details = gateway.details(TmdbAccess(V3_KEY), "series", 108545)

    assert details.title == "三体"
    assert details.runtime is None
    assert details.cast[0].character == "史强"
    assert details.directors[0].name == "杨磊"
    assert [w.name for w in details.writers] == ["刘慈欣"]
    assert details.content_rating == "IIB"
    append = recorder.requests[0].params["append_to_response"]
    assert "aggregate_credits" in append
    assert "content_ratings" in append


def test_season_falls_back_to_english_for_missing_text(
    gateway: TmdbHttpGateway, recorder: Recorder
) -> None:
    recorder.json(
        "/3/tv/1/season/1",
        {
            "season_number": 1,
            "name": "第 1 季",
            "episodes": [
                {"episode_number": 1, "name": "第 1 集", "overview": "中文简介"},
                {"episode_number": 2, "name": "", "overview": ""},
            ],
        },
    )
    recorder.json(
        "/3/tv/1/season/1?en",
        {
            "episodes": [
                {"episode_number": 1, "name": "Episode 1", "overview": "English"},
                {"episode_number": 2, "name": "Episode 2", "overview": "Second"},
            ]
        },
    )

    season = gateway.season(TmdbAccess(V3_KEY), 1, 1)

    assert [(e.name, e.overview) for e in season.episodes] == [
        ("第 1 集", "中文简介"),
        ("Episode 2", "Second"),
    ]


def test_search_and_find(gateway: TmdbHttpGateway, recorder: Recorder) -> None:
    recorder.json(
        "/3/search/tv",
        {"results": [{"id": 5, "name": "", "original_name": "Dark", "first_air_date": ""}]},
    )
    recorder.json("/3/find/tt0816692", {"movie_results": [{"id": 157336}], "tv_results": []})

    results = gateway.search(TmdbAccess(V3_KEY), "series", "Dark")

    assert [(r.tmdb_id, r.title, r.year) for r in results] == [(5, "Dark", None)]
    assert gateway.find_imdb(TmdbAccess(V3_KEY), "movie", "tt0816692") == 157336
    assert gateway.find_imdb(TmdbAccess(V3_KEY), "series", "tt0816692") is None


def test_image_requests_are_not_authorized(gateway: TmdbHttpGateway, recorder: Recorder) -> None:
    recorder.route("/t/p/w780/p.jpg", response(content=b"jpeg"))

    assert gateway.image(TmdbAccess(V3_KEY), "/p.jpg", 780) == b"jpeg"
    assert "api_key" not in recorder.requests[0].params
    assert "authorization" not in recorder.requests[0].headers


def test_invalid_json_is_unavailable(gateway: TmdbHttpGateway, recorder: Recorder) -> None:
    recorder.route("/3/configuration", response(text="<html>"))
    with pytest.raises(TmdbUnavailable):
        gateway.validate(TmdbAccess(V3_KEY))
