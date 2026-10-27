from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Any

from fastapi.testclient import TestClient

from app.api.browse import ItemCardOut
from app.config import AppConfig
from tests.fake_cloud115 import FakeCloud115
from tests.fake_tmdb import FakeTmdb
from tests.test_browse import build_catalog, find
from tests.test_sync import fresh_admin


def test_phase3_admin_surfaces_and_device_metadata(
    client: TestClient, admin: dict[str, str]
) -> None:
    assert client.get("/api/admin/alerts", headers=admin).json() == []

    task = client.post("/api/admin/scheduled-tasks/backup/run", headers=admin)
    assert task.status_code == 202
    assert task.json()["task_id"] > 0
    assert client.get("/api/admin/scheduled-tasks", headers=admin).json()[0]["key"] == "backup"

    webhook = client.post(
        "/api/admin/webhooks",
        headers=admin,
        json={"url": "https://example.com/hook", "events": ["alert"]},
    )
    assert webhook.status_code == 201
    assert (
        client.get("/api/admin/webhooks", headers=admin).json()[0]["url"]
        == "https://example.com/hook"
    )

    sessions = client.get("/api/me/sessions", headers=admin)
    assert sessions.status_code == 200
    assert sessions.json()[0]["platform"] is None


def _query(config: AppConfig, sql: str, *args: object) -> list[tuple[Any, ...]]:
    with closing(sqlite3.connect(config.database_path)) as connection, connection:
        return connection.execute(sql, args).fetchall()


def _deactivate(config: AppConfig, condition: str, *args: object) -> None:
    """把满足条件（别名 p 的可播放条目）的文件标成丢失，让它不再可播放。"""
    _query(
        config,
        "UPDATE media_file SET state = 'missing' WHERE id IN (SELECT v.media_file_id "
        f"FROM version v JOIN playable p ON p.id = v.playable_id WHERE {condition})",
        *args,
    )


def test_playlist_add_episode_whole_series_and_movie(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
    config: AppConfig,
) -> None:
    build_catalog(client, admin, cloud115, tmdb)
    headers = fresh_admin(client)
    series = find(client, "三体")["id"]
    movie = find(client, "星际穿越")["id"]
    episodes = {
        (row[0], row[1]): (row[2], row[3], row[4])
        for row in _query(
            config,
            "SELECT s.number, e.number, e.id, p.id, e.name FROM episode e "
            "JOIN season s ON s.id = e.season_id JOIN playable p ON p.episode_id = e.id "
            "WHERE s.media_item_id = ?",
            series,
        )
    }
    # S01E02 没有可用文件，整部剧加入时要跳过
    _deactivate(config, "p.id = ?", episodes[(1, 2)][1])
    playlist = client.post("/api/playlists", headers=headers, json={"title": "追剧"}).json()
    base = f"/api/playlists/{playlist['id']}/items"

    added = client.post(
        base, headers=headers, json={"item_id": series, "episode_id": episodes[(2, 1)][0]}
    )
    assert added.status_code == 201, added.text
    [first] = added.json()["items"]
    assert first["playable_id"] == episodes[(2, 1)][1]
    assert first["episode_id"] == episodes[(2, 1)][0]
    assert first["episode_name"] == episodes[(2, 1)][2]
    assert (first["season_number"], first["episode_number"]) == (2, 1)

    # 整部剧：按季、集顺序追加，已在列表里的 S02E01 不重复
    whole = client.post(base, headers=headers, json={"item_id": series})
    assert whole.status_code == 201, whole.text
    items = whole.json()["items"]
    assert [i["playable_id"] for i in items] == [
        episodes[(2, 1)][1],
        episodes[(0, 1)][1],
        episodes[(1, 1)][1],
    ]
    assert [i["position"] for i in items] == [0, 1, 2]

    film = client.post(base, headers=headers, json={"item_id": movie, "episode_id": None})
    assert film.status_code == 201, film.text
    last = film.json()["items"][-1]
    assert (last["item_id"], last["position"]) == (movie, 3)
    assert last["episode_id"] is None and last["episode_name"] is None
    assert client.get(f"/api/playlists/{playlist['id']}", headers=headers).json() == film.json()

    # 单集必须属于给定的条目
    for body in (
        {"item_id": movie, "episode_id": episodes[(1, 1)][0]},
        {"item_id": series, "episode_id": 999999},
    ):
        wrong = client.post(base, headers=headers, json=body)
        assert wrong.status_code == 404, wrong.text
        assert wrong.json()["error"]["message"] == "单集不存在"

    # 整部剧都没有可用文件时报没有可播放版本
    _deactivate(config, "p.media_item_id = ?", series)
    other = client.post("/api/playlists", headers=headers, json={"title": "空"}).json()
    empty = client.post(
        f"/api/playlists/{other['id']}/items", headers=headers, json={"item_id": series}
    )
    assert empty.status_code == 404, empty.text
    assert empty.json()["error"]["message"] == "条目没有可播放版本"


def test_collection_detail_cards_and_member_editing(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
    config: AppConfig,
) -> None:
    ids = build_catalog(client, admin, cloud115, tmdb)
    headers = fresh_admin(client)
    earth, stars, alien = (find(client, t)["id"] for t in ("流浪地球", "星际穿越", "疯狂的外星人"))
    series = find(client, "三体")["id"]
    collection = client.post(
        "/api/admin/collections",
        headers=headers,
        json={"library_id": ids["movies"], "title": "科幻", "sort_mode": "custom"},
    ).json()
    cid = collection["id"]
    response = client.put(
        f"/api/admin/collections/{cid}/items",
        headers=headers,
        json={"item_ids": [stars, earth, alien]},
    )
    assert response.status_code == 200, response.text

    def members() -> list[tuple[Any, ...]]:
        return _query(
            config,
            "SELECT media_item_id, manual FROM collection_item WHERE collection_id = ? "
            "ORDER BY position",
            cid,
        )

    order = [row[0] for row in members()]
    # 疯狂的外星人没有可用文件，影视墙不列它，合集详情也不列
    _deactivate(config, "p.media_item_id = ?", alien)

    def detail() -> dict[str, Any]:
        response = client.get(f"/api/collections/{cid}", headers=headers)
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    body = detail()
    assert body["collection"]["id"] == cid
    assert [i["id"] for i in body["items"]] == [i for i in order if i != alien]
    wall = client.get(f"/api/libraries/{ids['movies']}/items", headers=headers).json()["items"]
    cards = {card["id"]: card for card in wall}
    for item in body["items"]:
        assert set(item) == set(ItemCardOut.model_fields)
        assert item == cards[item["id"]]

    # 移除后再追加：排到最后，标为手动成员；重复追加什么也不做
    remove = client.delete(f"/api/admin/collections/{cid}/items/{stars}", headers=headers)
    assert remove.status_code == 204, remove.text
    again = client.delete(f"/api/admin/collections/{cid}/items/{stars}", headers=headers)
    assert again.status_code == 404, again.text
    for _ in range(2):
        add = client.post(
            f"/api/admin/collections/{cid}/items", headers=headers, json={"item_id": stars}
        )
        assert add.status_code == 204, add.text
    order = [i for i in order if i != stars] + [stars]
    assert members() == [(i, 1) for i in order]
    assert [i["id"] for i in detail()["items"]] == [i for i in order if i != alien]

    # 其他资料库的条目、不存在的合集一律 404
    wrong = client.post(
        f"/api/admin/collections/{cid}/items", headers=headers, json={"item_id": series}
    )
    assert wrong.status_code == 404, wrong.text
    assert wrong.json()["error"]["message"] == "条目不存在"
    assert (
        client.post(
            "/api/admin/collections/999999/items", headers=headers, json={"item_id": earth}
        ).status_code
        == 404
    )
    assert (
        client.delete(f"/api/admin/collections/999999/items/{earth}", headers=headers).status_code
        == 404
    )


def test_alert_lifecycle_and_fingerprint_dedup(client: TestClient, admin: dict[str, str]) -> None:
    body = {"kind": "disk", "level": "error", "message": "磁盘快满了", "fingerprint": "disk-full"}
    created = client.post("/api/admin/alerts", headers=admin, json=body)
    assert created.status_code == 201, created.text
    alert_id = created.json()["id"]

    duplicate = client.post("/api/admin/alerts", headers=admin, json=body)
    assert (duplicate.status_code, duplicate.json()["error"]["code"]) == (409, "alert_already_open")
    # 没有指纹的警报不去重
    plain = {"kind": "note", "message": "提示"}
    assert client.post("/api/admin/alerts", headers=admin, json=plain).status_code == 201
    assert client.post("/api/admin/alerts", headers=admin, json=plain).status_code == 201

    active = client.get("/api/admin/alerts?active=true", headers=admin).json()
    assert len(active) == 3

    resolved = client.post(f"/api/admin/alerts/{alert_id}", headers=admin, json={"resolved": True})
    assert resolved.json()["resolved_at"] is not None
    assert len(client.get("/api/admin/alerts?active=true", headers=admin).json()) == 2
    # 处理后同一指纹可以再次告警
    assert client.post("/api/admin/alerts", headers=admin, json=body).status_code == 201
