"""仓库和本地插件的安装、升级、卸载、恢复与启动加载。

代码和虚拟环境直接建在 ``data/plugin-packages/<id>/<version>/``：venv 和 editable 路径
里有绝对路径，不能先建在临时目录再搬。安装中的目录带 ``.installing`` 标记，启动时清理。
插件状态仍在 ``data/plugins/<id>/state/``，备份不包含代码和虚拟环境。
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sqlite3
import sys
import threading
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from iris_plugin_sdk import PluginManifest, PluginManifestError, load_manifest

from app.clock import Clock, to_iso
from app.config import AppConfig
from app.db import Database
from app.errors import AppError, NotFound
from app.ops.tasks import TaskRepository
from app.plugins.archive import (
    ArchiveError,
    ArchiveTooLarge,
    PackageError,
    PackageIncompatible,
    check_archive,
    check_package,
    extract_archive,
)
from app.plugins.fetch import FetchError, FetchTooLarge, SafeFetcher
from app.plugins.host import effective_proxy
from app.plugins.index_schema import (
    MAX_PACKAGE_BYTES,
    PLUGIN_ID,
    VERSION,
    IndexEntry,
    best_compatible,
    compare_versions,
    is_compatible,
)
from app.plugins.installer import DependencyInstaller, DependencyInstallError
from app.plugins.registry import PluginRegistry, PluginStartFailed, plugin_category
from app.plugins.repositories import PluginRepositories
from app.plugins.supervisor import (
    PluginNotInstalled,
    PluginRuntimeError,
    PluginSupervisor,
)

logger = logging.getLogger(__name__)

INSTALL_MARKER = ".installing"
UPLOAD_TTL = timedelta(hours=1)
OPERATION_PHASES = ("download", "verify", "extract", "dependencies", "start")
_PHASE_TOTAL = len(OPERATION_PHASES)

PackageStatus = Literal["ok", "installing", "missing", "failed"]


@dataclass(frozen=True)
class _Package:
    plugin_id: str
    name: str
    version: str
    sdk: str
    provides: tuple[str, ...]
    capabilities: tuple[str, ...]
    sha256: str
    size: int
    url: str | None = None

    @classmethod
    def from_entry(cls, entry: IndexEntry) -> _Package:
        return cls(
            plugin_id=entry.id,
            name=entry.name,
            version=entry.version,
            sdk=entry.sdk,
            provides=entry.provides,
            capabilities=entry.capabilities,
            sha256=entry.sha256,
            size=entry.size,
            url=entry.url,
        )


@dataclass(frozen=True)
class _InstallRow:
    plugin_id: str
    source: str
    repository_id: int | None
    version: str
    previous_version: str | None
    sha256: str
    status: str
    error: str | None


class _TaskError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _RepositoryUnavailable(Exception):
    """补装时仓库暂时拉不到索引。"""


class PluginPackages:
    def __init__(
        self,
        database: Database,
        clock: Clock,
        config: AppConfig,
        supervisor: PluginSupervisor,
        plugins: PluginRegistry,
        repositories: PluginRepositories,
        installer: DependencyInstaller,
        *,
        fetcher: SafeFetcher | None = None,
    ) -> None:
        self._database = database
        self._clock = clock
        self._config = config
        self._supervisor = supervisor
        self._plugins = plugins
        self._repositories = repositories
        self._installer = installer
        self._fetcher = fetcher or SafeFetcher()
        self._root = config.plugin_packages_dir
        self._uploads = self._root / ".uploads"
        self._downloads = self._root / ".downloads"
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._installing: set[str] = set()

    # ---- 供注册中心展示 ----

    def package_status(self, plugin_id: str) -> PackageStatus | None:
        with self._state_lock:
            return "installing" if plugin_id in self._installing else None

    @property
    def uploads_dir(self) -> Path:
        self._uploads.mkdir(parents=True, exist_ok=True)
        return self._uploads

    # ---- 启动加载 ----

    def load_installed(self) -> None:
        """宿主启动时按已安装清单注册插件，并清理残留目录。

        必须在 ``plugins.start_enabled()`` 之前调用；这里只注册，不启动。
        """
        self._root.mkdir(parents=True, exist_ok=True)
        self._uploads.mkdir(parents=True, exist_ok=True)
        self._downloads.mkdir(parents=True, exist_ok=True)
        self._cleanup_uploads()
        rows = self._install_rows()
        valid_ids = {row.plugin_id for row in rows}
        self._cleanup_directories(rows, valid_ids)
        for row in rows:
            directory = self._version_dir(row.plugin_id, row.version)
            if not directory.is_dir() or (directory / INSTALL_MARKER).exists():
                self._mark_missing(row.plugin_id, "插件代码目录不存在，等待重新安装")
                continue
            if self._plugins.is_built_in(row.plugin_id):
                self._mark_failed(row.plugin_id, "与系统插件 id 冲突")
                continue
            if not self._venv_matches_host(directory):
                self._mark_missing(row.plugin_id, "Python 解释器版本已变化，需要重建依赖")
                continue
            try:
                self._register_or_replace(row.plugin_id, directory)
            except PluginRuntimeError as exc:
                self._mark_failed(row.plugin_id, exc.message)
                continue
            except Exception as exc:
                logger.exception("加载已安装插件 %s 失败", row.plugin_id)
                self._mark_failed(row.plugin_id, str(exc))
                continue
            self._mark_installed(row.plugin_id)

    def start_all(self) -> None:
        """备份恢复后：补迁移、对齐运行时、重启插件，并后台补装缺失的包。"""
        self._database.migrate(self._clock)
        missing = self.reconcile()
        self._supervisor.start_all()
        self._plugins.start_enabled()
        if missing:
            threading.Thread(
                target=self.reinstall_missing,
                name="plugin-reinstall",
                daemon=True,
            ).start()

    def flush_all(self) -> list[str]:
        return self._supervisor.flush_all()

    def stop_all(self) -> None:
        self._supervisor.stop_all()

    # ---- 安装 ----

    def install(
        self,
        repository_id: int,
        plugin_id: str,
        version: str,
        accept_permissions: bool,
        *,
        created_by: int | None = None,
    ) -> int:
        if not accept_permissions:
            raise AppError(400, "permissions_not_accepted", "安装前需要确认插件申请的权限")
        repository = self._repositories.get(repository_id)
        if repository is None or not repository["enabled"] or repository["status"] == "unsupported":
            raise AppError(404, "plugin_not_in_repository", "插件不在这个仓库的目录里")
        entries = self._repositories.entries(repository_id, plugin_id)
        if not entries:
            raise AppError(404, "plugin_not_in_repository", "插件不在这个仓库的目录里")
        entry = next((item for item in entries if item.version == version), None)
        if entry is None:
            raise AppError(409, "catalog_changed", "仓库索引已变化，请刷新目录后重试")
        if not is_compatible(entry):
            raise AppError(409, "plugin_incompatible", "插件的 SDK 范围与宿主不兼容")
        self._acquire()
        try:
            # 冲突检查必须在锁内：否则另一个安装刚完成时，这里会进入 worker 删掉它的运行目录
            self._check_install_conflict(plugin_id)
            task_id = self._create_task(
                "plugin:install",
                plugin_id,
                version,
                created_by=created_by,
            )
            threading.Thread(
                target=self._install_worker,
                kwargs={
                    "task_id": task_id,
                    "package": _Package.from_entry(entry),
                    "source": "repository",
                    "repository_id": repository_id,
                    "previous": None,
                    "upload_id": None,
                    "download_proxy": self._proxy_for(plugin_id),
                },
                name=f"plugin-install-{plugin_id}",
                daemon=True,
            ).start()
        except BaseException:
            self._release()
            raise
        return task_id

    def upgrade(
        self,
        plugin_id: str,
        version: str,
        accept_permissions: bool,
        *,
        created_by: int | None = None,
    ) -> int:
        if not accept_permissions:
            raise AppError(400, "permissions_not_accepted", "升级前需要确认插件申请的权限")
        if self._plugins.is_built_in(plugin_id):
            raise AppError(409, "plugin_built_in", "系统插件随镜像升级，不能单独升级")
        self._acquire()
        try:
            row, entry = self._prepare_upgrade(plugin_id, version)
            task_id = self._create_task(
                "plugin:upgrade",
                plugin_id,
                version,
                from_version=row.version,
                created_by=created_by,
            )
            threading.Thread(
                target=self._install_worker,
                kwargs={
                    "task_id": task_id,
                    "package": _Package.from_entry(entry),
                    "source": "repository",
                    "repository_id": row.repository_id,
                    "previous": row,
                    "upload_id": None,
                    "download_proxy": self._proxy_for(plugin_id),
                },
                name=f"plugin-upgrade-{plugin_id}",
                daemon=True,
            ).start()
        except BaseException:
            self._release()
            raise
        return task_id

    def _prepare_upgrade(self, plugin_id: str, version: str) -> tuple[_InstallRow, IndexEntry]:
        """持有操作锁时读取安装记录并校验升级条件，避免用到别的操作改动前的旧记录。"""
        row = self._install_row(plugin_id)
        if row is None:
            raise AppError(404, "not_found", "插件没有安装")
        if row.source != "repository" or row.repository_id is None:
            raise AppError(409, "plugin_local_source", "本地安装的插件需要重新上传更高版本")
        repository = self._repositories.get(row.repository_id)
        if repository is None or repository["status"] == "unsupported":
            raise AppError(409, "plugin_no_update", "插件来源仓库不可用")
        entries = self._repositories.entries(row.repository_id, plugin_id)
        if not entries:
            raise AppError(409, "plugin_no_update", "来源仓库里没有这个插件了")
        entry = next((item for item in entries if item.version == version), None)
        if entry is None:
            raise AppError(409, "catalog_changed", "仓库索引已变化，请刷新目录后重试")
        if compare_versions(entry.version, row.version) <= 0:
            raise AppError(409, "plugin_no_update", "这个版本不是更新")
        if not is_compatible(entry):
            raise AppError(409, "plugin_incompatible", "插件的 SDK 范围与宿主不兼容")
        if (
            "resource.source" in self._installed_provides(plugin_id)
            and "resource.source" not in entry.provides
            and self._plugin_in_use(plugin_id)
        ):
            raise AppError(409, "plugin_in_use", "还有资源来源在使用这个插件，不能升级")
        return row, entry

    def uninstall(self, plugin_id: str, delete_data: bool) -> None:
        if self._plugins.is_built_in(plugin_id) or plugin_id.startswith("builtin."):
            raise AppError(409, "plugin_built_in", "系统插件不能卸载")
        self._acquire()
        try:
            if self._install_row(plugin_id) is None:
                raise NotFound("插件没有安装")
            if self._plugin_in_use(plugin_id):
                raise AppError(409, "plugin_in_use", "还有资源来源在使用这个插件，不能卸载")
            # 先删库：外键等原因失败时插件还在运行，状态没有变化
            try:
                with self._database.session() as connection:
                    connection.execute("DELETE FROM plugin WHERE id = ?", (plugin_id,))
                    connection.execute(
                        "DELETE FROM library_plugin WHERE plugin_id = ?", (plugin_id,)
                    )
                    connection.execute(
                        "DELETE FROM plugin_install WHERE plugin_id = ?", (plugin_id,)
                    )
            except sqlite3.IntegrityError as exc:
                raise AppError(409, "plugin_in_use", "还有数据在使用这个插件，不能卸载") from exc
            self._detach(plugin_id)
            if PLUGIN_ID.fullmatch(plugin_id):
                shutil.rmtree(self._root / plugin_id, ignore_errors=True)
            if delete_data and PLUGIN_ID.fullmatch(plugin_id):
                shutil.rmtree(self._config.data_dir / "plugins" / plugin_id, ignore_errors=True)
        finally:
            self._release()

    # ---- 本地上传 ----

    def create_upload(self, zip_path: Path) -> dict[str, Any]:
        """校验上传的 zip 并保存预览；确认安装走 :meth:`install_upload`。"""
        self._uploads.mkdir(parents=True, exist_ok=True)
        temporary = self._uploads / f".check-{uuid.uuid4().hex}"
        try:
            size = zip_path.stat().st_size
            digest = _sha256(zip_path)
            try:
                check_archive(zip_path)
            except ArchiveTooLarge as exc:
                raise AppError(413, "package_too_large", str(exc)) from exc
            except ArchiveError as exc:
                raise AppError(422, "package_invalid", str(exc)) from exc
            shutil.move(str(zip_path), temporary)
            extract = self._uploads / f".extract-{uuid.uuid4().hex}"
            try:
                extract_archive(temporary, extract)
                manifest = self._check_uploaded_package(extract)
            except ArchiveTooLarge as exc:
                raise AppError(413, "package_too_large", str(exc)) from exc
            except PackageIncompatible as exc:
                raise AppError(409, "plugin_incompatible", str(exc)) from exc
            except ArchiveError as exc:
                raise AppError(422, "package_invalid", str(exc)) from exc
            finally:
                shutil.rmtree(extract, ignore_errors=True)
            self._check_upload_conflict(manifest)
            existing = self._install_row(manifest.id)
            action = "upgrade" if existing is not None else "install"
            upload_id = f"u_{uuid.uuid4().hex}"
            target = self._uploads / f"{upload_id}.zip"
            temporary.replace(target)
            expires_at = to_iso(self._clock.now() + UPLOAD_TTL)
            record = {
                "upload_id": upload_id,
                "sha256": digest,
                "size": size,
                "manifest": {
                    "id": manifest.id,
                    "name": manifest.name,
                    "version": manifest.version,
                    "description": manifest.description,
                    "sdk": manifest.sdk,
                    "provides": list(manifest.provides),
                    "capabilities": list(manifest.capabilities),
                },
                "category": plugin_category(manifest.provides),
                "action": action,
                "installed_version": existing.version if existing is not None else None,
                "expires_at": expires_at,
            }
            (self._uploads / f"{upload_id}.json").write_text(
                json.dumps(record, ensure_ascii=False), encoding="utf-8"
            )
            return record
        finally:
            temporary.unlink(missing_ok=True)

    def install_upload(
        self,
        upload_id: str,
        accept_risk: bool,
        *,
        created_by: int | None = None,
    ) -> int:
        if not accept_risk:
            raise AppError(400, "risk_not_accepted", "安装本地插件前需要确认风险")
        record = self._read_upload(upload_id)
        manifest = record.get("manifest")
        if not isinstance(manifest, dict):
            raise AppError(422, "package_invalid", "上传记录已损坏")
        plugin_id = str(manifest.get("id", ""))
        version = str(manifest.get("version", ""))
        self._acquire()
        try:
            self._check_upload_conflict(manifest)
            row = self._install_row(plugin_id)
            if row is not None and (
                record.get("action") != "upgrade" or row.version != record.get("installed_version")
            ):
                raise AppError(409, "plugin_version_not_newer", "上传的版本不是更新")
            task_id = self._create_task(
                "plugin:upgrade" if row is not None else "plugin:install",
                plugin_id,
                version,
                from_version=row.version if row is not None else None,
                created_by=created_by,
            )
            package = _Package(
                plugin_id=plugin_id,
                name=str(manifest.get("name", "")),
                version=version,
                sdk=str(manifest.get("sdk", "")),
                provides=tuple(str(item) for item in manifest.get("provides", [])),
                capabilities=tuple(str(item) for item in manifest.get("capabilities", [])),
                sha256=str(record["sha256"]),
                size=int(record["size"]),
            )
            threading.Thread(
                target=self._install_worker,
                kwargs={
                    "task_id": task_id,
                    "package": package,
                    "source": "local",
                    "repository_id": None,
                    "previous": row,
                    "upload_id": upload_id,
                    "download_proxy": self._proxy_for(plugin_id),
                },
                name=f"plugin-upload-{plugin_id}",
                daemon=True,
            ).start()
        except BaseException:
            self._release()
            raise
        return task_id

    def delete_upload(self, upload_id: str) -> None:
        self._read_upload(upload_id)
        self._upload_paths(upload_id)
        (self._uploads / f"{upload_id}.zip").unlink(missing_ok=True)
        (self._uploads / f"{upload_id}.json").unlink(missing_ok=True)

    # ---- 恢复和缺失重装 ----

    def reconcile(self) -> list[str]:
        """恢复备份后让运行时和 plugin_install 对齐；返回仍然缺失的插件 id。"""
        rows = self._install_rows()
        by_id = {row.plugin_id: row for row in rows}
        for plugin_id in self._plugins.installed_ids():
            if plugin_id in by_id or self._plugins.is_built_in(plugin_id):
                continue
            with suppress(PluginNotInstalled):
                self._supervisor.unregister(plugin_id)
            self._plugins.remove(plugin_id)
        missing: list[str] = []
        for row in rows:
            if self._plugins.is_built_in(row.plugin_id):
                self._mark_failed(row.plugin_id, "与系统插件 id 冲突")
                continue
            directory = self._version_dir(row.plugin_id, row.version)
            if not directory.is_dir():
                newer = self._newest_local_version(row.plugin_id, row.version)
                if newer is not None:
                    directory = self._version_dir(row.plugin_id, newer)
                    row = self._adopt_version(row, newer)
            if not directory.is_dir() or not self._venv_matches_host(directory):
                if row.plugin_id in self._plugins.installed_ids():
                    with suppress(PluginNotInstalled):
                        self._supervisor.unregister(row.plugin_id)
                    self._plugins.remove(row.plugin_id)
                self._mark_missing(row.plugin_id, "插件代码目录不存在，等待重新安装")
                missing.append(row.plugin_id)
                continue
            try:
                self._register_or_replace(row.plugin_id, directory)
            except Exception as exc:
                logger.exception("恢复后切换插件 %s 失败", row.plugin_id)
                self._mark_failed(row.plugin_id, str(exc))
                continue
            self._mark_installed(row.plugin_id)
        return missing

    def reinstall_missing(self) -> None:
        """后台补装缺失的插件；逐个执行，调用方保证不在请求线程里。

        操作锁在 ``_create_task`` 失败时由这里释放；worker 自己在 finally 里释放，
        所以 worker 抛出的异常不能再释放一次。
        """
        for row in self._install_rows():
            if row.status != "missing":
                continue
            if self._needs_dependency_rebuild(row):
                self._lock.acquire()
                try:
                    task_id = self._create_task("plugin:dependencies", row.plugin_id, row.version)
                except Exception:
                    logger.exception("创建插件 %s 的依赖重建任务失败", row.plugin_id)
                    self._release()
                    continue
                try:
                    self._dependencies_worker(task_id, row)
                except Exception:
                    logger.exception("重建插件 %s 依赖失败", row.plugin_id)
                continue
            if row.source != "repository" or row.repository_id is None:
                self._mark_missing(row.plugin_id, "本地插件需要重新上传安装包")
                continue
            try:
                entry = self._reinstall_entry(row)
            except _RepositoryUnavailable as exc:
                # 仓库暂时拉不到：保持 missing，下次启动或恢复时再试
                logger.warning("补装插件 %s 时仓库不可用：%s", row.plugin_id, exc)
                self._mark_missing(row.plugin_id, f"插件仓库暂时不可用，稍后重试：{exc}")
                continue
            if entry is None:
                self._mark_failed(row.plugin_id, "找不到可安装的版本")
                continue
            self._lock.acquire()
            try:
                task_id = self._create_task(
                    "plugin:reinstall",
                    row.plugin_id,
                    entry.version,
                    from_version=row.version,
                )
            except Exception:
                logger.exception("创建插件 %s 的补装任务失败", row.plugin_id)
                self._release()
                continue
            try:
                self._install_worker(
                    task_id=task_id,
                    package=_Package.from_entry(entry),
                    source="repository",
                    repository_id=row.repository_id,
                    previous=row,
                    upload_id=None,
                    download_proxy=self._proxy_for(row.plugin_id),
                    allow_missing_previous=True,
                    warning=(
                        f"仓库里没有 {row.version}，改为安装兼容版本 {entry.version}"
                        if entry.version != row.version
                        else None
                    ),
                )
            except Exception:
                logger.exception("补装插件 %s 失败", row.plugin_id)

    # ---- 安装任务 ----

    def _install_worker(
        self,
        *,
        task_id: int,
        package: _Package,
        source: Literal["repository", "local"],
        repository_id: int | None,
        previous: _InstallRow | None,
        upload_id: str | None,
        download_proxy: str | None,
        allow_missing_previous: bool = False,
        warning: str | None = None,
    ) -> None:
        with self._state_lock:
            self._installing.add(package.plugin_id)
        destination = self._version_dir(package.plugin_id, package.version)
        temporary: Path | None = None
        error: _TaskError | None = None
        # 运行时已经切到 destination 之后为真：这时不能再删这个目录
        committed = False
        result: dict[str, Any] = {"rolled_back": False}
        if warning is not None:
            result["warning"] = warning
        try:
            self._progress(task_id, "download", 0)
            if destination.exists():
                shutil.rmtree(destination)
            destination.mkdir(parents=True)
            (destination / INSTALL_MARKER).write_text("", encoding="utf-8")
            if source == "repository":
                assert package.url is not None
                temporary = self._downloads / f"{uuid.uuid4().hex}.zip"
                fetched = self._fetcher.download(
                    package.url,
                    temporary,
                    proxy=download_proxy,
                    max_bytes=MAX_PACKAGE_BYTES,
                )
                if fetched.size != package.size:
                    raise _TaskError(
                        "package_checksum_mismatch",
                        f"下载大小和索引不一致：{fetched.size} != {package.size}",
                    )
                digest = fetched.sha256
            else:
                archive = self._upload_zip(upload_id)
                digest = _sha256(archive)
                if digest != package.sha256:
                    raise _TaskError("package_checksum_mismatch", "上传文件已损坏")
            self._progress(task_id, "verify", 1)
            if digest != package.sha256:
                raise _TaskError("package_checksum_mismatch", "插件包 sha256 和索引不一致")
            self._progress(task_id, "extract", 2)
            archive = temporary if temporary is not None else self._upload_zip(upload_id)
            extract_archive(archive, destination)
            manifest = check_package(destination)
            if not _package_matches(package, manifest):
                raise _TaskError("package_mismatch", "压缩包里的 manifest 和索引条目不一致")
            self._progress(task_id, "dependencies", 3)
            try:
                self._installer.install(destination, proxy=download_proxy)
            except DependencyInstallError as exc:
                raise _TaskError("dependency_install_failed", str(exc)) from exc
            except Exception as exc:
                raise _TaskError("dependency_install_failed", str(exc)) from exc
            (destination / INSTALL_MARKER).unlink(missing_ok=True)
            self._progress(task_id, "start", 4)
            if previous is None:
                started = self._activate_fresh(package, source, repository_id)
                committed = True
                result["error_code"] = None if started else "plugin_start_failed"
                if started:
                    self._delete_upload(upload_id)
            else:
                rolled_back, started = self._activate_upgrade(
                    package, source, repository_id, previous, allow_missing_previous
                )
                committed = not rolled_back
                result["rolled_back"] = rolled_back
                if rolled_back:
                    error = _TaskError("plugin_start_failed", "新版本启动失败，已回滚")
                    result["error_code"] = error.code
                elif not started:
                    result["error_code"] = "plugin_start_failed"
                else:
                    self._delete_upload(upload_id)
        except _TaskError as exc:
            error = exc
            result["error_code"] = exc.code
        except FetchTooLarge as exc:
            error = _TaskError("package_too_large", str(exc))
            result["error_code"] = error.code
        except FetchError as exc:
            error = _TaskError("package_download_failed", str(exc))
            result["error_code"] = error.code
        except ArchiveTooLarge as exc:
            error = _TaskError("package_too_large", str(exc))
            result["error_code"] = error.code
        except (ArchiveError, PackageError, PluginManifestError) as exc:
            error = _TaskError("package_invalid", str(exc))
            result["error_code"] = error.code
        except DependencyInstallError as exc:
            error = _TaskError("dependency_install_failed", str(exc))
            result["error_code"] = error.code
        except Exception as exc:
            logger.exception("安装插件 %s 失败", package.plugin_id)
            error = _TaskError("package_invalid", str(exc))
            result["error_code"] = error.code
        finally:
            # 每一步失败都不能跳过后面的收尾，尤其是操作锁的释放
            try:
                try:
                    if error is not None and not committed:
                        shutil.rmtree(destination, ignore_errors=True)
                        _remove_empty_parent(destination)
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
                finally:
                    try:
                        self._finish(task_id, error=error.message if error else None, result=result)
                    finally:
                        with self._state_lock:
                            self._installing.discard(package.plugin_id)
            finally:
                self._release()

    def _activate_fresh(
        self,
        package: _Package,
        source: str,
        repository_id: int | None,
    ) -> bool:
        destination = self._version_dir(package.plugin_id, package.version)
        try:
            discovered = self._supervisor.register(destination, built_in=False)
            self._plugins.add(discovered)
            self._write_install_row(
                package,
                source=source,
                repository_id=repository_id,
                previous_version=None,
            )
        except BaseException:
            # 运行时不能留着指向即将被清理的目录；注册时写入的插件记录一并清掉
            self._detach(package.plugin_id)
            with suppress(Exception):
                self._plugins.delete(package.plugin_id)
            raise
        try:
            self._plugins.set_enabled(package.plugin_id, True)
        except PluginStartFailed as exc:
            logger.warning("插件 %s 安装后启动失败：%s", package.plugin_id, exc.message)
            return False
        except Exception:
            # 安装记录已写入，目录要保留；只是没能启用
            logger.exception("插件 %s 安装后启用失败", package.plugin_id)
            return False
        return True

    def _activate_upgrade(
        self,
        package: _Package,
        source: str,
        repository_id: int | None,
        previous: _InstallRow,
        allow_missing_previous: bool,
    ) -> tuple[bool, bool]:
        """返回 (是否回滚, 是否启动成功)。成功后保留旧版本目录，并删除更早的版本。"""
        plugin_id = package.plugin_id
        destination = self._version_dir(plugin_id, package.version)
        old_directory = self._version_dir(plugin_id, previous.version)
        if plugin_id not in self._plugins.installed_ids():
            # 恢复备份后缺失的插件还没有注册，直接按新安装处理；旧目录若存在仍保留
            previous_version = (
                previous.version
                if previous.version != package.version and old_directory.is_dir()
                else None
            )
            try:
                discovered = self._supervisor.register(destination, built_in=False)
                self._plugins.add(discovered)
                self._write_install_row(
                    package,
                    source=source,
                    repository_id=repository_id,
                    previous_version=previous_version,
                )
            except BaseException:
                self._detach(plugin_id)
                raise
            self._prune_versions(
                plugin_id,
                {package.version} | ({previous.version} if previous_version else set()),
            )
            if self._plugins.is_enabled(plugin_id):
                try:
                    self._plugins.start_plugin(plugin_id)
                except PluginStartFailed:
                    return False, False
                except Exception:
                    logger.exception("插件 %s 重新安装后启动失败", plugin_id)
                    return False, False
            return False, True
        enabled = self._plugins.is_enabled(plugin_id)
        self._supervisor.stop(plugin_id)
        try:
            discovered = self._supervisor.replace(plugin_id, destination)
            self._plugins.replace(plugin_id, discovered)
            if enabled:
                self._plugins.start_plugin(plugin_id)
            # 写记录也在回滚范围内：记录没写成功时不能让运行时停在新版本上
            self._write_install_row(
                package,
                source=source,
                repository_id=repository_id,
                previous_version=previous.version,
            )
        except Exception as exc:
            logger.warning("插件 %s 升级后启动失败，回滚：%s", plugin_id, exc)
            self._supervisor.stop(plugin_id)
            if not old_directory.is_dir():
                if allow_missing_previous:
                    try:
                        self._write_install_row(
                            package,
                            source=source,
                            repository_id=repository_id,
                            previous_version=None,
                        )
                    except BaseException:
                        self._detach(plugin_id)
                        raise
                    return False, False
                logger.error("插件 %s 的旧版本目录不存在，无法回滚", plugin_id)
                return False, False
            try:
                old = self._supervisor.replace(plugin_id, old_directory)
                self._plugins.replace(plugin_id, old)
                if enabled:
                    self._plugins.start_plugin(plugin_id)
            except Exception:
                logger.exception("插件 %s 回滚后仍无法启动", plugin_id)
                with suppress(PluginNotInstalled):
                    self._supervisor.unregister(plugin_id)
                self._plugins.remove(plugin_id)
            self._prune_versions(plugin_id, {previous.version})
            return True, False
        self._prune_versions(plugin_id, {package.version, previous.version})
        return False, True

    def _dependencies_worker(self, task_id: int, row: _InstallRow) -> None:
        directory = self._version_dir(row.plugin_id, row.version)
        result: dict[str, Any] = {}
        error: str | None = None
        try:
            self._progress(task_id, "dependencies", 0)
            self._installer.install(directory, proxy=self._proxy_for(row.plugin_id))
            (directory / INSTALL_MARKER).unlink(missing_ok=True)
            discovered = self._supervisor.register(directory, built_in=False)
            self._plugins.add(discovered)
            self._mark_installed(row.plugin_id)
            if self._plugins.is_enabled(row.plugin_id):
                self._plugins.start_plugin(row.plugin_id)
        except PluginStartFailed as exc:
            logger.exception("重建依赖后插件 %s 启动失败", row.plugin_id)
            error = exc.message
            result["error_code"] = "plugin_start_failed"
        except Exception as exc:
            logger.exception("重建插件 %s 依赖失败", row.plugin_id)
            error = str(exc)
            result["error_code"] = "dependency_install_failed"
            self._mark_missing(row.plugin_id, error)
        finally:
            try:
                self._finish(task_id, error=error, result=result)
            finally:
                self._release()

    # ---- 数据访问 ----

    def _create_task(
        self,
        kind: str,
        plugin_id: str,
        version: str,
        *,
        from_version: str | None = None,
        created_by: int | None = None,
    ) -> int:
        result: dict[str, Any] = {"plugin_id": plugin_id, "version": version}
        if from_version is not None:
            result["from_version"] = from_version
        with self._database.session() as connection:
            task = TaskRepository(connection, self._clock).create(
                kind, total=_PHASE_TOTAL, created_by=created_by, result=result
            )
        return task.id

    def _progress(self, task_id: int, phase: str, done: int) -> None:
        with self._database.session() as connection:
            TaskRepository(connection, self._clock).progress(task_id, phase, done, _PHASE_TOTAL)

    def _finish(self, task_id: int, *, error: str | None, result: dict[str, Any]) -> None:
        with self._database.session() as connection:
            TaskRepository(connection, self._clock).finish(task_id, error=error, result=result)

    def _install_rows(self) -> list[_InstallRow]:
        with self._database.read_session() as connection:
            rows = connection.execute(
                "SELECT plugin_id, source, repository_id, version, previous_version, sha256, "
                "status, error FROM plugin_install ORDER BY plugin_id"
            ).fetchall()
        return [
            _InstallRow(
                plugin_id=str(row["plugin_id"]),
                source=str(row["source"]),
                repository_id=row["repository_id"],
                version=str(row["version"]),
                previous_version=row["previous_version"],
                sha256=str(row["sha256"]),
                status=str(row["status"]),
                error=row["error"],
            )
            for row in rows
        ]

    def _install_row(self, plugin_id: str) -> _InstallRow | None:
        for row in self._install_rows():
            if row.plugin_id == plugin_id:
                return row
        return None

    def _write_install_row(
        self,
        package: _Package,
        *,
        source: str,
        repository_id: int | None,
        previous_version: str | None,
    ) -> None:
        now = to_iso(self._clock.now())
        with self._database.session() as connection:
            connection.execute(
                "INSERT INTO plugin_install "
                "(plugin_id, source, repository_id, version, previous_version, sha256, "
                " status, error, installed_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'installed', NULL, ?, ?) "
                "ON CONFLICT (plugin_id) DO UPDATE SET "
                " source = excluded.source, repository_id = excluded.repository_id, "
                " version = excluded.version, previous_version = excluded.previous_version, "
                " sha256 = excluded.sha256, status = 'installed', error = NULL, "
                " updated_at = excluded.updated_at",
                (
                    package.plugin_id,
                    source,
                    repository_id,
                    package.version,
                    previous_version,
                    package.sha256,
                    now,
                    now,
                ),
            )

    def _mark_installed(self, plugin_id: str) -> None:
        self._set_status(plugin_id, "installed", None)

    def _mark_missing(self, plugin_id: str, message: str) -> None:
        self._set_status(plugin_id, "missing", message)

    def _mark_failed(self, plugin_id: str, message: str) -> None:
        self._set_status(plugin_id, "failed", message)

    def _set_status(self, plugin_id: str, status: str, error: str | None) -> None:
        with self._database.session() as connection:
            connection.execute(
                "UPDATE plugin_install SET status = ?, error = ?, updated_at = ? "
                "WHERE plugin_id = ?",
                (status, error, to_iso(self._clock.now()), plugin_id),
            )

    # ---- 路径和清理 ----

    def _version_dir(self, plugin_id: str, version: str) -> Path:
        if PLUGIN_ID.fullmatch(plugin_id) is None or VERSION.fullmatch(version) is None:
            raise RuntimeError("插件 id 或版本号不合法")
        return self._root / plugin_id / version

    def _cleanup_directories(self, rows: list[_InstallRow], valid_ids: set[str]) -> None:
        keep = {row.plugin_id: {row.version, row.previous_version} for row in rows}
        if not self._root.is_dir():
            return
        for directory in self._root.iterdir():
            if not directory.is_dir() or directory.name.startswith("."):
                continue
            if directory.name not in valid_ids:
                shutil.rmtree(directory, ignore_errors=True)
                continue
            versions = keep.get(directory.name, set())
            for child in directory.iterdir():
                if not child.is_dir():
                    continue
                if child.name not in versions or (child / INSTALL_MARKER).exists():
                    shutil.rmtree(child, ignore_errors=True)

    def _cleanup_uploads(self) -> None:
        for directory in (self._uploads, self._downloads):
            directory.mkdir(parents=True, exist_ok=True)
            for path in directory.iterdir():
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)

    def _prune_versions(self, plugin_id: str, keep: set[str]) -> None:
        base = self._root / plugin_id
        if not base.is_dir():
            return
        try:
            for child in list(base.iterdir()):
                if child.is_dir() and child.name not in keep:
                    shutil.rmtree(child, ignore_errors=True)
        except OSError:
            # 清理旧版本失败不影响已经生效的安装
            logger.warning("清理插件 %s 的旧版本失败", plugin_id, exc_info=True)

    def _newest_local_version(self, plugin_id: str, current: str) -> str | None:
        base = self._root / plugin_id
        if not base.is_dir():
            return None
        candidates = [
            child.name
            for child in base.iterdir()
            if child.is_dir()
            and VERSION.fullmatch(child.name) is not None
            and not (child / INSTALL_MARKER).exists()
        ]
        newer: list[str] = []
        for version in candidates:
            try:
                if compare_versions(version, current) > 0:
                    newer.append(version)
            except ValueError:
                continue
        if not newer:
            return None
        return max(newer, key=_version_key)

    def _adopt_version(self, row: _InstallRow, version: str) -> _InstallRow:
        with self._database.session() as connection:
            connection.execute(
                "UPDATE plugin_install SET version = ?, previous_version = NULL, updated_at = ? "
                "WHERE plugin_id = ?",
                (version, to_iso(self._clock.now()), row.plugin_id),
            )
        return _InstallRow(
            plugin_id=row.plugin_id,
            source=row.source,
            repository_id=row.repository_id,
            version=version,
            previous_version=None,
            sha256=row.sha256,
            status=row.status,
            error=row.error,
        )

    def _detach(self, plugin_id: str) -> None:
        """把插件从运行时摘掉；不动数据库和目录。"""
        with suppress(PluginNotInstalled):
            self._supervisor.unregister(plugin_id)
        self._plugins.remove(plugin_id)

    def _register_or_replace(self, plugin_id: str, directory: Path) -> None:
        if plugin_id in self._plugins.installed_ids():
            self._supervisor.stop(plugin_id)
            discovered = self._supervisor.replace(plugin_id, directory)
            self._plugins.replace(plugin_id, discovered)
            return
        discovered = self._supervisor.register(directory, built_in=False)
        if discovered.manifest is None or discovered.plugin_id != plugin_id:
            with suppress(PluginNotInstalled):
                self._supervisor.unregister(discovered.plugin_id)
            raise RuntimeError(f"插件目录的 manifest 与安装记录不一致：{directory}")
        self._plugins.add(discovered)

    def _venv_matches_host(self, directory: Path) -> bool:
        config = directory / ".venv" / "pyvenv.cfg"
        try:
            lines = config.read_text(encoding="utf-8").splitlines()
        except OSError:
            return False
        prefix = f"{sys.version_info.major}.{sys.version_info.minor}"
        for line in lines:
            key, _, value = line.partition("=")
            if key.strip() == "version_info":
                # pyvenv.cfg 里是 3.13.1 这样的完整版本，只比较主次版本
                return ".".join(value.strip().split(".")[:2]) == prefix
        return False

    def _needs_dependency_rebuild(self, row: _InstallRow) -> bool:
        directory = self._version_dir(row.plugin_id, row.version)
        if not directory.is_dir() or (directory / INSTALL_MARKER).exists():
            return False
        if self._venv_matches_host(directory):
            return False
        try:
            load_manifest(directory)
        except (PluginManifestError, OSError):
            return False
        return True

    def _reinstall_entry(self, row: _InstallRow) -> IndexEntry | None:
        """补装用的索引条目。

        返回 None 表示仓库明确返回了索引但没有可装的版本（标 failed）；仓库暂时拉不到时
        抛 :class:`_RepositoryUnavailable`，调用方保持 missing 以便以后重试。
        """
        if row.repository_id is None:
            return None
        repository = self._repositories.get(row.repository_id)
        if repository is None or repository["status"] == "unsupported":
            return None
        chosen = self._pick_reinstall_entry(row)
        if chosen is not None or repository["status"] == "ok":
            return chosen
        # 缓存里没有可用条目且最近没有成功拉取过（刚启动 pending，或拉取失败）：先刷新一次
        try:
            repository = self._repositories.refresh(row.repository_id)
        except Exception as exc:
            raise _RepositoryUnavailable(str(exc)) from exc
        if repository["status"] == "unsupported":
            return None
        if repository["status"] != "ok":
            raise _RepositoryUnavailable(str(repository.get("error") or repository["status"]))
        return self._pick_reinstall_entry(row)

    def _pick_reinstall_entry(self, row: _InstallRow) -> IndexEntry | None:
        assert row.repository_id is not None
        entries = self._repositories.entries(row.repository_id, row.plugin_id)
        if not entries:
            return None
        exact = next((entry for entry in entries if entry.version == row.version), None)
        if exact is not None and is_compatible(exact):
            return exact
        return best_compatible(entries)

    # ---- 上传辅助 ----

    def _upload_zip(self, upload_id: str | None) -> Path:
        if upload_id is None:
            raise _TaskError("package_invalid", "上传记录不存在")
        self._read_upload(upload_id)
        return self._uploads / f"{upload_id}.zip"

    def _read_upload(self, upload_id: str) -> dict[str, Any]:
        if not upload_id.startswith("u_") or not upload_id[2:].isalnum():
            raise AppError(404, "upload_not_found", "上传记录不存在")
        path = self._uploads / f"{upload_id}.json"
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise AppError(404, "upload_not_found", "上传记录不存在") from None
        if not isinstance(record, dict):
            raise AppError(404, "upload_not_found", "上传记录不存在")
        expires_at = record.get("expires_at")
        try:
            expired = not isinstance(expires_at, str) or self._clock.now() > datetime.fromisoformat(
                expires_at
            )
        except (TypeError, ValueError):
            expired = True
        if expired:
            self._upload_paths(upload_id)
            (self._uploads / f"{upload_id}.zip").unlink(missing_ok=True)
            (self._uploads / f"{upload_id}.json").unlink(missing_ok=True)
            raise AppError(404, "upload_not_found", "上传记录已过期")
        return record

    def _upload_paths(self, upload_id: str) -> None:
        for suffix in (".zip", ".json"):
            path = self._uploads / f"{upload_id}{suffix}"
            if not path.resolve().is_relative_to(self._uploads.resolve()):
                raise AppError(404, "upload_not_found", "上传记录不存在")

    def _delete_upload(self, upload_id: str | None) -> None:
        if upload_id is None:
            return
        try:
            self._read_upload(upload_id)
        except AppError:
            return
        try:
            (self._uploads / f"{upload_id}.zip").unlink(missing_ok=True)
            (self._uploads / f"{upload_id}.json").unlink(missing_ok=True)
        except OSError:
            # 安装已经完成，残留的上传文件由下次启动清理
            logger.warning("删除上传文件 %s 失败", upload_id, exc_info=True)

    def _check_uploaded_package(self, directory: Path) -> PluginManifest:
        try:
            return check_package(directory)
        except PluginManifestError as exc:
            raise AppError(422, "package_invalid", str(exc)) from exc

    def _check_upload_conflict(self, manifest: dict[str, Any] | PluginManifest) -> None:
        plugin_id = manifest.id if isinstance(manifest, PluginManifest) else str(manifest.get("id"))
        if plugin_id.startswith("builtin.") or self._plugins.is_built_in(plugin_id):
            raise AppError(409, "plugin_conflict", "系统插件的 id 保留给镜像，不能上传")
        row = self._install_row(plugin_id)
        if row is None:
            if self._plugins.manifest(plugin_id) is not None:
                raise AppError(409, "plugin_conflict", "这个插件 id 已经被占用")
            return
        if row.source == "repository":
            raise AppError(409, "plugin_conflict", "插件已由仓库安装，需要先卸载")
        if isinstance(manifest, PluginManifest):
            newer = compare_versions(manifest.version, row.version)
            # 安装记录不是 installed（目录缺失、恢复失败等）时允许同版本重传来修复
            repair = newer == 0 and row.status != "installed"
            if newer < 0 or (newer == 0 and not repair):
                raise AppError(409, "plugin_version_not_newer", "上传的版本必须比已安装的版本更高")

    # ---- 冲突和占用 ----

    def _check_install_conflict(self, plugin_id: str) -> None:
        if plugin_id.startswith("builtin.") or self._plugins.is_built_in(plugin_id):
            raise AppError(409, "plugin_conflict", "系统插件的 id 保留给镜像")
        if (
            self._install_row(plugin_id) is not None
            or self._plugins.manifest(plugin_id) is not None
        ):
            raise AppError(409, "plugin_conflict", "这个插件 id 已经安装")

    def _plugin_in_use(self, plugin_id: str) -> bool:
        with self._database.read_session() as connection:
            row = connection.execute(
                "SELECT 1 FROM resource_source WHERE plugin_id = ? LIMIT 1", (plugin_id,)
            ).fetchone()
        return row is not None

    def _installed_provides(self, plugin_id: str) -> tuple[str, ...]:
        manifest = self._plugins.manifest(plugin_id)
        return () if manifest is None else manifest.provides

    def _proxy_for(self, plugin_id: str) -> str | None:
        return effective_proxy(self._database, plugin_id)

    # ---- 操作锁 ----

    def _try_acquire(self) -> bool:
        return self._lock.acquire(blocking=False)

    def _acquire(self) -> None:
        if not self._try_acquire():
            raise AppError(409, "plugin_operation_running", "另一个插件安装或卸载正在进行")

    def _release(self) -> None:
        try:
            self._lock.release()
        except RuntimeError:
            logger.exception("释放插件操作锁失败")


def _package_matches(package: _Package, manifest: PluginManifest) -> bool:
    return (
        manifest.id == package.plugin_id
        and manifest.version == package.version
        and manifest.sdk == package.sdk
        and sorted(manifest.provides) == sorted(package.provides)
        and sorted(manifest.capabilities) == sorted(package.capabilities)
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while True:
            chunk = file.read(64 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _version_key(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def _remove_empty_parent(destination: Path) -> None:
    parent = destination.parent
    try:
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass
