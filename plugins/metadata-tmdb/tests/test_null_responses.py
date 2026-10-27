"""TMDB 把容器字段返回成 null 或直接省略时，网关不能崩。"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from iris_plugin_tmdb.errors import TmdbNotFound
from iris_plugin_tmdb.gateway import TmdbAccess, TmdbDetails, TmdbKind
from iris_plugin_tmdb.http import TmdbHttpGateway
from tests.test_gateway import Recorder

KEY = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def gateway(recorder: Recorder) -> TmdbHttpGateway:
    return TmdbHttpGateway(recorder.http, sleep=recorder.sleeps.append)


def _mutate(body: dict[str, Any], path: str, *, remove: bool) -> dict[str, Any]:
    """把嵌套字段置为 null，或按 remove 删掉该键；列表用数字下标。"""
    clone = copy.deepcopy(body)
    parts = path.split(".")
    target: Any = clone
    for part in parts[:-1]:
        target = target[int(part)] if isinstance(target, list) else target[part]
    key = parts[-1]
    if isinstance(target, list):
        index = int(key)
        if remove:
            del target[index]
        else:
            target[index] = None
    elif remove:
        target.pop(key, None)
    else:
        target[key] = None
    return clone


MOVIE_DETAILS: dict[str, Any] = {
    "id": 157336,
    "title": "星际穿越",
    "original_title": "Interstellar",
    "original_language": "en",
    "release_date": "2014-11-05",
    "overview": "中文简介",
    "tagline": "中文标语",
    "vote_average": 8.4,
    "runtime": 169,
    "imdb_id": "tt0816692",
    "genres": [{"id": 878, "name": "科幻"}],
    "production_companies": [{"id": 1, "name": "Legendary Pictures"}],
    "production_countries": [{"iso_3166_1": "US", "name": "United States"}],
    "belongs_to_collection": {"id": 10, "name": "星际穿越系列"},
    "credits": {
        "cast": [{"id": 1, "name": "马修·麦康纳", "character": "Cooper"}],
        "crew": [
            {"id": 2, "name": "克里斯托弗·诺兰", "job": "Director"},
            {"id": 3, "name": "乔纳森·诺兰", "job": "Writer"},
        ],
    },
    "translations": {
        "translations": [
            {"iso_639_1": "zh", "iso_3166_1": "CN", "data": {"title": "星际穿越"}},
            {"iso_639_1": "en", "iso_3166_1": "US", "data": {"title": "Interstellar"}},
        ]
    },
    "images": {
        "posters": [{"file_path": "/poster.jpg", "iso_639_1": "zh", "vote_average": 5}],
        "backdrops": [{"file_path": "/backdrop.jpg", "iso_639_1": "zh", "vote_average": 5}],
        "logos": [{"file_path": "/logo.png", "iso_639_1": "zh", "vote_average": 5}],
    },
    "release_dates": {
        "results": [
            {"iso_3166_1": "US", "release_dates": [{"certification": "PG-13"}]},
        ]
    },
}

MOVIE_PATHS = [
    "credits",
    "credits.cast",
    "credits.crew",
    "translations",
    "translations.translations",
    "images",
    "images.posters",
    "images.backdrops",
    "images.logos",
    "genres",
    "production_companies",
    "production_countries",
    "belongs_to_collection",
    "release_dates",
    "release_dates.results",
    "release_dates.results.0.release_dates",
]


def _assert_movie_detail(details: TmdbDetails, path: str) -> None:
    if path == "credits":
        assert details.cast == []
        assert details.directors == []
        assert details.writers == []
    elif path == "credits.cast":
        assert details.cast == []
    elif path == "credits.crew":
        assert details.directors == []
        assert details.writers == []
    elif path.startswith("translations"):
        assert details.en_title is None
    elif path == "images":
        assert details.thumb_path is None
        assert details.logo_path is None
        assert details.artwork == {"poster": [], "backdrop": [], "thumb": [], "logo": []}
    elif path == "images.posters":
        assert details.artwork["poster"] == []
    elif path == "images.backdrops":
        assert details.thumb_path is None
        assert details.artwork["backdrop"] == []
        assert details.artwork["thumb"] == []
    elif path == "images.logos":
        assert details.logo_path is None
        assert details.artwork["logo"] == []
    elif path == "genres":
        assert details.genres == []
    elif path == "production_companies":
        assert details.studios == []
    elif path == "production_countries":
        assert details.countries == []
    elif path == "belongs_to_collection":
        assert details.tmdb_collection_id is None
    else:
        assert details.content_rating is None


@pytest.mark.parametrize("remove", [False, True], ids=["null", "missing"])
@pytest.mark.parametrize("path", MOVIE_PATHS)
def test_movie_details_tolerate_null_containers(
    gateway: TmdbHttpGateway, recorder: Recorder, path: str, remove: bool
) -> None:
    recorder.json("/3/movie/157336", _mutate(MOVIE_DETAILS, path, remove=remove))

    details = gateway.details(TmdbAccess(KEY), "movie", 157336)

    _assert_movie_detail(details, path)


SERIES_DETAILS: dict[str, Any] = {
    "id": 108545,
    "name": "三体",
    "original_name": "三体",
    "original_language": "zh",
    "first_air_date": "2023-01-15",
    "overview": "中文简介",
    "vote_average": 8.0,
    "episode_run_time": [45],
    "created_by": [{"id": 7, "name": "杨磊"}],
    "aggregate_credits": {
        "cast": [{"id": 1, "name": "张鲁一", "roles": [{"character": "史强"}]}],
        "crew": [{"id": 5, "name": "刘慈欣", "jobs": [{"job": "Story"}]}],
    },
    "translations": {
        "translations": [
            {"iso_639_1": "en", "iso_3166_1": "US", "data": {"name": "Three-Body"}},
        ]
    },
    "images": {
        "posters": [{"file_path": "/poster.jpg", "iso_639_1": "zh", "vote_average": 5}],
        "backdrops": [{"file_path": "/backdrop.jpg", "iso_639_1": "zh", "vote_average": 5}],
        "logos": [{"file_path": "/logo.png", "iso_639_1": "zh", "vote_average": 5}],
    },
    "genres": [{"id": 10765, "name": "科幻"}],
    "production_companies": [{"id": 1, "name": "腾讯视频"}],
    "production_countries": [{"iso_3166_1": "CN", "name": "China"}],
    "external_ids": {"imdb_id": "tt20244034", "tvdb_id": 421183},
    "content_ratings": {
        "results": [
            {"iso_3166_1": "US", "rating": "TV-MA"},
        ]
    },
}

SERIES_PATHS = [
    "aggregate_credits",
    "aggregate_credits.cast",
    "aggregate_credits.crew",
    "created_by",
    "episode_run_time",
    "translations",
    "translations.translations",
    "images",
    "images.posters",
    "images.backdrops",
    "images.logos",
    "genres",
    "production_companies",
    "production_countries",
    "external_ids",
    "external_ids.imdb_id",
    "external_ids.tvdb_id",
    "content_ratings",
    "content_ratings.results",
]


def _assert_series_detail(details: TmdbDetails, path: str) -> None:
    if path == "aggregate_credits":
        assert details.cast == []
        assert details.writers == []
    elif path == "aggregate_credits.cast":
        assert details.cast == []
    elif path == "aggregate_credits.crew":
        assert details.writers == []
    elif path == "created_by":
        assert details.directors == []
    elif path == "episode_run_time":
        assert details.runtime is None
    elif path.startswith("translations"):
        assert details.en_title is None
    elif path == "images":
        assert details.thumb_path is None
        assert details.logo_path is None
        assert details.artwork == {"poster": [], "backdrop": [], "thumb": [], "logo": []}
    elif path == "images.posters":
        assert details.artwork["poster"] == []
    elif path == "images.backdrops":
        assert details.thumb_path is None
        assert details.artwork["backdrop"] == []
        assert details.artwork["thumb"] == []
    elif path == "images.logos":
        assert details.logo_path is None
        assert details.artwork["logo"] == []
    elif path == "genres":
        assert details.genres == []
    elif path == "production_companies":
        assert details.studios == []
    elif path == "production_countries":
        assert details.countries == []
    elif path == "external_ids":
        assert details.external_ids == {}
    elif path == "external_ids.imdb_id":
        assert "imdb" not in details.external_ids
    elif path == "external_ids.tvdb_id":
        assert "tvdb" not in details.external_ids
    else:
        assert details.content_rating is None


@pytest.mark.parametrize("remove", [False, True], ids=["null", "missing"])
@pytest.mark.parametrize("path", SERIES_PATHS)
def test_series_details_tolerate_null_containers(
    gateway: TmdbHttpGateway, recorder: Recorder, path: str, remove: bool
) -> None:
    recorder.json("/3/tv/108545", _mutate(SERIES_DETAILS, path, remove=remove))

    details = gateway.details(TmdbAccess(KEY), "series", 108545)

    _assert_series_detail(details, path)


@pytest.mark.parametrize("remove", [False, True], ids=["null", "missing"])
def test_movie_details_skip_credits_without_id(
    gateway: TmdbHttpGateway, recorder: Recorder, remove: bool
) -> None:
    body = copy.deepcopy(MOVIE_DETAILS)
    body["credits"] = {
        "cast": [
            {"name": "没有 ID"},
            {"id": None, "name": "ID 为 null"},
            {"id": 1, "name": "演员"},
        ],
        "crew": [
            {"name": "没有 ID 的导演", "job": "Director"},
            {"id": None, "name": "ID 为 null 的导演", "job": "Director"},
            {"id": 2, "name": "导演", "job": "Director"},
            {"name": "没有 ID 的编剧", "job": "Writer"},
            {"id": 3, "name": "编剧", "job": "Writer"},
        ],
    }
    if remove:
        del body["credits"]["cast"][0]
        del body["credits"]["crew"][0]
    recorder.json("/3/movie/157336", body)

    details = gateway.details(TmdbAccess(KEY), "movie", 157336)

    assert [person.tmdb_id for person in details.cast] == [1]
    assert [person.tmdb_id for person in details.directors] == [2]
    assert [person.tmdb_id for person in details.writers] == [3]


SEASON: dict[str, Any] = {
    "season_number": 1,
    "name": "第 1 季",
    "overview": "中文简介",
    "air_date": "2023-01-15",
    "poster_path": "/season.jpg",
    "episodes": [
        {"episode_number": 1, "name": "第 1 集", "overview": "中文简介"},
        {"episode_number": 2, "name": "", "overview": ""},
    ],
}

SEASON_EN: dict[str, Any] = {
    "episodes": [
        {"episode_number": 1, "name": "Episode 1", "overview": "English"},
        {"episode_number": 2, "name": "Episode 2", "overview": "Second"},
    ]
}


@pytest.mark.parametrize("remove", [False, True], ids=["null", "missing"])
def test_season_tolerates_null_episodes(
    gateway: TmdbHttpGateway, recorder: Recorder, remove: bool
) -> None:
    recorder.json("/3/tv/1/season/1", _mutate(SEASON, "episodes", remove=remove))

    season = gateway.season(TmdbAccess(KEY), 1, 1)

    assert season.episodes == []
    # 没有剧集就不必再发英文回退请求
    assert len(recorder.requests) == 1


@pytest.mark.parametrize("remove", [False, True], ids=["null", "missing"])
def test_season_tolerates_null_fallback_episodes(
    gateway: TmdbHttpGateway, recorder: Recorder, remove: bool
) -> None:
    recorder.json("/3/tv/1/season/1", SEASON)
    recorder.json("/3/tv/1/season/1?en", _mutate(SEASON_EN, "episodes", remove=remove))

    season = gateway.season(TmdbAccess(KEY), 1, 1)

    assert [episode.number for episode in season.episodes] == [1, 2]
    assert season.episodes[1].name is None
    assert season.episodes[1].overview is None


@pytest.mark.parametrize("remove", [False, True], ids=["null", "missing"])
def test_season_missing_number_falls_back_to_argument(
    gateway: TmdbHttpGateway, recorder: Recorder, remove: bool
) -> None:
    recorder.json("/3/tv/1/season/7", _mutate(SEASON, "season_number", remove=remove))
    recorder.json("/3/tv/1/season/7?en", SEASON_EN)

    season = gateway.season(TmdbAccess(KEY), 1, 7)

    assert season.number == 7


def test_season_skips_episodes_without_number(gateway: TmdbHttpGateway, recorder: Recorder) -> None:
    recorder.json(
        "/3/tv/1/season/1",
        {
            "season_number": 1,
            "episodes": [
                None,
                {"name": "没有编号", "overview": "简介"},
                {"episode_number": None, "name": "编号为 null", "overview": "简介"},
                {"episode_number": 2, "name": "第 2 集", "overview": "简介"},
            ],
        },
    )

    season = gateway.season(TmdbAccess(KEY), 1, 1)

    assert [episode.number for episode in season.episodes] == [2]


SEARCH: dict[str, Any] = {
    "results": [
        {
            "id": 5,
            "name": "Dark",
            "original_name": "Dark",
            "first_air_date": "2017-12-01",
            "overview": "简介",
        }
    ]
}


@pytest.mark.parametrize("remove", [False, True], ids=["null", "missing"])
@pytest.mark.parametrize("path", ["results", "results.0"])
def test_search_tolerates_null_results(
    gateway: TmdbHttpGateway, recorder: Recorder, path: str, remove: bool
) -> None:
    recorder.json("/3/search/tv", _mutate(SEARCH, path, remove=remove))

    assert gateway.search(TmdbAccess(KEY), "series", "Dark") == []


def test_search_skips_results_without_id(gateway: TmdbHttpGateway, recorder: Recorder) -> None:
    recorder.json(
        "/3/search/tv",
        {
            "results": [
                {"name": "没有 ID"},
                {"id": None, "name": "ID 为 null"},
                {"id": 5, "name": "Dark", "original_name": "Dark", "first_air_date": ""},
            ]
        },
    )

    results = gateway.search(TmdbAccess(KEY), "series", "Dark")

    assert [result.tmdb_id for result in results] == [5]


FIND: dict[str, Any] = {
    "movie_results": [{"id": 157336}],
    "tv_results": [{"id": 108545}],
}


@pytest.mark.parametrize("remove", [False, True], ids=["null", "missing"])
@pytest.mark.parametrize(
    ("kind", "path"),
    [
        ("movie", "movie_results"),
        ("movie", "movie_results.0"),
        ("movie", "movie_results.0.id"),
        ("series", "tv_results"),
        ("series", "tv_results.0"),
        ("series", "tv_results.0.id"),
    ],
)
def test_find_imdb_tolerates_null_results(
    gateway: TmdbHttpGateway, recorder: Recorder, kind: TmdbKind, path: str, remove: bool
) -> None:
    recorder.json("/3/find/tt0816692", _mutate(FIND, path, remove=remove))

    assert gateway.find_imdb(TmdbAccess(KEY), kind, "tt0816692") is None


PERSON_TRANSLATIONS: dict[str, Any] = {
    "id": 42,
    "name": "Actor",
    "also_known_as": ["A. Actor"],
    "translations": {
        "translations": [
            {
                "iso_639_1": "zh",
                "iso_3166_1": "CN",
                "data": {"name": "演员", "biography": "简介"},
            },
            {"iso_639_1": "en", "iso_3166_1": "US", "data": {"biography": "Bio"}},
        ]
    },
}


@pytest.mark.parametrize("remove", [False, True], ids=["null", "missing"])
@pytest.mark.parametrize("path", ["translations", "translations.translations"])
def test_person_tolerates_null_translations(
    gateway: TmdbHttpGateway, recorder: Recorder, path: str, remove: bool
) -> None:
    recorder.json("/3/person/42", _mutate(PERSON_TRANSLATIONS, path, remove=remove))

    person = gateway.person(TmdbAccess(KEY), 42)

    assert person.name == "Actor"
    assert person.original_name is None


PERSON_ALIASES: dict[str, Any] = {
    "id": 42,
    "name": "Actor",
    "also_known_as": ["演员"],
    "translations": {"translations": []},
}


@pytest.mark.parametrize("remove", [False, True], ids=["null", "missing"])
def test_person_tolerates_null_aliases(
    gateway: TmdbHttpGateway, recorder: Recorder, remove: bool
) -> None:
    recorder.json("/3/person/42", _mutate(PERSON_ALIASES, "also_known_as", remove=remove))

    person = gateway.person(TmdbAccess(KEY), 42)

    assert person.name == "Actor"
    assert person.original_name is None


def test_person_ignores_non_string_aliases(gateway: TmdbHttpGateway, recorder: Recorder) -> None:
    body = copy.deepcopy(PERSON_ALIASES)
    body["also_known_as"] = ["演员", 123, None, {"name": "不是字符串"}]
    recorder.json("/3/person/42", body)

    person = gateway.person(TmdbAccess(KEY), 42)

    assert person.name == "演员"


@pytest.mark.parametrize("remove", [False, True], ids=["null", "missing"])
def test_person_missing_id_is_not_found(
    gateway: TmdbHttpGateway, recorder: Recorder, remove: bool
) -> None:
    recorder.json("/3/person/42", _mutate(PERSON_ALIASES, "id", remove=remove))

    with pytest.raises(TmdbNotFound):
        gateway.person(TmdbAccess(KEY), 42)


@pytest.mark.parametrize("remove", [False, True], ids=["null", "missing"])
@pytest.mark.parametrize(
    ("kind", "path", "body"),
    [
        ("movie", "/3/movie/157336", MOVIE_DETAILS),
        ("series", "/3/tv/108545", SERIES_DETAILS),
    ],
)
def test_details_missing_id_is_not_found(
    gateway: TmdbHttpGateway,
    recorder: Recorder,
    kind: TmdbKind,
    path: str,
    body: dict[str, Any],
    remove: bool,
) -> None:
    recorder.json(path, _mutate(body, "id", remove=remove))

    with pytest.raises(TmdbNotFound):
        gateway.details(TmdbAccess(KEY), kind, 1)
