"""运维类管理接口：播放历史与停止、Webhook、计划任务、回收站、首页推荐行和资料库设置。"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.ops.log_buffer import current as current_log_buffer
from tests.conftest import FakeClock
from tests.test_browse import build_catalog, find, get
from tests.test_playback import issue, movie, progress, save_public_url
from tests.test_sync import fresh_admin

HOOK = {"url": "https://example.com/hook", "events": ["alert", "playback_ended"]}


class _Response:
    status = 204

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None


@pytest.fixture
def delivered(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Any]]:
    """代替真实网络：记录 Webhook 请求；url 含 fail 时模拟网络故障。"""
    requests: list[Any] = []

    def fake_urlopen(request: Any, timeout: float) -> _Response:
        requests.append(request)
        if "fail" in request.full_url:
            raise OSError("连接被拒绝")
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    yield requests


def _wait_for_delivery(
    client: TestClient, headers: dict[str, str], hook_id: int, delivery_id: int
) -> dict[str, Any]:
    for _ in range(100):
        rows = client.get(f"/api/admin/webhooks/{hook_id}/deliveries", headers=headers).json()
        row = next(r for r in rows if r["id"] == delivery_id)
        if row["status"] != "pending":
            result: dict[str, Any] = row
            return result
        time.sleep(0.05)
    raise AssertionError("投递没有在预期时间内结束")


# ---- Webhook ----


def test_webhook_crud_keeps_wire_format(client: TestClient, admin: dict[str, str]) -> None:
    created = client.post("/api/admin/webhooks", headers=admin, json=HOOK)
    assert created.status_code == 201
    hook_id = created.json()["id"]
    assert created.json() == {"id": hook_id, **HOOK, "enabled": True}

    listed = client.get("/api/admin/webhooks", headers=admin).json()
    assert [(h["id"], h["url"], h["events"], h["enabled"]) for h in listed] == [
        (hook_id, HOOK["url"], '["alert","playback_ended"]', 1)
    ]

    updated = client.patch(
        f"/api/admin/webhooks/{hook_id}",
        headers=admin,
        json={"url": "https://example.com/other", "events": ["*"], "enabled": False},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert set(body) == {"id", "url", "events", "enabled", "created_at", "updated_at"}
    assert (body["url"], body["events"], body["enabled"]) == (
        "https://example.com/other",
        '["*"]',
        0,
    )

    assert client.delete(f"/api/admin/webhooks/{hook_id}", headers=admin).status_code == 204
    for response in (
        client.delete(f"/api/admin/webhooks/{hook_id}", headers=admin),
        client.patch(f"/api/admin/webhooks/{hook_id}", headers=admin, json=HOOK),
        client.get(f"/api/admin/webhooks/{hook_id}/deliveries", headers=admin),
    ):
        assert response.status_code == 404
        assert response.json()["error"]["message"] == "Webhook 不存在"


def test_webhook_test_and_retry_record_delivery_results(
    client: TestClient, admin: dict[str, str], delivered: list[Any]
) -> None:
    hook_id = client.post("/api/admin/webhooks", headers=admin, json=HOOK).json()["id"]
    sent = client.post(f"/api/admin/webhooks/{hook_id}/test", headers=admin)
    assert sent.status_code == 202
    delivery_id = sent.json()["delivery_id"]
    signature = sent.json()["signature"]
    assert signature.startswith("sha256=")

    row = _wait_for_delivery(client, admin, hook_id, delivery_id)
    assert (row["event"], row["status"], row["attempts"], row["response"]) == (
        "test",
        "succeeded",
        1,
        "HTTP 204",
    )
    assert delivered[0].get_header("X-iris-signature") == signature

    retry = client.post(
        f"/api/admin/webhooks/{hook_id}/deliveries/{delivery_id}/retry", headers=admin
    )
    assert (retry.status_code, retry.json()) == (202, {"delivery_id": delivery_id})
    row = _wait_for_delivery(client, admin, hook_id, delivery_id)
    assert (row["status"], row["attempts"]) == ("succeeded", 2)

    missing = client.post(f"/api/admin/webhooks/{hook_id}/deliveries/9999/retry", headers=admin)
    assert (missing.status_code, missing.json()["error"]["message"]) == (404, "投递记录不存在")


def test_webhook_test_reports_network_failure_and_disabled_hook(
    client: TestClient, admin: dict[str, str], delivered: list[Any]
) -> None:
    failing = {"url": "https://example.com/fail", "events": []}
    hook_id = client.post("/api/admin/webhooks", headers=admin, json=failing).json()["id"]
    delivery_id = client.post(f"/api/admin/webhooks/{hook_id}/test", headers=admin).json()[
        "delivery_id"
    ]
    row = _wait_for_delivery(client, admin, hook_id, delivery_id)
    assert (row["status"], row["attempts"], row["response"]) == ("failed", 1, "连接被拒绝")

    client.patch(
        f"/api/admin/webhooks/{hook_id}", headers=admin, json={**failing, "enabled": False}
    )
    response = client.post(f"/api/admin/webhooks/{hook_id}/test", headers=admin)
    assert (response.status_code, response.json()["error"]["message"]) == (
        404,
        "Webhook 不存在或已停用",
    )


# ---- 计划任务 ----


def test_scheduled_task_patch_creates_missing_key_and_run_records_task(
    client: TestClient, admin: dict[str, str]
) -> None:
    patched = client.patch(
        "/api/admin/scheduled-tasks/custom", headers=admin, json={"enabled": True, "interval": 120}
    )
    assert patched.json() == {
        "key": "custom",
        "enabled": 1,
        "interval": 120,
        "last_run_at": None,
        "last_task_id": None,
    }
    # 空补丁不改任何字段
    assert client.patch("/api/admin/scheduled-tasks/custom", headers=admin, json={}).json() == (
        patched.json()
    )

    run = client.post("/api/admin/scheduled-tasks/cleanup_sessions/run", headers=admin)
    assert run.status_code == 202
    task = client.get(f"/api/admin/tasks/{run.json()['task_id']}", headers=admin).json()
    assert task["status"] == "succeeded"
    assert task["result"]["manual"] is True and "deleted_sessions" in task["result"]
    row = next(
        t
        for t in client.get("/api/admin/scheduled-tasks", headers=admin).json()
        if t["key"] == "cleanup_sessions"
    )
    assert row["last_task_id"] == run.json()["task_id"] and row["last_run_at"] is not None

    unknown = client.post("/api/admin/scheduled-tasks/not_a_job/run", headers=admin)
    result = client.get(f"/api/admin/tasks/{unknown.json()['task_id']}", headers=admin).json()
    assert result["result"]["status"] == "queued"


def test_cleanup_images_runs_as_a_visible_background_task(
    client: TestClient, admin: dict[str, str], config: AppConfig
) -> None:
    with sqlite3.connect(config.database_path) as connection:
        connection.execute(
            "INSERT INTO image(kind, source, hash, created_at) "
            "VALUES ('poster', '/orphan.jpg', ?, '2026-01-01T00:00:00Z')",
            ("a" * 32,),
        )
    for run in (
        client.post("/api/admin/images/cleanup", headers=admin),
        client.post("/api/admin/scheduled-tasks/cleanup_images/run", headers=admin),
    ):
        assert run.status_code in (200, 202), run.text
    listed = client.get("/api/admin/tasks", headers=admin).json()
    kinds = [t["kind"] for t in listed]
    assert "cleanup_images" in kinds and "scheduled:cleanup_images" in kinds
    direct = next(t for t in listed if t["kind"] == "cleanup_images")
    assert direct["status"] == "succeeded"
    assert direct["result"]["deleted_images"] == 1
    scheduled = next(t for t in listed if t["kind"] == "scheduled:cleanup_images")
    assert scheduled["result"]["deleted_images"] == 0


# ---- 播放历史与停止 ----


@pytest.fixture
def catalog(client: TestClient, admin: dict[str, str], cloud115: Any, tmdb: Any) -> dict[str, int]:
    ids = build_catalog(client, admin, cloud115, tmdb)
    save_public_url(client, "https://movie.example.com")
    return ids


def test_activity_history_stats_and_stop(
    client: TestClient, catalog: dict[str, int], config: AppConfig, clock: FakeClock
) -> None:
    headers = fresh_admin(client)
    earth = movie(client)
    builtin = issue(client, headers, item_id=earth["id"], player="builtin")
    external = issue(client, headers, item_id=earth["id"], player="external")

    page = get(client, headers, "/admin/activity/history")
    assert page["total"] == 2
    newest = page["items"][0]
    assert set(newest) == {
        "id",
        "user_id",
        "playable_id",
        "version_id",
        "client",
        "created_at",
        "device_id",
        "mode",
        "ended_at",
        "watched_seconds",
        "username",
        "title",
        "library_id",
    }
    assert (newest["username"], newest["title"], newest["mode"]) == (
        "admin",
        "流浪地球",
        "external",
    )
    filtered = client.get("/api/admin/activity/history?client=builtin", headers=headers)
    assert filtered.json()["total"] == 1
    assert get(client, headers, "/admin/activity/history", library_id=999)["total"] == 0
    assert len(get(client, headers, "/admin/activity/history", limit=1)["items"]) == 1

    stats = get(client, headers, "/admin/activity/stats")
    assert stats["plays"] == 2
    assert stats["watched_seconds"] == 0
    assert {(c["mode"], c["count"]) for c in stats["by_client"]} == {
        ("builtin", 1),
        ("external", 1),
    }
    assert stats["top_items"] == [{"id": earth["id"], "title": "流浪地球", "count": 2}]

    # 外部播放器无法终止
    refused = client.post(f"/api/admin/activity/sessions/{newest['id']}/stop", headers=headers)
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "session_not_stoppable")
    assert client.post("/api/admin/activity/sessions/9999/stop", headers=headers).status_code == 404

    builtin_event = page["items"][1]
    stopped = client.post(
        f"/api/admin/activity/sessions/{builtin_event['id']}/stop", headers=headers
    )
    assert (stopped.status_code, stopped.json()) == (
        200,
        {"event_id": builtin_event["id"], "stopped": True},
    )
    # 播放器下一次上报进度时取走停止信号（消息是非 ASCII 文本，响应头里做百分号编码），且只取到一次
    playable_id = builtin_event["playable_id"]
    reported = progress(client, headers, playable_id, 10, 7200)
    assert (reported.status_code, reported.headers["X-Iris-Stop"]) == (204, "1")
    assert unquote(reported.headers["X-Iris-Stop-Message"]) == "管理员已请求停止播放"
    assert "X-Iris-Stop" not in progress(client, headers, playable_id, 20, 7200).headers
    assert external["playable_id"] == builtin["playable_id"]


def test_progress_closes_the_open_play_event(client: TestClient, catalog: dict[str, int]) -> None:
    headers = fresh_admin(client)
    ticket = issue(client, headers, item_id=movie(client)["id"], player="builtin")
    progress(client, headers, ticket["playable_id"], 600, 7200)
    event = get(client, headers, "/admin/activity/history")["items"][0]
    assert (event["watched_seconds"], event["ended_at"]) == (600, None)

    progress(client, headers, ticket["playable_id"], 6600, 7200)
    event = get(client, headers, "/admin/activity/history")["items"][0]
    assert (event["watched_seconds"], event["ended_at"] is None) == (6600, False)

    # 事件结束后再上报不会改动它
    progress(client, headers, ticket["playable_id"], 100, 7200)
    assert get(client, headers, "/admin/activity/history")["items"][0]["watched_seconds"] == 6600


# ---- 资料库统计、回收站、首页推荐行与设置 ----


def test_library_stats_recycle_bin_and_empty(
    client: TestClient, catalog: dict[str, int], config: AppConfig, clock: FakeClock
) -> None:
    headers = fresh_admin(client)
    stats = get(client, headers, f"/admin/libraries/{catalog['movies']}/stats")
    assert (stats["items"], stats["movies"], stats["series"]) == (3, 3, 0)
    assert (stats["unmatched"], stats["missing"]) == (0, 0)
    assert stats["bytes"] > 0
    assert client.get("/api/admin/libraries/9999/stats", headers=headers).status_code == 404

    assert get(client, headers, "/admin/recycle-bin") == []
    star = find(client, "星际穿越")
    with sqlite3.connect(config.database_path) as connection:
        connection.execute(
            "UPDATE media_file SET state='missing', missing_at=? WHERE id IN ("
            "SELECT v.media_file_id FROM version v JOIN playable p ON p.id=v.playable_id "
            "WHERE p.media_item_id=?)",
            ("2026-01-01T00:00:00+00:00", star["id"]),
        )
    listed = get(client, headers, "/admin/recycle-bin")
    assert len(listed) == 1
    assert set(listed[0]) == {
        "media_file_id",
        "rel_path",
        "size",
        "missing_at",
        "library_id",
        "library_name",
        "media_item_id",
        "title",
    }
    assert (listed[0]["library_id"], listed[0]["title"]) == (catalog["movies"], "星际穿越")
    assert get(client, headers, "/admin/recycle-bin", library_id=catalog["series"]) == []

    # 只清空指定资料库：剧集资料库里没有失效文件
    other = client.post(
        f"/api/admin/recycle-bin/empty?library_id={catalog['series']}", headers=headers
    )
    assert (other.status_code, other.json()["deleted"]) == (202, 0)
    assert len(get(client, headers, "/admin/recycle-bin")) == 1

    emptied = client.post("/api/admin/recycle-bin/empty", headers=headers)
    assert (emptied.status_code, emptied.json()["deleted"]) == (202, 1)
    assert get(client, headers, "/admin/recycle-bin") == []
    # 不存在的资料库不报错，只是什么也不删
    ghost = client.post("/api/admin/recycle-bin/empty?library_id=9999", headers=headers)
    assert (ghost.status_code, ghost.json()["deleted"]) == (202, 0)


def test_library_hubs_replace_and_public_visibility(
    client: TestClient, catalog: dict[str, int]
) -> None:
    headers = fresh_admin(client)
    path = f"/api/admin/libraries/{catalog['movies']}/hubs"
    defaults = client.get(path, headers=headers).json()
    assert [(h["kind"], h["show_home"], h["show_library"]) for h in defaults][:4] == [
        ("continue", False, True),
        ("latest", True, True),
        ("released", False, True),
        ("rating", False, True),
    ]
    assert defaults[0]["id"] == 0 and defaults[1]["title"] == "最近添加的电影"

    hubs = [
        {"kind": "latest", "show_home": True, "show_library": False},
        {"kind": "genre", "arg": "科幻", "show_home": False, "show_library": True},
    ]
    saved = client.put(path, headers=headers, json=hubs)
    assert saved.status_code == 200
    assert [
        (h["kind"], h["arg"], h["position"], h["show_home"], h["show_library"])
        for h in saved.json()
    ] == [
        ("latest", None, 0, True, False),
        ("genre", "科幻", 1, False, True),
    ]
    assert saved.json()[0]["id"] > 0
    assert saved.json() == client.get(path, headers=headers).json()

    public = client.get(f"/api/libraries/{catalog['movies']}/hubs", headers=headers)
    assert [(h["kind"], h["arg"]) for h in public.json()] == [("genre", "科幻")]
    assert public.json()[0]["title"] == "科幻 · 电影"

    invalid = client.put(
        path,
        headers=headers,
        json=[{"kind": "genre", "arg": "没有这个类型"}],
    )
    assert (invalid.status_code, invalid.json()["error"]["code"]) == (
        400,
        "invalid_library_hub",
    )
    # 空列表恢复到默认布局
    assert client.put(path, headers=headers, json=[]).json()[1]["kind"] == "latest"
    ghost = client.put("/api/admin/libraries/9999/hubs", headers=headers, json=[])
    assert (ghost.status_code, ghost.json()["error"]["message"]) == (404, "资料库不存在")


def test_library_settings_fall_back_to_server_defaults(
    client: TestClient, catalog: dict[str, int]
) -> None:
    headers = fresh_admin(client)
    base = f"/api/admin/libraries/{catalog['movies']}/settings"
    before = client.get(base, headers=headers).json()
    assert before["metadata_provider"] == "builtin.tmdb"

    saved = client.put(f"{base}/extras_min_mb", headers=headers, json={"value": "77"})
    assert saved.json() == {"extras_min_mb": "77"}
    after = client.get(base, headers=headers).json()
    assert after["extras_min_mb"] == "77"
    assert {k: v for k, v in after.items() if k != "extras_min_mb"} == {
        k: v for k, v in before.items() if k != "extras_min_mb"
    }
    client.put(f"{base}/extras_min_mb", headers=headers, json={"value": "88"})
    assert client.get(base, headers=headers).json()["extras_min_mb"] == "88"

    ghost = client.put("/api/admin/libraries/9999/settings/x", headers=headers, json={"value": "1"})
    assert ghost.status_code == 404
    bad = client.put(f"{base}/metadata_provider", headers=headers, json={"value": "nope"})
    assert bad.status_code >= 400


def test_admin_logs_rows_keep_their_shape(client: TestClient) -> None:
    headers = fresh_admin(client)
    current_log_buffer()  # 缓冲区按需挂到 app 日志上，先挂好再写日志
    logging.getLogger("app.test_logs").warning("日志接口测试 token=abc")
    rows = get(client, headers, "/admin/logs", level="warning")
    row = next(item for item in rows if item["module"] == "app.test_logs")
    assert set(row) == {"id", "at", "level", "module", "message"}
    assert (row["level"], row["message"]) == ("warning", "日志接口测试 token=***")
    assert isinstance(row["id"], float)
    # after 以记录时间戳为游标：只返回更新的记录
    assert all(
        item["id"] > row["id"] for item in get(client, headers, "/admin/logs", after=row["id"])
    )
