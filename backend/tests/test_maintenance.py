from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from app.container import AppContainer
from app.ops.maintenance import run_due
from app.ops.tasks import TaskRepository
from tests.conftest import FakeClock


def container_of(client: TestClient) -> AppContainer:
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    return container


def run(container: AppContainer) -> None:
    run_due(
        container.database,
        container.backups,
        container.images,
        container.metadata,
        container.subtitle_auto,
        container.clock,
    )


def test_only_enabled_and_due_tasks_run(client: TestClient, clock: FakeClock) -> None:
    container = container_of(client)
    with container.database.session() as connection:
        connection.execute("UPDATE scheduled_task SET enabled = 1 WHERE key = 'cleanup_sessions'")
        # 自动下载字幕默认启用，但会在独立线程里跑；这里只看维护循环本身的执行记录
        connection.execute("UPDATE scheduled_task SET enabled = 0 WHERE key = 'download_subtitles'")
        connection.execute(
            "INSERT INTO auth_session (user_id, token_hash, token_version, created_at, expires_at) "
            "VALUES ((SELECT id FROM user LIMIT 1), 'h', 1, '2020-01-01T00:00:00+00:00', "
            "'2020-01-02T00:00:00+00:00')"
        )

    run(container)

    with container.database.read_session() as connection:
        assert connection.execute("SELECT COUNT(*) FROM auth_session").fetchone()[0] == 0
        row = connection.execute(
            "SELECT last_run_at, last_task_id FROM scheduled_task WHERE key = 'cleanup_sessions'"
        ).fetchone()
        disabled = connection.execute(
            "SELECT last_run_at FROM scheduled_task WHERE key = 'cleanup_play_events'"
        ).fetchone()
        task = TaskRepository(connection, clock).require(row["last_task_id"])
    assert disabled["last_run_at"] is None
    assert (task.kind, task.status, task.result["deleted_sessions"]) == (
        "scheduled:cleanup_sessions",
        "succeeded",
        1,
    )

    # 间隔未到时不会再次执行
    run(container)
    with container.database.read_session() as connection:
        assert connection.execute("SELECT COUNT(*) FROM background_task").fetchone()[0] == 1

    clock.advance(seconds=timedelta(days=1, minutes=1).total_seconds())
    run(container)
    with container.database.read_session() as connection:
        assert connection.execute("SELECT COUNT(*) FROM background_task").fetchone()[0] == 2
