from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from iris_plugin_sdk import ResourceListing

from app.clock import Clock, to_iso
from app.errors import AppError, NotFound

LibraryKind = Literal["movie", "series"]


@dataclass(frozen=True)
class FolderRef:
    """在来源上确认过的目录。

    scope_key 以来源 ID 开头，是资料库目录在来源内的稳定身份，只用于重叠校验和比较；
    不同来源的目录永远不会重叠。层级来自插件 browse 返回的 ancestors 定位符链，目录改名、
    移动后仍然认得出来。resource_root_id 是它登记的来源目录，规划阶段还没登记时为空，
    写入资料库前必须补上。
    """

    scope_key: str
    path: str
    resource_root_id: int | None = None

    @classmethod
    def of(
        cls,
        source_id: int,
        listing: ResourceListing,
        resource_root_id: int | None = None,
    ) -> FolderRef:
        """用插件返回的目录路径和定位符链生成目录键。

        ancestors 从来源根下的第一层到当前目录；来源根目录的 ancestors 为空。
        """
        chain = "".join(f"{locator}/" for locator, _ in listing.ancestors)
        return cls(
            scope_key=f"/source/{source_id}/{chain}",
            path=listing.path,
            resource_root_id=resource_root_id,
        )


@dataclass(frozen=True)
class LibraryFolder:
    id: int
    library_id: int
    scope_key: str
    path: str
    resource_root_id: int


@dataclass(frozen=True)
class Library:
    id: int
    name: str
    kind: LibraryKind
    sort_order: int
    folders: list[LibraryFolder]
    # 未失效的视频数和失效的视频数
    video_count: int
    missing_count: int
    created_at: str


@dataclass(frozen=True)
class Occupant:
    library_id: int
    library_name: str
    path: str


class LibraryRepository:
    """资料库和资料库目录。同一个来源目录及其子目录只能属于一个资料库（ADR 0011）。"""

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        self._db = connection
        self._clock = clock

    def all(self) -> list[Library]:
        rows = self._db.execute(f"{_SELECT} ORDER BY l.sort_order, l.id").fetchall()
        folders = self._folders_by_library()
        return [_to_library(row, folders.get(row["id"], [])) for row in rows]

    def get(self, library_id: int) -> Library | None:
        row = self._db.execute(f"{_SELECT} WHERE l.id = ?", (library_id,)).fetchone()
        if row is None:
            return None
        return _to_library(row, self._folders_by_library(library_id).get(library_id, []))

    def count(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM library").fetchone()[0])

    def item_ids(self, library_id: int) -> list[int]:
        """资料库下全部条目的 ID，按 ID 升序。"""
        rows = self._db.execute(
            "SELECT id FROM media_item WHERE library_id = ? ORDER BY id", (library_id,)
        ).fetchall()
        return [int(row[0]) for row in rows]

    def require(self, library_id: int) -> Library:
        library = self.get(library_id)
        if library is None:
            raise NotFound("资料库不存在")
        return library

    def folders(self, library_id: int | None = None) -> list[LibraryFolder]:
        """按资料库排序返回目录；指定资料库时只返回它的目录。"""
        grouped = self._folders_by_library(library_id)
        ordered = self._db.execute("SELECT id FROM library ORDER BY sort_order, id").fetchall()
        return [folder for row in ordered for folder in grouped.get(row["id"], [])]

    def create(self, name: str, kind: LibraryKind, folders: Sequence[FolderRef]) -> Library:
        name = _clean_name(name)
        if not folders:
            raise AppError(400, "library_folder_required", "至少要选择一个资源根目录")
        self._check_overlap(folders, library_id=None)
        now = to_iso(self._clock.now())
        row = self._db.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM library").fetchone()
        try:
            cursor = self._db.execute(
                "INSERT INTO library (name, kind, sort_order, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, kind, row[0], now, now),
            )
        except sqlite3.IntegrityError as exc:
            raise _name_taken() from exc
        assert cursor.lastrowid is not None
        for folder in folders:
            self._insert_folder(cursor.lastrowid, folder, now)
        return self.require(cursor.lastrowid)

    def rename(self, library_id: int, name: str) -> Library:
        library = self.require(library_id)
        name = _clean_name(name)
        try:
            self._db.execute(
                "UPDATE library SET name = ?, updated_at = ? WHERE id = ?",
                (name, to_iso(self._clock.now()), library.id),
            )
        except sqlite3.IntegrityError as exc:
            raise _name_taken() from exc
        return self.require(library.id)

    def set_folders(self, library_id: int, folders: Sequence[FolderRef]) -> Library:
        """用新的目录列表替换资料库目录。保留的目录不动，移除的目录连同云端文件一起删除。"""
        library = self.require(library_id)
        if not folders:
            raise AppError(400, "library_folder_required", "至少要选择一个资源根目录")
        self._check_overlap(folders, library_id=library.id)
        keep = {folder.scope_key for folder in folders}
        current = {folder.scope_key for folder in library.folders}
        now = to_iso(self._clock.now())
        for folder in library.folders:
            if folder.scope_key not in keep:
                self._db.execute("DELETE FROM library_folder WHERE id = ?", (folder.id,))
        for ref in folders:
            if ref.scope_key not in current:
                self._insert_folder(library.id, ref, now)
        self._db.execute("UPDATE library SET updated_at = ? WHERE id = ?", (now, library.id))
        return self.require(library.id)

    def refresh_folder(self, folder_id: int, ref: FolderRef) -> None:
        """同步时用来源上的最新位置刷新目录键和路径：上级目录可能被改名或移动。"""
        self._db.execute(
            "UPDATE library_folder SET scope_key = ?, path = ? WHERE id = ?",
            (ref.scope_key, ref.path, folder_id),
        )
        self._db.execute(
            "UPDATE resource_root SET display_path = ? "
            "WHERE id = (SELECT resource_root_id FROM library_folder WHERE id = ?)",
            (ref.path, folder_id),
        )

    def refresh_folder_path(self, folder_id: int, path: str) -> None:
        """刷新资源来源目录的展示路径，定位符仍由来源本身保存。"""
        self._db.execute("UPDATE library_folder SET path = ? WHERE id = ?", (path, folder_id))
        self._db.execute(
            "UPDATE resource_root SET display_path = ? "
            "WHERE id = (SELECT resource_root_id FROM library_folder WHERE id = ?)",
            (path, folder_id),
        )

    def delete(self, library_id: int) -> None:
        library = self.require(library_id)
        self._db.execute("DELETE FROM library WHERE id = ?", (library.id,))

    def reorder(self, ids: Sequence[int]) -> list[Library]:
        existing = {row["id"] for row in self._db.execute("SELECT id FROM library")}
        if len(ids) != len(existing) or set(ids) != existing:
            raise AppError(400, "invalid_order", "排序必须包含全部资料库，且每个只出现一次")
        now = to_iso(self._clock.now())
        for position, library_id in enumerate(ids, start=1):
            self._db.execute(
                "UPDATE library SET sort_order = ?, updated_at = ? WHERE id = ?",
                (position, now, library_id),
            )
        return self.all()

    def occupant(self, scope_key: str, *, excluding: int | None = None) -> Occupant | None:
        """返回和这个目录重叠的资料库目录：它自己、它的上级或它的下级已属于某个资料库。

        excluding 指定的资料库不算，编辑资料库时用来排除它自己。
        """
        row = self._db.execute(
            "SELECT f.library_id, l.name, f.path FROM library_folder f "
            "JOIN library l ON l.id = f.library_id "
            "WHERE (substr(?, 1, length(f.scope_key)) = f.scope_key "
            "OR substr(f.scope_key, 1, length(?)) = ?) AND f.library_id IS NOT ? "
            "ORDER BY length(f.scope_key) LIMIT 1",
            (scope_key, scope_key, scope_key, excluding),
        ).fetchone()
        if row is None:
            return None
        return Occupant(library_id=row[0], library_name=row[1], path=row[2])

    def _check_overlap(self, folders: Sequence[FolderRef], library_id: int | None) -> None:
        for index, folder in enumerate(folders):
            for other in folders[index + 1 :]:
                if _overlaps(folder.scope_key, other.scope_key):
                    raise AppError(
                        409,
                        "library_folder_overlap",
                        f"目录「{folder.path}」和「{other.path}」重叠，只能保留一个",
                    )
            occupant = self.occupant(folder.scope_key, excluding=library_id)
            if occupant is not None:
                raise AppError(
                    409,
                    "library_folder_overlap",
                    f"目录「{folder.path}」与资料库「{occupant.library_name}」的目录"
                    f"「{occupant.path}」重叠",
                )

    def _insert_folder(self, library_id: int, folder: FolderRef, now: str) -> None:
        if folder.resource_root_id is None:
            raise ValueError(f"资料库目录 {folder.path} 还没有登记来源目录")
        self._db.execute(
            "INSERT INTO library_folder "
            "(library_id, scope_key, path, resource_root_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (library_id, folder.scope_key, folder.path, folder.resource_root_id, now),
        )

    def _folders_by_library(self, library_id: int | None = None) -> dict[int, list[LibraryFolder]]:
        sql = "SELECT id, library_id, scope_key, path, resource_root_id FROM library_folder"
        params: tuple[int, ...] = ()
        if library_id is not None:
            sql += " WHERE library_id = ?"
            params = (library_id,)
        grouped: dict[int, list[LibraryFolder]] = {}
        for row in self._db.execute(f"{sql} ORDER BY id", params):
            grouped.setdefault(row["library_id"], []).append(
                LibraryFolder(
                    id=row["id"],
                    library_id=row["library_id"],
                    scope_key=row["scope_key"],
                    path=row["path"],
                    resource_root_id=row["resource_root_id"],
                )
            )
        return grouped


MAX_NAME_LENGTH = 64

_SELECT = """
    SELECT l.id, l.name, l.kind, l.sort_order, l.created_at,
        (SELECT COUNT(*) FROM media_file c JOIN library_folder f ON f.id = c.folder_id
            WHERE f.library_id = l.id AND c.kind = 'video' AND c.state != 'missing') AS videos,
        (SELECT COUNT(*) FROM media_file c JOIN library_folder f ON f.id = c.folder_id
            WHERE f.library_id = l.id AND c.kind = 'video' AND c.state = 'missing') AS missing
    FROM library l
"""


def _overlaps(a: str, b: str) -> bool:
    return a.startswith(b) or b.startswith(a)


def _clean_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise AppError(400, "invalid_library_name", "资料库名称不能为空")
    if len(name) > MAX_NAME_LENGTH:
        raise AppError(400, "invalid_library_name", f"资料库名称最多 {MAX_NAME_LENGTH} 个字符")
    return name


def _name_taken() -> AppError:
    return AppError(409, "library_name_taken", "已有同名的资料库")


def _to_library(row: sqlite3.Row, folders: list[LibraryFolder]) -> Library:
    return Library(
        id=row["id"],
        name=row["name"],
        kind=row["kind"],
        sort_order=row["sort_order"],
        folders=folders,
        video_count=row["videos"],
        missing_count=row["missing"],
        created_at=row["created_at"],
    )
