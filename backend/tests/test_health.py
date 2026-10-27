from __future__ import annotations

import sqlite3
from datetime import timedelta
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ops.tasks import TaskRepository
from app.sync.jobs import SyncJobRepository, SyncStats
from tests.conftest import FakeClock, ensure_cloud115_source
from tests.fake_cloud115 import FakeCloud115
from tests.test_sync import fresh_admin

NOW = "2024-01-01T00:00:00+00:00"


def checks(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/admin/health", headers=fresh_admin(client))
    assert response.status_code == 200, response.text
    return {check["key"]: check for check in response.json()["checks"]}


def database(client: TestClient) -> Any:
    return cast(FastAPI, client.app).state.container.database


def test_admin_health_reports_actionable_safe_statuses(
    client: TestClient, cloud115: FakeCloud115, tmdb: object
) -> None:
    source_id = ensure_cloud115_source(client, fresh_admin(client))
    cloud115.set_status("failed", message="账号未配置", page="account")
    assert hasattr(tmdb, "reason")
    tmdb.reason = "还没有配置 TMDB Key"

    response = client.get("/api/admin/health", headers=fresh_admin(client))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema_version"] >= 1
    assert body["checked_at"]
    assert {check["key"] for check in body["checks"]} == {
        f"source:{source_id}",
        "metadata",
        "sync",
        "last_sync_success",
        "failed_task",
        "pending",
        "missing_files",
        "image_cache",
        "database",
        "public_url",
    }
    source = next(check for check in body["checks"] if check["key"] == f"source:{source_id}")
    assert source["title"] == "115 网盘"
    assert source["state"] == "error"
    assert source["action_path"] == "/settings/manage/plugins/source.115?page=account"
    assert (
        next(check for check in body["checks"] if check["key"] == "metadata")["state"] == "warning"
    )
    assert next(check for check in body["checks"] if check["key"] == "database")["state"] == "ok"
    serialized = response.text.lower()
    assert "cookie" not in serialized
    assert "token" not in serialized


def test_failed_sync_is_an_error_and_success_time_is_tracked_separately(
    client: TestClient, clock: FakeClock
) -> None:
    with database(client).session() as connection:
        jobs = SyncJobRepository(connection, clock)
        ok = jobs.create("manual", None, None)
        jobs.finish(ok.id, SyncStats())
        clock.advance(minutes=5)
        bad = jobs.create("manual", None, None)
        jobs.finish(bad.id, SyncStats(), error="115 目录读取失败")

    found = checks(client)

    assert found["sync"]["state"] == "error"
    assert found["sync"]["message"] == "115 目录读取失败"
    assert found["sync"]["action_path"] == "/settings/status/sync"
    assert found["last_sync_success"]["state"] == "ok"
    assert found["last_sync_success"]["message"].startswith("完成于 ")
    # 失败的同步同时是失败的后台任务
    assert found["failed_task"]["state"] == "warning"
    assert "sync" in found["failed_task"]["message"]
    assert found["failed_task"]["action_path"] == "/settings/status/tasks"


def test_failed_task_is_cleared_by_a_later_success_of_the_same_kind(
    client: TestClient, clock: FakeClock
) -> None:
    assert checks(client)["last_sync_success"]["state"] == "warning"
    assert checks(client)["failed_task"]["state"] == "ok"
    with database(client).session() as connection:
        tasks = TaskRepository(connection, clock)
        tasks.finish(tasks.create("rematch").id, error="x" * 500)
    failed = checks(client)["failed_task"]
    assert failed["state"] == "warning"
    assert len(failed["message"]) < 200

    with database(client).session() as connection:
        tasks = TaskRepository(connection, clock)
        tasks.finish(tasks.create("rematch").id)

    assert checks(client)["failed_task"]["state"] == "ok"


def test_missing_video_files_are_counted(client: TestClient) -> None:
    assert checks(client)["missing_files"]["state"] == "ok"
    raw = sqlite3.connect(database(client).path)
    try:
        raw.execute(
            "INSERT INTO library_folder (id, library_id, scope_key, path, created_at, "
            "resource_root_id) VALUES (1, 1, '/source/1/影视/', '/影视', ?, 1)",
            (NOW,),
        )
        for i, (kind, state) in enumerate(
            [
                ("video", "missing"),
                ("video", "missing"),
                ("video", "active"),
                ("subtitle", "missing"),
            ]
        ):
            raw.execute(
                "INSERT INTO media_file (folder_id, rel_path, kind, state, created_at, updated_at) "
                "VALUES (1, ?, ?, ?, ?, ?)",
                (f"f{i}", kind, state, NOW, NOW),
            )
        raw.commit()
    finally:
        raw.close()

    check = checks(client)["missing_files"]

    assert check["state"] == "warning"
    assert check["message"].startswith("2 个")
    assert "在资源来源上已找不到" in check["message"]
    assert check["action_path"] == "/settings/status/sync"


def test_image_cache_reports_missing_local_files(client: TestClient, clock: FakeClock) -> None:
    assert checks(client)["image_cache"]["message"] == "已缓存 0 张图片"
    with database(client).session() as connection:
        connection.execute(
            "INSERT INTO image (hash, kind, source, created_at) VALUES (?, 'poster', 's', ?)",
            ("a" * 32, NOW),
        )

    check = checks(client)["image_cache"]

    assert check["state"] == "warning"
    assert "1 张缺少本地文件" in check["message"]


def test_limited_source_is_a_warning_with_retry_information(
    client: TestClient, cloud115: FakeCloud115, clock: FakeClock
) -> None:
    source_id = ensure_cloud115_source(client, fresh_admin(client))
    resume_at = (clock.now() + timedelta(minutes=30)).isoformat()
    cloud115.set_status(
        "limited",
        message="115 暂时受限，请稍后再试",
        resume_at=resume_at,
        page="account",
    )

    source = checks(client)[f"source:{source_id}"]

    assert source["state"] == "warning"
    assert "受限" in source["message"]
    assert resume_at in source["message"]
    assert source["action_path"] == "/settings/manage/plugins/source.115?page=account"
