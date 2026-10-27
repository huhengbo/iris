"""打包备份：内容、插件状态、恢复顺序和格式校验。"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from app.clock import SystemClock
from app.db import Database
from app.errors import AppError, NotFound
from app.ops.backup import BackupService


class FakeMaintenance:
    def __init__(self, timeouts: list[str] | None = None) -> None:
        self.calls: list[str] = []
        self.timeouts = timeouts or []

    def flush_all(self) -> list[str]:
        self.calls.append("flush")
        return list(self.timeouts)

    def stop_all(self) -> None:
        self.calls.append("stop")

    def start_all(self) -> None:
        self.calls.append("start")


@pytest.fixture
def service(tmp_path: Path) -> tuple[BackupService, Database, Path, FakeMaintenance]:
    data_dir = tmp_path / "data"
    database = Database(data_dir / "iris.sqlite3")
    database.migrate(SystemClock())
    maintenance = FakeMaintenance()
    backups = BackupService(database, data_dir, SystemClock(), plugin_maintenance=maintenance)
    return backups, database, data_dir, maintenance


def _state(data_dir: Path, plugin_id: str, name: str, content: str) -> Path:
    path = data_dir / "plugins" / plugin_id / "state" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _names(info_name: str, data_dir: Path) -> set[str]:
    with tarfile.open(data_dir / "backups" / info_name, "r:gz") as archive:
        return {member.name for member in archive.getmembers()}


def test_archive_contains_database_and_plugin_state_only(
    service: tuple[BackupService, Database, Path, FakeMaintenance],
    caplog: pytest.LogCaptureFixture,
) -> None:
    backups, _, data_dir, maintenance = service
    _state(data_dir, "org.example.a", "settings.json", '{"key": "value"}')
    _state(data_dir, "org.example.b", "nested/run.json", "{}")
    (data_dir / "plugins" / "org.example.a" / ".venv" / "bin").mkdir(parents=True)
    (data_dir / "plugins" / "org.example.a" / ".venv" / "bin" / "python").write_text("x")
    (data_dir / "plugins" / "org.example.a" / "cache").mkdir()
    (data_dir / "plugins" / "org.example.a" / "cache" / "blob").write_text("cache")
    maintenance.timeouts = ["org.example.a"]

    caplog.set_level("WARNING")
    info = backups.create()

    assert info.name.endswith(".tar.gz")
    assert info.schema_version >= 1
    names = _names(info.name, data_dir)
    assert "iris.sqlite3" in names
    assert "plugins/org.example.a/state/settings.json" in names
    assert "plugins/org.example.b/state/nested/run.json" in names
    assert not any(".venv" in name or "cache" in name for name in names)
    assert "插件 org.example.a flush 失败" in caplog.text
    assert maintenance.calls == ["flush"]


def test_restore_replaces_database_and_plugin_state(
    service: tuple[BackupService, Database, Path, FakeMaintenance],
) -> None:
    backups, database, data_dir, maintenance = service
    with database.session() as connection:
        connection.execute(
            "INSERT INTO setting (key, value, is_secret, updated_at) VALUES (?, ?, 0, ?)",
            ("test.key", "before", "2026-01-01T00:00:00+00:00"),
        )
    _state(data_dir, "org.example.a", "data.json", "before")
    _state(data_dir, "org.example.b", "data.json", "b-before")
    backup = backups.create()
    assert maintenance.calls == ["flush"]

    with database.session() as connection:
        connection.execute("UPDATE setting SET value = 'after' WHERE key = 'test.key'")
    _state(data_dir, "org.example.a", "data.json", "after")
    _state(data_dir, "org.example.b", "data.json", "b-after")
    _state(data_dir, "org.example.c", "data.json", "c-after")
    maintenance.calls.clear()

    restored, safety = backups.restore(backup.name)

    assert restored.name == backup.name
    assert safety.name != backup.name
    assert maintenance.calls == ["flush", "stop", "start"]
    with database.read_session() as connection:
        row = connection.execute("SELECT value FROM setting WHERE key = 'test.key'").fetchone()
    assert row["value"] == "before"
    assert (data_dir / "plugins" / "org.example.a" / "state" / "data.json").read_text() == "before"
    assert (
        data_dir / "plugins" / "org.example.b" / "state" / "data.json"
    ).read_text() == "b-before"
    assert not (data_dir / "plugins" / "org.example.c" / "state").exists()


def test_list_ignores_legacy_sqlite_backups(
    service: tuple[BackupService, Database, Path, FakeMaintenance],
) -> None:
    backups, _, data_dir, _ = service
    directory = data_dir / "backups"
    directory.mkdir(parents=True)
    legacy = directory / "iris-20260101T000000Z.sqlite3"
    legacy.write_bytes(b"legacy")
    current = backups.create()

    assert [item.name for item in backups.list()] == [current.name]
    with pytest.raises(NotFound):
        backups.restore(legacy.name)


def test_archive_rejects_unsafe_members(
    service: tuple[BackupService, Database, Path, FakeMaintenance],
) -> None:
    backups, _, data_dir, _ = service
    directory = data_dir / "backups"
    directory.mkdir(parents=True)
    path = directory / "iris-20260101T000001Z.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo("../evil")
        member.size = 8
        archive.addfile(member, io.BytesIO(b"malicious"))

    with pytest.raises(AppError) as error:
        backups.list()
    assert error.value.code == "invalid_backup"


def test_archive_requires_database(
    service: tuple[BackupService, Database, Path, FakeMaintenance],
) -> None:
    backups, _, data_dir, _ = service
    directory = data_dir / "backups"
    directory.mkdir(parents=True)
    path = directory / "iris-20260101T000002Z.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        data = b"{}"
        member = tarfile.TarInfo("plugins/org.example.a/state/data.json")
        member.size = len(data)
        archive.addfile(member, io.BytesIO(data))

    with pytest.raises(AppError) as error:
        backups.list()
    assert error.value.code == "invalid_backup"
