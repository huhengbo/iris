"""收藏：Iris 接口、可见范围、首页和影视墙筛选。"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.fake_cloud115 import FakeCloud115
from tests.fake_tmdb import FakeTmdb
from tests.test_browse import build_catalog, find, get, new_user, set_access
from tests.test_sync import fresh_admin


@pytest.fixture
def catalog(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115, tmdb: FakeTmdb
) -> dict[str, int]:
    return build_catalog(client, admin, cloud115, tmdb)


def favorites(
    client: TestClient, headers: dict[str, str], kind: str, **params: Any
) -> dict[str, Any]:
    result: dict[str, Any] = get(client, headers, "/me/favorites", kind=kind, **params)
    return result


def test_movie_and_series_favorite_roundtrip(client: TestClient, catalog: dict[str, int]) -> None:
    admin = fresh_admin(client)
    earth = find(client, "流浪地球")
    assert get(client, admin, f"/items/{earth['id']}")["favorite"] is False

    assert client.put(f"/api/items/{earth['id']}/favorite", headers=admin).status_code == 204
    assert get(client, admin, f"/items/{earth['id']}")["favorite"] is True
    # 幂等，不改变第一次的收藏时间
    assert client.put(f"/api/items/{earth['id']}/favorite", headers=admin).status_code == 204
    assert favorites(client, admin, "movie")["total"] == 1

    series = find(client, "三体")
    assert client.put(f"/api/items/{series['id']}/favorite", headers=admin).status_code == 204
    assert get(client, admin, f"/items/{series['id']}")["favorite"] is True
    assert [i["title"] for i in favorites(client, admin, "series")["items"]] == ["三体"]

    page = favorites(client, admin, "movie")
    assert page["total"] == 1
    card = page["items"][0]
    assert (card["title"], card["kind"], card["year"]) == ("流浪地球", "movie", 2019)
    assert card["poster"][0]["url"].startswith("/api/images/")

    assert client.delete(f"/api/items/{earth['id']}/favorite", headers=admin).status_code == 204
    assert get(client, admin, f"/items/{earth['id']}")["favorite"] is False
    assert favorites(client, admin, "movie")["total"] == 0


def test_episode_and_person_favorites(client: TestClient, catalog: dict[str, int]) -> None:
    admin = fresh_admin(client)
    series = get(client, admin, f"/items/{find(client, '三体')['id']}")
    season = get(client, admin, f"/seasons/{series['seasons'][0]['id']}")
    episode = season["episodes"][0]
    assert episode["favorite"] is False

    assert client.put(f"/api/episodes/{episode['id']}/favorite", headers=admin).status_code == 204
    assert get(client, admin, f"/seasons/{season['id']}")["episodes"][0]["favorite"] is True
    page = favorites(client, admin, "episode")
    assert page["total"] == 1
    assert page["items"][0]["item"]["title"] == "三体"
    assert (
        page["items"][0]["episode"]["season_number"],
        page["items"][0]["episode"]["number"],
    ) == (
        1,
        1,
    )
    assert page["items"][0]["still"][0]["url"].startswith("/api/images/")

    person_id = get(client, admin, f"/items/{find(client, '流浪地球')['id']}")["cast"][0][
        "person_id"
    ]
    assert get(client, admin, f"/people/{person_id}")["favorite"] is False
    assert client.put(f"/api/people/{person_id}/favorite", headers=admin).status_code == 204
    assert get(client, admin, f"/people/{person_id}")["favorite"] is True
    people = favorites(client, admin, "person")
    assert people["total"] == 1 and people["items"][0]["id"] == person_id
    assert people["items"][0]["profile"][0]["url"].startswith("/api/images/")

    # 季不能收藏：没有这个接口
    assert client.put(f"/api/seasons/{season['id']}/favorite", headers=admin).status_code in (
        404,
        405,
    )


def test_favorites_are_per_user_and_respect_visibility(
    client: TestClient, catalog: dict[str, int]
) -> None:
    alice = new_user(client)
    set_access(client, alice["id"], [catalog["movies"], catalog["series"]])
    # 用 alice 自己的令牌收藏电影和剧集
    from tests.conftest import bearer, login

    alice_headers = bearer(login(client, "alice", "alice-password"))
    earth = find(client, "流浪地球")
    series = find(client, "三体")
    episode = get(client, alice_headers, f"/items/{series['id']}")["seasons"][0]
    episode = get(client, alice_headers, f"/seasons/{episode['id']}")["episodes"][0]
    client.put(f"/api/items/{earth['id']}/favorite", headers=alice_headers)
    client.put(f"/api/items/{series['id']}/favorite", headers=alice_headers)
    client.put(f"/api/episodes/{episode['id']}/favorite", headers=alice_headers)

    assert favorites(client, alice_headers, "movie")["total"] == 1
    assert favorites(client, alice_headers, "series")["total"] == 1
    assert favorites(client, alice_headers, "episode")["total"] == 1

    # 别人看不到她的收藏
    admin = fresh_admin(client)
    assert favorites(client, admin, "movie")["total"] == 0

    # 可见范围收缩后，剧集和单集收藏立即不再返回
    set_access(client, alice["id"], [catalog["movies"]])
    from tests.conftest import bearer, login

    alice_headers = bearer(login(client, "alice", "alice-password"))
    assert favorites(client, alice_headers, "movie")["total"] == 1
    assert favorites(client, alice_headers, "series")["total"] == 0
    assert favorites(client, alice_headers, "episode")["total"] == 0

    # 范围外的目标不能收藏
    series_season = episode["id"]
    assert (
        client.put(f"/api/episodes/{series_season}/favorite", headers=alice_headers).status_code
        == 404
    )
    assert client.put("/api/items/999999/favorite", headers=alice_headers).status_code == 404
    assert client.put("/api/people/999999/favorite", headers=alice_headers).status_code == 404


def test_home_and_wall_favorite_filters(client: TestClient, catalog: dict[str, int]) -> None:
    admin = fresh_admin(client)
    earth = find(client, "流浪地球")
    series = find(client, "三体")
    client.put(f"/api/items/{earth['id']}/favorite", headers=admin)
    client.put(f"/api/items/{series['id']}/favorite", headers=admin)

    home = get(client, admin, "/home")
    assert [i["title"] for i in home["favorites"]] == ["三体", "流浪地球"]

    wall = get(client, admin, f"/libraries/{catalog['movies']}/items", favorite="true")
    assert [i["title"] for i in wall["items"]] == ["流浪地球"]
    assert wall["total"] == 1
    assert get(client, admin, f"/libraries/{catalog['movies']}/items")["total"] == 3
    assert get(client, admin, "/search", q="流浪", favorite="true")["total"] == 1
    assert get(client, admin, "/search", q="流浪")["total"] == 1
    assert get(client, admin, "/search", q="星际", favorite="true")["total"] == 0

    # 取消后首页和筛选同步消失
    client.delete(f"/api/items/{earth['id']}/favorite", headers=admin)
    assert [i["title"] for i in get(client, admin, "/home")["favorites"]] == ["三体"]
    assert get(client, admin, f"/libraries/{catalog['movies']}/items", favorite="true") == {
        "items": [],
        "total": 0,
        "people": [],
        "episodes": [],
    }


def test_deleting_target_cleans_favorites(
    client: TestClient, catalog: dict[str, int], cloud115: FakeCloud115, tmdb: FakeTmdb
) -> None:
    admin = fresh_admin(client)
    earth = find(client, "流浪地球")
    series = find(client, "三体")
    person_id = get(client, admin, f"/items/{earth['id']}")["cast"][0]["person_id"]
    episode = get(client, admin, f"/items/{series['id']}")["seasons"][0]
    episode = get(client, admin, f"/seasons/{episode['id']}")["episodes"][0]
    for path in [
        f"/api/items/{earth['id']}/favorite",
        f"/api/items/{series['id']}/favorite",
        f"/api/episodes/{episode['id']}/favorite",
        f"/api/people/{person_id}/favorite",
    ]:
        assert client.put(path, headers=admin).status_code == 204

    with client.app.state.container.database.session() as connection:  # type: ignore[attr-defined]
        connection.execute("DELETE FROM media_item WHERE id IN (?, ?)", (earth["id"], series["id"]))
        connection.execute("DELETE FROM person WHERE id = ?", (person_id,))

    assert favorites(client, admin, "movie")["total"] == 0
    assert favorites(client, admin, "series")["total"] == 0
    assert favorites(client, admin, "episode")["total"] == 0
    assert favorites(client, admin, "person")["total"] == 0


def test_favorite_endpoints_require_login_and_validate_paging(
    client: TestClient, catalog: dict[str, int]
) -> None:
    assert client.get("/api/me/favorites", params={"kind": "movie"}).status_code == 401
    assert client.put("/api/items/1/favorite").status_code == 401
    admin = fresh_admin(client)
    bad_params: list[dict[str, Any]] = [
        {"kind": "season"},
        {"kind": "movie", "limit": 0},
        {"kind": "movie", "offset": -1},
    ]
    for params in bad_params:
        response = client.get("/api/me/favorites", params=params, headers=admin)
        assert response.status_code == 422, params
    assert (
        client.get(
            "/api/me/favorites", params={"kind": "movie", "limit": 101}, headers=admin
        ).status_code
        == 422
    )
