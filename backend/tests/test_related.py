"""第 ③ 批验收：更多类似（排序、可见范围、数量）。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    bearer,
    create_user,
    login,
    save_tmdb_key,
)
from tests.fake_cloud115 import FakeCloud115
from tests.fake_tmdb import FakeTmdb
from tests.test_catalog import movie_library
from tests.test_sync import create_library, fresh_admin, sync


def get_related(
    client: TestClient,
    headers: dict[str, str],
    item_id: int,
    **params: Any,
) -> list[dict[str, Any]]:
    response = client.get(f"/api/items/{item_id}/related", params=params, headers=headers)
    assert response.status_code == 200, response.text
    body: list[dict[str, Any]] = response.json()
    return body


def titles(cards: list[dict[str, Any]]) -> list[str]:
    return [card["title"] for card in cards]


def find(client: TestClient, title: str) -> dict[str, Any]:
    page = client.get("/api/search", params={"q": title}, headers=fresh_admin(client)).json()
    item: dict[str, Any] = next(i for i in page["items"] if i["title"] == title)
    return item


def set_access(client: TestClient, user_id: object, library_ids: list[int]) -> None:
    response = client.put(
        f"/api/admin/users/{user_id}/libraries",
        json={"library_ids": library_ids},
        headers=fresh_admin(client),
    )
    assert response.status_code == 200, response.text


def new_user(client: TestClient, username: str = "alice") -> dict[str, object]:
    # 同步会推进假时钟，用新登录的管理员令牌创建
    return create_user(
        client,
        login(client, ADMIN_USERNAME, ADMIN_PASSWORD),
        username=username,
        password=f"{username}-password",
    )


@pytest.fixture
def catalog(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115, tmdb: FakeTmdb
) -> dict[str, int]:
    """电影库五部、剧集库两部：类型、评分、年份都不同，方便验证排序。

    流浪地球（科幻、冒险，2019，7.9）是基准条目，库里的其余四部按重合数、评分、
    年份接近依次排开；剧集只和剧集互相推荐。
    """
    save_tmdb_key(client, admin)

    def movie(tmdb_id: int, title: str, year: int, *, rating: float, genres: list[str]) -> Any:
        details = tmdb.add_movie(tmdb_id, title, year)
        tmdb.titles[("movie", tmdb_id)].details = replace(details, rating=rating, genres=genres)

    movie(535167, "流浪地球", 2019, rating=7.9, genres=["科幻", "冒险"])
    movie(157336, "星际穿越", 2014, rating=8.7, genres=["科幻"])
    movie(438631, "沙丘", 2021, rating=8.7, genres=["科幻"])
    movie(286217, "火星救援", 2015, rating=7.0, genres=["科幻"])
    movie(412302, "疯狂的外星人", 2019, rating=6.5, genres=["喜剧"])
    three_body = tmdb.add_series(108545, "三体", 2023, {1: 2})
    tmdb.titles[("series", 108545)].details = replace(three_body, genres=["科幻"], rating=8.1)
    long_season = tmdb.add_series(116665, "漫长的季节", 2023, {1: 1})
    tmdb.titles[("series", 116665)].details = replace(long_season, genres=["剧情"], rating=9.4)

    movies = movie_library(
        client,
        admin,
        cloud115,
        [
            "流浪地球 (2019)/流浪地球.2019.1080p.BluRay.mkv",
            "星际穿越 (2014)/Interstellar.2014.1080p.mp4",
            "沙丘 (2021)/Dune.2021.2160p.mkv",
            "火星救援 (2015)/The.Martian.2015.1080p.mkv",
            "疯狂的外星人 (2019)/疯狂的外星人.2019.1080p.mkv",
        ],
    )
    for path in [
        "三体 (2023)/Season 1/三体.S01E01.1080p.mkv",
        "三体 (2023)/Season 1/三体.S01E02.1080p.mkv",
        "漫长的季节 (2023)/Season 1/漫长的季节.S01E01.1080p.mkv",
    ]:
        cloud115.make_path(f"影视/剧集/{path}")
    series = create_library(client, admin, "剧集", "series", cloud115.make_path("影视/剧集"))
    assert sync(client, admin)["status"] == "succeeded"
    return {"movies": movies["id"], "series": series["id"]}


def test_related_orders_by_genre_rating_and_year(
    client: TestClient, catalog: dict[str, int]
) -> None:
    admin = fresh_admin(client)
    earth = find(client, "流浪地球")

    cards = get_related(client, admin, earth["id"])

    # 科幻重合一条的排在前面：同分时年份更接近 2019 的在前，分低的靠后；没有重合的最后
    assert titles(cards) == ["沙丘", "星际穿越", "火星救援", "疯狂的外星人"]
    # 条目卡片和影视墙同一套结构
    card = cards[0]
    assert card["kind"] == "movie" and card["year"] == 2021
    assert [p["width"] for p in card["poster"]] == [342, 780]
    assert card["watched"] is False


def test_related_keeps_the_kind(client: TestClient, catalog: dict[str, int]) -> None:
    admin = fresh_admin(client)
    show = find(client, "三体")
    movie = find(client, "沙丘")

    shows = get_related(client, admin, show["id"])
    assert titles(shows) == ["漫长的季节"]
    assert all(card["kind"] == "series" for card in shows)
    assert all(card["kind"] == "movie" for card in get_related(client, admin, movie["id"]))


def test_related_excludes_the_item_itself(client: TestClient, catalog: dict[str, int]) -> None:
    admin = fresh_admin(client)
    earth = find(client, "流浪地球")

    ids = [card["id"] for card in get_related(client, admin, earth["id"])]
    assert earth["id"] not in ids
    assert len(ids) == len(set(ids))


def test_related_is_limited(client: TestClient, catalog: dict[str, int]) -> None:
    admin = fresh_admin(client)
    earth = find(client, "流浪地球")

    assert titles(get_related(client, admin, earth["id"], limit=2)) == ["沙丘", "星际穿越"]
    response = client.get(f"/api/items/{earth['id']}/related", params={"limit": 0}, headers=admin)
    assert response.status_code == 422


def test_related_only_reads_visible_libraries(client: TestClient, catalog: dict[str, int]) -> None:
    alice = new_user(client)
    set_access(client, alice["id"], [catalog["movies"]])
    user = bearer(login(client, "alice", "alice-password"))
    show = find(client, "三体")
    movie = find(client, "沙丘")

    # 剧集不在可见范围里，按不存在处理
    response = client.get(f"/api/items/{show['id']}/related", headers=user)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    # 电影只推荐同一个可见资料库里的电影，剧集不会混进来
    cards = get_related(client, user, movie["id"])
    assert titles(cards) == ["星际穿越", "流浪地球", "火星救援", "疯狂的外星人"]
    assert all(card["library_id"] == catalog["movies"] for card in cards)

    # 没有分配任何资料库时什么都看不到
    set_access(client, alice["id"], [])
    user = bearer(login(client, "alice", "alice-password"))
    assert client.get(f"/api/items/{movie['id']}/related", headers=user).status_code == 404


def test_related_requires_sign_in_and_an_existing_item(
    client: TestClient, catalog: dict[str, int]
) -> None:
    assert client.get("/api/items/1/related").status_code == 401

    admin = fresh_admin(client)
    response = client.get("/api/items/999999/related", headers=admin)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
