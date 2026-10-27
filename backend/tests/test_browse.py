"""第五阶段验收：浏览接口和可见范围。"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig
from tests.conftest import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    bearer,
    create_user,
    login,
    save_tmdb_key,
)
from tests.fake_cloud115 import VIDEO_SIZE, FakeCloud115
from tests.fake_tmdb import FakeTmdb, TmdbCast, TmdbPerson, TmdbUnavailable
from tests.test_catalog import movie_library
from tests.test_sync import create_library, fresh_admin, sync

WU_JING = 7001
WRITER_GUO = 7101
WRITER_LIU = 7102


def get(client: TestClient, headers: dict[str, str], path: str, **params: Any) -> Any:
    response = client.get(f"/api{path}", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def missing(client: TestClient, headers: dict[str, str], path: str, **params: Any) -> None:
    response = client.get(f"/api{path}", params=params, headers=headers)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"


def titles(page: dict[str, Any]) -> list[str]:
    return [i["title"] for i in page["items"]]


def set_access(client: TestClient, user_id: object, library_ids: list[int]) -> Any:
    response = client.put(
        f"/api/admin/users/{user_id}/libraries",
        json={"library_ids": library_ids},
        headers=fresh_admin(client),
    )
    assert response.status_code == 200, response.text
    return response.json()


def build_catalog(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115, tmdb: FakeTmdb
) -> dict[str, int]:
    """电影资料库三部片、剧集资料库一部剧（含特别篇）。吴京在两个资料库里都有作品。

    流浪地球有 1080p 和 2160p 两个版本，1080p 版本带一条简体中文外挂字幕。
    """
    save_tmdb_key(client, admin)
    wu_jing = TmdbCast(WU_JING, "Wu Jing", "/wujing.jpg", "刘培强")
    earth = tmdb.add_movie(
        535167, "流浪地球", 2019, original_title="The Wandering Earth", cast=[wu_jing]
    )
    tmdb.titles[("movie", 535167)].details = replace(
        earth,
        rating=7.9,
        genres=["科幻", "冒险"],
        writers=[TmdbCast(WRITER_GUO, "郭帆", None, None)],
    )
    stars = tmdb.add_movie(157336, "星际穿越", 2014, original_title="Interstellar")
    tmdb.titles[("movie", 157336)].details = replace(stars, rating=8.7, genres=["科幻"])
    alien = tmdb.add_movie(412302, "疯狂的外星人", 2019, cast=[replace(wu_jing, character="大飞")])
    tmdb.titles[("movie", 412302)].details = replace(alien, rating=6.5, genres=["喜剧"])
    three_body = tmdb.add_series(
        108545,
        "三体",
        2023,
        {0: 1, 1: 30, 2: 10},
        original_title="Three-Body",
        cast=[TmdbCast(7003, "张鲁一", None, "汪淼"), replace(wu_jing, character="史强")],
    )
    tmdb.titles[("series", 108545)].details = replace(
        three_body, writers=[TmdbCast(WRITER_LIU, "刘慈欣", None, None)]
    )
    tmdb.people[WU_JING] = TmdbPerson(
        WU_JING, "吴京", "Wu Jing", "中国内地男演员、导演。", "1974-04-03", None, "中国北京"
    )
    movies = movie_library(
        client,
        admin,
        cloud115,
        [
            "流浪地球 (2019)/流浪地球.2019.1080p.BluRay.mkv",
            "流浪地球 (2019)/流浪地球.2019.1080p.BluRay.chs.srt",
            "星际穿越 (2014)/Interstellar.2014.1080p.mp4",
            "疯狂的外星人 (2019)/疯狂的外星人.2019.1080p.mkv",
        ],
    )
    cloud115.add_file(
        cloud115.make_path("影视/电影/流浪地球 (2019)"),
        "流浪地球.2019.2160p.WEB-DL.mkv",
        size=VIDEO_SIZE * 3,
    )
    for path in [
        "三体 (2023)/Season 1/三体.S01E02.1080p.mkv",
        "三体 (2023)/Season 1/三体.S01E01.1080p.mkv",
        "三体 (2023)/Season 2/三体.S02E01.1080p.mkv",
        "三体 (2023)/Specials/三体.S00E01.1080p.mkv",
    ]:
        cloud115.make_path(f"影视/剧集/{path}")
    series = create_library(client, admin, "剧集", "series", cloud115.make_path("影视/剧集"))
    assert sync(client, admin)["status"] == "succeeded"
    return {"movies": movies["id"], "series": series["id"]}


@pytest.fixture
def catalog(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115, tmdb: FakeTmdb
) -> dict[str, int]:
    return build_catalog(client, admin, cloud115, tmdb)


def new_user(client: TestClient) -> dict[str, object]:
    # 同步会推进假时钟，用新登录的管理员令牌创建
    return create_user(client, login(client, ADMIN_USERNAME, ADMIN_PASSWORD))


def find(client: TestClient, title: str) -> dict[str, Any]:
    page = get(client, fresh_admin(client), "/search", q=title)
    item: dict[str, Any] = next(i for i in page["items"] if i["title"] == title)
    return item


# ---- 可见范围 ----


def test_new_user_sees_existing_libraries_but_not_later_ones(
    client: TestClient,
    admin: dict[str, str],
    admin_tokens: dict[str, str],
    cloud115: FakeCloud115,
) -> None:
    first = create_library(client, admin, "电影", "movie", cloud115.make_path("影视/电影"))
    alice = create_user(client, admin_tokens)
    second = create_library(client, admin, "剧集", "series", cloud115.make_path("影视/剧集"))

    response = client.get(f"/api/admin/users/{alice['id']}/libraries", headers=admin)
    assert response.json() == {"library_ids": [first["id"]], "all_libraries": False}
    user = bearer(login(client, "alice", "alice-password"))
    assert [lib["id"] for lib in get(client, user, "/libraries")] == [first["id"]]
    # 管理员始终看到全部资料库
    assert [lib["id"] for lib in get(client, admin, "/libraries")] == [first["id"], second["id"]]


def test_user_without_access_sees_nothing_of_the_library(
    client: TestClient, catalog: dict[str, int]
) -> None:
    alice = new_user(client)
    series_item = find(client, "三体")
    series_detail = get(client, fresh_admin(client), f"/items/{series_item['id']}")
    season_id = series_detail["seasons"][0]["id"]
    zhang = next(c for c in series_detail["cast"] if c["name"] == "张鲁一")
    wu_jing = next(c for c in series_detail["cast"] if c["character"] == "史强")

    set_access(client, alice["id"], [catalog["movies"]])
    user = bearer(login(client, "alice", "alice-password"))

    # 首页：没有剧集资料库的入口和推荐行
    home = get(client, user, "/home")
    assert [lib["id"] for lib in home["libraries"]] == [catalog["movies"]]
    assert [shelf["library"]["id"] for shelf in home["shelves"]] == [catalog["movies"]]
    # 列表、详情、季
    missing(client, user, f"/libraries/{catalog['series']}")
    missing(client, user, f"/libraries/{catalog['series']}/items")
    missing(client, user, f"/items/{series_item['id']}")
    missing(client, user, f"/seasons/{season_id}")
    # 演员页：只在剧集里出现的人物不存在；两边都有作品的只列出电影
    missing(client, user, f"/people/{zhang['person_id']}")
    person = get(client, user, f"/people/{wu_jing['person_id']}")
    assert [w["title"] for w in person["works"]] == ["流浪地球", "疯狂的外星人"]
    # 搜索
    assert get(client, user, "/search", q="三体")["total"] == 0
    assert get(client, user, "/search", q="Three")["total"] == 0
    # 能看到的资料库照常
    assert get(client, user, f"/libraries/{catalog['movies']}/items")["total"] == 3

    # 一个资料库都没有分配时什么都看不到
    set_access(client, alice["id"], [])
    user = bearer(login(client, "alice", "alice-password"))
    assert get(client, user, "/home") == {
        "libraries": [],
        "recent": [],
        "favorites": [],
        "watchlist": [],
        "shelves": [],
    }
    assert get(client, user, "/search", q="流浪")["total"] == 0


def test_search_finds_works_by_credited_person_within_visible_libraries(
    client: TestClient, catalog: dict[str, int]
) -> None:
    admin = fresh_admin(client)
    # 演员原名、中文名都能反查；管理员看到电影和剧集里的全部作品
    assert set(titles(get(client, admin, "/search", q="Wu Jing"))) == {
        "流浪地球",
        "疯狂的外星人",
        "三体",
    }
    assert titles(get(client, admin, "/search", q="张鲁一")) == ["三体"]

    alice = new_user(client)
    set_access(client, alice["id"], [catalog["movies"]])
    user = bearer(login(client, "alice", "alice-password"))
    # 只分配了电影库：演员的剧集作品不出现，只在剧集里的演员什么都反查不到
    page = get(client, user, "/search", q="Wu Jing")
    assert set(titles(page)) == {"流浪地球", "疯狂的外星人"} and page["total"] == 2
    assert get(client, user, "/search", q="张鲁一")["total"] == 0


def test_search_finds_works_by_writer_within_visible_libraries(
    client: TestClient, catalog: dict[str, int]
) -> None:
    admin = fresh_admin(client)
    assert titles(get(client, admin, "/search", q="郭帆")) == ["流浪地球"]
    assert titles(get(client, admin, "/search", q="刘慈欣")) == ["三体"]
    # 剧集详情也带编剧
    series = find(client, "三体")
    assert [w["name"] for w in get(client, admin, f"/items/{series['id']}")["writers"]] == [
        "刘慈欣"
    ]

    alice = new_user(client)
    set_access(client, alice["id"], [catalog["movies"]])
    user = bearer(login(client, "alice", "alice-password"))
    # 权限范围不变：只分配电影库时，剧集编剧反查不到
    assert titles(get(client, user, "/search", q="郭帆")) == ["流浪地球"]
    assert get(client, user, "/search", q="刘慈欣")["total"] == 0


def test_changing_access_signs_the_user_out(client: TestClient, catalog: dict[str, int]) -> None:
    alice = new_user(client)
    tokens = login(client, "alice", "alice-password")

    # 内容没变：令牌照常可用
    set_access(client, alice["id"], [catalog["series"], catalog["movies"]])
    assert client.get("/api/home", headers=bearer(tokens)).status_code == 200

    assert set_access(client, alice["id"], [catalog["movies"]]) == {
        "library_ids": [catalog["movies"]],
        "all_libraries": False,
    }
    assert client.get("/api/home", headers=bearer(tokens)).status_code == 401
    refreshed = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == 401


def test_library_access_is_validated_and_admin_only(
    client: TestClient, admin: dict[str, str], admin_tokens: dict[str, str]
) -> None:
    alice = create_user(client, admin_tokens)
    response = client.put(
        f"/api/admin/users/{alice['id']}/libraries", json={"library_ids": [999]}, headers=admin
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unknown_library"
    assert client.get("/api/admin/users/999/libraries", headers=admin).status_code == 404

    user = bearer(login(client, "alice", "alice-password"))
    response = client.get(f"/api/admin/users/{alice['id']}/libraries", headers=user)
    assert response.status_code == 403
    assert client.get("/api/home").status_code == 401


# ---- 海报墙 ----


def test_library_items_sort_filter_and_paging(client: TestClient, catalog: dict[str, int]) -> None:
    admin = fresh_admin(client)
    path = f"/libraries/{catalog['movies']}/items"

    library = get(client, admin, f"/libraries/{catalog['movies']}")
    assert library["item_count"] == 3
    assert library["genres"] == sorted(["冒险", "喜剧", "科幻"])
    assert library["years"] == [2019, 2014]
    # 「分类」标签：类型名、条目数和一张代表图，条目多的在前
    assert [(c["name"], c["item_count"]) for c in library["categories"]] == [
        ("科幻", 2),
        ("冒险", 1),
        ("喜剧", 1),
    ]
    assert library["categories"][0]["image"]

    assert titles(get(client, admin, path, sort="rating")) == [
        "星际穿越",
        "流浪地球",
        "疯狂的外星人",
    ]
    assert titles(get(client, admin, path, sort="rating", order="asc"))[0] == "疯狂的外星人"
    assert titles(get(client, admin, path, sort="release"))[-1] == "星际穿越"
    assert len(titles(get(client, admin, path, sort="name"))) == 3
    assert titles(get(client, admin, path, genre="科幻", sort="rating")) == ["星际穿越", "流浪地球"]
    assert sorted(titles(get(client, admin, path, year=2019))) == ["流浪地球", "疯狂的外星人"]

    first = get(client, admin, path, sort="rating", limit=2)
    second = get(client, admin, path, sort="rating", limit=2, offset=2)
    assert first["total"] == second["total"] == 3
    assert titles(first) + titles(second) == ["星际穿越", "流浪地球", "疯狂的外星人"]
    card = first["items"][0]
    assert card["kind"] == "movie" and card["year"] == 2014 and card["rating"] == 8.7
    assert [p["width"] for p in card["poster"]] == [342, 780]
    assert card["backdrop"][0]["url"].startswith("/api/images/")

    response = client.get(f"/api{path}", params={"sort": "random"}, headers=admin)
    assert response.status_code == 422
    missing(client, admin, "/libraries/999/items")


def test_home_shelves_list_latest_per_library(client: TestClient, catalog: dict[str, int]) -> None:
    home = get(client, fresh_admin(client), "/home")

    assert [(lib["name"], lib["item_count"]) for lib in home["libraries"]] == [
        ("电影", 3),
        ("剧集", 1),
    ]
    shelves = {s["library"]["name"]: titles(s["hub"]) for s in home["shelves"]}
    assert sorted(shelves["电影"]) == ["星际穿越", "流浪地球", "疯狂的外星人"]
    assert shelves["剧集"] == ["三体"]


def test_browse_etag_returns_not_modified(client: TestClient, catalog: dict[str, int]) -> None:
    headers = fresh_admin(client)
    first = client.get("/api/home", headers=headers)
    assert first.status_code == 200
    etag = first.headers.get("ETag")
    assert etag

    second = client.get("/api/home", headers={**headers, "If-None-Match": etag})
    assert second.status_code == 304
    assert second.content == b""


# ---- 详情 ----


def test_movie_detail_has_credits_and_versions(client: TestClient, catalog: dict[str, int]) -> None:
    admin = fresh_admin(client)
    detail = get(client, admin, f"/items/{find(client, '流浪地球')['id']}")

    assert detail["title"] == "流浪地球"
    assert detail["original_title"] == "The Wandering Earth"
    assert detail["overview"] == "流浪地球的简介"
    assert detail["genres"] == ["科幻", "冒险"]
    assert detail["content_rating"] == "PG-13"
    assert detail["logo"][0]["url"].startswith("/api/images/")
    assert detail["seasons"] == [] and detail["next_episode"] is None
    # 版本大的在前
    assert [v["label"].split(" · ")[0] for v in detail["versions"]] == ["2160p", "1080p"]
    assert [d["name"] for d in detail["directors"]] == ["导演535167"]
    assert [w["name"] for w in detail["writers"]] == ["郭帆"]
    cast = detail["cast"][0]
    assert (cast["name"], cast["character"]) == ("Wu Jing", "刘培强")
    assert cast["profile"][0]["width"] == 185

    missing(client, admin, "/items/999")


def test_detail_lists_studios_and_countries(
    client: TestClient, config: AppConfig, catalog: dict[str, int]
) -> None:
    item_id = find(client, "流浪地球")["id"]
    with sqlite3.connect(config.database_path) as connection:
        connection.execute(
            "UPDATE media_item SET studios = ?, countries = ? WHERE id = ?",
            ('["中国电影集团"]', '["中国", "美国"]', item_id),
        )

    detail = get(client, fresh_admin(client), f"/items/{item_id}")

    assert detail["studios"] == ["中国电影集团"]
    assert detail["countries"] == ["中国", "美国"]


def test_series_detail_seasons_episodes_and_next_episode(
    client: TestClient, catalog: dict[str, int]
) -> None:
    admin = fresh_admin(client)
    detail = get(client, admin, f"/items/{find(client, '三体')['id']}")

    assert detail["versions"] == []
    assert detail["content_rating"] is None
    # 只列出有文件的季和集，特别篇在最后
    assert [(s["number"], s["episode_count"]) for s in detail["seasons"]] == [
        (1, 2),
        (2, 1),
        (0, 1),
    ]
    assert detail["seasons"][0]["name"] == "第 1 季"
    next_episode = detail["next_episode"]
    assert (next_episode["season_number"], next_episode["number"]) == (1, 1)
    assert next_episode["season_id"] == detail["seasons"][0]["id"]

    season = get(client, admin, f"/seasons/{detail['seasons'][0]['id']}")
    assert season["item_id"] == detail["id"]
    assert [(e["number"], e["name"]) for e in season["episodes"]] == [
        (1, "第 1 集"),
        (2, "第 2 集"),
    ]
    episode = season["episodes"][0]
    assert episode["still"][0]["width"] == 300
    assert episode["runtime"] == 45
    assert len(episode["versions"]) == 1
    assert episode["versions"][0]["subtitles"] == []
    missing(client, admin, "/seasons/999")


# ---- 演员页 ----


def test_person_detail_is_fetched_once_and_lists_local_works(
    client: TestClient, tmdb: FakeTmdb, catalog: dict[str, int]
) -> None:
    admin = fresh_admin(client)
    earth = get(client, admin, f"/items/{find(client, '流浪地球')['id']}")
    person_id = earth["cast"][0]["person_id"]
    tmdb.calls.clear()

    person = get(client, admin, f"/people/{person_id}")

    assert tmdb.calls == [f"person:{WU_JING}"]
    assert person["detailed"] is True
    assert (person["name"], person["original_name"]) == ("吴京", "Wu Jing")
    assert person["birthday"] == "1974-04-03" and person["place_of_birth"] == "中国北京"
    assert person["biography"] == "中国内地男演员、导演。"
    assert person["profile"][0]["url"].startswith("/api/images/")
    # 按年份从新到旧，带角色名
    assert [(w["title"], w["character"]) for w in person["works"]] == [
        ("三体", "史强"),
        ("流浪地球", "刘培强"),
        ("疯狂的外星人", "大飞"),
    ]
    assert person["works"][0]["roles"] == ["cast"]

    tmdb.calls.clear()
    assert get(client, admin, f"/people/{person_id}")["name"] == "吴京"
    assert tmdb.calls == []
    # 拉过资料后，详情页也显示中文名
    assert get(client, admin, f"/items/{earth['id']}")["cast"][0]["name"] == "吴京"


def test_person_detail_falls_back_to_name_when_tmdb_fails(
    client: TestClient, tmdb: FakeTmdb, catalog: dict[str, int]
) -> None:
    admin = fresh_admin(client)
    earth = get(client, admin, f"/items/{find(client, '流浪地球')['id']}")
    person_id = earth["cast"][0]["person_id"]
    tmdb.error = TmdbUnavailable()

    person = get(client, admin, f"/people/{person_id}")

    assert person["detailed"] is False
    assert person["name"] == "Wu Jing" and person["biography"] is None
    assert len(person["works"]) == 3
    # 恢复后下次打开再拉
    tmdb.error = None
    assert get(client, admin, f"/people/{person_id}")["detailed"] is True

    # 导演没有登记在 TMDB 上：记为拉过，不再重复请求
    director_id = earth["directors"][0]["person_id"]
    assert get(client, admin, f"/people/{director_id}")["works"][0]["roles"] == ["director"]
    tmdb.calls.clear()
    get(client, admin, f"/people/{director_id}")
    assert tmdb.calls == []
    missing(client, admin, "/people/999999")


# ---- 搜索 ----


def test_search_matches_chinese_original_and_english_titles(
    client: TestClient, catalog: dict[str, int]
) -> None:
    admin = fresh_admin(client)

    assert titles(get(client, admin, "/search", q="流浪")) == ["流浪地球"]
    assert titles(get(client, admin, "/search", q="interstellar")) == ["星际穿越"]
    assert titles(get(client, admin, "/search", q="three-body")) == ["三体"]
    # 片名完全一致的排在前面
    assert titles(get(client, admin, "/search", q="地球")) == ["流浪地球"]
    assert get(client, admin, "/search", q="  ")["total"] == 0
    assert get(client, admin, "/search", q="100%")["total"] == 0
    people = get(client, admin, "/search", q="Wu Jing")["people"]
    assert [person["name"] for person in people] == ["Wu Jing"]
    episodes = get(client, admin, "/search", q="三体")["episodes"]
    assert episodes and all(episode["show_title"] == "三体" for episode in episodes)
    page = get(client, admin, "/search", q="的", limit=1)
    assert page["total"] == 1 and titles(page) == ["疯狂的外星人"]
