from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import (
    ensure_cloud115_source,
    save_tmdb_key,
)
from tests.fake_cloud115 import FakeCloud115
from tests.test_sync import create_library, fresh_admin, sync


def test_admin_setup_reports_ordered_first_time_steps(client: TestClient) -> None:
    response = client.get("/api/admin/setup", headers=fresh_admin(client))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["complete"] is False
    assert body["current_key"] == "source"
    assert [step["key"] for step in body["steps"]] == [
        "source",
        "metadata",
        "public_url",
        "library",
        "sync",
    ]
    assert all(step["state"] == "pending" for step in body["steps"])
    assert body["steps"][0]["action_path"] == "/settings/manage/plugins"


def steps_of(client: TestClient) -> dict[str, dict[str, Any]]:
    response = client.get("/api/admin/setup", headers=fresh_admin(client))
    assert response.status_code == 200, response.text
    return {step["key"]: step for step in response.json()["steps"]}


def test_connected_cloud115_completes_only_its_step(
    client: TestClient, admin: dict[str, str]
) -> None:
    ensure_cloud115_source(client, admin)

    steps = steps_of(client)

    assert steps["source"]["state"] == "complete"
    assert steps["source"]["action_path"] is None
    assert steps["metadata"]["state"] == "pending"
    body = client.get("/api/admin/setup", headers=admin).json()
    assert body["current_key"] == "metadata"


def test_disabled_tmdb_plugin_is_failed(client: TestClient, admin: dict[str, str]) -> None:
    save_tmdb_key(client, admin)
    assert steps_of(client)["metadata"]["state"] == "complete"

    client.put("/api/admin/plugins/builtin.tmdb/enabled", json={"enabled": False}, headers=admin)

    metadata = steps_of(client)["metadata"]
    assert metadata["state"] == "pending"
    assert metadata["action_path"] == "/settings/manage/plugins/builtin.tmdb"


def test_failed_sync_shows_error_then_success_completes_everything(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115
) -> None:
    ensure_cloud115_source(client, admin)
    cloud115.make_path("影视/电影/流浪地球 (2019)/流浪地球.2019.2160p.mkv")
    create_library(client, admin, "电影", "movie", cloud115.make_path("影视/电影"))
    assert steps_of(client)["library"]["state"] == "complete"
    assert steps_of(client)["sync"]["state"] == "pending"

    cloud115.error = "unavailable"
    blocked = client.post("/api/admin/sync", json={}, headers=admin)
    assert blocked.status_code == 503
    assert blocked.json()["error"]["code"] == "resource_unavailable"

    # 来源恢复后再同步成功
    cloud115.error = None
    assert sync(client, admin)["status"] == "succeeded"
    steps = steps_of(client)
    assert steps["sync"]["state"] == "complete"
    assert steps["source"]["state"] == "complete"


def test_failed_source_is_reported_in_setup(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115
) -> None:
    ensure_cloud115_source(client, admin)
    cloud115.set_status("failed", message="115 登录已失效", page="account")

    source = steps_of(client)["source"]

    assert source["state"] == "failed"
    assert "失效" in source["message"]
    assert source["action_path"] == "/settings/manage/plugins/source.115?page=account"


def test_limit_control_is_reported_in_setup(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115
) -> None:
    ensure_cloud115_source(client, admin)
    cloud115.set_status("limited", message="115 暂时受限，请稍后再试", page="account")

    source = steps_of(client)["source"]

    assert source["state"] == "failed"
    assert "受限" in source["message"]
    assert source["action_path"] == "/settings/manage/plugins/source.115?page=account"
