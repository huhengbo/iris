"""待处理删除的规划和数据库清理规则。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.db import Database
from app.errors import AppError
from app.library.deletion import escape_like
from app.library.pending import PendingDeletion
from tests.conftest import FakeClock

NOW = "2024-01-01T00:00:00+00:00"


@pytest.fixture
def connection(tmp_path: Path, clock: FakeClock) -> Iterator[sqlite3.Connection]:
    path = tmp_path / "pending.sqlite3"
    Database(path).migrate(clock)
    # 直接插入测试行，不需要完整的资源来源和条目，所以不开外键检查
    with sqlite3.connect(path) as raw:
        raw.row_factory = sqlite3.Row
        raw.execute(
            "INSERT INTO resource_root (id, source_id, locator, display_path, created_at) "
            "VALUES (1, 1, '影视', '/影视', ?)",
            (NOW,),
        )
        raw.execute(
            "INSERT INTO library_folder (id, library_id, scope_key, path, created_at, "
            "resource_root_id) VALUES (1, 1, '/source/1/影视/', '/影视', ?, 1)",
            (NOW,),
        )
        yield raw


def add_work(
    connection: sqlite3.Connection, work_id: int, path: str, state: str = "unmatched"
) -> None:
    connection.execute(
        "INSERT INTO work (id, folder_id, path, state, created_at, updated_at) "
        "VALUES (?, 1, ?, ?, ?, ?)",
        (work_id, path, state, NOW, NOW),
    )


def add_file(
    connection: sqlite3.Connection,
    media_file_id: int,
    rel_path: str,
    work_id: int | None = None,
    kind: str = "video",
    state: str = "active",
) -> None:
    connection.execute(
        "INSERT INTO media_file (id, folder_id, rel_path, kind, state, identity, work_id, "
        "created_at, updated_at) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?)",
        (media_file_id, rel_path, kind, state, f"source-{media_file_id}", work_id, NOW, NOW),
    )


def attach_version(connection: sqlite3.Connection, media_file_id: int) -> None:
    connection.execute(
        "INSERT INTO version (playable_id, media_file_id, label, created_at) VALUES (1, ?, 'v', ?)",
        (media_file_id, NOW),
    )


def remaining(connection: sqlite3.Connection, table: str) -> set[int]:
    return {row[0] for row in connection.execute(f"SELECT id FROM {table}")}


def test_escape_like_makes_wildcards_literal() -> None:
    assert escape_like("a_b%c\\d") == "a\\_b\\%c\\\\d"


def test_work_with_own_folder_is_deleted_as_a_whole_folder(connection: sqlite3.Connection) -> None:
    add_work(connection, 1, "奇怪的片名")
    add_file(connection, 1, "奇怪的片名/a.mkv", work_id=1)
    add_file(connection, 2, "奇怪的片名/a.srt", kind="subtitle")
    add_file(connection, 3, "别的/b.mkv")
    pending = PendingDeletion(connection)

    plan = pending.plan([1], [])

    assert plan.dirs == [(1, "奇怪的片名")]
    assert plan.identities == []
    assert pending.folder_roots({1}) == {1: "影视"}


def test_folder_like_prefix_does_not_match_lookalike_names(connection: sqlite3.Connection) -> None:
    add_work(connection, 1, "a_b")
    add_file(connection, 1, "a_b/x.mkv", work_id=1)
    add_file(connection, 2, "aXb/y.mkv")
    pending = PendingDeletion(connection)

    pending.cleanup(pending.plan([1], []), [1], [])

    assert remaining(connection, "media_file") == {2}
    assert remaining(connection, "work") == set()


def test_loose_work_only_deletes_its_own_videos(connection: sqlite3.Connection) -> None:
    add_work(connection, 1, "散放.mkv")
    add_file(connection, 1, "散放.mkv", work_id=1)
    add_file(connection, 2, "旧.mkv", work_id=1, state="missing")

    plan = PendingDeletion(connection).plan([1], [])

    assert plan.dirs == []
    assert plan.identities == ["source-1"]


def test_matched_work_is_rejected(connection: sqlite3.Connection) -> None:
    add_work(connection, 1, "已匹配", state="matched")

    with pytest.raises(AppError) as error:
        PendingDeletion(connection).plan([1], [])

    assert (error.value.status_code, error.value.code) == (409, "work_matched")


def test_folder_shared_with_other_work_is_rejected(connection: sqlite3.Connection) -> None:
    add_work(connection, 1, "合集")
    add_work(connection, 2, "合集/下级")
    add_file(connection, 1, "合集/a.mkv", work_id=1)
    add_file(connection, 2, "合集/下级/b.mkv", work_id=2)

    with pytest.raises(AppError) as error:
        PendingDeletion(connection).plan([1], [])

    assert error.value.code == "media_folder_shared"


def test_loose_work_with_attached_version_is_rejected(connection: sqlite3.Connection) -> None:
    add_work(connection, 1, "散放.mkv")
    add_file(connection, 1, "散放.mkv", work_id=1)
    attach_version(connection, 1)

    with pytest.raises(AppError) as error:
        PendingDeletion(connection).plan([1], [])

    assert error.value.code == "file_in_use"


def test_unknown_ids_are_not_found(connection: sqlite3.Connection) -> None:
    pending = PendingDeletion(connection)

    with pytest.raises(AppError) as work_error:
        pending.plan([404], [])
    with pytest.raises(AppError) as file_error:
        pending.plan([], [404])

    assert (work_error.value.status_code, file_error.value.status_code) == (404, 404)


def test_only_unplaced_videos_can_be_deleted_directly(connection: sqlite3.Connection) -> None:
    add_file(connection, 1, "字幕.srt", kind="subtitle")
    add_file(connection, 2, "已归入.mkv")
    attach_version(connection, 2)
    add_file(connection, 3, "待处理.mkv")
    add_file(connection, 4, "已消失.mkv", state="missing")
    pending = PendingDeletion(connection)

    with pytest.raises(AppError) as subtitle_error:
        pending.plan([], [1])
    with pytest.raises(AppError) as in_use_error:
        pending.plan([], [2])
    plan = pending.plan([], [3, 4])

    assert subtitle_error.value.code == "not_pending_file"
    assert in_use_error.value.code == "file_in_use"
    assert plan.identities == ["source-3"]


def test_cleanup_keeps_records_attached_meanwhile(connection: sqlite3.Connection) -> None:
    add_work(connection, 1, "奇怪的片名")
    add_file(connection, 1, "奇怪的片名/a.mkv", work_id=1)
    add_file(connection, 2, "奇怪的片名/b.mkv", work_id=1)
    add_file(connection, 3, "单独.mkv")
    add_file(connection, 4, "单独2.mkv")
    pending = PendingDeletion(connection)
    plan = pending.plan([1], [3, 4])

    attach_version(connection, 2)
    attach_version(connection, 4)
    pending.cleanup(plan, [1], [3, 4])

    assert remaining(connection, "media_file") == {2, 4}
    assert remaining(connection, "work") == set()
