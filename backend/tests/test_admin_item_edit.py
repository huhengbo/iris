"""条目信息、元数据编辑、取消匹配、合并、批量操作，以及季和单集的管理接口。"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig
from tests.fake_cloud115 import FakeCloud115
from tests.fake_tmdb import FakeTmdb
from tests.test_browse import build_catalog, find
from tests.test_sync import fresh_admin

ADMIN = "/api/admin"


@pytest.fixture
def catalog(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115, tmdb: FakeTmdb
) -> dict[str, int]:
    return build_catalog(client, admin, cloud115, tmdb)


def call(client: TestClient, method: str, path: str, status: int, **kwargs: Any) -> Any:
    response = client.request(method, f"{ADMIN}{path}", headers=fresh_admin(client), **kwargs)
    assert response.status_code == status, response.text
    return response.json() if response.content else None


def error_code(client: TestClient, method: str, path: str, status: int, **kwargs: Any) -> str:
    body = call(client, method, path, status, **kwargs)
    return str(body["error"]["code"])


def query(config: AppConfig, sql: str, *args: Any) -> list[tuple[Any, ...]]:
    with sqlite3.connect(config.database_path) as connection:
        return [tuple(row) for row in connection.execute(sql, args).fetchall()]


def item_id(client: TestClient, title: str) -> int:
    return int(find(client, title)["id"])


def wait_task(client: TestClient, task_id: int) -> dict[str, Any]:
    for _ in range(100):
        task = call(client, "GET", f"/tasks/{task_id}", 200)
        if task["status"] != "running":
            return dict(task)
        time.sleep(0.05)
    raise AssertionError("任务没有结束")


# ---- 条目信息与元数据编辑 ----


@pytest.mark.usefixtures("catalog")
def test_item_info_lists_versions_subtitles_and_metadata(client: TestClient) -> None:
    earth = item_id(client, "流浪地球")
    info = call(client, "GET", f"/items/{earth}/info", 200)

    assert set(info) == {"item", "versions", "subtitles", "metadata"}
    assert info["item"]["id"] == earth
    assert info["item"]["title"] == "流浪地球"
    assert {"library_id", "kind", "provider_id", "remote_id", "genres", "poster_image_id"} <= set(
        info["item"]
    )
    assert len(info["versions"]) == 2
    assert set(info["versions"][0]) == {
        "id",
        "label",
        "attributes",
        "rel_path",
        "size",
        "state",
        "identity",
        "duration",
        "width",
        "height",
        "video_codec",
        "hdr",
        "audio",
        "subtitles",
    }
    assert [v["id"] for v in info["versions"]] == sorted(v["id"] for v in info["versions"])
    assert len(info["subtitles"]) == 1
    assert set(info["subtitles"][0]) == {"id", "label", "language", "forced", "rel_path"}
    assert info["metadata"]
    assert set(info["metadata"][0]) == {"field", "source", "locked", "updated_at"}
    assert [m["field"] for m in info["metadata"]] == sorted(m["field"] for m in info["metadata"])

    assert error_code(client, "GET", "/items/999999/info", 404) == "not_found"


@pytest.mark.usefixtures("catalog")
def test_patch_item_metadata_writes_values_and_locks(client: TestClient) -> None:
    earth = item_id(client, "流浪地球")
    info = call(
        client,
        "PATCH",
        f"/items/{earth}/metadata",
        200,
        json={
            "values": {"title": "新片名", "genres": '["科幻","灾难"]', "tagline": None},
            "locked": {"tagline": False},
        },
    )

    assert info["item"]["title"] == "新片名"
    assert info["item"]["genres"] == '["科幻","灾难"]'
    assert info["item"]["tagline"] is None
    fields = {m["field"]: m for m in info["metadata"]}
    assert {"title", "genres", "tagline"} <= set(fields)
    assert fields["title"]["source"] == "manual"
    assert fields["title"]["locked"] == 1
    assert fields["tagline"]["locked"] == 0

    again = call(
        client,
        "PATCH",
        f"/items/{earth}/metadata",
        200,
        json={"values": {"tagline": "新口号"}, "locked": {"tagline": True}},
    )
    assert {m["field"]: m["locked"] for m in again["metadata"]}["tagline"] == 1


@pytest.mark.usefixtures("catalog")
def test_patch_item_metadata_rejects_bad_input(client: TestClient, config: AppConfig) -> None:
    earth = item_id(client, "流浪地球")
    path = f"/items/{earth}/metadata"
    manual = "SELECT COUNT(*) FROM metadata_field WHERE source = 'manual'"

    unknown = call(client, "PATCH", path, 400, json={"values": {"year": "2020", "id": "1"}})
    assert unknown["error"]["code"] == "invalid_metadata_field"
    assert unknown["error"]["message"] == "不支持的字段: id, year"
    # 字段名先于条目是否存在校验
    assert error_code(client, "PATCH", "/items/999999/metadata", 400, json={"values": {"id": "1"}})
    assert error_code(client, "PATCH", "/items/999999/metadata", 404, json={"values": {}}) == (
        "not_found"
    )
    bad = call(client, "PATCH", path, 400, json={"values": {"genres": "不是 JSON"}})
    assert bad["error"]["code"] == "invalid_metadata_value"
    assert bad["error"]["message"] == "字段 genres 必须是 JSON 数组"
    assert query(config, manual) == [(0,)]


@pytest.mark.usefixtures("catalog")
def test_unmatch_item_detaches_works_and_deletes_the_item(
    client: TestClient, config: AppConfig
) -> None:
    earth = item_id(client, "流浪地球")
    works = query(config, "SELECT COUNT(*) FROM work WHERE media_item_id = ?", earth)[0][0]
    assert works >= 1

    body = call(client, "POST", f"/items/{earth}/unmatch", 202)

    assert body["item_id"] == earth
    assert body["works"] == works
    assert query(config, "SELECT COUNT(*) FROM media_item WHERE id = ?", earth) == [(0,)]
    assert query(
        config,
        "SELECT COUNT(*) FROM work WHERE state = 'unmatched' AND manual = 0 "
        "AND media_item_id IS NULL",
    ) == [(works,)]
    task = call(client, "GET", f"/tasks/{body['task_id']}", 200)
    assert (task["kind"], task["status"]) == ("unmatch_item", "succeeded")
    assert task["result"] == {"item_id": earth, "works": works}
    assert error_code(client, "POST", f"/items/{earth}/unmatch", 404) == "not_found"


# ---- 合并 ----


@pytest.mark.usefixtures("catalog")
def test_merge_items_moves_versions_and_merges_playback(
    client: TestClient, config: AppConfig
) -> None:
    earth, stars = item_id(client, "流浪地球"), item_id(client, "星际穿越")
    playable = "SELECT id FROM playable WHERE media_item_id = ? AND episode_id IS NULL"
    earth_playable = query(config, playable, earth)[0][0]
    stars_playable = query(config, playable, stars)[0][0]
    user = query(config, "SELECT id FROM user LIMIT 1")[0][0]
    with sqlite3.connect(config.database_path) as connection:
        connection.executemany(
            "INSERT INTO playback_state(user_id,playable_id,position,duration,watched,"
            "last_played_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            [
                (user, earth_playable, 10, 100, 0, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
                (user, stars_playable, 90, 100, 1, "2026-02-01T00:00:00Z", "2026-02-01T00:00:00Z"),
            ],
        )

    body = call(
        client, "POST", "/items/merge", 202, json={"target_id": earth, "source_ids": [stars]}
    )

    assert (body["target_id"], body["source_ids"]) == (earth, [stars])
    assert query(config, "SELECT COUNT(*) FROM media_item WHERE id = ?", stars) == [(0,)]
    assert query(config, "SELECT COUNT(*) FROM playable WHERE id = ?", stars_playable) == [(0,)]
    assert query(
        config,
        "SELECT COUNT(*) FROM version WHERE playable_id = ?",
        earth_playable,
    ) == [(3,)]
    # 目标已有的播放进度保留，已看标记和最近播放时间取两者较新的
    assert query(
        config,
        "SELECT position, watched, last_played_at FROM playback_state WHERE playable_id = ?",
        earth_playable,
    ) == [(10, 1, "2026-02-01T00:00:00Z")]
    assert query(config, "SELECT COUNT(*) FROM work WHERE media_item_id = ?", earth) == query(
        config, "SELECT COUNT(*) FROM work WHERE media_item_id = ?", earth
    )
    task = call(client, "GET", f"/tasks/{body['task_id']}", 200)
    assert (task["kind"], task["status"]) == ("merge_items", "succeeded")
    assert task["result"]["target_id"] == earth
    assert task["result"]["source_ids"] == [stars]


@pytest.mark.usefixtures("catalog")
def test_merge_items_validates_selection(client: TestClient, config: AppConfig) -> None:
    earth, stars = item_id(client, "流浪地球"), item_id(client, "星际穿越")
    series = item_id(client, "三体")

    def merge(target: int, sources: list[int], status: int) -> str:
        return error_code(
            client,
            "POST",
            "/items/merge",
            status,
            json={"target_id": target, "source_ids": sources},
        )

    assert merge(earth, [earth], 400) == "invalid_merge"
    assert merge(earth, [stars, stars], 400) == "invalid_merge"
    assert merge(999999, [stars], 404) == "not_found"
    assert merge(earth, [stars, 999999], 409) == "merge_kind_mismatch"
    assert merge(earth, [series], 409) == "merge_kind_mismatch"
    assert merge(series, [earth], 409) == "merge_kind_mismatch"
    assert query(config, "SELECT COUNT(*) FROM media_item WHERE id IN (?, ?)", earth, stars) == [
        (2,)
    ]


# ---- 批量操作 ----


@pytest.mark.usefixtures("catalog")
def test_batch_labels_add_and_remove(client: TestClient, config: AppConfig) -> None:
    earth, stars = item_id(client, "流浪地球"), item_id(client, "星际穿越")

    added = call(
        client,
        "POST",
        "/items/batch",
        202,
        json={
            "ids": [earth, stars, 999999],
            "action": "labels_add",
            "payload": {"label": " 收藏 "},
        },
    )
    assert added["success"] == sorted([earth, stars])
    assert added["failed"] == [999999]
    assert query(config, "SELECT media_item_id FROM item_label WHERE label = '收藏'") == [
        (i,) for i in sorted([earth, stars])
    ]
    task = call(client, "GET", f"/tasks/{added['task_id']}", 200)
    assert (task["kind"], task["status"]) == ("batch_items", "succeeded")
    assert task["result"]["success"] == sorted([earth, stars])

    call(
        client,
        "POST",
        "/items/batch",
        202,
        json={"ids": [earth], "action": "labels_remove", "payload": {"label": "收藏"}},
    )
    assert query(config, "SELECT media_item_id FROM item_label") == [(stars,)]


@pytest.mark.usefixtures("catalog")
def test_batch_watched_updates_only_existing_playback_state(
    client: TestClient, config: AppConfig
) -> None:
    earth, stars = item_id(client, "流浪地球"), item_id(client, "星际穿越")
    admin_id = query(config, "SELECT id FROM user WHERE role = 'admin' LIMIT 1")[0][0]
    playable = query(config, "SELECT id FROM playable WHERE media_item_id = ?", earth)[0][0]
    with sqlite3.connect(config.database_path) as connection:
        connection.execute(
            "INSERT INTO playback_state(user_id,playable_id,watched,updated_at) "
            "VALUES (?,?,0,'2026-01-01T00:00:00Z')",
            (admin_id, playable),
        )

    call(client, "POST", "/items/batch", 202, json={"ids": [earth, stars], "action": "watched"})
    assert query(config, "SELECT watched FROM playback_state") == [(1,)]
    call(client, "POST", "/items/batch", 202, json={"ids": [earth], "action": "unwatched"})
    assert query(config, "SELECT watched FROM playback_state") == [(0,)]


@pytest.mark.usefixtures("catalog")
def test_batch_validates_action_and_selection(client: TestClient) -> None:
    earth = item_id(client, "流浪地球")

    def batch(status: int, **body: Any) -> str:
        return error_code(
            client, "POST", "/items/batch", status, json={"action": "watched", **body}
        )

    assert batch(400) == "empty_selection"
    assert batch(400, ids=[earth], action="explode") == "invalid_batch_action"
    assert batch(400, filter={"genre": "科幻"}) == "invalid_filter"
    assert batch(400, filter={"library_id": 1, "genre": 5}) == "invalid_filter"
    assert batch(400, filter={"library_id": 1, "year": 1700}) == "invalid_filter"
    assert batch(400, filter={"library_id": 1, "watched": "yes"}) == "invalid_filter"
    assert batch(400, filter={"library_id": 1, "status": "bogus"}) == "invalid_filter"


def test_batch_filter_selects_by_library_genre_year_and_status(
    client: TestClient, catalog: dict[str, int]
) -> None:
    movies = catalog["movies"]

    def select(**filters: Any) -> list[int]:
        body = call(
            client,
            "POST",
            "/items/batch",
            202,
            json={
                "filter": {"library_id": movies, **filters},
                "action": "labels_add",
                "payload": {"label": "x"},
            },
        )
        return list(body["success"])

    everything = select()
    assert len(everything) == 3
    scifi = select(genre="科幻")
    assert scifi == sorted([item_id(client, "流浪地球"), item_id(client, "星际穿越")])
    assert select(genre="科幻", year=2019) == [item_id(client, "流浪地球")]
    assert select(watched=True) == []
    assert select(watched=False) == everything
    multiple = select(status="multiple_versions")
    assert multiple == [item_id(client, "流浪地球")]
    assert select(status="missing") == []


@pytest.mark.usefixtures("catalog")
def test_batch_refresh_runs_in_background(client: TestClient) -> None:
    earth = item_id(client, "流浪地球")

    body = call(client, "POST", "/items/batch", 202, json={"ids": [earth], "action": "refresh"})

    assert (body["success"], body["failed"]) == ([], [])
    task = wait_task(client, body["task_id"])
    assert task["status"] == "succeeded"
    assert (task["result"]["success"], task["result"]["failed"]) == ([earth], [])


# ---- 季与单集 ----


def season_and_episode(config: AppConfig) -> tuple[int, int]:
    season = query(
        config,
        "SELECT s.id, e.id FROM season s JOIN episode e ON e.season_id = s.id "
        "WHERE s.number = 1 ORDER BY e.number LIMIT 1",
    )[0]
    return int(season[0]), int(season[1])


@pytest.mark.usefixtures("catalog")
def test_patch_season_and_episode_metadata(client: TestClient, config: AppConfig) -> None:
    season, episode = season_and_episode(config)

    assert call(
        client,
        "PATCH",
        f"/seasons/{season}/metadata",
        200,
        json={"values": {"name": "第一季", "overview": None}, "locked": {"overview": False}},
    ) == {"id": season, "kind": "season"}
    assert query(config, "SELECT name FROM season WHERE id = ?", season) == [("第一季",)]
    assert query(
        config,
        "SELECT field, source, locked FROM metadata_field "
        "WHERE entity_kind = 'season' AND entity_id = ? ORDER BY field",
        season,
    ) == [("name", "manual", 1), ("overview", "manual", 0)]

    assert call(
        client,
        "PATCH",
        f"/episodes/{episode}/metadata",
        200,
        json={"values": {"still_path": "/s.jpg"}},
    ) == {"id": episode, "kind": "episode"}
    assert query(config, "SELECT still_path FROM episode WHERE id = ?", episode) == [("/s.jpg",)]

    unsupported = call(
        client, "PATCH", f"/seasons/{season}/metadata", 400, json={"values": {"still_path": "x"}}
    )
    assert unsupported["error"]["code"] == "invalid_metadata_field"
    assert unsupported["error"]["message"] == "不支持的字段"
    missing = call(client, "PATCH", "/seasons/999999/metadata", 404, json={"values": {}})
    assert missing["error"]["message"] == "季或单集不存在"
    assert call(client, "PATCH", "/episodes/999999/metadata", 404, json={"values": {}})


@pytest.mark.usefixtures("catalog")
def test_refresh_season_and_episode_create_tasks(client: TestClient, config: AppConfig) -> None:
    season, episode = season_and_episode(config)

    body = call(client, "POST", f"/seasons/{season}/refresh", 202)
    task = call(client, "GET", f"/tasks/{body['task_id']}", 200)
    assert (task["kind"], task["status"]) == ("refresh_season", "succeeded")
    assert task["result"] == {"season_id": season}

    body = call(client, "POST", f"/episodes/{episode}/refresh", 202)
    task = call(client, "GET", f"/tasks/{body['task_id']}", 200)
    assert (task["kind"], task["status"]) == ("refresh_episode", "succeeded")
    assert task["result"] == {"episode_id": episode}

    missing = call(client, "POST", "/seasons/999999/refresh", 404)
    assert missing["error"]["message"] == "季不存在"
    missing = call(client, "POST", "/episodes/999999/refresh", 404)
    assert missing["error"]["message"] == "单集不存在"


@pytest.mark.usefixtures("catalog")
def test_delete_episode_and_season_remove_media_files_and_rows(
    client: TestClient, config: AppConfig, cloud115: FakeCloud115
) -> None:
    season, episode = season_and_episode(config)
    episode_files = query(
        config,
        "SELECT DISTINCT c.identity FROM playable p JOIN version v ON v.playable_id = p.id "
        "JOIN media_file c ON c.id = v.media_file_id WHERE p.episode_id = ?",
        episode,
    )
    season_files = query(
        config,
        "SELECT DISTINCT c.identity FROM episode e JOIN playable p ON p.episode_id = e.id "
        "JOIN version v ON v.playable_id = p.id JOIN media_file c ON c.id = v.media_file_id "
        "WHERE e.season_id = ?",
        season,
    )
    assert len(episode_files) == 1
    assert len(season_files) == 2

    assert error_code(client, "DELETE", f"/episodes/{episode}", 409) == (
        "delete_confirmation_required"
    )
    assert error_code(client, "DELETE", "/episodes/999999?confirm=true", 404) == "not_found"
    assert error_code(client, "DELETE", "/seasons/999999?confirm=true", 404) == "not_found"

    response = client.delete(
        f"{ADMIN}/episodes/{episode}", params={"confirm": True}, headers=fresh_admin(client)
    )
    assert response.status_code == 202, response.text
    task = call(client, "GET", f"/tasks/{response.headers['X-Task-Id']}", 200)
    assert (task["kind"], task["status"]) == ("delete_episode", "succeeded")
    assert task["result"] == {"requested": 1, "succeeded": 1}
    assert query(config, "SELECT COUNT(*) FROM episode WHERE id = ?", episode) == [(0,)]
    assert str(episode_files[0][0]) not in cloud115.nodes

    response = client.delete(
        f"{ADMIN}/seasons/{season}", params={"confirm": True}, headers=fresh_admin(client)
    )
    assert response.status_code == 202, response.text
    task = call(client, "GET", f"/tasks/{response.headers['X-Task-Id']}", 200)
    assert (task["kind"], task["status"]) == ("delete_season", "succeeded")
    assert query(config, "SELECT COUNT(*) FROM season WHERE id = ?", season) == [(0,)]
    assert query(config, "SELECT COUNT(*) FROM episode WHERE season_id = ?", season) == [(0,)]
