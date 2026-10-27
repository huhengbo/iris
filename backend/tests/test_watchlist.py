"""稍后观看：加入、移出、可见范围、首页和「已看」自动移出。"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import bearer, login
from tests.fake_cloud115 import FakeCloud115
from tests.fake_tmdb import FakeTmdb
from tests.test_browse import build_catalog, find, get, new_user, set_access
from tests.test_emby import emby_login, token_headers
from tests.test_sync import fresh_admin


@pytest.fixture
def catalog(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115, tmdb: FakeTmdb
) -> dict[str, int]:
    return build_catalog(client, admin, cloud115, tmdb)


def watchlist(client: TestClient, headers: dict[str, str], **params: Any) -> dict[str, Any]:
    result: dict[str, Any] = get(client, headers, "/me/watchlist", **params)
    return result


def add(client: TestClient, headers: dict[str, str], item_id: int) -> None:
    response = client.put(f"/api/items/{item_id}/watchlist", headers=headers)
    assert response.status_code == 204, response.text


def remove(client: TestClient, headers: dict[str, str], item_id: int) -> None:
    response = client.delete(f"/api/items/{item_id}/watchlist", headers=headers)
    assert response.status_code == 204, response.text


def titles(page: dict[str, Any]) -> list[str]:
    return [item["title"] for item in page["items"]]


def episodes(client: TestClient) -> dict[str, dict[str, Any]]:
    """三体的每一集，键是 S01E02 这样的编号（包括特别篇）。"""
    headers = fresh_admin(client)
    detail = get(client, headers, f"/items/{find(client, '三体')['id']}")
    result: dict[str, dict[str, Any]] = {}
    for season in detail["seasons"]:
        season_detail = get(client, headers, f"/seasons/{season['id']}")
        for episode in season_detail["episodes"]:
            result[f"S{season['number']:02d}E{episode['number']:02d}"] = episode
    return result


def test_add_remove_and_idempotent(client: TestClient, catalog: dict[str, int]) -> None:
    admin = fresh_admin(client)
    earth = find(client, "流浪地球")
    series = find(client, "三体")
    assert get(client, admin, f"/items/{earth['id']}")["watchlisted"] is False

    add(client, admin, earth["id"])
    add(client, admin, series["id"])
    # 幂等：重复加入不报错，也不改变原来的顺序和时间
    add(client, admin, earth["id"])

    assert get(client, admin, f"/items/{earth['id']}")["watchlisted"] is True
    assert get(client, admin, f"/items/{series['id']}")["watchlisted"] is True
    page = watchlist(client, admin)
    assert page["total"] == 2
    assert titles(page) == ["三体", "流浪地球"]
    assert page["items"][0]["kind"] == "series"
    assert page["items"][1]["year"] == 2019
    assert page["items"][1]["poster"][0]["url"].startswith("/api/images/")

    remove(client, admin, earth["id"])
    # 移出也是幂等的
    remove(client, admin, earth["id"])
    assert get(client, admin, f"/items/{earth['id']}")["watchlisted"] is False
    assert titles(watchlist(client, admin)) == ["三体"]
    assert watchlist(client, admin)["total"] == 1


def test_watchlist_is_per_user_and_respects_visibility(
    client: TestClient, catalog: dict[str, int]
) -> None:
    alice = new_user(client)
    set_access(client, alice["id"], [catalog["movies"]])
    alice_headers = bearer(login(client, "alice", "alice-password"))
    earth = find(client, "流浪地球")
    series = find(client, "三体")

    add(client, alice_headers, earth["id"])
    # 不可见资料库里的条目和不存在的一样返回 404
    assert (
        client.put(f"/api/items/{series['id']}/watchlist", headers=alice_headers).status_code == 404
    )
    assert client.put("/api/items/999999/watchlist", headers=alice_headers).status_code == 404
    assert titles(watchlist(client, alice_headers)) == ["流浪地球"]
    # 别人看不到她的列表
    assert watchlist(client, fresh_admin(client))["total"] == 0

    # 失去资料库权限后，列表里不再返回，但记录保留，恢复权限后重新出现
    set_access(client, alice["id"], [catalog["series"]])
    alice_headers = bearer(login(client, "alice", "alice-password"))
    assert watchlist(client, alice_headers) == {"items": [], "total": 0}
    set_access(client, alice["id"], [catalog["movies"]])
    alice_headers = bearer(login(client, "alice", "alice-password"))
    assert titles(watchlist(client, alice_headers)) == ["流浪地球"]


def test_season_and_episode_cannot_be_added(client: TestClient, catalog: dict[str, int]) -> None:
    admin = fresh_admin(client)
    detail = get(client, admin, f"/items/{find(client, '三体')['id']}")
    season = detail["seasons"][0]
    episode = get(client, admin, f"/seasons/{season['id']}")["episodes"][0]
    # 季和单集没有稍后观看接口
    for path in [
        f"/api/seasons/{season['id']}/watchlist",
        f"/api/episodes/{episode['id']}/watchlist",
    ]:
        assert client.put(path, headers=admin).status_code in (404, 405), path
        assert client.delete(path, headers=admin).status_code in (404, 405), path


def test_watchlist_order_and_paging(client: TestClient, catalog: dict[str, int]) -> None:
    admin = fresh_admin(client)
    for title in ["流浪地球", "星际穿越", "疯狂的外星人", "三体"]:
        add(client, admin, find(client, title)["id"])

    first = watchlist(client, admin, limit=2)
    assert first["total"] == 4
    assert titles(first) == ["三体", "疯狂的外星人"]
    second = watchlist(client, admin, limit=2, offset=2)
    assert second["total"] == 4
    assert titles(second) == ["星际穿越", "流浪地球"]
    middle = watchlist(client, admin, limit=2, offset=1)
    assert titles(middle) == ["疯狂的外星人", "星际穿越"]

    for params in [{"limit": 0}, {"offset": -1}, {"limit": 101}]:
        assert client.get("/api/me/watchlist", params=params, headers=admin).status_code == 422, (
            params
        )


def test_home_has_watchlist_row(client: TestClient, catalog: dict[str, int]) -> None:
    admin = fresh_admin(client)
    earth = find(client, "流浪地球")
    series = find(client, "三体")
    add(client, admin, earth["id"])
    add(client, admin, series["id"])

    home = get(client, admin, "/home")
    assert [item["title"] for item in home["watchlist"]] == ["三体", "流浪地球"]

    remove(client, admin, earth["id"])
    assert [item["title"] for item in get(client, admin, "/home")["watchlist"]] == ["三体"]


def test_movie_watched_moves_out_of_watchlist(client: TestClient, catalog: dict[str, int]) -> None:
    admin = fresh_admin(client)
    earth = find(client, "流浪地球")
    add(client, admin, earth["id"])

    assert client.put(f"/api/items/{earth['id']}/watched", headers=admin).status_code == 204
    assert watchlist(client, admin)["total"] == 0
    assert get(client, admin, f"/items/{earth['id']}")["watchlisted"] is False
    # 取消已看不会自动加回来
    assert client.delete(f"/api/items/{earth['id']}/watched", headers=admin).status_code == 204
    assert watchlist(client, admin)["total"] == 0


def test_finished_progress_moves_movie_out_of_watchlist(
    client: TestClient, catalog: dict[str, int]
) -> None:
    admin = fresh_admin(client)
    earth = find(client, "流浪地球")
    add(client, admin, earth["id"])
    played = client.post(
        "/api/play", json={"item_id": earth["id"], "player": "builtin"}, headers=admin
    )
    assert played.status_code == 200, played.text
    response = client.put(
        f"/api/playables/{played.json()['playable_id']}/progress",
        json={"position": 6600, "duration": 7200},
        headers=admin,
    )
    assert response.status_code == 204, response.text
    assert watchlist(client, admin)["total"] == 0


def test_emby_marking_played_moves_movie_out_of_watchlist(
    client: TestClient, catalog: dict[str, int]
) -> None:
    admin = fresh_admin(client)
    earth = find(client, "流浪地球")
    add(client, admin, earth["id"])

    session = emby_login(client)
    # Emby 的条目 ID 是「内部 ID × 10 + 类型位」，电影的类型位是 2
    item_id = str(earth["id"] * 10 + 2)
    response = client.post(
        f"/emby/users/{session['User']['Id']}/playeditems/{item_id}",
        headers=token_headers(session["AccessToken"]),
    )
    assert response.status_code == 200, response.text
    assert watchlist(client, admin)["total"] == 0


def test_batch_marking_watched_moves_movie_out_of_watchlist(
    client: TestClient, catalog: dict[str, int]
) -> None:
    admin = fresh_admin(client)
    earth = find(client, "流浪地球")
    add(client, admin, earth["id"])
    # 批量改已看只改已有的播放记录，先播放一次
    played = client.post(
        "/api/play", json={"item_id": earth["id"], "player": "builtin"}, headers=admin
    )
    assert played.status_code == 200, played.text

    response = client.post(
        "/api/admin/items/batch",
        json={"ids": [earth["id"]], "action": "watched"},
        headers=admin,
    )
    assert response.status_code == 202, response.text
    assert watchlist(client, admin)["total"] == 0


def test_series_stays_until_last_episode_watched(
    client: TestClient, catalog: dict[str, int]
) -> None:
    admin = fresh_admin(client)
    series = find(client, "三体")
    add(client, admin, series["id"])
    eps = episodes(client)
    ordered = [eps["S01E01"], eps["S01E02"], eps["S02E01"], eps["S00E01"]]

    for episode in ordered[:-1]:
        response = client.put(f"/api/episodes/{episode['id']}/watched", headers=admin)
        assert response.status_code == 204
        assert watchlist(client, admin)["total"] == 1

    assert (
        client.put(f"/api/episodes/{ordered[-1]['id']}/watched", headers=admin).status_code == 204
    )
    assert watchlist(client, admin)["total"] == 0


def test_marking_whole_series_or_season_watched_moves_out(
    client: TestClient, catalog: dict[str, int]
) -> None:
    admin = fresh_admin(client)
    series = find(client, "三体")
    detail = get(client, admin, f"/items/{series['id']}")
    season = detail["seasons"][0]

    # 只标一季：整部剧还没看完，保留
    add(client, admin, series["id"])
    assert client.put(f"/api/seasons/{season['id']}/watched", headers=admin).status_code == 204
    assert watchlist(client, admin)["total"] == 1

    # 整剧标记已看：触发剧集判断并移出
    assert client.put(f"/api/items/{series['id']}/watched", headers=admin).status_code == 204
    assert watchlist(client, admin)["total"] == 0


def test_deleting_item_cleans_watchlist(client: TestClient, catalog: dict[str, int]) -> None:
    admin = fresh_admin(client)
    earth = find(client, "流浪地球")
    series = find(client, "三体")
    add(client, admin, earth["id"])
    add(client, admin, series["id"])

    with client.app.state.container.database.session() as connection:  # type: ignore[attr-defined]
        connection.execute("DELETE FROM media_item WHERE id IN (?, ?)", (earth["id"], series["id"]))
        left = connection.execute("SELECT COUNT(*) FROM user_watchlist").fetchone()[0]
    assert left == 0
    assert watchlist(client, admin)["total"] == 0


def test_watchlist_requires_login(client: TestClient, catalog: dict[str, int]) -> None:
    assert client.get("/api/me/watchlist").status_code == 401
    assert client.put("/api/items/1/watchlist").status_code == 401
    assert client.delete("/api/items/1/watchlist").status_code == 401
