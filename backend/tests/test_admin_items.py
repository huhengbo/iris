from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from app.config import AppConfig
from tests.conftest import bearer, create_user, login
from tests.fake_cloud115 import FakeCloud115
from tests.fake_tmdb import FakeTmdb
from tests.test_browse import build_catalog, find
from tests.test_sync import fresh_admin


def test_delete_item_is_admin_only_and_cleans_cloud_and_catalog(
    client: TestClient,
    admin: dict[str, str],
    admin_tokens: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
    config: AppConfig,
) -> None:
    build_catalog(client, admin, cloud115, tmdb)
    item = find(client, "流浪地球")
    item_id = int(item["id"])
    folder = cloud115.make_path("影视/电影/流浪地球 (2019)")
    identities = {
        cloud115.child(folder, name)
        for name in (
            "流浪地球.2019.1080p.BluRay.mkv",
            "流浪地球.2019.1080p.BluRay.chs.srt",
            "流浪地球.2019.2160p.WEB-DL.mkv",
        )
    }
    assert None not in identities
    # 数据库未识别的残留也必须随作品目录一起删除。
    orphan_id = cloud115.add_file(folder, "poster.jpg")

    user = create_user(client, admin_tokens)
    user_headers = bearer(login(client, str(user["username"]), "alice-password"))
    denied = client.delete(
        f"/api/admin/items/{item_id}", params={"confirm": True}, headers=user_headers
    )
    assert denied.status_code == 403

    response = client.delete(
        f"/api/admin/items/{item_id}", params={"confirm": True}, headers=fresh_admin(client)
    )
    assert response.status_code == 202, response.text
    task_id = response.headers.get("X-Task-Id")
    assert task_id is not None
    task = client.get(f"/api/admin/tasks/{task_id}", headers=fresh_admin(client))
    assert task.status_code == 200, task.text
    assert task.json()["kind"] == "delete_media"
    assert task.json()["status"] == "succeeded"
    assert task.json()["result"]["succeeded"] >= 1
    assert folder not in cloud115.nodes
    assert all(identity not in cloud115.nodes for identity in identities)
    assert orphan_id not in cloud115.nodes

    with sqlite3.connect(config.database_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM media_item WHERE id = ?", (item_id,)
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM work WHERE media_item_id = ?", (item_id,)
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM media_file c "
                "JOIN version v ON v.media_file_id = c.id "
                "JOIN playable p ON p.id = v.playable_id "
                "WHERE p.media_item_id = ?",
                (item_id,),
            ).fetchone()[0]
            == 0
        )


def test_delete_failure_keeps_partial_task_details(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
) -> None:
    build_catalog(client, admin, cloud115, tmdb)
    item_id = int(find(client, "流浪地球")["id"])
    cloud115.control.call("source", "fail_next", {"method": "delete", "count": 1})

    response = client.delete(
        f"/api/admin/items/{item_id}", params={"confirm": True}, headers=fresh_admin(client)
    )

    assert response.status_code == 202
    tasks = client.get("/api/admin/tasks", headers=fresh_admin(client)).json()
    task = next(task for task in tasks if task["kind"] == "delete_media")
    assert task["status"] == "failed"
    assert task["result"]["failed"] == 1
    assert task["result"]["failed_targets"]
    assert task["result"]["unprocessed"] >= 0
    retried = client.post(f"/api/admin/tasks/{task['id']}/retry", headers=fresh_admin(client))
    assert retried.status_code == 200, retried.text
    assert retried.json()["kind"] == "delete_media"
    assert retried.json()["status"] == "succeeded"
    gone = client.get(f"/api/admin/items/{item_id}", headers=fresh_admin(client))
    assert gone.status_code == 404, gone.text


def test_delete_idempotency_key_reuses_completed_task(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
) -> None:
    build_catalog(client, admin, cloud115, tmdb)
    item_id = int(find(client, "流浪地球")["id"])
    headers = {**fresh_admin(client), "Idempotency-Key": "delete-earth-once"}

    first = client.delete(f"/api/admin/items/{item_id}", params={"confirm": True}, headers=headers)
    assert first.status_code == 202
    calls = cloud115.calls.count("delete")
    second = client.delete(f"/api/admin/items/{item_id}", params={"confirm": True}, headers=headers)
    assert second.status_code == 202
    assert second.headers["X-Task-Id"] == first.headers["X-Task-Id"]
    assert cloud115.calls.count("delete") == calls
