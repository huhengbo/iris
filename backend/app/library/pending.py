"""删除待处理的作品和文件：先规划要删哪些云端目录和文件，云端删完后再清理数据库记录。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.errors import AppError
from app.library.deletion import escape_like
from app.library.review import join_path
from app.resources.repository import model_locator


@dataclass(frozen=True)
class PendingDeletePlan:
    # 整个删除的作品目录：资料库目录和相对路径
    dirs: list[tuple[int, str]]
    # 逐个删除的来源文件标识
    identities: list[str]


def _marks(count: int) -> str:
    # 空列表用 NULL 占位，保证 IN 子句仍是合法 SQL 且不匹配任何行
    return ", ".join("?" for _ in range(count)) or "NULL"


# 挂着版本或字幕的记录说明期间被归入了条目，留给同步按失效处理
_NOT_IN_USE = (
    "id NOT IN (SELECT media_file_id FROM version) "
    "AND id NOT IN (SELECT media_file_id FROM subtitle)"
)


class PendingDeletion:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def plan(self, work_ids: list[int], media_file_ids: list[int]) -> PendingDeletePlan:
        """检查选中的作品和文件能否删除，并算出来源上要删的目录和文件。"""
        dirs: list[tuple[int, str]] = []
        identities: list[str] = []
        work_marks = _marks(len(work_ids))
        works = self._connection.execute(
            f"SELECT w.id, w.folder_id, w.path, w.state, f.path AS folder_path FROM work w "
            f"JOIN library_folder f ON f.id = w.folder_id WHERE w.id IN ({work_marks})",
            work_ids,
        ).fetchall()
        if len(works) != len(work_ids):
            raise AppError(404, "not_found", "作品不存在，可能已经被删除")
        for work in works:
            shown = join_path(work["folder_path"], work["path"])
            if work["state"] == "matched":
                raise AppError(409, "work_matched", f"「{shown}」已经匹配到条目，请在条目里删除")
            path = work["path"].rstrip("/")
            prefix = f"{escape_like(path)}/%"
            # 电影散放在分类目录里时，作品路径就是视频文件本身，不是目录
            inside = self._connection.execute(
                "SELECT 1 FROM media_file WHERE folder_id = ? AND rel_path LIKE ? ESCAPE '\\' "
                "LIMIT 1",
                (work["folder_id"], prefix),
            ).fetchone()
            if path and inside is not None:
                # 目录里还有别的作品或已经归入条目的文件时不能整个删
                conflict = self._connection.execute(
                    f"""
                    SELECT 1 FROM media_file c
                    WHERE c.folder_id = ? AND c.state != 'missing' AND c.rel_path LIKE ? ESCAPE '\\'
                        AND ((c.work_id IS NOT NULL AND c.work_id NOT IN ({work_marks}))
                            OR c.id IN (SELECT media_file_id FROM version)
                            OR c.id IN (SELECT media_file_id FROM subtitle))
                    LIMIT 1
                    """,
                    (work["folder_id"], prefix, *work_ids),
                ).fetchone()
                if conflict is not None:
                    raise AppError(
                        409,
                        "media_folder_shared",
                        f"「{shown}」里还有其他作品或条目的文件，未执行删除",
                    )
                dirs.append((work["folder_id"], path))
                continue
            files = self._connection.execute(
                "SELECT c.identity, v.id AS version_id FROM media_file c "
                "LEFT JOIN version v ON v.media_file_id = c.id "
                "WHERE c.work_id = ? AND c.state != 'missing'",
                (work["id"],),
            ).fetchall()
            if any(file["version_id"] is not None for file in files):
                raise AppError(409, "file_in_use", f"「{shown}」的文件已经归入条目，请在条目里删除")
            identities.extend(str(file["identity"]) for file in files if file["identity"])
        files = self._connection.execute(
            f"SELECT c.id, c.kind, c.state, c.identity, c.rel_path, f.path AS folder_path, "
            f"v.id AS version_id FROM media_file c JOIN library_folder f ON f.id = c.folder_id "
            f"LEFT JOIN version v ON v.media_file_id = c.id "
            f"WHERE c.id IN ({_marks(len(media_file_ids))})",
            media_file_ids,
        ).fetchall()
        if len(files) != len(media_file_ids):
            raise AppError(404, "not_found", "文件不存在，可能已经被删除")
        for file in files:
            if file["kind"] != "video":
                raise AppError(409, "not_pending_file", "只能删除待处理的视频文件")
            if file["version_id"] is not None:
                shown = join_path(file["folder_path"], file["rel_path"])
                raise AppError(409, "file_in_use", f"「{shown}」已经归入条目，请在条目里删除")
            if file["identity"] and file["state"] != "missing":
                identities.append(str(file["identity"]))
        return PendingDeletePlan(dirs, list(dict.fromkeys(identities)))

    def folder_roots(self, folder_ids: set[int]) -> dict[int, str | None]:
        """资料库目录的根定位符；None 表示来源根目录。"""
        return {
            int(row["id"]): model_locator(row["locator"])
            for row in self._connection.execute(
                f"SELECT f.id, rr.locator FROM library_folder f "
                f"JOIN resource_root rr ON rr.id = f.resource_root_id "
                f"WHERE f.id IN ({_marks(len(folder_ids))})",
                tuple(folder_ids),
            )
        }

    def cleanup(
        self, plan: PendingDeletePlan, work_ids: list[int], media_file_ids: list[int]
    ) -> None:
        """云端删除成功后清理数据库记录；期间被归入条目的记录保留。"""
        for folder_id, path in plan.dirs:
            self._connection.execute(
                f"DELETE FROM media_file WHERE folder_id = ? AND rel_path LIKE ? ESCAPE '\\' "
                f"AND {_NOT_IN_USE}",
                (folder_id, f"{escape_like(path)}/%"),
            )
        work_marks = _marks(len(work_ids))
        self._connection.execute(
            f"DELETE FROM media_file WHERE work_id IN ({work_marks}) AND {_NOT_IN_USE}", work_ids
        )
        self._connection.execute(
            f"DELETE FROM work WHERE id IN ({work_marks}) AND state != 'matched'", work_ids
        )
        self._connection.execute(
            f"DELETE FROM media_file WHERE id IN ({_marks(len(media_file_ids))}) AND {_NOT_IN_USE}",
            media_file_ids,
        )
