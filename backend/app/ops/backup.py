"""打包备份：SQLite 在线快照加各插件的 state 目录。

备份文件是 ``backups/iris-<时间>.tar.gz``，只包含 ``iris.sqlite3`` 和
``plugins/<id>/state/``；插件虚拟环境、缓存和资产都不进备份。恢复时先停插件，替换主库和
各插件状态，再重启插件。
"""

from __future__ import annotations

import logging
import re
import shutil
import sqlite3
import tarfile
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Protocol

from app.clock import Clock, to_iso
from app.db import Database, schema_version
from app.errors import AppError, NotFound

logger = logging.getLogger(__name__)

_BACKUP_NAME = re.compile(r"^iris-[0-9]{8}T[0-9]{6}Z(?:-[0-9]+)?\.tar\.gz$")
# 插件 id 和 manifest 的规则一致；备份包里的目录名也按它校验。
_PLUGIN_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_DB_MEMBER = "iris.sqlite3"
_PLUGINS_PREFIX = "plugins"


class PluginMaintenance(Protocol):
    """备份和恢复需要的插件生命周期接口；第三波接到 PluginSupervisor。"""

    def flush_all(self) -> list[str]:
        """flush 全部运行中的插件，返回失败的插件 id。"""
        ...

    def stop_all(self) -> None: ...

    def start_all(self) -> None: ...


@dataclass(frozen=True)
class BackupInfo:
    name: str
    size: int
    created_at: str
    schema_version: int


class BackupService:
    def __init__(
        self,
        database: Database,
        data_dir: Path,
        clock: Clock,
        *,
        plugin_maintenance: PluginMaintenance | None = None,
    ) -> None:
        self._database = database
        self._directory = data_dir / "backups"
        self._data_dir = data_dir
        self._clock = clock
        self._plugin_maintenance = plugin_maintenance

    def list(self) -> list[BackupInfo]:
        self._directory.mkdir(parents=True, exist_ok=True)
        backups = [
            self._inspect(path)
            for path in self._directory.iterdir()
            if path.is_file() and _BACKUP_NAME.fullmatch(path.name)
        ]
        return sorted(backups, key=lambda item: item.created_at, reverse=True)

    def create(self) -> BackupInfo:
        if self._database.integrity_check() != "ok":
            raise AppError(500, "database_corrupt", "数据库完整性检查失败，未创建备份")
        if self._plugin_maintenance is not None:
            for plugin_id in self._plugin_maintenance.flush_all():
                logger.warning("插件 %s flush 失败，继续创建备份", plugin_id)
        self._directory.mkdir(parents=True, exist_ok=True)
        stem = f"iris-{self._clock.now().strftime('%Y%m%dT%H%M%SZ')}"
        name = f"{stem}.tar.gz"
        suffix = 2
        while (self._directory / name).exists():
            name = f"{stem}-{suffix}.tar.gz"
            suffix += 1
        target = self._directory / name
        temporary = self._directory / f".{name}.tmp"
        snapshot = self._directory / f".{name}.sqlite3"
        try:
            self._database.backup_to(snapshot)
            self._inspect_database(snapshot)
            with tarfile.open(temporary, "w:gz") as archive:
                archive.add(snapshot, arcname=_DB_MEMBER, filter=_regular_file)
                for plugin_id, state_dir in self._state_dirs():
                    archive.add(
                        state_dir,
                        arcname=f"{_PLUGINS_PREFIX}/{plugin_id}/state",
                        filter=_regular_file,
                    )
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
            snapshot.unlink(missing_ok=True)
        return self._inspect(target)

    def prune(self, keep: int = 5) -> int:
        """保留最近的常规备份；恢复前创建的安全备份由调用方另行管理。"""
        keep = max(1, keep)
        backups = self.list()
        deleted = 0
        for backup in backups[keep:]:
            path = self._directory / backup.name
            path.unlink(missing_ok=True)
            deleted += 1
        return deleted

    def restore(self, name: str) -> tuple[BackupInfo, BackupInfo]:
        source = self._path(name)
        candidate = self._inspect(source)
        safety = self.create()
        extract_root = Path(tempfile.mkdtemp(dir=self._directory, prefix=".restore-"))
        stopped = False
        try:
            database_path = self._extract(source, extract_root)
            if self._plugin_maintenance is not None:
                stopped = True
                self._plugin_maintenance.stop_all()
            try:
                self._database.restore_from(database_path)
                if self._database.integrity_check() != "ok":
                    raise AppError(500, "restore_failed", "恢复后的数据库完整性检查失败")
                self._replace_plugin_state(extract_root)
            except BaseException:
                self._rollback(safety, extract_root)
                raise
        finally:
            if stopped:
                assert self._plugin_maintenance is not None
                self._plugin_maintenance.start_all()
            shutil.rmtree(extract_root, ignore_errors=True)
        return candidate, safety

    def _rollback(self, safety: BackupInfo, extract_root: Path) -> None:
        rollback_root = Path(tempfile.mkdtemp(dir=self._directory, prefix=".rollback-"))
        try:
            self._extract(self._path(safety.name), rollback_root)
            self._database.restore_from(rollback_root / _DB_MEMBER)
            self._replace_plugin_state(rollback_root)
        finally:
            shutil.rmtree(rollback_root, ignore_errors=True)

    def _state_dirs(self) -> Sequence[tuple[str, Path]]:
        plugins_dir = self._data_dir / "plugins"
        if not plugins_dir.is_dir():
            return []
        found: list[tuple[str, Path]] = []
        for directory in sorted(plugins_dir.iterdir()):
            if (
                not directory.is_dir()
                or directory.is_symlink()
                or not _PLUGIN_ID.fullmatch(directory.name)
            ):
                continue
            state = directory / "state"
            if state.is_dir():
                found.append((directory.name, state))
        return found

    def _replace_plugin_state(self, extracted_root: Path) -> None:
        plugins_dir = self._data_dir / "plugins"
        plugins_dir.mkdir(parents=True, exist_ok=True)
        for directory in plugins_dir.iterdir():
            if directory.is_symlink():
                continue
            state = directory / "state"
            if state.is_symlink():
                state.unlink()
            elif state.is_dir():
                shutil.rmtree(state)
        extracted = extracted_root / _PLUGINS_PREFIX
        if not extracted.is_dir():
            return
        for plugin_dir in sorted(extracted.iterdir()):
            source = plugin_dir / "state"
            if not source.is_dir():
                continue
            target = plugins_dir / plugin_dir.name / "state"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target)

    def _path(self, name: str) -> Path:
        if not _BACKUP_NAME.fullmatch(name):
            raise NotFound("备份不存在")
        path = self._directory / name
        if not path.is_file():
            raise NotFound("备份不存在")
        return path

    def _inspect(self, path: Path) -> BackupInfo:
        try:
            with tarfile.open(path, "r:gz") as archive:
                members = archive.getmembers()
                _check_members(members)
                database = archive.extractfile(_DB_MEMBER)
                if database is None:
                    raise AppError(422, "invalid_backup", "备份包缺少数据库")
                with tempfile.NamedTemporaryFile(
                    dir=self._directory, suffix=".inspect.sqlite3", delete=False
                ) as temporary:
                    shutil.copyfileobj(database, temporary)
                    temporary_path = Path(temporary.name)
            try:
                version = self._inspect_database(temporary_path)
            finally:
                temporary_path.unlink(missing_ok=True)
        except (OSError, tarfile.TarError) as error:
            raise AppError(422, "invalid_backup", "备份文件无法读取") from error
        created = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        return BackupInfo(path.name, path.stat().st_size, to_iso(created), version)

    @staticmethod
    def _inspect_database(path: Path) -> int:
        try:
            connection = sqlite3.connect(path)
            try:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise AppError(422, "invalid_backup", "备份文件完整性检查失败")
                version = schema_version(connection)
            finally:
                connection.close()
        except sqlite3.DatabaseError as error:
            raise AppError(422, "invalid_backup", "备份文件无法读取") from error
        if version < 1:
            raise AppError(422, "invalid_backup", "备份文件缺少数据库结构")
        return version

    def _extract(self, path: Path, destination: Path) -> Path:
        destination.mkdir(parents=True, exist_ok=True)
        try:
            with tarfile.open(path, "r:gz") as archive:
                members = archive.getmembers()
                _check_members(members)
                for member in members:
                    target = destination / PurePosixPath(member.name)
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = archive.extractfile(member)
                    if source is None:
                        raise AppError(422, "invalid_backup", "备份文件内容不完整")
                    with target.open("wb") as file:
                        shutil.copyfileobj(source, file)
        except (OSError, tarfile.TarError) as error:
            raise AppError(422, "invalid_backup", "备份文件无法读取") from error
        database_path = destination / _DB_MEMBER
        self._inspect_database(database_path)
        return database_path


def _check_members(members: list[tarfile.TarInfo]) -> None:
    names: set[str] = set()
    for member in members:
        if member.issym() or member.islnk() or member.isdev():
            raise AppError(422, "invalid_backup", "备份包包含不支持的文件类型")
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise AppError(422, "invalid_backup", "备份包包含不安全的路径")
        name = path.as_posix()
        if name in names:
            raise AppError(422, "invalid_backup", "备份包包含重复路径")
        names.add(name)
        if name == _DB_MEMBER:
            if not member.isfile():
                raise AppError(422, "invalid_backup", "备份包的数据库不是普通文件")
            continue
        parts = path.parts
        if (
            len(parts) < 3
            or parts[0] != _PLUGINS_PREFIX
            or parts[2] != "state"
            or not _PLUGIN_ID.fullmatch(parts[1])
            or (len(parts) == 3 and not member.isdir())
        ):
            raise AppError(422, "invalid_backup", "备份包包含未知路径")
        if len(parts) > 3 and not (member.isfile() or member.isdir()):
            raise AppError(422, "invalid_backup", "备份包包含不支持的插件状态文件")
    if _DB_MEMBER not in names:
        raise AppError(422, "invalid_backup", "备份包缺少数据库")


def _regular_file(member: tarfile.TarInfo) -> tarfile.TarInfo | None:
    if member.isfile() or member.isdir():
        return member
    raise AppError(500, "invalid_backup", "插件状态目录包含不支持的文件")
