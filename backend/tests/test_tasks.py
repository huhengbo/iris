"""后台任务：取消、重启收束、重试的边界条件和幂等。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import Database
from app.ops.tasks import BackgroundTask, TaskRepository
from tests.conftest import FakeClock, bearer, create_user, login
from tests.test_sync import fresh_admin


@pytest.fixture
def database(tmp_path: Path, clock: FakeClock) -> Database:
    database = Database(tmp_path / "tasks.db")
    database.migrate(clock)
    return database


def make_task(client: TestClient, clock: FakeClock, kind: str, **options: Any) -> BackgroundTask:
    database = cast(FastAPI, client.app).state.container.database
    with database.session() as connection:
        return TaskRepository(connection, clock).create(kind, **options)


def finish(client: TestClient, clock: FakeClock, task_id: int, **options: Any) -> None:
    database = cast(FastAPI, client.app).state.container.database
    with database.session() as connection:
        TaskRepository(connection, clock).finish(task_id, **options)


def test_cancel_request_turns_a_finishing_task_into_cancelled(
    database: Database, clock: FakeClock
) -> None:
    with database.session() as connection:
        tasks = TaskRepository(connection, clock)
        task = tasks.create("demo", total=3)
        requested = tasks.request_cancel(task.id)
        assert (requested.status, requested.cancel_requested) == ("cancel_requested", True)
        assert tasks.cancel_requested(task.id)

        # 任务在检查点收到取消后结束，即使带着错误也记为已取消
        tasks.finish(task.id, error="stopped", result={"left": 2})
        stored = tasks.require(task.id)
    assert (stored.status, stored.result) == ("cancelled", {"left": 2})


def test_cancel_does_not_reopen_a_finished_task(database: Database, clock: FakeClock) -> None:
    with database.session() as connection:
        tasks = TaskRepository(connection, clock)
        task = tasks.create("demo")
        tasks.finish(task.id)
        after = tasks.request_cancel(task.id)
        assert (after.status, after.cancel_requested) == ("succeeded", False)

        # 已结束的任务不能再被改写
        tasks.finish(task.id, error="late")
        assert tasks.require(task.id).status == "succeeded"


def test_restart_closes_unfinished_tasks_and_keeps_their_input(
    database: Database, clock: FakeClock
) -> None:
    with database.session() as connection:
        tasks = TaskRepository(connection, clock)
        running = tasks.create("batch_ignore_file", result={"input": {"media_file_ids": [1]}})
        cancelling = tasks.request_cancel(tasks.create("demo").id)
        done = tasks.create("demo")
        tasks.finish(done.id)

        tasks.fail_interrupted()

        interrupted = tasks.require(running.id)
        assert (interrupted.status, interrupted.error) == ("failed", "服务重启，任务中断")
        assert interrupted.result == {"input": {"media_file_ids": [1]}}
        assert tasks.require(cancelling.id).status == "cancelled"
        assert tasks.require(done.id).status == "succeeded"


def test_same_idempotency_key_returns_the_same_task(database: Database, clock: FakeClock) -> None:
    with database.session() as connection:
        tasks = TaskRepository(connection, clock)
        first = tasks.create("demo", idempotency_key="once")
        second = tasks.create("demo", idempotency_key="once")
        assert second.id == first.id
        assert len(tasks.list()) == 1


def test_task_api_cancels_and_rejects_unknown_ids(
    client: TestClient, admin: dict[str, str], clock: FakeClock
) -> None:
    task = make_task(client, clock, "demo")
    response = client.post(f"/api/admin/tasks/{task.id}/cancel", headers=fresh_admin(client))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancel_requested"

    missing = client.post("/api/admin/tasks/9999/cancel", headers=fresh_admin(client))
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"


def test_task_api_is_admin_only(
    client: TestClient, admin: dict[str, str], admin_tokens: dict[str, str], clock: FakeClock
) -> None:
    task = make_task(client, clock, "demo")
    create_user(client, admin_tokens, "alice", "alice-password")
    user = bearer(login(client, "alice", "alice-password"))
    for method, path in (
        ("GET", "/api/admin/tasks"),
        ("GET", f"/api/admin/tasks/{task.id}"),
        ("POST", f"/api/admin/tasks/{task.id}/cancel"),
        ("POST", f"/api/admin/tasks/{task.id}/retry"),
    ):
        assert client.request(method, path, headers=user).status_code == 403, path


def test_retry_only_accepts_failed_or_cancelled_tasks_with_saved_input(
    client: TestClient, admin: dict[str, str], clock: FakeClock
) -> None:
    def retry(task_id: int) -> Any:
        return client.post(f"/api/admin/tasks/{task_id}/retry", headers=fresh_admin(client))

    running = make_task(
        client, clock, "batch_ignore_file", result={"input": {"media_file_ids": [1]}}
    )
    assert retry(running.id).json()["error"]["code"] == "task_not_retryable"

    succeeded = make_task(
        client, clock, "batch_ignore_file", result={"input": {"media_file_ids": [1]}}
    )
    finish(client, clock, succeeded.id)
    assert retry(succeeded.id).json()["error"]["code"] == "task_not_retryable"

    no_input = make_task(client, clock, "batch_ignore_file")
    finish(client, clock, no_input.id, error="boom")
    assert retry(no_input.id).json()["error"]["code"] == "task_not_retryable"

    unsupported = make_task(client, clock, "sync", result={"input": {"x": 1}})
    finish(client, clock, unsupported.id, error="boom")
    response = retry(unsupported.id)
    assert (response.status_code, response.json()["error"]["code"]) == (409, "task_not_retryable")


def test_retry_runs_a_batch_again_as_a_new_task(
    client: TestClient, admin: dict[str, str], clock: FakeClock
) -> None:
    failed = make_task(
        client, clock, "batch_ignore_file", result={"input": {"media_file_ids": [404]}}
    )
    finish(client, clock, failed.id, error="1 项失败")

    response = client.post(f"/api/admin/tasks/{failed.id}/retry", headers=fresh_admin(client))
    assert response.status_code == 200, response.text
    retried = response.json()
    assert retried["id"] != failed.id
    assert retried["kind"] == "batch_ignore_file"
    assert retried["result"]["input"] == {"media_file_ids": [404]}
    # 文件不存在，所以仍然失败，但留下了逐项结果，原任务保持不变
    assert retried["status"] == "failed"
    original = client.get(f"/api/admin/tasks/{failed.id}", headers=fresh_admin(client)).json()
    assert (original["status"], original["error"]) == ("failed", "1 项失败")
