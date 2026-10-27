from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from app.clock import Clock, to_iso
from app.errors import AppError, NotFound

MAX_SOURCE_NAME = 64


@dataclass(frozen=True)
class ResourceSource:
    id: int
    plugin_id: str
    name: str
    enabled: bool
    config: dict[str, Any]
    # unknown：还没同步过；ok：上次同步正常；error：上次同步出错，原因在 last_error
    status: str
    last_error: str | None
    checked_at: str | None
    # 使用这个来源的资料库目录数
    folder_count: int


@dataclass(frozen=True)
class ResourceRoot:
    """来源里被资料库使用的目录。"""

    id: int
    source_id: int
    # 来源里的定位符；None 表示来源根目录（数据库里存空字符串）
    locator: str | None
    display_path: str
    library_id: int | None
    library_name: str | None


class ResourceRepository:
    """资源来源实例和来源目录。来源目录只为资料库目录而存在，随资料库目录创建和清理。"""

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        self._db = connection
        self._clock = clock

    def sources(self) -> list[ResourceSource]:
        rows = self._db.execute(f"{_SELECT_SOURCE} ORDER BY rs.id").fetchall()
        return [_source(row) for row in rows]

    def source(self, source_id: int) -> ResourceSource:
        row = self._db.execute(f"{_SELECT_SOURCE} WHERE rs.id = ?", (source_id,)).fetchone()
        if row is None:
            raise NotFound("资源来源不存在")
        return _source(row)

    def first_of(self, plugin_id: str) -> ResourceSource | None:
        row = self._db.execute(
            f"{_SELECT_SOURCE} WHERE rs.plugin_id = ? ORDER BY rs.id LIMIT 1", (plugin_id,)
        ).fetchone()
        return None if row is None else _source(row)

    def create_source(self, plugin_id: str, name: str, config: dict[str, Any]) -> ResourceSource:
        name = _clean_name(name)
        now = to_iso(self._clock.now())
        try:
            cursor = self._db.execute(
                "INSERT INTO resource_source "
                "(plugin_id, name, config, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (plugin_id, name, json.dumps(config, ensure_ascii=False), now, now),
            )
        except sqlite3.IntegrityError as exc:
            raise _name_taken() from exc
        assert cursor.lastrowid is not None
        return self.source(cursor.lastrowid)

    def rename(self, source_id: int, name: str) -> ResourceSource:
        self.source(source_id)
        try:
            self._db.execute(
                "UPDATE resource_source SET name = ?, updated_at = ? WHERE id = ?",
                (_clean_name(name), to_iso(self._clock.now()), source_id),
            )
        except sqlite3.IntegrityError as exc:
            raise _name_taken() from exc
        return self.source(source_id)

    def set_enabled(self, source_id: int, enabled: bool) -> ResourceSource:
        self.source(source_id)
        self._db.execute(
            "UPDATE resource_source SET enabled = ?, updated_at = ? WHERE id = ?",
            (int(enabled), to_iso(self._clock.now()), source_id),
        )
        return self.source(source_id)

    def set_config(self, source_id: int, config: dict[str, Any]) -> ResourceSource:
        self.source(source_id)
        self._db.execute(
            "UPDATE resource_source SET config = ?, status = 'unknown', last_error = NULL, "
            "updated_at = ? WHERE id = ?",
            (json.dumps(config, ensure_ascii=False), to_iso(self._clock.now()), source_id),
        )
        return self.source(source_id)

    def delete_source(self, source_id: int) -> None:
        source = self.source(source_id)
        users = self.roots(source.id)
        if users:
            names = "、".join(dict.fromkeys(f"「{root.library_name}」" for root in users))
            raise AppError(
                409, "resource_source_in_use", f"资料库{names}还在使用这个来源，先移除这些目录"
            )
        self._db.execute("DELETE FROM resource_source WHERE id = ?", (source.id,))

    def record_check(self, source_id: int, error: str | None) -> None:
        """同步后记录来源状态。"""
        now = to_iso(self._clock.now())
        self._db.execute(
            "UPDATE resource_source SET status = ?, last_error = ?, checked_at = ? WHERE id = ?",
            ("error" if error else "ok", error, now, source_id),
        )

    def roots(self, source_id: int) -> list[ResourceRoot]:
        rows = self._db.execute(
            "SELECT rr.id, rr.source_id, rr.locator, rr.display_path, l.id, l.name "
            "FROM resource_root rr "
            "JOIN library_folder f ON f.resource_root_id = rr.id "
            "JOIN library l ON l.id = f.library_id "
            "WHERE rr.source_id = ? ORDER BY l.sort_order, l.id, rr.display_path",
            (source_id,),
        ).fetchall()
        return [
            ResourceRoot(root_id, source_id, model_locator(locator), display_path, library_id, name)
            for root_id, source_id, locator, display_path, library_id, name in rows
        ]

    def find_root(self, source_id: int, locator: str | None) -> int | None:
        row = self._db.execute(
            "SELECT id FROM resource_root WHERE source_id = ? AND locator = ?",
            (source_id, stored_locator(locator)),
        ).fetchone()
        return None if row is None else int(row[0])

    def ensure_root(self, source_id: int, locator: str | None, display_path: str) -> int:
        """登记来源目录并返回 ID；已登记的刷新显示路径。"""
        now = to_iso(self._clock.now())
        self._db.execute(
            "INSERT INTO resource_root (source_id, locator, display_path, created_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (source_id, locator) "
            "DO UPDATE SET display_path = excluded.display_path",
            (source_id, stored_locator(locator), display_path, now),
        )
        row = self._db.execute(
            "SELECT id FROM resource_root WHERE source_id = ? AND locator = ?",
            (source_id, stored_locator(locator)),
        ).fetchone()
        return int(row[0])

    def prune_roots(self) -> None:
        """删除没有资料库目录使用的来源目录。"""
        self._db.execute(
            "DELETE FROM resource_root WHERE id NOT IN "
            "(SELECT resource_root_id FROM library_folder)"
        )


_SELECT_SOURCE = """
    SELECT rs.id, rs.plugin_id, rs.name, rs.enabled, rs.config, rs.status, rs.last_error,
        rs.checked_at,
        (SELECT COUNT(*) FROM library_folder f JOIN resource_root rr ON rr.id = f.resource_root_id
            WHERE rr.source_id = rs.id) AS folder_count
    FROM resource_source rs
"""


def stored_locator(locator: str | None) -> str:
    """模型和协议用 None 表示来源根目录，数据库里存空字符串。"""
    return "" if locator is None else locator


def model_locator(locator: str) -> str | None:
    """数据库里空字符串表示来源根目录，模型和协议用 None。"""
    return locator or None


def _clean_name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > MAX_SOURCE_NAME:
        raise AppError(400, "invalid_source_name", f"来源名称必须是 1 到 {MAX_SOURCE_NAME} 个字符")
    return name


def _name_taken() -> AppError:
    return AppError(409, "source_name_taken", "已有同名的来源")


def _source(row: sqlite3.Row) -> ResourceSource:
    try:
        config = json.loads(row["config"])
    except (TypeError, json.JSONDecodeError):
        config = {}
    return ResourceSource(
        id=int(row["id"]),
        plugin_id=str(row["plugin_id"]),
        name=str(row["name"]),
        enabled=bool(row["enabled"]),
        config=config if isinstance(config, dict) else {},
        status=str(row["status"]),
        last_error=row["last_error"],
        checked_at=row["checked_at"],
        folder_count=int(row["folder_count"]),
    )
