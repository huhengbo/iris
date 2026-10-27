"""资料库目录和资源来源之间的唯一分派点。

同步、播放和删除都通过资源插件协议分派；来源定位符只由插件解释。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, cast

from iris_plugin_sdk import (
    ResourceEntry,
    ResourceListing,
    ResourceNotFound,
    ResourceProvider,
    ResourceSource,
    ResourceUnavailable,
)
from iris_plugin_sdk.protocol import WaitReason

from app.library.subtitle_plugin import LOCAL_SUBTITLES, LocalSubtitles
from app.plugins.registry import PluginRegistry
from app.resources.repository import model_locator

__all__ = ["FolderSource", "ResourceSources", "file_key", "locator_of"]

WaitCallback = Callable[[datetime, WaitReason], None]


class HostResourceSource(ResourceSource, Protocol):
    """宿主对来源的额外要求：调用时可以挂一个接收 progress 的等待回调。"""

    def browse(
        self, locator: str | None = None, *, waiter: WaitCallback | None = None
    ) -> ResourceListing: ...

    def scan(
        self,
        locator: str | None,
        known: Mapping[str, ResourceEntry] | None = None,
        *,
        waiter: WaitCallback | None = None,
    ) -> list[ResourceEntry]: ...


class HostResourceProvider(ResourceProvider, Protocol):
    def open(
        self,
        source_id: int,
        config: Mapping[str, Any],
        *,
        waiter: WaitCallback | None = None,
    ) -> HostResourceSource: ...


@dataclass(frozen=True)
class FolderSource:
    """资料库目录所在的来源。"""

    folder_id: int
    source_id: int
    plugin_id: str
    name: str
    config: dict[str, Any]
    # 来源和它的插件都启用时才为 True
    enabled: bool
    # 资料库目录在来源里的定位符；None 表示来源根目录
    locator: str | None
    display_path: str

    def disabled_reason(self) -> str:
        return f"资源来源「{self.name}」已停用"


class ResourceSources:
    def __init__(self, plugins: PluginRegistry) -> None:
        self._plugins = plugins

    def provider(self, plugin_id: str) -> ResourceProvider | None:
        return self._plugins.resource_provider(plugin_id)

    def local_subtitles(self) -> LocalSubtitles:
        """宿主内置的外挂字幕匹配；只按路径匹配，不访问网络。"""
        return LOCAL_SUBTITLES

    def open(self, source: FolderSource, waiter: WaitCallback | None = None) -> HostResourceSource:
        provider = self.provider(source.plugin_id)
        if provider is None:
            raise ResourceUnavailable(f"资源来源「{source.name}」的插件没有启用或启动失败")
        host_provider = cast(HostResourceProvider, provider)
        return host_provider.open(source.source_id, source.config, waiter=waiter)

    def check(self, source: FolderSource) -> None:
        provider = self.provider(source.plugin_id)
        if provider is None:
            raise ResourceUnavailable(f"资源来源「{source.name}」的插件没有启用或启动失败")
        provider.check(source.config)

    def check_config(self, plugin_id: str, config: dict[str, Any]) -> None:
        provider = self.provider(plugin_id)
        if provider is None:
            raise ResourceUnavailable(f"资源插件「{plugin_id}」没有启用或启动失败")
        provider.check(config)

    def source(self, connection: sqlite3.Connection, folder_id: int) -> FolderSource:
        """Resolve a library folder to its configured source."""
        source = self.folder(connection, folder_id)
        if source is None:
            raise ResourceNotFound("资料库目录不存在")
        if not source.enabled:
            raise ResourceUnavailable(source.disabled_reason())
        return source

    def resolve_path(
        self,
        source: FolderSource,
        relative_path: str,
        waiter: Any = None,
    ) -> str | None:
        """Resolve a relative directory path through the source's browse protocol.

        The core does not interpret source locators. This helper deliberately walks
        directories using ``browse`` so folder deletion and directory selection do
        not need a provider-specific path resolver.
        """
        locator = source.locator
        parts = [part for part in relative_path.strip("/").split("/") if part]
        for name in parts:
            listing = self.open(source, waiter).browse(locator)
            entry = next(
                (
                    entry
                    for entry in listing.entries
                    if entry.is_dir and entry.relative_path == name
                ),
                None,
            )
            if entry is None:
                return None
            locator = entry.locator
        return locator

    def delete_file(self, connection: sqlite3.Connection, identity: str) -> None:
        """Delete one indexed file through its owning resource source."""
        row = connection.execute(
            "SELECT folder_id, identity FROM media_file WHERE identity = ?", (identity,)
        ).fetchone()
        if row is None:
            raise ResourceNotFound("文件不存在")
        source = self.source(connection, int(row[0]))
        self.open(source).delete(locator_of(str(row[1] or identity)))

    @staticmethod
    def folders(
        connection: sqlite3.Connection, folder_ids: list[int] | None = None
    ) -> dict[int, FolderSource]:
        sql = _SELECT
        params: tuple[int, ...] = ()
        if folder_ids is not None:
            sql += " WHERE f.id IN ({})".format(", ".join("?" for _ in folder_ids) or "NULL")
            params = tuple(folder_ids)
        return {row["folder_id"]: _folder_source(row) for row in connection.execute(sql, params)}

    @classmethod
    def folder(cls, connection: sqlite3.Connection, folder_id: int) -> FolderSource | None:
        return cls.folders(connection, [folder_id]).get(folder_id)


def file_key(source_id: int, locator: str) -> str:
    """来源没有全局稳定键时写进 media_file.identity 的值。

    identity 在同步时用来认出挪动过的文件，要在所有来源之间唯一。
    """
    return f"source:{source_id}:{locator}"


def locator_of(identity: str) -> str:
    """file_key 的逆运算。"""
    if not identity.startswith("source:"):
        return identity
    _, _, rest = identity.partition(":")
    return rest.partition(":")[2]


_SELECT = """
    SELECT f.id AS folder_id, rr.locator, rr.display_path, rs.id AS source_id,
        rs.plugin_id, rs.name, rs.config, rs.enabled AND COALESCE(p.enabled, 0) AS enabled
    FROM library_folder f
    JOIN resource_root rr ON rr.id = f.resource_root_id
    JOIN resource_source rs ON rs.id = rr.source_id
    LEFT JOIN plugin p ON p.id = rs.plugin_id
"""


def _folder_source(row: sqlite3.Row) -> FolderSource:
    try:
        config = json.loads(row["config"])
    except (TypeError, json.JSONDecodeError):
        config = {}
    return FolderSource(
        folder_id=row["folder_id"],
        source_id=row["source_id"],
        plugin_id=row["plugin_id"],
        name=row["name"],
        config=config if isinstance(config, dict) else {},
        enabled=bool(row["enabled"]),
        locator=model_locator(row["locator"]),
        display_path=str(row["display_path"] or ""),
    )
