"""资料库推荐行：默认布局、首页顺序、公开接口和稍后观看筛选。"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import bearer, login
from tests.fake_cloud115 import FakeCloud115
from tests.fake_tmdb import FakeTmdb
from tests.test_browse import build_catalog, find, get, new_user, set_access
from tests.test_playback import issue, progress
from tests.test_sync import fresh_admin


@pytest.fixture
def catalog(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115, tmdb: FakeTmdb
) -> dict[str, int]:
    return build_catalog(client, admin, cloud115, tmdb)


def hub_kinds(rows: list[dict[str, Any]]) -> list[tuple[str, str | None]]:
    return [(row["kind"], row["arg"]) for row in rows]


def test_default_hubs_and_empty_rows(client: TestClient, catalog: dict[str, int]) -> None:
    admin = fresh_admin(client)
    rows = get(client, admin, f"/libraries/{catalog['movies']}/hubs")
    # 继续观看还没有记录，不返回空行；默认只有最近添加同时在首页显示
    assert hub_kinds(rows) == [
        ("latest", None),
        ("released", None),
        ("rating", None),
        ("genre", "科幻"),
        ("genre", "冒险"),
        ("genre", "喜剧"),
    ]
    assert [row["title"] for row in rows[:3]] == [
        "最近添加的电影",
        "最近上映的电影",
        "评分最高的电影",
    ]
    latest = rows[0]["items"]
    assert len(latest) == 3 and latest[0]["watched"] is False
    # 最近上映按上映日期，评分最高按评分
    # 2019 年两部同一天上映时按条目 ID 从新到旧
    assert [item["title"] for item in rows[1]["items"]] == [
        "疯狂的外星人",
        "流浪地球",
        "星际穿越",
    ]
    assert [item["title"] for item in rows[2]["items"]] == [
        "星际穿越",
        "流浪地球",
        "疯狂的外星人",
    ]
    assert {item["title"] for item in rows[3]["items"]} == {"流浪地球", "星际穿越"}

    series = get(client, admin, f"/libraries/{catalog['series']}/hubs")
    assert hub_kinds(series) == [
        ("latest", None),
        ("released", None),
        ("rating", None),
        ("genre", "剧情"),
    ]


def test_home_shelves_follow_library_and_hub_order(
    client: TestClient, catalog: dict[str, int]
) -> None:
    admin = fresh_admin(client)
    home = get(client, admin, "/home")
    # 默认每个库只有「最近添加」在首页显示，先电影后剧集
    assert [(shelf["library"]["name"], shelf["hub"]["kind"]) for shelf in home["shelves"]] == [
        ("电影", "latest"),
        ("剧集", "latest"),
    ]
    assert home["shelves"][0]["hub"]["title"] == "最近添加的电影"

    path = f"/api/admin/libraries/{catalog['movies']}/hubs"
    saved = client.put(
        path,
        headers=admin,
        json=[
            {"kind": "rating", "show_home": True, "show_library": True},
            {"kind": "latest", "show_home": False, "show_library": True},
            {"kind": "genre", "arg": "科幻", "show_home": True, "show_library": True},
        ],
    )
    assert saved.status_code == 200, saved.text
    home = get(client, admin, "/home")
    assert [
        (shelf["library"]["name"], shelf["hub"]["kind"], shelf["hub"]["arg"])
        for shelf in home["shelves"]
    ] == [
        ("电影", "rating", None),
        ("电影", "genre", "科幻"),
        ("剧集", "latest", None),
    ]


def test_continue_hub_uses_recent_plays(client: TestClient, catalog: dict[str, int]) -> None:
    admin = fresh_admin(client)
    item = find(client, "流浪地球")
    playable = issue(client, admin, item_id=item["id"], player="builtin")["playable_id"]
    assert progress(client, admin, playable, 600, 7200).status_code == 204

    rows = get(client, admin, f"/libraries/{catalog['movies']}/hubs")
    assert rows[0]["kind"] == "continue"
    assert rows[0]["title"] == "继续观看"
    assert [play["item"]["title"] for play in rows[0]["recent"]] == ["流浪地球"]
    assert rows[0]["items"][0]["title"] == "流浪地球"

    home = get(client, admin, "/home")
    assert home["shelves"][0]["hub"]["kind"] == "latest"

    # 管理员把「继续观看」放到首页时，首页这一行也带剧集和进度信息
    saved = client.put(
        f"/api/admin/libraries/{catalog['movies']}/hubs",
        headers=admin,
        json=[{"kind": "continue", "show_home": True, "show_library": True}],
    )
    assert saved.status_code == 200, saved.text
    shelf = get(client, admin, "/home")["shelves"][0]
    assert (shelf["library"]["name"], shelf["hub"]["kind"]) == ("电影", "continue")
    assert shelf["hub"]["items"][0]["title"] == "流浪地球"
    assert shelf["hub"]["recent"][0]["item"]["title"] == "流浪地球"
    assert shelf["hub"]["recent"][0]["progress"] == {"position": 600, "duration": 7200}


def test_watchlist_filter(client: TestClient, catalog: dict[str, int]) -> None:
    admin = fresh_admin(client)
    item = find(client, "星际穿越")
    assert client.put(f"/api/items/{item['id']}/watchlist", headers=admin).status_code == 204

    page = get(
        client,
        admin,
        f"/libraries/{catalog['movies']}/items",
        watchlist="true",
        sort="name",
    )
    assert [row["title"] for row in page["items"]] == ["星际穿越"]
    page = get(client, admin, f"/libraries/{catalog['movies']}/items", watchlist="false")
    assert {row["title"] for row in page["items"]} == {"流浪地球", "疯狂的外星人"}


def test_letter_jump_groups_latin_and_other_titles(
    client: TestClient, catalog: dict[str, int]
) -> None:
    admin = fresh_admin(client)
    path = f"/libraries/{catalog['movies']}/items"
    # 三部片的中文片名都归到 #；没有任何拉丁字母开头的条目
    assert len(get(client, admin, path, sort="name", letter="#")["items"]) == 3
    assert get(client, admin, path, sort="name", letter="A")["items"] == []
    response = client.get(f"/api{path}", params={"letter": "1"}, headers=admin)
    assert response.status_code == 422


def test_hubs_respect_library_visibility(client: TestClient, catalog: dict[str, int]) -> None:
    viewer = new_user(client)
    set_access(client, viewer["id"], [catalog["series"]])
    headers = bearer(login(client, "alice", "alice-password"))
    assert get(client, headers, f"/libraries/{catalog['series']}/hubs")[0]["kind"] == "latest"
    response = client.get(f"/api/libraries/{catalog['movies']}/hubs", headers=headers)
    assert response.status_code == 404
    # 首页只按可见资料库给推荐行
    home = get(client, headers, "/home")
    assert [shelf["library"]["name"] for shelf in home["shelves"]] == ["剧集"]
