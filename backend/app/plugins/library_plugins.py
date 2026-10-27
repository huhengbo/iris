"""资料库按顺序启用的下载器（ADR 0025）：元数据下载器、图片获取器、字幕下载器三个有序列表。

某资料库在某个 role 下没有保存任何行时用默认值：元数据按能力选第一个已启用插件，图片跟随它，
字幕启用全部下载器；这里不写死任何插件 id。
这里不导入 app.library，资料库和插件注册中心都可以用。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

Role = Literal["metadata", "image", "subtitle"]
ROLES: tuple[Role, ...] = ("metadata", "image", "subtitle")
# 能进入各列表的插件必须提供的能力
ROLE_CAPABILITY: dict[Role, str] = {
    "metadata": "metadata.details",
    "image": "image.fetch",
    "subtitle": "subtitle.download",
}
# 没有保存过列表时默认勾选的插件必须提供的能力；不按插件 id 写死。
ROLE_DEFAULT_CAPABILITIES: dict[Role, frozenset[str]] = {
    "metadata": frozenset({"metadata.search", "metadata.details"}),
    "image": frozenset({"image.fetch"}),
}


@dataclass(frozen=True)
class Entry:
    id: str
    enabled: bool


def _enabled(connection: sqlite3.Connection) -> list[tuple[str, frozenset[str]]]:
    rows = connection.execute(
        "SELECT id, provides FROM plugin WHERE enabled = 1 ORDER BY built_in DESC, id"
    ).fetchall()
    return [(str(row[0]), frozenset(json.loads(row[1]))) for row in rows]


def default_ids(connection: sqlite3.Connection, role: Role) -> tuple[str, ...]:
    """默认启用的插件：按能力从已启用插件里选，宿主不认识具体插件 id。

    图片默认跟随主元数据插件；它没有图片能力时取第一个已启用的图片获取器。
    """
    if role == "subtitle":
        return ()
    enabled = _enabled(connection)
    if role == "metadata":
        required = ROLE_DEFAULT_CAPABILITIES["metadata"]
        found = next((plugin_id for plugin_id, provides in enabled if required <= provides), None)
        return () if found is None else (found,)
    primary = next(
        (
            plugin_id
            for plugin_id, provides in enabled
            if ROLE_DEFAULT_CAPABILITIES["metadata"] <= provides
        ),
        None,
    )
    if primary is not None:
        for plugin_id, provides in enabled:
            if plugin_id == primary and ROLE_DEFAULT_CAPABILITIES["image"] <= provides:
                return (primary,)
    fallback = next(
        (
            plugin_id
            for plugin_id, provides in enabled
            if ROLE_DEFAULT_CAPABILITIES["image"] <= provides
        ),
        None,
    )
    return () if fallback is None else (fallback,)


def _saved(connection: sqlite3.Connection, library_id: int, role: Role) -> list[Entry]:
    rows = connection.execute(
        "SELECT plugin_id, enabled FROM library_plugin WHERE library_id = ? AND role = ? "
        "ORDER BY position, plugin_id",
        (library_id, role),
    ).fetchall()
    return [Entry(str(row[0]), bool(row[1])) for row in rows]


def entries(
    connection: sqlite3.Connection, library_id: int, role: Role, candidates: Iterable[str]
) -> list[Entry]:
    """资料库这一列表的完整顺序：已保存的按位置排，没保存过的候选插件按 id 追加在后面且不勾选。

    candidates 是当前安装、提供对应能力的插件；已保存但不在候选里的插件（被卸载）不返回。
    """
    available = sorted(set(candidates))
    allowed = set(available)
    saved = _saved(connection, library_id, role)
    if saved:
        result = [entry for entry in saved if entry.id in allowed]
    elif role == "subtitle":
        result = [Entry(plugin_id, True) for plugin_id in available]
    else:
        result = [
            Entry(plugin_id, True)
            for plugin_id in default_ids(connection, role)
            if plugin_id in allowed
        ]
    listed = {entry.id for entry in result}
    result.extend(Entry(plugin_id, False) for plugin_id in available if plugin_id not in listed)
    return result


def enabled_ids(
    connection: sqlite3.Connection, library_id: int, role: Role, candidates: Iterable[str]
) -> list[str]:
    """资料库勾选、并且仍是候选的插件，保持顺序。"""
    return [
        entry.id for entry in entries(connection, library_id, role, candidates) if entry.enabled
    ]


def first_enabled(connection: sqlite3.Connection, library_id: int, role: Role) -> str | None:
    """排在最前、勾选的插件，不管它是否还安装着（用来报「插件没有安装」）。"""
    saved = _saved(connection, library_id, role)
    if not saved:
        defaults = default_ids(connection, role)
        return defaults[0] if defaults else None
    return next((entry.id for entry in saved if entry.enabled), None)


def replace(
    connection: sqlite3.Connection,
    library_id: int,
    role: Role,
    items: Sequence[Entry],
    now: str,
) -> None:
    """整体替换资料库这一列表。"""
    connection.execute(
        "DELETE FROM library_plugin WHERE library_id = ? AND role = ?", (library_id, role)
    )
    connection.executemany(
        "INSERT INTO library_plugin (library_id, role, plugin_id, position, enabled, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (library_id, role, entry.id, position, int(entry.enabled), now)
            for position, entry in enumerate(items)
        ],
    )


def move_first(
    connection: sqlite3.Connection, library_id: int, role: Role, plugin_id: str, now: str
) -> None:
    """把插件移到第一位并勾选，其余保持原状。资料库还没保存过这一列表时只保存这一个插件。"""
    rest = [entry for entry in _saved(connection, library_id, role) if entry.id != plugin_id]
    replace(connection, library_id, role, [Entry(plugin_id, True), *rest], now)
