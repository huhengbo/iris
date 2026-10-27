"""待处理里删除作品和文件：连同 115 上的文件一起删，已经归入条目的不在这里删。"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi.testclient import TestClient

from app.config import AppConfig
from tests.conftest import bearer, create_user, login
from tests.fake_cloud115 import FakeCloud115
from tests.fake_tmdb import FakeTmdb
from tests.test_browse import build_catalog, find
from tests.test_catalog import get, movie_library
from tests.test_sync import fresh_admin, sync

URL = "/api/admin/pending/delete"


def delete(
    client: TestClient,
    body: dict[str, Any],
    status: int = 202,
    headers: dict[str, str] | None = None,
) -> Any:
    response = client.post(
        URL,
        params={"confirm": True},
        json=body,
        headers={**fresh_admin(client), **(headers or {})},
    )
    assert response.status_code == status, response.text
    if status != 202:
        return response.json()["error"]
    task = client.get(
        f"/api/admin/tasks/{response.headers['X-Task-Id']}", headers=fresh_admin(client)
    )
    return task.json()


def works(client: TestClient) -> dict[str, dict[str, Any]]:
    return {w["path"]: w for w in get(client, "/works", state="pending")["items"]}


def rel_paths(config: AppConfig) -> set[str]:
    with sqlite3.connect(config.database_path) as connection:
        return {row[0] for row in connection.execute("SELECT rel_path FROM media_file")}


def test_delete_work_removes_its_folder_on_115(
    client: TestClient,
    admin: dict[str, str],
    admin_tokens: dict[str, str],
    cloud115: FakeCloud115,
    config: AppConfig,
) -> None:
    # 没配 TMDB，作品都在等待匹配
    movie_library(
        client,
        admin,
        cloud115,
        [
            "奇怪的片名 (2021)/奇怪的片名.2021.mkv",
            "奇怪的片名 (2021)/奇怪的片名.2021.chs.srt",
            "星际穿越 (2014)/Interstellar.2014.1080p.mp4",
        ],
    )
    sync(client, admin)
    folder = cloud115.make_path("影视/电影/奇怪的片名 (2021)")
    work = works(client)["/影视/电影/奇怪的片名 (2021)"]

    response = client.post(URL, json={"work_ids": [work["id"]]}, headers=fresh_admin(client))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "delete_confirmation_required"
    created = create_user(client, admin_tokens)
    username = created["username"]
    assert isinstance(username, str)
    user = bearer(login(client, username, "alice-password"))
    denied = client.post(
        URL, params={"confirm": True}, json={"work_ids": [work["id"]]}, headers=user
    )
    assert denied.status_code == 403
    assert folder in cloud115.nodes

    task = delete(client, {"work_ids": [work["id"]]})

    assert (task["kind"], task["status"]) == ("delete_pending", "succeeded")
    assert task["result"]["input"] == {"work_ids": [work["id"]], "media_file_ids": []}
    assert folder not in cloud115.nodes
    assert cloud115.make_path("影视/电影/星际穿越 (2014)/Interstellar.2014.1080p.mp4")
    assert rel_paths(config) == {"星际穿越 (2014)/Interstellar.2014.1080p.mp4"}
    assert set(works(client)) == {"/影视/电影/星际穿越 (2014)"}
    assert get(client, "/pending")["waiting"] == 1
    # 下次同步不会再把它找回来
    sync(client, admin)
    assert set(works(client)) == {"/影视/电影/星际穿越 (2014)"}


def test_delete_loose_work_only_removes_its_file(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115, config: AppConfig
) -> None:
    # 散放在分类目录里的电影各自成为一部作品，删除时不能删整个分类目录
    movie_library(client, admin, cloud115, ["动作/Movie.A.2020.mkv", "动作/Movie.B.2019.mkv"])
    sync(client, admin)
    work = works(client)["/影视/电影/动作/Movie.A.2020.mkv"]

    task = delete(client, {"work_ids": [work["id"]]})

    assert task["status"] == "succeeded"
    assert cloud115.files_in(cloud115.make_path("影视/电影/动作")) == ["Movie.B.2019.mkv"]
    assert rel_paths(config) == {"动作/Movie.B.2019.mkv"}


def test_delete_ignored_and_shared_folders(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115
) -> None:
    movie_library(
        client,
        admin,
        cloud115,
        [
            "系列 (2020)/系列.2020.mkv",
            "系列 (2020)/续集 (2022)/续集.2022.mkv",
            "星际穿越 (2014)/Interstellar.2014.1080p.mp4",
            "星际穿越 (2014)/Interstellar.trailer.mp4",
        ],
    )
    sync(client, admin)
    found = works(client)
    parent = found["/影视/电影/系列 (2020)"]["id"]
    child = found["/影视/电影/系列 (2020)/续集 (2022)"]["id"]

    # 目录里还有别的作品，不能单独整个删
    error = delete(client, {"work_ids": [parent]}, status=409)
    assert error["code"] == "media_folder_shared"
    assert cloud115.make_path("影视/电影/系列 (2020)/续集 (2022)/续集.2022.mkv")
    # 一起选上时可以删
    assert delete(client, {"work_ids": [parent, child]})["status"] == "succeeded"
    assert cloud115.child(cloud115.make_path("影视/电影"), "系列 (2020)") is None

    trailer = get(client, "/files", status="ignored")["items"][0]
    assert trailer["path"].endswith("Interstellar.trailer.mp4")
    assert delete(client, {"media_file_ids": [trailer["id"]]})["status"] == "succeeded"
    assert cloud115.files_in(cloud115.make_path("影视/电影/星际穿越 (2014)")) == [
        "Interstellar.2014.1080p.mp4"
    ]
    assert get(client, "/pending")["ignored"] == 0


def test_items_in_the_catalog_are_not_deleted_here(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
    config: AppConfig,
) -> None:
    build_catalog(client, admin, cloud115, tmdb)
    earth = find(client, "流浪地球")
    with sqlite3.connect(config.database_path) as connection:
        work_id, media_file_id = connection.execute(
            "SELECT w.id, c.id FROM work w JOIN media_file c ON c.work_id = w.id "
            "WHERE w.media_item_id = ? AND c.kind = 'video' LIMIT 1",
            (earth["id"],),
        ).fetchone()
    calls = cloud115.calls.count("delete")

    assert delete(client, {"work_ids": [work_id]}, status=409)["code"] == "work_matched"
    assert delete(client, {"media_file_ids": [media_file_id]}, status=409)["code"] == "file_in_use"
    assert delete(client, {"work_ids": [99999]}, status=404)["code"] == "not_found"
    assert delete(client, {}, status=422)["code"]
    assert cloud115.calls.count("delete") == calls
    assert find(client, "流浪地球")["id"] == earth["id"]


def test_failed_delete_keeps_records_and_can_be_retried(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115, config: AppConfig
) -> None:
    movie_library(client, admin, cloud115, ["奇怪的片名 (2021)/奇怪的片名.2021.mkv"])
    sync(client, admin)
    work = works(client)["/影视/电影/奇怪的片名 (2021)"]
    cloud115.control.call("source", "fail_next", {"method": "delete", "count": 1})

    task = delete(
        client,
        {"work_ids": [work["id"]]},
        headers={"Idempotency-Key": "delete-strange"},
    )

    assert task["status"] == "failed"
    assert task["result"]["failed"] == 1
    assert rel_paths(config) == {"奇怪的片名 (2021)/奇怪的片名.2021.mkv"}
    again = client.post(
        URL,
        params={"confirm": True},
        json={"work_ids": [work["id"]]},
        headers={**fresh_admin(client), "Idempotency-Key": "delete-strange"},
    )
    assert again.json()["error"]["code"] == "task_already_failed"

    retried = client.post(f"/api/admin/tasks/{task['id']}/retry", headers=fresh_admin(client))
    assert retried.status_code == 200, retried.text
    assert (retried.json()["kind"], retried.json()["status"]) == ("delete_pending", "succeeded")
    assert rel_paths(config) == set()
    assert works(client) == {}


def test_works_whose_videos_are_gone_are_not_pending(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115
) -> None:
    movie_library(
        client,
        admin,
        cloud115,
        ["奇怪的片名 (2021)/奇怪的片名.2021.mkv", "星际穿越 (2014)/Interstellar.2014.1080p.mp4"],
    )
    sync(client, admin)
    cloud115.remove(cloud115.make_path("影视/电影/奇怪的片名 (2021)"))

    sync(client, admin)

    # 文件刚消失时只标记失效，作品记录还在，但不算待处理
    assert set(works(client)) == {"/影视/电影/星际穿越 (2014)"}
    assert get(client, "/pending")["waiting"] == 1
