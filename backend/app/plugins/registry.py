"""插件的发现、启用状态和生命周期。

插件由 :class:`PluginSupervisor` 从宿主配置的插件目录发现并运行在独立进程里；这里只保存
数据库里的启用状态、代理设置，并按 ADR 0021 的语义提供按需启动的 provider 代理。
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from iris_plugin_sdk import (
    AdminUiProvider,
    OnlineSubtitleProvider,
    PluginManifest,
    PluginPage,
    ResourceProvider,
    SubtitleProvider,
)

from app.clock import Clock, to_iso
from app.db import Database
from app.errors import AppError
from app.plugins import library_plugins
from app.plugins.remote import providers_for
from app.plugins.supervisor import (
    DiscoveredPlugin,
    PluginRuntimeError,
    PluginSupervisor,
)
from app.plugins.supervisor import (
    PluginNotInstalled as SupervisorPluginNotInstalled,
)
from app.settings import mask_proxy

if TYPE_CHECKING:
    from app.plugins.packages import PluginPackages
    from app.plugins.repositories import PluginRepositories

logger = logging.getLogger(__name__)

# 类里有 list 方法会遮住内置 list，回调类型在模块级定义
ResourceChangedHandler = Callable[[int, list[str]], None]
Locators = list[str]

PluginCategory = Literal["metadata", "subtitle", "resource", "other"]
ProxyMode = Literal["inherit", "none", "custom"]


class PluginStartFailed(AppError):
    """用 supervisor 的错误码生成对外错误，宿主不再自己维护一套启动失败状态。"""

    def __init__(
        self,
        name: str,
        *,
        code: str = "plugin_start_failed",
        detail: str | None = None,
    ) -> None:
        message = f"插件「{name}」启动失败"
        if detail:
            message = f"{message}：{detail}"
        super().__init__(409, code, message)


class PluginNotInstalled(AppError):
    def __init__(self, plugin_id: str) -> None:
        super().__init__(409, "plugin_not_installed", f"插件「{plugin_id}」没有安装，不能启用")


class PluginInstalled(AppError):
    def __init__(self, name: str) -> None:
        super().__init__(409, "plugin_installed", f"插件「{name}」仍然安装，不能删除记录")


class PluginInUse(AppError):
    def __init__(self, name: str) -> None:
        super().__init__(409, "plugin_in_use", f"还有资源来源在使用插件「{name}」，不能删除记录")


@dataclass(frozen=True)
class _Installed:
    manifest: PluginManifest
    directory: Path
    built_in: bool
    providers: Mapping[str, object]


def _plugin_state(
    plugin_id: str,
    enabled: bool,
    installed: set[str],
    supervisor_state: str,
    error: PluginRuntimeError | None,
) -> str:
    if plugin_id not in installed:
        return "not_installed"
    if error is not None or supervisor_state == "failed":
        return "failed"
    if not enabled:
        return "stopped"
    return "running" if supervisor_state in ("running", "starting", "restarting") else "stopped"


def plugin_category(provides: Iterable[str]) -> PluginCategory:
    """按提供的能力推导插件分类；优先级为元数据、字幕、资源来源、其他。"""
    capabilities = set(provides)
    # 图片获取器和 Emby 一样归在元数据里
    if any(capability.startswith(("metadata.", "image.")) for capability in capabilities):
        return "metadata"
    if any(capability.startswith("subtitle.") for capability in capabilities):
        return "subtitle"
    if "resource.source" in capabilities:
        return "resource"
    return "other"


class PluginRegistry:
    """管理插件发现、启停状态和生命周期，并按能力解析已启动插件的 provider。

    启用状态持久化在 plugin 表；运行状态由 supervisor 持有。宿主只调用已启动插件的 provider，
    插件实现全部在子进程里。
    """

    def __init__(self, database: Database, clock: Clock, supervisor: PluginSupervisor) -> None:
        self._database = database
        self._clock = clock
        self._supervisor = supervisor
        self._lock = threading.RLock()
        self._plugins: dict[str, _Installed] = {}
        self._repositories: PluginRepositories | None = None
        self._packages: PluginPackages | None = None
        # 发现失败的插件进不了 _plugins，但列表里仍要显示发现阶段错误。
        discovered = supervisor.discover()
        self._discovered: dict[str, DiscoveredPlugin] = {
            item.plugin_id: item for item in discovered
        }
        # 通过校验的 resource.changed 交给定向同步；在 compose 根装配完成后接入
        self._on_resource_changed: ResourceChangedHandler | None = None
        for item in discovered:
            manifest = item.manifest
            if manifest is None:
                continue
            self._plugins[manifest.id] = _Installed(
                manifest=manifest,
                directory=item.directory,
                built_in=item.built_in,
                providers=providers_for(
                    partial(supervisor.client, manifest.id), manifest, item.directory
                ),
            )
        self._sync_manifests()

    def attach_repositories(self, repositories: PluginRepositories) -> None:
        """目录和更新状态由仓库服务提供；构造顺序要求注册中心先发现系统插件。"""
        self._repositories = repositories

    def attach_packages(self, packages: PluginPackages) -> None:
        """安装中的插件在列表里显示 installing。"""
        self._packages = packages

    # ---- 发现与落库 ----

    def _sync_manifests(self) -> None:
        """把发现的 manifest 同步到数据库，保留管理员设置的启用状态和代理。"""
        for item in self._installed():
            self._sync_manifest(item)

    def _sync_manifest(self, item: _Installed) -> None:
        manifest = item.manifest
        now = to_iso(self._clock.now())
        with self._database.session() as connection:
            connection.execute(
                """
                INSERT INTO plugin
                    (id, name, version, sdk_version, provides, requires, trust,
                     built_in, enabled, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT (id) DO UPDATE SET
                    name = excluded.name,
                    version = excluded.version,
                    sdk_version = excluded.sdk_version,
                    provides = excluded.provides,
                    requires = excluded.requires,
                    trust = excluded.trust,
                    built_in = excluded.built_in,
                    updated_at = excluded.updated_at
                WHERE (plugin.name, plugin.version, plugin.sdk_version, plugin.provides,
                       plugin.requires, plugin.trust, plugin.built_in)
                    IS NOT (excluded.name, excluded.version, excluded.sdk_version,
                            excluded.provides, excluded.requires, excluded.trust,
                            excluded.built_in)
                """,
                (
                    manifest.id,
                    manifest.name,
                    manifest.version,
                    manifest.sdk,
                    json.dumps(manifest.provides, ensure_ascii=False),
                    json.dumps(manifest.capabilities, ensure_ascii=False),
                    "built_in" if item.built_in else "external",
                    int(item.built_in),
                    now,
                ),
            )

    # ---- 动态增删换（仓库和本地安装） ----

    def add(self, discovered: DiscoveredPlugin) -> None:
        """把已经注册到 supervisor 的插件加入注册中心；manifest 不合法时只记录。"""
        manifest = discovered.manifest
        item: _Installed | None = None
        with self._lock:
            self._discovered[discovered.plugin_id] = discovered
            if manifest is not None:
                item = _Installed(
                    manifest=manifest,
                    directory=discovered.directory,
                    built_in=discovered.built_in,
                    providers=providers_for(
                        partial(self._supervisor.client, manifest.id),
                        manifest,
                        discovered.directory,
                    ),
                )
                self._plugins[manifest.id] = item
        if item is not None:
            self._sync_manifest(item)

    def replace(self, plugin_id: str, discovered: DiscoveredPlugin) -> None:
        """换掉已停止插件的目录：重建 providers，因为 admin.ui 绑定了旧的 ui_root。"""
        manifest = discovered.manifest
        if manifest is None:
            raise ValueError("替换插件时 manifest 不能为空")
        if manifest.id != plugin_id:
            raise ValueError(f"manifest.id {manifest.id} 与已安装的 {plugin_id} 不一致")
        item = _Installed(
            manifest=manifest,
            directory=discovered.directory,
            built_in=discovered.built_in,
            providers=providers_for(
                partial(self._supervisor.client, plugin_id), manifest, discovered.directory
            ),
        )
        with self._lock:
            self._plugins[plugin_id] = item
            self._discovered[plugin_id] = discovered
        self._sync_manifest(item)

    def remove(self, plugin_id: str) -> None:
        with self._lock:
            self._plugins.pop(plugin_id, None)
            self._discovered.pop(plugin_id, None)

    def built_in_ids(self) -> set[str]:
        with self._lock:
            ids = {plugin_id for plugin_id, item in self._plugins.items() if item.built_in}
            ids.update(plugin_id for plugin_id, item in self._discovered.items() if item.built_in)
        with self._database.read_session() as connection:
            ids.update(
                str(row[0])
                for row in connection.execute("SELECT id FROM plugin WHERE built_in = 1")
            )
        return ids

    def start_plugin(self, plugin_id: str) -> None:
        """启动单个插件；失败时抛 PluginStartFailed。"""
        self._start(plugin_id)

    def installed_ids(self) -> set[str]:
        with self._lock:
            return set(self._plugins)

    # ---- 生命周期 ----

    def start_enabled(self) -> None:
        """启动所有已启用的插件；单个插件启动失败只记录日志，不影响宿主启动。"""
        for item in self._installed():
            if not self.is_enabled(item.manifest.id):
                continue
            try:
                self._start(item.manifest.id)
            except PluginStartFailed:
                continue

    def shutdown(self) -> None:
        self._supervisor.shutdown()

    def set_enabled(self, plugin_id: str, enabled: bool) -> bool:
        """先启停插件再持久化：启动失败时抛出 PluginStartFailed，启用状态不变。"""
        item = self._plugin(plugin_id)
        if enabled:
            if item is None:
                with self._lock:
                    discovered = self._discovered.get(plugin_id)
                if discovered is not None and discovered.manifest is None:
                    error = discovered.error
                    raise PluginStartFailed(
                        self._display_name(plugin_id),
                        code=error.code if error is not None else "plugin_manifest_invalid",
                        detail=error.message if error is not None else None,
                    )
                raise PluginNotInstalled(plugin_id)
            self._start(plugin_id)
        with self._database.session() as connection:
            cursor = connection.execute(
                "UPDATE plugin SET enabled = ?, updated_at = ? WHERE id = ?",
                (int(enabled), to_iso(self._clock.now()), plugin_id),
            )
            found = cursor.rowcount > 0
            if enabled and found and item is None:
                raise PluginNotInstalled(plugin_id)
        if not enabled and item is not None:
            self._stop(plugin_id)
        return found

    def delete(self, plugin_id: str) -> bool:
        """删除未安装插件的旧记录；仍有资源来源引用时拒绝。"""
        item = self._plugin(plugin_id)
        if item is not None:
            raise PluginInstalled(item.manifest.name)
        with self._database.session() as connection:
            row = connection.execute(
                "SELECT name FROM plugin WHERE id = ?", (plugin_id,)
            ).fetchone()
            if row is None:
                return False
            used = connection.execute(
                "SELECT 1 FROM resource_source WHERE plugin_id = ? LIMIT 1", (plugin_id,)
            ).fetchone()
            if used is not None:
                raise PluginInUse(str(row[0]))
            connection.execute("DELETE FROM plugin WHERE id = ?", (plugin_id,))
            connection.execute("DELETE FROM library_plugin WHERE plugin_id = ?", (plugin_id,))
        return True

    def _start(self, plugin_id: str) -> None:
        item = self._plugins.get(plugin_id)
        name = item.manifest.name if item is not None else self._display_name(plugin_id)
        try:
            self._supervisor.start(plugin_id)
        except PluginRuntimeError as exc:
            logger.exception("插件 %s 启动失败", plugin_id)
            raise PluginStartFailed(name, code=exc.code, detail=exc.message) from exc
        except Exception as exc:
            logger.exception("插件 %s 启动失败", plugin_id)
            raise PluginStartFailed(name, detail=str(exc)) from exc
        logger.info("插件 %s 已启动", plugin_id)

    def _stop(self, plugin_id: str) -> None:
        try:
            self._supervisor.stop(plugin_id)
        except SupervisorPluginNotInstalled:
            return
        logger.info("插件 %s 已停止", plugin_id)

    # ---- provider ----

    def providers(self, plugin_id: str) -> Mapping[str, object]:
        """已启用插件的全部 provider；未启动的按需启动。"""
        item = self._plugin(plugin_id)
        if not self.is_enabled(plugin_id):
            # 备份恢复可能把数据库改成停用，但进程里的插件还在运行，先释放资源
            if item is not None:
                self._stop_disabled(plugin_id)
            return {}
        if item is None:
            return {}
        self._ensure_running(plugin_id, item)
        return dict(item.providers)

    def provider(self, plugin_id: str, capability: str) -> object | None:
        return self.providers(plugin_id).get(capability)

    def running_provider(self, plugin_id: str, capability: str) -> object | None:
        """已启动插件的 provider，不触发按需启动；给未认证的请求（如插件静态页面）用。"""
        if not self.is_enabled(plugin_id):
            return None
        item = self._plugin(plugin_id)
        if item is None or not self._supervisor.is_running(plugin_id):
            return None
        return item.providers.get(capability)

    def admin_ui(self, plugin_id: str) -> AdminUiProvider | None:
        return cast(AdminUiProvider | None, self.provider(plugin_id, "admin.ui"))

    def resource_provider(self, plugin_id: str) -> ResourceProvider | None:
        return cast(ResourceProvider | None, self.provider(plugin_id, "resource.source"))

    def subtitle_provider(self, plugin_id: str) -> SubtitleProvider | None:
        return cast(SubtitleProvider | None, self.provider(plugin_id, "subtitle.search"))

    def subtitle_downloaders(
        self, library_id: int | None = None
    ) -> list[tuple[str, str, OnlineSubtitleProvider]]:
        """已启用、声明 subtitle.download 的插件。

        不给 library_id 时返回全部、按插件 id 排序；给了就只返回资料库字幕下载器列表里勾选的，
        按列表顺序。
        """
        found: dict[str, tuple[str, str, OnlineSubtitleProvider]] = {}
        for manifest in self.manifests():
            if "subtitle.download" not in manifest.provides:
                continue
            if not self.is_enabled(manifest.id):
                continue
            provider = self.provider(manifest.id, "subtitle.download") or self.provider(
                manifest.id, "subtitle.search"
            )
            if provider is None:
                continue
            found[manifest.id] = (
                manifest.id,
                manifest.name,
                cast(OnlineSubtitleProvider, provider),
            )
        if library_id is None:
            return [found[plugin_id] for plugin_id in sorted(found)]
        candidates = [
            manifest.id for manifest in self.manifests() if "subtitle.download" in manifest.provides
        ]
        with self._database.read_session() as connection:
            ordered = library_plugins.enabled_ids(connection, library_id, "subtitle", candidates)
        return [found[plugin_id] for plugin_id in ordered if plugin_id in found]

    def _ensure_running(self, plugin_id: str, item: _Installed) -> None:
        if self._supervisor.is_running(plugin_id):
            return
        if self._supervisor.state(plugin_id) == "failed":
            error = self._supervisor.error(plugin_id)
            raise PluginStartFailed(
                item.manifest.name,
                code=error.code if error is not None else "plugin_start_failed",
                detail=error.message if error is not None else None,
            )
        self._start(plugin_id)

    def _stop_disabled(self, plugin_id: str) -> None:
        """数据库已停用但进程里还在运行的插件；停用后再确认一次，避免停掉刚重新启用的插件。"""
        try:
            if not self._supervisor.is_running(plugin_id):
                return
        except SupervisorPluginNotInstalled:
            return
        if self.is_enabled(plugin_id):
            return
        self._stop(plugin_id)

    # ---- 查询 ----

    def _installed(self) -> list[_Installed]:
        with self._lock:
            return [self._plugins[plugin_id] for plugin_id in sorted(self._plugins)]

    def manifests(self) -> list[PluginManifest]:
        """当前安装插件的声明，按 ID 排序。"""
        return [item.manifest for item in self._installed()]

    def manifest(self, plugin_id: str) -> PluginManifest | None:
        item = self._plugin(plugin_id)
        return None if item is None else item.manifest

    def is_built_in(self, plugin_id: str) -> bool:
        item = self._plugin(plugin_id)
        if item is not None:
            return item.built_in
        with self._lock:
            discovered = self._discovered.get(plugin_id)
        if discovered is not None and discovered.built_in:
            return True
        # 插件目录暂时缺失时，数据库里的 built_in 仍是系统插件的唯一凭据
        with self._database.read_session() as connection:
            row = connection.execute(
                "SELECT built_in FROM plugin WHERE id = ?", (plugin_id,)
            ).fetchone()
        return row is not None and bool(row[0])

    def _display_name(self, plugin_id: str) -> str:
        item = self._plugin(plugin_id)
        if item is not None:
            return item.manifest.name
        with self._lock:
            discovered = self._discovered.get(plugin_id)
        return discovered.directory.name if discovered is not None else plugin_id

    def pages(self, plugin_id: str) -> tuple[PluginPage, ...]:
        item = self._plugin(plugin_id)
        return () if item is None else item.manifest.pages

    def list(self) -> list[dict[str, Any]]:
        with self._database.read_session() as connection:
            rows = connection.execute(
                "SELECT p.id AS plugin_id, p.name, p.version, p.sdk_version, p.provides, "
                "p.requires, p.trust, p.built_in, p.enabled, p.updated_at, p.proxy_mode, "
                "p.proxy, i.source AS install_source, "
                "i.repository_id AS install_repository_id, "
                "i.version AS install_version, i.status AS install_status, "
                "i.error AS install_error, r.name AS repository_name, r.url AS repository_url "
                "FROM plugin p "
                "LEFT JOIN plugin_install i ON i.plugin_id = p.id "
                "LEFT JOIN plugin_repository r ON r.id = i.repository_id "
                "ORDER BY p.id"
            ).fetchall()
            install_rows = connection.execute(
                "SELECT i.plugin_id, i.source, i.repository_id, i.version, i.status, i.error, "
                "r.name AS repository_name, r.url AS repository_url "
                "FROM plugin_install i "
                "LEFT JOIN plugin_repository r ON r.id = i.repository_id "
                "ORDER BY i.plugin_id"
            ).fetchall()
        with self._lock:
            installed = dict(self._plugins)
            discovered = dict(self._discovered)
        installed_ids = set(installed)
        items: dict[str, dict[str, Any]] = {}
        for row in rows:
            plugin_id = str(row["plugin_id"])
            item = installed.get(plugin_id)
            manifest = None if item is None else item.manifest
            # 分类按当前进程的 manifest 推导；已卸载的旧记录只能看数据库里存的能力
            provides = (
                manifest.provides
                if manifest is not None
                else tuple(json.loads(str(row["provides"])))
            )
            proxy_mode = cast(ProxyMode, row["proxy_mode"])
            supervisor_state = (
                self._supervisor.state(plugin_id) if manifest is not None else "stopped"
            )
            error = self._supervisor.error(plugin_id) if manifest is not None else None
            built_in = bool(row["built_in"])
            install_version = row["install_version"]
            installed_version = str(install_version or row["version"])
            source, repository_id, repository_name = self._source_of(
                plugin_id,
                built_in=built_in,
                install_source=row["install_source"],
                install_repository_id=row["install_repository_id"],
                repository_name=row["repository_name"],
                repository_url=row["repository_url"],
            )
            items[plugin_id] = {
                "id": plugin_id,
                "name": row["name"],
                "version": row["version"],
                "sdk_version": row["sdk_version"],
                "description": manifest.description if manifest is not None else "",
                "category": plugin_category(provides),
                "provides": json.loads(str(row["provides"])),
                "requires": json.loads(str(row["requires"])),
                "trust": row["trust"],
                "built_in": built_in,
                "enabled": bool(row["enabled"]),
                "updated_at": row["updated_at"],
                "state": _plugin_state(
                    plugin_id, bool(row["enabled"]), installed_ids, supervisor_state, error
                ),
                "error": None if error is None else error.message,
                "proxy_mode": proxy_mode,
                "proxy": (
                    mask_proxy(str(row["proxy"]))
                    if proxy_mode == "custom" and row["proxy"] is not None
                    else None
                ),
                # 页面只来自当前安装的 manifest；已卸载插件的旧记录没有页面
                "pages": (
                    [{"id": page.id, "title": page.title} for page in manifest.pages]
                    if manifest is not None
                    else []
                ),
                "source": source,
                "repository_id": repository_id,
                "repository_name": repository_name,
                "available_version": self._available_version(
                    plugin_id, source, installed_version, repository_id
                ),
                "package_status": self._package_status(plugin_id, built_in, row["install_status"]),
                "package_error": row["install_error"],
            }
        # 清单里有、plugin 表里没有的安装记录（例如恢复备份后目录丢失）也要出现在列表里。
        for row in install_rows:
            plugin_id = str(row["plugin_id"])
            if plugin_id in items:
                continue
            source, repository_id, repository_name = self._source_of(
                plugin_id,
                built_in=False,
                install_source=row["source"],
                install_repository_id=row["repository_id"],
                repository_name=row["repository_name"],
                repository_url=row["repository_url"],
            )
            status = self._package_status(plugin_id, False, row["status"])
            items[plugin_id] = {
                "id": plugin_id,
                "name": plugin_id,
                "version": str(row["version"]),
                "sdk_version": "",
                "description": "",
                "category": "other",
                "provides": [],
                "requires": [],
                "trust": "external",
                "built_in": False,
                "enabled": False,
                "updated_at": "",
                "state": "not_installed",
                "error": row["error"],
                "proxy_mode": "inherit",
                "proxy": None,
                "pages": [],
                "source": source,
                "repository_id": repository_id,
                "repository_name": repository_name,
                "available_version": self._available_version(
                    plugin_id, source, str(row["version"]), repository_id
                ),
                "package_status": status,
                "package_error": row["error"],
            }
        # manifest 不合法的插件没有数据库记录，也要出现在列表里并带上发现阶段错误。
        for plugin_id, entry in discovered.items():
            if entry.manifest is not None or plugin_id in installed:
                continue
            error = entry.error
            built_in = entry.built_in
            items[plugin_id] = {
                "id": plugin_id,
                "name": entry.directory.name,
                "version": "",
                "sdk_version": "",
                "description": "",
                "category": "other",
                "provides": [],
                "requires": [],
                "trust": "built_in" if built_in else "external",
                "built_in": built_in,
                "enabled": False,
                "updated_at": "",
                "state": "failed",
                "error": None if error is None else error.message,
                "proxy_mode": "inherit",
                "proxy": None,
                "pages": [],
                "source": "system" if built_in else "local",
                "repository_id": None,
                "repository_name": None,
                "available_version": None,
                "package_status": "ok" if built_in else "missing",
                "package_error": None,
            }
        return [items[plugin_id] for plugin_id in sorted(items)]

    def _source_of(
        self,
        plugin_id: str,
        *,
        built_in: bool,
        install_source: Any,
        install_repository_id: Any,
        repository_name: Any,
        repository_url: Any,
    ) -> tuple[Literal["system", "repository", "local"], int | None, str | None]:
        if built_in:
            return "system", None, None
        if install_source == "repository" and isinstance(install_repository_id, int):
            name = str(repository_name) if repository_name else None
            if name is None and repository_url:
                name = str(repository_url)
            return "repository", install_repository_id, name
        return "local", None, None

    def _available_version(
        self,
        plugin_id: str,
        source: str,
        installed_version: str,
        repository_id: int | None,
    ) -> str | None:
        if source != "repository" or self._repositories is None:
            return None
        return self._repositories.available_version(plugin_id, installed_version, repository_id)

    def _package_status(
        self, plugin_id: str, built_in: bool, install_status: Any
    ) -> Literal["ok", "installing", "missing", "failed"]:
        if built_in:
            return "ok"
        if self._packages is not None:
            status = self._packages.package_status(plugin_id)
            if status is not None:
                return status
        if install_status == "installed":
            return "ok"
        if install_status == "failed":
            return "failed"
        return "missing"

    def proxy_config(self, plugin_id: str) -> tuple[ProxyMode, str | None] | None:
        """插件当前的代理模式和原始地址（未打码）；记录不存在时返回 None。"""
        with self._database.read_session() as connection:
            row = connection.execute(
                "SELECT proxy_mode, proxy FROM plugin WHERE id = ?", (plugin_id,)
            ).fetchone()
        if row is None:
            return None
        return cast(ProxyMode, row[0]), (str(row[1]) if row[1] is not None else None)

    def set_proxy(self, plugin_id: str, mode: ProxyMode, proxy: str | None) -> bool:
        """保存插件的代理模式；非自定义模式时 proxy 必须为 None，由调用方清空。"""
        with self._database.session() as connection:
            cursor = connection.execute(
                "UPDATE plugin SET proxy_mode = ?, proxy = ?, updated_at = ? WHERE id = ?",
                (mode, proxy, to_iso(self._clock.now()), plugin_id),
            )
            found = cursor.rowcount > 0
        if found:
            self.config_changed(plugin_id)
        return found

    def config_changed(self, plugin_id: str) -> None:
        self._supervisor.config_changed(plugin_id)

    def config_changed_all(self) -> None:
        self._supervisor.config_changed_all()

    def set_resource_changed_handler(self, handler: ResourceChangedHandler) -> None:
        self._on_resource_changed = handler

    def resource_changed(self, plugin_id: str, source_id: int, locators: Locators) -> None:
        """校验 resource.changed 的来源归属和插件能力，通过后交给变更处理器。

        来源不存在、不属于发通知的插件，或插件没有声明 changes 能力时记 warning 并丢弃。
        """
        with self._database.read_session() as connection:
            row = connection.execute(
                "SELECT plugin_id FROM resource_source WHERE id = ?", (source_id,)
            ).fetchone()
        if row is None:
            logger.warning("来源 %s 不存在，丢弃插件 %s 的 resource.changed", source_id, plugin_id)
            return
        owner = str(row[0])
        if owner != plugin_id:
            logger.warning(
                "来源 %s 属于插件 %s，丢弃插件 %s 的 resource.changed",
                source_id,
                owner,
                plugin_id,
            )
            return
        if self.manifest(plugin_id) is None:
            logger.warning("插件 %s 不是已安装插件，丢弃 resource.changed", plugin_id)
            return
        provider = self.running_provider(plugin_id, "resource.source")
        if provider is None:
            logger.warning("插件 %s 没有运行中的资源来源，丢弃 resource.changed", plugin_id)
            return
        if "changes" not in cast(ResourceProvider, provider).capabilities:
            logger.warning("插件 %s 没有声明 changes 能力，丢弃 resource.changed", plugin_id)
            return
        if self._on_resource_changed is not None:
            self._on_resource_changed(source_id, locators)

    def is_enabled(self, plugin_id: str) -> bool:
        with self._database.read_session() as connection:
            row = connection.execute(
                "SELECT enabled FROM plugin WHERE id = ?", (plugin_id,)
            ).fetchone()
        return row is not None and bool(row[0])

    def _plugin(self, plugin_id: str) -> _Installed | None:
        with self._lock:
            return self._plugins.get(plugin_id)
