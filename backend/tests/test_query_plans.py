"""大库下的查询计划：问题筛选和已看筛选不能从「全部 active 文件」出发反查条目。

条目数 × 文件数的二次增长在几千部电影的库里会让「未看」筛选超过 10 秒（scripts/bench-large-db.py）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.auth.users import User
from app.db import Database
from app.library.browse import BrowseQueries
from app.library.browse_rows import PLAYABLE, WATCHED
from app.library.item_batch import _STATUS_SQL
from tests.conftest import FakeClock

NOW = "2026-01-01T00:00:00+00:00"
ADMIN = User(1, "admin", "admin", False, 1, NOW, all_libraries=True)


@pytest.fixture
def database(tmp_path: Path, clock: FakeClock) -> Database:
    database = Database(tmp_path / "plans.db")
    database.migrate(clock)
    with database.session() as connection:
        connection.execute(
            "INSERT INTO library (id, name, kind, sort_order, created_at, updated_at) "
            "VALUES (1, '电影', 'movie', 0, ?, ?)",
            (NOW, NOW),
        )
    return database


def plan(database: Database, sql: str, args: tuple[object, ...] = ()) -> list[str]:
    with database.read_session() as connection:
        return [row[3] for row in connection.execute(f"EXPLAIN QUERY PLAN {sql}", args)]


def assert_driven_by_item(lines: list[str]) -> None:
    scans = [line for line in lines if "media_file_state" in line]
    assert not scans, f"查询从 media_file_state 索引出发：{scans}"


def test_watched_filter_is_driven_by_the_item(database: Database) -> None:
    sql = (
        f"SELECT COUNT(*) FROM media_item m WHERE m.library_id = 1 AND {PLAYABLE} AND NOT {WATCHED}"
    )
    assert_driven_by_item(plan(database, sql, (2,)))


@pytest.mark.parametrize("status", ["multiple_versions", "missing"])
def test_batch_status_filters_are_driven_by_the_item(database: Database, status: str) -> None:
    sql = f"SELECT COUNT(*) FROM media_item m WHERE {_STATUS_SQL[status]}"
    assert_driven_by_item(plan(database, sql))


@pytest.mark.parametrize("status", ["multiple_versions", "missing"])
def test_library_status_filters_are_driven_by_the_item(database: Database, status: str) -> None:
    statements: list[str] = []
    with database.read_session() as connection:
        connection.set_trace_callback(statements.append)
        BrowseQueries(connection, ADMIN).library_items(
            1,
            sort="added",
            order=None,
            genre=None,
            year=None,
            status=status,  # type: ignore[arg-type]
            offset=0,
            limit=50,
        )
        connection.set_trace_callback(None)
    page_query = next(sql for sql in statements if "LIMIT" in sql and "ORDER BY" in sql)
    assert_driven_by_item(plan(database, page_query))
