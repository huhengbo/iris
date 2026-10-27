from __future__ import annotations

from pathlib import Path

import pytest

from app.clock import to_iso
from app.db import Database
from app.errors import AppError, NotFound
from app.library.settings import LibraryHubSpec, LibrarySettings
from app.ops.alerts import AlertRepository
from app.ops.cleanup import Cleanup
from app.ops.schedule import ScheduledTaskRepository
from app.ops.webhooks import WebhookRepository, sign
from app.settings import put_setting
from tests.conftest import FakeClock


@pytest.fixture
def database(tmp_path: Path, clock: FakeClock) -> Database:
    database = Database(tmp_path / "ops.db")
    database.migrate(clock)
    return database


def test_webhook_repository_tolerates_malformed_events_and_generates_secret(
    database: Database, clock: FakeClock
) -> None:
    with database.session() as connection:
        hooks = WebhookRepository(connection, clock)
        hook_id = hooks.create("https://example.com/a", ["alert"], None, True)
        connection.execute("UPDATE webhook SET events = 'not json' WHERE id = ?", (hook_id,))
        assert hooks.require(hook_id).events == []
        secret = connection.execute("SELECT secret FROM webhook").fetchone()[0]
        assert len(secret) >= 16


def test_webhook_prepare_test_signs_payload_with_the_stored_secret(
    database: Database, clock: FakeClock
) -> None:
    with database.session() as connection:
        hooks = WebhookRepository(connection, clock)
        hook_id = hooks.create("https://example.com/a", [], "s" * 16, True)
        pending = hooks.prepare_test(hook_id, actor_id=7)
        assert pending.signature == sign("s" * 16, pending.payload)
        assert '"actor_id":7' in pending.payload
        hooks.record_result(pending.delivery_id, "succeeded", "HTTP 200", count_attempt=True)
        delivery = hooks.deliveries(hook_id)[0]
        assert (delivery.status, delivery.attempts) == ("succeeded", 1)
        connection.execute("UPDATE webhook SET enabled = 0 WHERE id = ?", (hook_id,))
        with pytest.raises(NotFound):
            hooks.prepare_test(hook_id, actor_id=7)


def test_alert_update_sets_keeps_and_clears_marks(database: Database, clock: FakeClock) -> None:
    with database.session() as connection:
        alerts = AlertRepository(connection, clock)
        alert = alerts.create("disk", "error", "满了", "disk")
        with pytest.raises(AppError):
            alerts.create("disk", "error", "满了", "disk")
        read = alerts.update(alert.id, read=True, resolved=None)
        assert (read.read_at, read.resolved_at) == (to_iso(clock.now()), None)
        clock.advance(hours=1)
        # 已经标记过的不会被后一次操作改写时间；None 保持原样
        again = alerts.update(alert.id, read=True, resolved=True)
        assert again.read_at == read.read_at
        assert again.resolved_at == to_iso(clock.now())
        cleared = alerts.update(alert.id, read=False, resolved=None)
        assert (cleared.read_at, cleared.resolved_at) == (None, again.resolved_at)
        with pytest.raises(NotFound):
            alerts.update(999, read=True, resolved=None)


def test_scheduled_task_ensure_does_not_overwrite_existing_rows(
    database: Database, clock: FakeClock
) -> None:
    with database.session() as connection:
        tasks = ScheduledTaskRepository(connection, clock)
        tasks.patch("backup", enabled=True, interval=600)
        tasks.ensure("backup", enabled=False)
        backup = tasks.get("backup")
        assert backup is not None and (backup.enabled, backup.interval) == (True, 600)
        tasks.ensure("fresh", enabled=True)
        assert [t.key for t in tasks.all()] == sorted(t.key for t in tasks.all())
        fresh = tasks.get("fresh")
        assert fresh is not None and fresh.enabled is True and fresh.last_run_at is None


def test_cleanup_removes_only_expired_sessions_and_missing_files(
    database: Database, clock: FakeClock
) -> None:
    with database.session() as connection:
        cleanup = Cleanup(connection, clock)
        assert (cleanup.expired_sessions(), cleanup.old_play_events(), cleanup.missing_files()) == (
            0,
            0,
            0,
        )
        cleanup.optimize_database()


def test_library_settings_prefer_library_values_over_server_defaults(
    database: Database, clock: FakeClock
) -> None:
    with database.session() as connection:
        now = to_iso(clock.now())
        connection.execute(
            "INSERT INTO library(name, kind, sort_order, created_at, updated_at) "
            "VALUES ('电影', 'movie', 1, ?, ?)",
            (now, now),
        )
        library_id = connection.execute("SELECT id FROM library").fetchone()[0]
        settings = LibrarySettings(connection, clock)
        assert "extras_min_mb" not in settings.effective(library_id)
        put_setting(connection, "library.extras_min_mb", "50", now)
        assert settings.effective(library_id)["extras_min_mb"] == "50"
        settings.put(library_id, "extras_min_mb", "10")
        assert settings.effective(library_id)["extras_min_mb"] == "10"

        settings.replace_hubs(
            library_id,
            [
                LibraryHubSpec("rating", None, 3, True, True),
                LibraryHubSpec("genre", "科幻", 1, False, True),
            ],
        )
        assert [(h.kind, h.arg) for h in settings.hubs(library_id)] == [
            ("genre", "科幻"),
            ("rating", None),
        ]
        assert [h.show_home for h in settings.hubs(library_id)] == [False, True]
