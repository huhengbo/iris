from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.sync.files import MediaFileRepository, _media_files
from app.sync.schedule import SyncSchedule
from tests.conftest import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    FakeClock,
    SyncRunner,
    bearer,
    cloud115_roots,
    create_user,
    ensure_cloud115_source,
    login,
    make_app,
)
from tests.fake_cloud115 import FakeCloud115

BASE = "/api/admin/sync"


def test_disc_images_are_ignored_before_cataloging() -> None:
    paths = [
        "Movie (2024)/Movie.iso",
        "Movie (2024)/BDMV/STREAM/00001.m2ts",
        "Movie (2024)/VIDEO_TS/VTS_01_1.VOB",
        "Movie (2024)/Movie.2024.1080p.mkv",
    ]
    assert _media_files(paths) == {
        "Movie (2024)/Movie.2024.1080p.mkv": "video",
    }


def test_episode_nfo_is_cataloged() -> None:
    paths = ["剧/tvshow.nfo", "剧/Season 1/剧.S01E01.mkv", "剧/Season 1/剧.S01E01.nfo"]
    assert _media_files(paths) == {
        "剧/tvshow.nfo": "nfo",
        "剧/Season 1/剧.S01E01.mkv": "video",
        "剧/Season 1/剧.S01E01.nfo": "nfo",
    }


def test_existing_disc_files_are_ignored_instead_of_marked_missing(
    client: TestClient,
    admin: dict[str, str],
    movies: dict[str, Any],
    config: AppConfig,
    clock: FakeClock,
) -> None:
    sync(client, admin)
    now = clock.now().isoformat()
    with sqlite3.connect(config.database_path) as connection:
        connection.row_factory = sqlite3.Row
        folder_id = connection.execute("SELECT id FROM library_folder LIMIT 1").fetchone()[0]
        connection.execute(
            "INSERT INTO media_file (folder_id, rel_path, kind, state, identity, created_at, "
            "updated_at) "
            "VALUES (?, ?, 'video', 'active', ?, ?, ?)",
            (folder_id, "旧片/旧片.iso", "legacy-iso", now, now),
        )
        connection.execute(
            "INSERT INTO media_file (folder_id, rel_path, kind, state, created_at, updated_at) "
            "VALUES (?, ?, 'video', 'pending_enrich', ?, ?)",
            (folder_id, "旧盘/BDMV/STREAM/00001.m2ts", now, now),
        )
        paths = [
            row[0]
            for row in connection.execute(
                "SELECT rel_path FROM media_file WHERE folder_id = ? AND state = 'active'",
                (folder_id,),
            )
        ] + ["旧片/旧片.iso", "旧盘/BDMV/STREAM/00001.m2ts"]
        MediaFileRepository(connection, clock).apply_snapshot(folder_id, paths)
        states = dict(
            connection.execute("SELECT rel_path, state FROM media_file WHERE rel_path LIKE '旧%'")
        )
    assert states == {
        "旧片/旧片.iso": "ignored",
        "旧盘/BDMV/STREAM/00001.m2ts": "ignored",
    }


@pytest.fixture
def movies(client: TestClient, admin: dict[str, str], cloud115: FakeCloud115) -> dict[str, Any]:
    """电影资料库：两部片，带字幕、海报和 NFO（海报不入库，NFO 只用来读取 TMDB ID）。"""
    for path in [
        "影视/电影/流浪地球 (2019)/流浪地球.2019.2160p.mkv",
        "影视/电影/流浪地球 (2019)/流浪地球.2019.chs.ass",
        "影视/电影/流浪地球 (2019)/poster.jpg",
        "影视/电影/流浪地球 (2019)/movie.nfo",
        "影视/电影/星际穿越 (2014)/Interstellar.2014.1080p.mp4",
    ]:
        cloud115.make_path(path)
    return create_library(client, admin, "电影", "movie", cloud115.make_path("影视/电影"))


def create_library(
    client: TestClient, admin: dict[str, str], name: str, kind: str, cid: str
) -> dict[str, Any]:
    response = client.post(
        "/api/admin/libraries",
        json={"name": name, "kind": kind, "roots": cloud115_roots(client, cid)},
        headers=admin,
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def fresh_admin(client: TestClient) -> dict[str, str]:
    # 任务里的等待会推进假时钟，之前拿到的访问令牌可能已过期
    return bearer(login(client, ADMIN_USERNAME, ADMIN_PASSWORD))


def sync(client: TestClient, admin: dict[str, str], library_id: int | None = None) -> Any:
    response = client.post(BASE, json={"library_id": library_id}, headers=fresh_admin(client))
    assert response.status_code == 202, response.text
    return job(client, admin, response.json()["id"])


def job(client: TestClient, admin: dict[str, str], job_id: int) -> dict[str, Any]:
    jobs: list[dict[str, Any]] = client.get(f"{BASE}/jobs", headers=fresh_admin(client)).json()
    return next(j for j in jobs if j["id"] == job_id)


def libraries(client: TestClient) -> list[dict[str, Any]]:
    body: list[dict[str, Any]] = client.get(
        "/api/admin/libraries", headers=fresh_admin(client)
    ).json()
    return body


def files(config: AppConfig) -> dict[str, tuple[int, str, str | None]]:
    """rel_path -> (id, state, missing_at)"""
    with sqlite3.connect(config.database_path) as connection:
        rows = connection.execute("SELECT id, rel_path, state, missing_at FROM media_file")
        return {
            path: (media_file_id, state, missing_at)
            for media_file_id, path, state, missing_at in rows
        }


def stats(body: dict[str, Any]) -> dict[str, int]:
    keys = ("added", "removed", "renamed", "restored", "failed", "paused")
    return {key: body["stats"][key] for key in keys}


def running_job(config: AppConfig) -> sqlite3.Row:
    connection = sqlite3.connect(config.database_path)
    connection.row_factory = sqlite3.Row
    with connection:
        row: sqlite3.Row | None = connection.execute(
            "SELECT * FROM sync_job WHERE status = 'running'"
        ).fetchone()
    connection.close()
    assert row is not None
    return row


def test_first_sync_imports_videos_and_subtitles(
    client: TestClient,
    admin: dict[str, str],
    movies: dict[str, Any],
    cloud115: FakeCloud115,
    config: AppConfig,
) -> None:
    body = sync(client, admin)

    assert body["status"] == "succeeded", body
    assert body["trigger"] == "manual"
    assert body["scope"] == "all"
    assert body["phase"] == "done"
    assert stats(body) == {
        "added": 3,
        "removed": 0,
        "renamed": 0,
        "restored": 0,
        "failed": 0,
        "paused": 0,
    }
    assert body["finished_at"] is not None
    assert {path: state for path, (_, state, _) in files(config).items()} == {
        "流浪地球 (2019)/流浪地球.2019.2160p.mkv": "active",
        "流浪地球 (2019)/流浪地球.2019.chs.ass": "active",
        "流浪地球 (2019)/movie.nfo": "active",
        "星际穿越 (2014)/Interstellar.2014.1080p.mp4": "active",
    }
    library = libraries(client)[0]
    assert library["video_count"] == 2
    assert client.get(f"{BASE}/current", headers=admin).json() == {"job": None}


def test_enrichment_writes_source_identifiers(
    client: TestClient,
    admin: dict[str, str],
    movies: dict[str, Any],
    cloud115: FakeCloud115,
    config: AppConfig,
) -> None:
    sync(client, admin)

    with sqlite3.connect(config.database_path) as connection:
        row = connection.execute(
            "SELECT kind, identity, playback_locator, size, sha1 FROM media_file "
            "WHERE rel_path LIKE ?",
            ("%2160p.mkv",),
        ).fetchone()
    node = cloud115.nodes[row[1]]
    assert row == ("video", node.id, node.id, node.size, node.sha1)


def test_changes_between_syncs_are_detected(
    client: TestClient,
    admin: dict[str, str],
    movies: dict[str, Any],
    cloud115: FakeCloud115,
    config: AppConfig,
) -> None:
    sync(client, admin)
    before = files(config)
    wandering = cloud115.make_path("影视/电影/流浪地球 (2019)")
    interstellar = cloud115.make_path("影视/电影/星际穿越 (2014)")
    # 改名：同一个文件换了名字
    cloud115.move(
        cloud115.make_path("影视/电影/流浪地球 (2019)/流浪地球.2019.2160p.mkv"),
        wandering,
        "The.Wandering.Earth.2019.2160p.mkv",
    )
    # 删除和新增
    cloud115.remove(cloud115.make_path("影视/电影/流浪地球 (2019)/流浪地球.2019.chs.ass"))
    cloud115.add_file(interstellar, "Interstellar.2014.chs.srt")

    body = sync(client, admin)

    assert stats(body) == {
        "added": 1,
        "removed": 1,
        "renamed": 1,
        "restored": 0,
        "failed": 0,
        "paused": 0,
    }
    after = files(config)
    renamed = after["流浪地球 (2019)/The.Wandering.Earth.2019.2160p.mkv"]
    # 改名后还是原来那条记录
    assert renamed[0] == before["流浪地球 (2019)/流浪地球.2019.2160p.mkv"][0]
    assert renamed[1] == "active"
    assert "流浪地球 (2019)/流浪地球.2019.2160p.mkv" not in after
    assert after["流浪地球 (2019)/流浪地球.2019.chs.ass"][1] == "missing"
    assert after["星际穿越 (2014)/Interstellar.2014.chs.srt"][1] == "active"


def test_unchanged_library_needs_no_enrichment(
    client: TestClient, admin: dict[str, str], movies: dict[str, Any], cloud115: FakeCloud115
) -> None:
    sync(client, admin)
    cloud115.calls.clear()

    body = sync(client, admin)

    assert stats(body)["added"] == 0
    # 没有变化时仍扫描一次资料库目录
    assert cloud115.calls.count("scan") == 1


def test_only_directories_with_new_files_are_listed(
    client: TestClient,
    admin: dict[str, str],
    movies: dict[str, Any],
    cloud115: FakeCloud115,
    config: AppConfig,
) -> None:
    sync(client, admin)
    cloud115.calls.clear()
    wandering = cloud115.make_path("影视/电影/流浪地球 (2019)")
    cloud115.add_file(wandering, "流浪地球.2019.1080p.mkv")

    body = sync(client, admin)

    assert stats(body)["added"] == 1
    assert cloud115.calls.count("scan") == 1
    assert files(config)["流浪地球 (2019)/流浪地球.2019.1080p.mkv"][1] == "active"


def test_removed_files_are_marked_missing_without_listing(
    client: TestClient,
    admin: dict[str, str],
    movies: dict[str, Any],
    cloud115: FakeCloud115,
    config: AppConfig,
) -> None:
    sync(client, admin)
    cloud115.calls.clear()
    cloud115.remove(cloud115.make_path("影视/电影/星际穿越 (2014)/Interstellar.2014.1080p.mp4"))

    body = sync(client, admin)

    assert stats(body)["removed"] == 1
    assert cloud115.calls.count("scan") == 1
    assert files(config)["星际穿越 (2014)/Interstellar.2014.1080p.mp4"][1] == "missing"


def test_missing_files_are_restored_by_sha1(
    client: TestClient,
    admin: dict[str, str],
    movies: dict[str, Any],
    cloud115: FakeCloud115,
    config: AppConfig,
    clock: FakeClock,
) -> None:
    sync(client, admin)
    original_id = files(config)["星际穿越 (2014)/Interstellar.2014.1080p.mp4"][0]
    video = cloud115.make_path("影视/电影/星际穿越 (2014)/Interstellar.2014.1080p.mp4")
    sha1 = cloud115.nodes[video].sha1
    cloud115.remove(video)
    assert stats(sync(client, admin))["removed"] == 1
    assert libraries(client)[0]["missing_count"] == 1

    # 几天后以另一个名字放回来（例如重新上传到了别的目录）
    clock.advance(days=3)
    cloud115.add_file(cloud115.make_path("影视/电影/星际穿越"), "星际穿越.mp4", sha1=sha1)
    body = sync(client, admin)

    assert stats(body)["restored"] == 1
    assert stats(body)["added"] == 0
    assert files(config)["星际穿越/星际穿越.mp4"] == (original_id, "active", None)
    library = libraries(client)[0]
    assert library["missing_count"] == 0
    assert library["video_count"] == 2


def test_file_reappearing_at_the_same_path_is_restored(
    client: TestClient,
    admin: dict[str, str],
    movies: dict[str, Any],
    cloud115: FakeCloud115,
    config: AppConfig,
) -> None:
    sync(client, admin)
    folder = cloud115.make_path("影视/电影/星际穿越 (2014)")
    detached = cloud115.make_path("回收/暂存")
    cloud115.move(folder, detached)
    sync(client, admin)
    cloud115.move(folder, cloud115.make_path("影视/电影"))

    body = sync(client, admin)

    assert stats(body)["restored"] == 1
    assert files(config)["星际穿越 (2014)/Interstellar.2014.1080p.mp4"][1:] == ("active", None)


def test_series_library_walks_nested_folders(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115, config: AppConfig
) -> None:
    for episode in range(1, 4):
        cloud115.make_path(f"影视/剧集/繁花 (2023)/Season 1/繁花.S01E0{episode}.mkv")
    cloud115.make_path("影视/剧集/繁花 (2023)/Season 1/空目录/readme.txt")
    shows = create_library(client, admin, "剧集", "series", cloud115.make_path("影视/剧集"))
    cloud115.calls.clear()

    body = sync(client, admin, shows["id"])

    assert body["scope"] == "library"
    assert body["library_name"] == "剧集"
    assert stats(body)["added"] == 3
    assert all(state == "active" for _, state, _ in files(config).values())
    assert cloud115.calls.count("scan") == 1


def test_syncing_one_library_leaves_others_untouched(
    client: TestClient, admin: dict[str, str], movies: dict[str, Any], cloud115: FakeCloud115
) -> None:
    cloud115.make_path("影视/剧集/繁花 (2023)/Season 1/繁花.S01E01.mkv")
    shows = create_library(client, admin, "剧集", "series", cloud115.make_path("影视/剧集"))

    body = sync(client, admin, shows["id"])

    assert stats(body)["added"] == 1
    assert cloud115.calls.count("scan") == 1
    by_name = {library["name"]: library for library in libraries(client)}
    assert by_name["电影"]["video_count"] == 0


def test_concurrent_sync_is_rejected(
    client: TestClient, admin: dict[str, str], movies: dict[str, Any], sync_runner: SyncRunner
) -> None:
    sync_runner.deferred = True
    first = client.post(BASE, json={}, headers=admin)
    assert first.status_code == 202
    current = client.get(f"{BASE}/current", headers=admin).json()["job"]
    assert current["id"] == first.json()["id"]
    assert current["status"] == "running"

    second = client.post(BASE, json={"library_id": movies["id"]}, headers=admin)

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "sync_running"
    sync_runner.run_pending()
    assert job(client, admin, first.json()["id"])["status"] == "succeeded"
    assert client.post(BASE, json={}, headers=admin).status_code == 202


def test_sync_cancellation_stops_the_run_and_finishes_both_task_views(
    client: TestClient,
    admin: dict[str, str],
    movies: dict[str, Any],
    sync_runner: SyncRunner,
) -> None:
    sync_runner.deferred = True
    started = client.post(BASE, json={}, headers=admin)
    assert started.status_code == 202
    body = started.json()
    assert isinstance(body["task_id"], int)

    cancelled = client.post(f"/api/admin/tasks/{body['task_id']}/cancel", headers=admin)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancel_requested"

    sync_runner.run_pending()
    finished = job(client, admin, body["id"])
    assert finished["status"] == "cancelled"
    task = client.get(f"/api/admin/tasks/{body['task_id']}", headers=admin)
    assert task.status_code == 200
    assert task.json()["status"] == "cancelled"
    assert task.json()["error"] == "同步已取消"


def test_missing_library_folder_does_not_mark_files_missing(
    client: TestClient,
    admin: dict[str, str],
    movies: dict[str, Any],
    cloud115: FakeCloud115,
    config: AppConfig,
) -> None:
    sync(client, admin)
    cloud115.remove(cloud115.make_path("影视/电影"))

    body = sync(client, admin)

    assert body["status"] == "succeeded"
    assert stats(body)["failed"] == 1
    assert "「电影」的目录「/影视/电影」目录不存在" in body["stats"]["problems"][0]
    assert all(state == "active" for _, state, _ in files(config).values())


def test_folder_path_is_refreshed_after_parent_rename(
    client: TestClient, admin: dict[str, str], movies: dict[str, Any], cloud115: FakeCloud115
) -> None:
    cloud115.nodes[cloud115.make_path("影视")].name = "媒体"

    sync(client, admin)

    library = libraries(client)[0]
    assert library["folders"][0]["path"] == "/媒体/电影"


def test_failed_scan_is_retried_next_time(
    client: TestClient,
    admin: dict[str, str],
    movies: dict[str, Any],
    cloud115: FakeCloud115,
    config: AppConfig,
) -> None:
    # 来源扫描要么完整要么失败：一个目录读不出来，整个资料库目录这次都不入库
    cloud115.control.call("source", "fail_next", {"method": "scan", "count": 1})

    body = sync(client, admin)

    assert stats(body)["added"] == 0
    assert stats(body)["failed"] == 1
    assert "「电影」的目录「/影视/电影」扫描失败" in body["stats"]["problems"][0]
    assert files(config) == {}

    body = sync(client, admin)

    assert stats(body)["added"] == 3
    assert stats(body)["failed"] == 0
    assert all(state == "active" for _, state, _ in files(config).values())


def test_interrupted_job_is_marked_failed_on_restart(
    client: TestClient,
    admin: dict[str, str],
    movies: dict[str, Any],
    sync_runner: SyncRunner,
    config: AppConfig,
    clock: FakeClock,
    cloud115: FakeCloud115,
) -> None:
    sync_runner.deferred = True
    job_id = client.post(BASE, json={}, headers=admin).json()["id"]

    with TestClient(make_app(config, clock, SyncRunner(clock))) as restarted:
        body = job(restarted, admin, job_id)
        assert body["status"] == "failed"
        assert body["error"] == "服务重启，任务中断"
        assert restarted.post(BASE, json={}, headers=admin).status_code == 202


def test_only_admins_can_sync(
    client: TestClient, admin: dict[str, str], admin_tokens: dict[str, str]
) -> None:
    create_user(client, admin_tokens)
    user = bearer(login(client, "alice", "alice-password"))

    assert client.post(BASE, json={}, headers=user).status_code == 403
    assert client.get(f"{BASE}/jobs", headers=user).status_code == 403
    assert client.get(f"{BASE}/schedule", headers=user).status_code == 403


# ---- 定时同步 ----


def schedule(client: TestClient) -> SyncSchedule:
    value: SyncSchedule = client.app.state.container.sync_schedule  # type: ignore[attr-defined]
    return value


def test_schedule_is_off_by_default(client: TestClient, admin: dict[str, str]) -> None:
    body = client.get(f"{BASE}/schedule", headers=admin).json()

    assert body == {"times": [], "timezone": "Asia/Shanghai", "next_run_at": None}
    assert schedule(client).tick() is None


def test_scheduled_sync_runs_once_per_time_point(
    client: TestClient,
    admin: dict[str, str],
    movies: dict[str, Any],
    clock: FakeClock,
) -> None:
    # 假时钟从 2026-01-01 00:00 UTC 开始，即上海时间 08:00
    response = client.put(
        f"{BASE}/schedule", json={"times": ["20:30", "03:00", "03:00"]}, headers=admin
    )
    assert response.status_code == 200
    body = response.json()
    assert body["times"] == ["03:00", "20:30"]
    assert body["next_run_at"] == "2026-01-01T12:30:00+00:00"
    # 保存时已经过去的 03:00 不补跑
    assert schedule(client).tick() is None

    clock.current = datetime.fromisoformat("2026-01-01T12:30:20+00:00")
    job_ = schedule(client).tick()
    assert job_ is not None
    assert job_.trigger == "scheduled"
    assert schedule(client).tick() is None

    # 服务停了一整天，错过的时间点只补跑一次
    clock.current = datetime.fromisoformat("2026-01-03T00:00:00+00:00")
    assert schedule(client).tick() is not None
    assert schedule(client).tick() is None
    history = client.get(f"{BASE}/jobs", headers=fresh_admin(client)).json()
    assert [j["trigger"] for j in history] == ["scheduled", "scheduled"]


def test_scheduled_sync_skips_when_one_is_running(
    client: TestClient,
    admin: dict[str, str],
    movies: dict[str, Any],
    clock: FakeClock,
    sync_runner: SyncRunner,
) -> None:
    client.put(f"{BASE}/schedule", json={"times": ["09:00"]}, headers=admin)
    sync_runner.deferred = True
    assert client.post(BASE, json={}, headers=admin).status_code == 202

    clock.advance(hours=1, minutes=1)

    assert schedule(client).tick() is None
    assert len(client.get(f"{BASE}/jobs", headers=admin).json()) == 1


def test_invalid_schedule_is_rejected(client: TestClient, admin: dict[str, str]) -> None:
    for times in (["25:00"], ["3:00"], ["03:00:00"]):
        response = client.put(f"{BASE}/schedule", json={"times": times}, headers=admin)
        assert response.status_code == 400, times
        assert response.json()["error"]["code"] == "invalid_schedule"
    assert client.get(f"{BASE}/schedule", headers=admin).json()["times"] == []


def test_sync_without_source_or_library_reports_no_library(
    client: TestClient, admin_tokens: dict[str, str]
) -> None:
    headers = bearer(admin_tokens)
    # 没有任何来源和资料库
    empty = client.post(BASE, json={}, headers=headers)
    assert empty.status_code == 400
    assert empty.json()["error"]["code"] == "no_library"

    # 有来源但没有资料库
    ensure_cloud115_source(client, headers)
    no_library = client.post(BASE, json={}, headers=headers)
    assert no_library.status_code == 400
    assert no_library.json()["error"]["code"] == "no_library"
    assert client.post(BASE, json={"library_id": 42}, headers=headers).status_code == 404


def test_rate_limit_pauses_the_sync_and_is_recorded(
    client: TestClient,
    admin: dict[str, str],
    movies: dict[str, Any],
    cloud115: FakeCloud115,
    sync_runner: SyncRunner,
    clock: FakeClock,
    config: AppConfig,
) -> None:
    until = clock.now() + timedelta(minutes=30)
    cloud115.control.call(
        "source",
        "set_wait",
        {"until": until.isoformat(), "reason": "rate_limited"},
    )
    paused: list[sqlite3.Row] = []
    sync_runner.on_sleep = lambda _: paused.append(running_job(config))

    body = sync(client, admin)

    assert paused[0]["waiting_reason"] == "rate_limited"
    assert paused[0]["waiting_until"] == until.isoformat()
    assert sync_runner.sleeps[0] == 30 * 60
    assert body["status"] == "succeeded"
    assert stats(body)["paused"] == 1
    assert stats(body)["added"] == 3
    assert body["waiting_until"] is None


def test_unknown_wait_reason_is_dropped(
    client: TestClient,
    admin: dict[str, str],
    movies: dict[str, Any],
    cloud115: FakeCloud115,
    sync_runner: SyncRunner,
    clock: FakeClock,
) -> None:
    # 协议只允许 rate_limited / source_busy；未知原因整条通知丢弃，不暂停也不续期
    cloud115.control.call(
        "source",
        "set_wait",
        {"until": (clock.now() + timedelta(minutes=2)).isoformat(), "reason": "unknown_reason"},
    )

    body = sync(client, admin)

    assert sync_runner.sleeps == []
    assert body["status"] == "succeeded"
    assert stats(body)["paused"] == 0
    assert stats(body)["added"] == 3
    jobs = client.get("/api/admin/sync/jobs", headers=admin)
    assert jobs.status_code == 200


def test_fatal_unavailable_during_sync_fails_the_job(
    client: TestClient,
    admin: dict[str, str],
    movies: dict[str, Any],
    cloud115: FakeCloud115,
) -> None:
    cloud115.control.call("source", "set_fatal_scan", {"value": True})

    body = sync(client, admin)

    assert body["status"] == "failed"
    assert "凭据已失效" in body["error"]
    cloud115.control.call("source", "set_fatal_scan", {"value": False})
    assert sync(client, admin)["status"] == "succeeded"
