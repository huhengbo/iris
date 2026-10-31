"""元数据插件的分派点（ADR 0022、0025）。

每个资料库的元数据下载器列表里排第一、勾选的插件负责按片名匹配，新匹配的条目由它刮削；
列表里后面的插件只按外部 ID 补缺。条目记下负责它的插件（media_item.provider_id），之后的
刷新、季信息、图片和演员资料都找这个插件，资料库换插件不影响已有条目。插件的调用都经过
BoundProvider / BoundImageSource：设置请求优先级，并把插件抛出的错误统一成带状态码的
AppError（同时保留 SDK 的错误类型，方便同步按类型中止）。
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from iris_plugin_sdk import (
    ImageFetcher,
    ImageLookupRequest,
    MetadataCandidate,
    MetadataImageProvider,
    MetadataKind,
    MetadataNotFound,
    MetadataPeopleProvider,
    MetadataPerson,
    MetadataProvider,
    MetadataRecord,
    MetadataSearchRequest,
    MetadataSeason,
    MetadataUnavailable,
    RemoteImage,
)

from app.db import Database
from app.errors import AppError
from app.plugins import library_plugins
from app.plugins.registry import PluginRegistry
from app.priority import Priority, priority_scope

logger = logging.getLogger(__name__)

# 内置 TMDB 元数据插件的 ID；插件搬出进程后仍然是这个名字
TMDB_PROVIDER = "builtin.tmdb"
# 还没有资料库时默认使用的元数据插件
DEFAULT_PROVIDER = TMDB_PROVIDER
SETTING_KEY = "metadata_provider"
# 能负责匹配的插件必须同时提供这两项能力；只提供 metadata.details 的是补缺插件
REQUIRED_CAPABILITIES = ("metadata.search", "metadata.details")
FILL_CAPABILITIES = ("metadata.details",)


class UnknownMetadataProvider(AppError, MetadataUnavailable):
    """插件没有安装（外置插件被移除）或者不提供元数据。"""

    def __init__(self, plugin_id: str) -> None:
        super().__init__(409, "unknown_metadata_provider", f"元数据插件 {plugin_id} 没有安装")


class MetadataProviderDisabled(AppError, MetadataUnavailable):
    def __init__(self, name: str) -> None:
        super().__init__(
            409, "metadata_provider_disabled", f"元数据插件「{name}」没有启用或启动失败"
        )


class MetadataProviderNotReady(AppError, MetadataUnavailable):
    def __init__(self, message: str) -> None:
        super().__init__(409, "metadata_provider_not_ready", message)


class MetadataProviderNotFound(AppError, MetadataNotFound):
    def __init__(self, message: str) -> None:
        super().__init__(404, "metadata_not_found", message)


class MetadataProviderUnavailable(AppError, MetadataUnavailable):
    def __init__(self, message: str) -> None:
        super().__init__(502, "metadata_unavailable", message)


@dataclass(frozen=True)
class MetadataProblem:
    message: str
    # 管理端处理问题的页面
    action_path: str


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    name: str
    built_in: bool
    enabled: bool
    # 启用时不能请求的原因（例如没填 Key）；停用或可用时为 None
    reason: str | None


def image_key(provider_id: str, source: str) -> str:
    """图片缓存表里的来源键。TMDB 保持原来的图片路径，其他插件加上插件 ID，免得路径撞车。"""
    return source if provider_id == TMDB_PROVIDER else f"{provider_id}:{source}"


def library_provider_id(connection: sqlite3.Connection, library_id: int) -> str:
    """资料库负责匹配的元数据插件：元数据下载器列表里排第一、勾选的那个。"""
    return library_plugins.first_enabled(connection, library_id, "metadata") or DEFAULT_PROVIDER


def save_library_provider(
    connection: sqlite3.Connection, library_id: int, plugin_id: str, now: str
) -> None:
    """把插件设为负责匹配的元数据插件（移到列表第一位并勾选）。"""
    library_plugins.move_first(connection, library_id, "metadata", plugin_id, now)


class BoundProvider:
    """一个已启动的元数据插件。"""

    def __init__(
        self,
        plugin_id: str,
        name: str,
        provider: MetadataProvider,
        images: MetadataImageProvider | None,
        people: MetadataPeopleProvider | None,
    ) -> None:
        self.id = plugin_id
        self.name = name
        self._provider = provider
        self._images = images
        self._people = people

    def unavailable_reason(self) -> str | None:
        try:
            return self._provider.unavailable_reason()
        except Exception:
            logger.exception("元数据插件 %s 检查状态时出错", self.id)
            return f"元数据插件「{self.name}」出错，请查看日志"

    def ensure_ready(self) -> None:
        reason = self.unavailable_reason()
        if reason is not None:
            raise MetadataProviderNotReady(reason)

    def search(
        self, kind: MetadataKind, query: str, priority: Priority, year: int | None = None
    ) -> list[MetadataCandidate]:
        request = MetadataSearchRequest(title=query, year=year, kind=kind)
        try:
            results = self._call(priority, lambda: self._provider.search(request))
        except MetadataNotFound:
            # 有的插件（MetaTube）搜不到时回 404，对搜索来说就是没有结果，不能让同步中止
            return []
        # 候选必须属于这个插件，插件填错时以宿主为准
        return [r for r in results if r.provider_id == self.id and r.remote_id]

    def find(self, kind: MetadataKind, scheme: str, value: str, priority: Priority) -> str | None:
        return self._call(priority, lambda: self._provider.find(kind, scheme, value)) or None

    def details(self, kind: MetadataKind, remote_id: str, priority: Priority) -> MetadataRecord:
        record = self._call(priority, lambda: self._provider.details(remote_id, kind=kind))
        if record.provider_id != self.id or not record.remote_id or not record.title:
            raise MetadataProviderUnavailable(f"元数据插件「{self.name}」返回的条目不完整")
        return record

    def season(self, remote_id: str, number: int, priority: Priority) -> MetadataSeason:
        return self._call(priority, lambda: self._provider.season(remote_id, number))

    def page_url(self, kind: MetadataKind, remote_id: str) -> str | None:
        try:
            return self._provider.page_url(kind, remote_id)
        except Exception:
            logger.exception("元数据插件 %s 生成条目链接时出错", self.id)
            return None

    @property
    def has_images(self) -> bool:
        return self._images is not None

    def image(self, source: str, width: int, priority: Priority) -> bytes:
        images = self._images
        if images is None:
            raise MetadataProviderNotFound(f"元数据插件「{self.name}」不提供图片")
        return self._call(priority, lambda: images.image(source, width))

    @property
    def has_people(self) -> bool:
        return self._people is not None

    def person(self, remote_id: str, priority: Priority) -> MetadataPerson:
        people = self._people
        if people is None:
            raise MetadataProviderNotFound(f"元数据插件「{self.name}」不提供人物资料")
        return self._call(priority, lambda: people.person(remote_id))

    def _call[T](self, priority: Priority, call: Callable[[], T]) -> T:
        with priority_scope(priority):
            try:
                return call()
            except AppError:
                # 内置插件（TMDB）直接抛出带状态码的错误
                raise
            except MetadataNotFound as exc:
                raise MetadataProviderNotFound(exc.message) from exc
            except MetadataUnavailable as exc:
                raise MetadataProviderUnavailable(f"{self.name}：{exc.message}") from exc
            except Exception as exc:
                logger.exception("元数据插件 %s 调用出错", self.id)
                raise MetadataProviderUnavailable(
                    f"元数据插件「{self.name}」出错，请查看日志"
                ) from exc


class BoundImageSource:
    """一个已启动插件提供的图片能力：image.fetch 能列图，metadata.images 只能下载已知来源。"""

    def __init__(
        self,
        plugin_id: str,
        name: str,
        fetcher: ImageFetcher | None,
        images: MetadataImageProvider | None,
    ) -> None:
        self.id = plugin_id
        self.name = name
        self._fetcher = fetcher
        self._images = images

    @property
    def can_list(self) -> bool:
        return self._fetcher is not None

    def unavailable_reason(self) -> str | None:
        if self._fetcher is None:
            return None
        try:
            return self._fetcher.unavailable_reason()
        except Exception:
            logger.exception("图片插件 %s 检查状态时出错", self.id)
            return f"图片插件「{self.name}」出错，请查看日志"

    def images(self, request: ImageLookupRequest, priority: Priority) -> list[RemoteImage]:
        fetcher = self._fetcher
        if fetcher is None:
            return []
        return self._call(priority, lambda: fetcher.images(request))

    def image(self, source: str, width: int, priority: Priority) -> bytes:
        provider: ImageFetcher | MetadataImageProvider | None = self._fetcher or self._images
        if provider is None:
            raise MetadataProviderNotFound(f"图片插件「{self.name}」不提供图片")
        return self._call(priority, lambda: provider.image(source, width))

    def _call[T](self, priority: Priority, call: Callable[[], T]) -> T:
        with priority_scope(priority):
            try:
                return call()
            except AppError:
                raise
            except MetadataNotFound as exc:
                raise MetadataProviderNotFound(exc.message) from exc
            except MetadataUnavailable as exc:
                raise MetadataProviderUnavailable(f"{self.name}：{exc.message}") from exc
            except Exception as exc:
                logger.exception("图片插件 %s 调用出错", self.id)
                raise MetadataProviderUnavailable(
                    f"图片插件「{self.name}」出错，请查看日志"
                ) from exc


class MetadataProviders:
    def __init__(self, database: Database, plugins: PluginRegistry) -> None:
        self._database = database
        self._plugins = plugins

    def installed(self) -> list[ProviderInfo]:
        """可以给资料库选用的元数据插件，内置的排在前面。

        只提供 metadata.details 的补缺插件不能负责匹配，不出现在这个列表里。
        """
        infos = []
        for manifest in self._plugins.manifests():
            if not self.can_match(manifest.id):
                continue
            enabled = self._plugins.is_enabled(manifest.id)
            reason = None
            if enabled:
                try:
                    reason = self.get(manifest.id).unavailable_reason()
                except MetadataProviderDisabled as exc:
                    reason = exc.message
            infos.append(
                ProviderInfo(
                    manifest.id,
                    manifest.name,
                    self._plugins.is_built_in(manifest.id),
                    enabled,
                    reason,
                )
            )
        return sorted(infos, key=lambda info: (not info.built_in, info.name))

    def is_metadata_plugin(self, plugin_id: str) -> bool:
        manifest = self._plugins.manifest(plugin_id)
        return manifest is not None and all(c in manifest.provides for c in FILL_CAPABILITIES)

    def can_match(self, plugin_id: str) -> bool:
        """能负责匹配：同时提供 search 和 details。"""
        manifest = self._plugins.manifest(plugin_id)
        return manifest is not None and all(c in manifest.provides for c in REQUIRED_CAPABILITIES)

    def check_selectable(self, plugin_id: str) -> None:
        """资料库的主元数据插件要已安装、启用，并且能按片名匹配。

        没配置好（例如没填 Key）的可以先选。
        """
        if not self.is_metadata_plugin(plugin_id):
            raise AppError(400, "unknown_metadata_provider", "没有这个元数据插件")
        if not self.can_match(plugin_id):
            raise AppError(
                400,
                "primary_cannot_match",
                f"元数据插件「{self.name(plugin_id)}」只能补缺，不能放在第一位",
            )
        if not self._plugins.is_enabled(plugin_id):
            raise AppError(
                409, "metadata_provider_disabled", f"元数据插件「{self.name(plugin_id)}」没有启用"
            )

    def name(self, plugin_id: str) -> str:
        manifest = self._plugins.manifest(plugin_id)
        return plugin_id if manifest is None else manifest.name

    def get(self, plugin_id: str) -> BoundProvider:
        if not self.is_metadata_plugin(plugin_id):
            raise UnknownMetadataProvider(plugin_id)
        name = self.name(plugin_id)
        # 一次取齐这个插件的 provider，只查一次启用状态
        providers = self._plugins.providers(plugin_id)
        provider = providers.get("metadata.details")
        if provider is None:
            raise MetadataProviderDisabled(name)
        return BoundProvider(
            plugin_id,
            name,
            cast(MetadataProvider, provider),
            cast(MetadataImageProvider | None, providers.get("metadata.images")),
            cast(MetadataPeopleProvider | None, providers.get("metadata.people")),
        )

    def image_source(self, plugin_id: str) -> BoundImageSource | None:
        """按插件 ID 取它提供的图片能力；插件不存在或没有图片能力时返回 None。

        状态检查、错误映射和元数据插件共用，图片下载和补缺选图都走这里。
        """
        manifest = self._plugins.manifest(plugin_id)
        if manifest is None:
            return None
        if "image.fetch" not in manifest.provides and "metadata.images" not in manifest.provides:
            return None
        name = manifest.name
        if not self._plugins.is_enabled(plugin_id):
            raise MetadataProviderDisabled(name)
        providers = self._plugins.providers(plugin_id)
        fetcher = providers.get("image.fetch")
        images = providers.get("metadata.images")
        if fetcher is None and images is None:
            raise MetadataProviderDisabled(name)
        return BoundImageSource(
            plugin_id,
            name,
            cast(ImageFetcher | None, fetcher),
            cast(MetadataImageProvider | None, images),
        )

    def image_fetchers(self, library_id: int) -> list[BoundImageSource]:
        """资料库图片列表里勾选、已启用的图片获取器，按列表顺序。"""
        candidates = [
            manifest.id
            for manifest in self._plugins.manifests()
            if "image.fetch" in manifest.provides
        ]
        with self._database.read_session() as connection:
            ordered = library_plugins.enabled_ids(connection, library_id, "image", candidates)
        fetchers: list[BoundImageSource] = []
        for plugin_id in ordered:
            if plugin_id not in candidates or not self._plugins.is_enabled(plugin_id):
                continue
            try:
                source = self.image_source(plugin_id)
            except AppError:
                continue
            if source is not None and source.can_list:
                fetchers.append(source)
        return fetchers

    def fill_provider_ids(self, library_id: int) -> list[str]:
        """元数据列表里第一个（主插件）之后勾选的插件 ID，按列表顺序。"""
        candidates = [
            manifest.id
            for manifest in self._plugins.manifests()
            if "metadata.details" in manifest.provides
        ]
        with self._database.read_session() as connection:
            primary = library_provider_id(connection, library_id)
            ordered = library_plugins.enabled_ids(connection, library_id, "metadata", candidates)
        start = ordered.index(primary) + 1 if primary in ordered else 0
        return [plugin_id for plugin_id in ordered[start:] if plugin_id != primary]

    def fill_providers(self, library_id: int) -> list[BoundProvider]:
        """可用的补缺插件；停用或启动失败的跳过，调用方可用 fill_provider_ids 记问题。"""
        providers: list[BoundProvider] = []
        for plugin_id in self.fill_provider_ids(library_id):
            try:
                providers.append(self.get(plugin_id))
            except AppError:
                continue
        return providers

    def external_url(self, plugin_id: str, kind: MetadataKind, remote_id: str) -> str | None:
        """条目在插件网站上的页面；插件不可用或不提供时为 None。"""
        return self.url_lookup()(plugin_id, kind, remote_id)

    def url_lookup(self) -> Callable[[str, MetadataKind, str], str | None]:
        """批量生成条目页面链接，每个插件只解析一次。"""
        cache: dict[str, BoundProvider | None] = {}

        def url(plugin_id: str, kind: MetadataKind, remote_id: str) -> str | None:
            if plugin_id not in cache:
                try:
                    cache[plugin_id] = self.get(plugin_id)
                except AppError:
                    cache[plugin_id] = None
            provider = cache[plugin_id]
            return None if provider is None else provider.page_url(kind, remote_id)

        return url

    def problem(self, library_id: int | None = None) -> MetadataProblem | None:
        """资料库（不指定时为全部资料库）选用的元数据插件中，第一个不能用的插件和原因。"""
        with self._database.read_session() as connection:
            if library_id is None:
                ids = [
                    int(row[0]) for row in connection.execute("SELECT id FROM library ORDER BY id")
                ]
            else:
                ids = [library_id]
            # 还没有资料库时检查新资料库默认用的插件
            provider_ids = dict.fromkeys(library_provider_id(connection, i) for i in ids) or {
                DEFAULT_PROVIDER: None
            }
        for provider_id in provider_ids:
            path = f"/settings/manage/plugins/{provider_id}"
            try:
                provider = self.get(provider_id)
            except AppError as exc:
                return MetadataProblem(exc.message, path)
            reason = provider.unavailable_reason()
            if reason is not None:
                return MetadataProblem(reason, path)
        return None

    def library_provider(self, library_id: int) -> str:
        with self._database.read_session() as connection:
            return library_provider_id(connection, library_id)

    def for_library(self, library_id: int) -> BoundProvider:
        return self.get(self.library_provider(library_id))
