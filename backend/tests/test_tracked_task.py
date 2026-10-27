from __future__ import annotations

from pathlib import Path

import pytest

from app.db import Database
from app.ops.tasks import TaskRepository, tracked_task
from tests.conftest import FakeClock


@pytest.fixture
def database(tmp_path: Path, clock: FakeClock) -> Database:
    database = Database(tmp_path / "tasks.db")
    database.migrate(clock)
    return database


def test_tracked_task_records_progress_and_result(database: Database, clock: FakeClock) -> None:
    with tracked_task(database, clock, "demo", total=2) as task:
        task.progress("working", 1)
        task.result["count"] = 2
    with database.session() as connection:
        stored = TaskRepository(connection, clock).require(task.id)
    assert (stored.status, stored.progress_done, stored.result) == (
        "succeeded",
        1,
        {"count": 2},
    )


def test_tracked_task_records_the_error_and_reraises(database: Database, clock: FakeClock) -> None:
    with pytest.raises(RuntimeError, match="boom"), tracked_task(database, clock, "demo") as task:
        task.result["partial"] = 1
        raise RuntimeError("boom")
    with database.session() as connection:
        stored = TaskRepository(connection, clock).require(task.id)
    assert (stored.status, stored.error, stored.result) == ("failed", "boom", {"partial": 1})
