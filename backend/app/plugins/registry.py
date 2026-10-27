"""插件的发现、启用状态和生命周期。

插件由 :class:`PluginSupervisor` 从宿主配置的插件目录发现并运行在独立进程里；这里只保存
数据库里的启用状态、代理设置，并按 ADR 0021 的语义提供按需启动的 provider 代理。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Literal, cast

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
        self._plugins: dict[str, _Installed] = {}
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

    # ---- 发现与落库 ----

    def _sync_manifests(self) -> None:
        """把发现的 manifest 同步到数据库，保留管理员设置的启用状态和代理。"""
        now = to_iso(self._clock.now())
        with self._database.session() as connection:
            for item in self._installed():
                manifest = item.manifest
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
        discovered = self._discovered.get(plugin_id)
        return discovered is not None and discovered.built_in

    def _display_name(self, plugin_id: str) -> str:
        item = self._plugin(plugin_id)
        if item is not None:
            return item.manifest.name
        discovered = self._discovered.get(plugin_id)
        return discovered.directory.name if discovered is not None else plugin_id

    def pages(self, plugin_id: str) -> tuple[PluginPage, ...]:
        item = self._plugin(plugin_id)
        return () if item is None else item.manifest.pages

    def list(self) -> list[dict[str, Any]]:
        with self._database.read_session() as connection:
            rows = connection.execute(
                "SELECT id, name, version, sdk_version, provides, requires, trust, "
                "built_in, enabled, updated_at, proxy_mode, proxy "
                "FROM plugin ORDER BY id"
            ).fetchall()
        installed = self._plugins
        installed_ids = set(installed)
        items: dict[str, dict[str, Any]] = {}
        for row in rows:
            plugin_id = str(row[0])
            item = installed.get(plugin_id)
            manifest = None if item is None else item.manifest
            # 分类按当前进程的 manifest 推导；已卸载的旧记录只能看数据库里存的能力
            provides = manifest.provides if manifest is not None else tuple(json.loads(row[4]))
            proxy_mode = cast(ProxyMode, row[10])
            supervisor_state = (
                self._supervisor.state(plugin_id) if manifest is not None else "stopped"
            )
            error = self._supervisor.error(plugin_id) if manifest is not None else None
            items[plugin_id] = {
                "id": plugin_id,
                "name": row[1],
                "version": row[2],
                "sdk_version": row[3],
                "description": manifest.description if manifest is not None else "",
                "category": plugin_category(provides),
                "provides": json.loads(row[4]),
                "requires": json.loads(row[5]),
                "trust": row[6],
                "built_in": bool(row[7]),
                "enabled": bool(row[8]),
                "updated_at": row[9],
                "state": _plugin_state(
                    plugin_id, bool(row[8]), installed_ids, supervisor_state, error
                ),
                "error": None if error is None else error.message,
                "proxy_mode": proxy_mode,
                "proxy": (
                    mask_proxy(str(row[11]))
                    if proxy_mode == "custom" and row[11] is not None
                    else None
                ),
                # 页面只来自当前安装的 manifest；已卸载插件的旧记录没有页面
                "pages": (
                    [{"id": page.id, "title": page.title} for page in manifest.pages]
                    if manifest is not None
                    else []
                ),
            }
        # manifest 不合法的插件没有数据库记录，也要出现在列表里并带上发现阶段错误。
        for plugin_id, discovered in self._discovered.items():
            if discovered.manifest is not None or plugin_id in installed:
                continue
            error = discovered.error
            items[plugin_id] = {
                "id": plugin_id,
                "name": discovered.directory.name,
                "version": "",
                "sdk_version": "",
                "description": "",
                "category": "other",
                "provides": [],
                "requires": [],
                "trust": "built_in" if discovered.built_in else "external",
                "built_in": discovered.built_in,
                "enabled": False,
                "updated_at": "",
                "state": "failed",
                "error": None if error is None else error.message,
                "proxy_mode": "inherit",
                "proxy": None,
                "pages": [],
            }
        return [items[plugin_id] for plugin_id in sorted(items)]

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
        return self._plugins.get(plugin_id)
