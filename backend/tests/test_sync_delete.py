"""M8 验收：115 上删掉的文件，过了保留期后同步删除；成批消失时留给管理员确认。"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi.testclient import TestClient

from app.config import AppConfig
from tests.conftest import FakeClock, SyncRunner
from tests.fake_cloud115 import FakeCloud115
from tests.fake_tmdb import FakeTmdb
from tests.test_browse import build_catalog, find
from tests.test_catalog import movie_library
from tests.test_sync import files, fresh_admin, sync

HELD = "/api/admin/sync/held-deletions"


def count(config: AppConfig, table: str) -> int:
    with sqlite3.connect(config.database_path) as connection:
        value: int = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
    return value


def held(client: TestClient) -> list[dict[str, Any]]:
    response = client.get(HELD, headers=fresh_admin(client))
    assert response.status_code == 200, response.text
    body: list[dict[str, Any]] = response.json()
    return body


def save_grace(client: TestClient, days: int) -> Any:
    return client.put(
        "/api/admin/settings/library",
        json={"extras_min_mb": 200, "missing_grace_days": days},
        headers=fresh_admin(client),
    )


def remove_dir(cloud115: FakeCloud115, path: str) -> None:
    node = cloud115.make_path(path)
    cloud115.remove(node)


def test_missing_files_are_purged_after_grace_period(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
    clock: FakeClock,
    config: AppConfig,
) -> None:
    build_catalog(client, admin, cloud115, tmdb)
    stars = find(client, "星际穿越")
    headers = fresh_admin(client)
    assert client.put(f"/api/items/{stars['id']}/watched", headers=headers).status_code == 204
    images = count(config, "image")
    remove_dir(cloud115, "影视/电影/星际穿越 (2014)")

    # 刚消失：标记失效，条目和播放记录都保留，以便移回来时恢复
    body = sync(client, admin)
    assert body["stats"]["removed"] == 1
    assert body["stats"]["purged"] == 0
    assert files(config)["星际穿越 (2014)/Interstellar.2014.1080p.mp4"][1] == "missing"
    # 没有可用文件的条目对用户隐藏，但记录还在
    items = count(config, "media_item")
    assert count(config, "playback_state") == 1

    # 保留期内不删
    clock.advance(days=6)
    assert sync(client, admin)["stats"]["purged"] == 0
    assert "星际穿越 (2014)/Interstellar.2014.1080p.mp4" in files(config)

    # 过了保留期：文件、条目、播放记录和只有它用的图片一起删掉
    clock.advance(days=2)
    body = sync(client, admin)
    assert body["status"] == "succeeded", body
    assert body["stats"]["purged"] == 1
    assert "星际穿越 (2014)/Interstellar.2014.1080p.mp4" not in files(config)
    assert count(config, "media_item") == items - 1
    assert count(config, "playback_state") == 0
    assert count(config, "image") < images
    # 其他作品不受影响
    assert find(client, "流浪地球")["title"] == "流浪地球"
    assert held(client) == []


def test_grace_days_are_configurable(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    clock: FakeClock,
    config: AppConfig,
) -> None:
    settings = client.get("/api/admin/settings", headers=admin).json()
    assert settings["library"] == {"extras_min_mb": 200, "missing_grace_days": 7}
    for bad in (0, 366):
        assert save_grace(client, bad).status_code == 422
    response = save_grace(client, 1)
    assert response.status_code == 200, response.text
    assert response.json()["library"]["missing_grace_days"] == 1
    # 只改附加内容阈值时保留期不变
    response = client.put(
        "/api/admin/settings/library", json={"extras_min_mb": 100}, headers=fresh_admin(client)
    )
    assert response.json()["library"] == {"extras_min_mb": 100, "missing_grace_days": 1}

    movie_library(
        client,
        admin,
        cloud115,
        ["流浪地球 (2019)/流浪地球.2019.mkv", "星际穿越 (2014)/星际穿越.2014.mkv"],
    )
    sync(client, admin)
    remove_dir(cloud115, "影视/电影/星际穿越 (2014)")
    sync(client, admin)
    clock.advance(days=1, minutes=1)
    assert sync(client, admin)["stats"]["purged"] == 1
    assert list(files(config)) == ["流浪地球 (2019)/流浪地球.2019.mkv"]


def many_movies(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115, total: int
) -> dict[str, Any]:
    return movie_library(
        client,
        admin,
        cloud115,
        [f"电影{i:02d} (20{i:02d})/电影{i:02d}.20{i:02d}.mkv" for i in range(total)],
    )


def test_mass_disappearance_waits_for_confirmation(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    clock: FakeClock,
    config: AppConfig,
    sync_runner: SyncRunner,
) -> None:
    library = many_movies(client, admin, cloud115, 20)
    sync(client, admin)
    # 目录被误移走一大半，不能当作删除
    for i in range(6):
        remove_dir(cloud115, f"影视/电影/电影{i:02d} (20{i:02d})")
    sync(client, admin)
    clock.advance(days=8)

    body = sync(client, admin)
    assert body["status"] == "succeeded", body
    assert body["stats"]["purged"] == 0
    assert any("确认" in problem for problem in body["stats"]["problems"]), body
    assert len(files(config)) == 20
    assert held(client) == [
        {"library_id": library["id"], "library_name": "电影", "due": 6, "total": 20}
    ]

    # 同步进行中不能确认
    sync_runner.deferred = True
    started = client.post("/api/admin/sync", json={}, headers=fresh_admin(client))
    assert started.status_code == 202
    response = client.post(f"{HELD}/{library['id']}/confirm", headers=fresh_admin(client))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "sync_running"
    sync_runner.run_pending()
    sync_runner.deferred = False

    response = client.post(f"{HELD}/{library['id']}/confirm", headers=fresh_admin(client))
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": 6}
    assert len(files(config)) == 14
    assert held(client) == []
    assert count(config, "work") == 14

    missing = client.post(f"{HELD}/999/confirm", headers=fresh_admin(client))
    assert missing.status_code == 404


def test_few_files_in_small_library_are_purged_without_confirmation(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    clock: FakeClock,
    config: AppConfig,
) -> None:
    # 比例超过一成但只有几个文件，不必打扰管理员
    many_movies(client, admin, cloud115, 6)
    sync(client, admin)
    for i in range(3):
        remove_dir(cloud115, f"影视/电影/电影{i:02d} (20{i:02d})")
    sync(client, admin)
    clock.advance(days=8)
    assert sync(client, admin)["stats"]["purged"] == 3
    assert len(files(config)) == 3


def test_nothing_is_purged_when_library_sync_is_incomplete(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    clock: FakeClock,
    config: AppConfig,
) -> None:
    many_movies(client, admin, cloud115, 3)
    sync(client, admin)
    remove_dir(cloud115, "影视/电影/电影00 (2000)")
    sync(client, admin)
    clock.advance(days=8)
    # 这次扫描失败，说明看到的来源不完整
    cloud115.control.call("source", "fail_next", {"method": "scan", "count": 1})
    cloud115.make_path("影视/电影/新片 (2024)/新片.2024.mkv")

    body = sync(client, admin)
    assert body["stats"]["failed"] > 0
    assert body["stats"]["purged"] == 0
    assert files(config)["电影00 (2000)/电影00.2000.mkv"][1] == "missing"

    assert sync(client, admin)["stats"]["purged"] == 1


def test_restored_file_keeps_its_records(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
    clock: FakeClock,
    config: AppConfig,
) -> None:
    build_catalog(client, admin, cloud115, tmdb)
    stars = find(client, "星际穿越")
    headers = fresh_admin(client)
    assert client.put(f"/api/items/{stars['id']}/watched", headers=headers).status_code == 204
    folder = cloud115.make_path("影视/电影/星际穿越 (2014)")
    parking = cloud115.make_path("暂存")
    cloud115.move(folder, parking)
    sync(client, admin)
    clock.advance(days=5)
    # 保留期内移回来
    cloud115.move(folder, cloud115.make_path("影视/电影"))
    body = sync(client, admin)
    assert body["stats"]["restored"] == 1
    clock.advance(days=5)
    assert sync(client, admin)["stats"]["purged"] == 0
    assert find(client, "星际穿越")["watched"] is True
