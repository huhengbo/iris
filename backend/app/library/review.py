"""管理端的只读视图：待处理的作品和文件、被忽略的文件、条目列表和条目详情。

写操作在 Curation 里；这里只查询，供管理接口分页展示。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Literal

from app.library.images import ImageSize, image_sizes
from app.library.metadata_providers import TMDB_PROVIDER
from app.library.recognize import LibraryKind
from app.library.scraper import require_item

WorkState = Literal["pending", "matched", "unmatched"]
FileStatus = Literal["unplaced", "ignored"]

DETACHED_REASON = "已从条目中拆出，等待重新指定"
NO_EPISODE_REASON = "无法从文件名识别季号和集号"
NO_ITEM_REASON = "没有归入任何条目"


@dataclass(frozen=True)
class Page[T]:
    items: list[T]
    total: int


@dataclass(frozen=True)
class ItemRef:
    id: int
    provider_id: str
    remote_id: str
    title: str


@dataclass(frozen=True)
class CandidateView:
    provider_id: str
    remote_id: str
    title: str
    original_title: str | None
    year: int | None
    # 匹配度和依据；旧数据没有，分数为 None
    score: int | None = None
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkView:
    id: int
    library_id: int
    library_name: str
    kind: LibraryKind
    path: str
    title: str | None
    year: int | None
    state: WorkState
    manual: bool
    reason: str | None
    candidates: list[CandidateView]
    file_count: int
    item: ItemRef | None


@dataclass(frozen=True)
class FileView:
    id: int
    library_id: int
    library_name: str
    kind: LibraryKind
    path: str
    size: int | None
    reason: str
    # 文件所属作品匹配到的条目，手动指定集号时可以直接沿用
    item: ItemRef | None


@dataclass(frozen=True)
class Summary:
    waiting: int
    unmatched: int
    unplaced: int
    ignored: int


@dataclass(frozen=True)
class ItemView:
    id: int
    library_id: int
    library_name: str
    kind: LibraryKind
    provider_id: str
    remote_id: str
    title: str
    original_title: str | None
    year: int | None
    poster: list[ImageSize]
    version_count: int
    added_at: str


@dataclass(frozen=True)
class ItemWork:
    id: int
    path: str
    manual: bool
    # AI 自动匹配的结果，可以撤销
    ai_matched: bool = False


@dataclass(frozen=True)
class ItemVersion:
    id: int
    media_file_id: int
    path: str
    size: int | None
    state: str
    label: str
    manual: bool
    season: int | None
    episode: int | None


@dataclass(frozen=True)
class ItemDetail:
    item: ItemView
    overview: str | None
    release_date: str | None
    rating: float | None
    genres: list[str]
    runtime: int | None
    backdrop: list[ImageSize]
    thumb: list[ImageSize]
    logo: list[ImageSize]
    scraped_at: str
    works: list[ItemWork]
    versions: list[ItemVersion]


class ReviewQueries:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def summary(self, library_id: int | None) -> Summary:
        scope, args = _library_scope(library_id)
        works = dict(
            self._db.execute(
                "SELECT w.state, COUNT(*) FROM work w JOIN library_folder f ON f.id = w.folder_id "
                f"WHERE {_HAS_VIDEO} {scope} GROUP BY w.state",
                args,
            ).fetchall()
        )
        unplaced = self._db.execute(
            f"SELECT COUNT(*) FROM media_file c {_FILE_JOINS} WHERE {_UNPLACED} {scope}", args
        ).fetchone()[0]
        ignored = self._db.execute(
            f"SELECT COUNT(*) FROM media_file c {_FILE_JOINS} WHERE {_IGNORED} {scope}", args
        ).fetchone()[0]
        return Summary(
            waiting=works.get("pending", 0),
            unmatched=works.get("unmatched", 0),
            unplaced=unplaced,
            ignored=ignored,
        )

    def works(
        self, state: WorkState, library_id: int | None, offset: int, limit: int
    ) -> Page[WorkView]:
        scope, args = _library_scope(library_id)
        where = f"WHERE w.state = ? AND {_HAS_VIDEO} {scope}"
        total = self._db.execute(
            f"SELECT COUNT(*) FROM work w JOIN library_folder f ON f.id = w.folder_id {where}",
            (state, *args),
        ).fetchone()[0]
        rows = self._db.execute(
            f"""
            SELECT w.id, f.library_id, l.name AS library_name, l.kind, f.path AS folder_path,
                w.path, w.title, w.year, w.state, w.manual, w.reason, w.candidates,
                m.id AS item_id, m.provider_id AS item_provider_id, m.remote_id AS item_remote_id,
                m.title AS item_title,
                (SELECT COUNT(*) FROM media_file c WHERE c.work_id = w.id AND c.kind = 'video'
                    AND c.state = 'active') AS file_count
            FROM work w JOIN library_folder f ON f.id = w.folder_id
            JOIN library l ON l.id = f.library_id
            LEFT JOIN media_item m ON m.id = w.media_item_id
            {where} ORDER BY l.sort_order, l.id, f.path, w.path LIMIT ? OFFSET ?
            """,
            (state, *args, limit, offset),
        ).fetchall()
        return Page(
            [
                WorkView(
                    id=row["id"],
                    library_id=row["library_id"],
                    library_name=row["library_name"],
                    kind=row["kind"],
                    path=join_path(row["folder_path"], row["path"]),
                    title=row["title"],
                    year=row["year"],
                    state=row["state"],
                    manual=bool(row["manual"]),
                    reason=row["reason"],
                    candidates=_candidates(row["candidates"]),
                    file_count=row["file_count"],
                    item=_item_ref(row),
                )
                for row in rows
            ],
            total,
        )

    def files(
        self,
        status: FileStatus,
        library_id: int | None,
        offset: int,
        limit: int,
        reason: str | None = None,
    ) -> Page[FileView]:
        scope, scope_args = _library_scope(library_id)
        args: tuple[int | str, ...] = scope_args
        where = f"WHERE {_UNPLACED if status == 'unplaced' else _IGNORED} {scope}"
        if reason is not None:
            where += f" AND {_FILE_REASON} = ?"
            args = (*args, reason)
        total = self._db.execute(
            f"SELECT COUNT(*) FROM media_file c {_FILE_JOINS} "
            f"JOIN library l ON l.id = f.library_id {where}",
            args,
        ).fetchone()[0]
        rows = self._db.execute(
            f"""
            SELECT c.id, f.library_id, l.name AS library_name, l.kind, f.path AS folder_path,
                c.rel_path, c.size, c.detached, c.ignore_reason, w.state AS work_state,
                m.id AS item_id, m.provider_id AS item_provider_id, m.remote_id AS item_remote_id,
                m.title AS item_title
            FROM media_file c {_FILE_JOINS}
            JOIN library l ON l.id = f.library_id
            LEFT JOIN media_item m ON m.id = w.media_item_id
            {where} ORDER BY l.sort_order, l.id, f.path, c.rel_path LIMIT ? OFFSET ?
            """,
            (*args, limit, offset),
        ).fetchall()
        return Page([_file_view(row) for row in rows], total)

    def file_reasons(self, status: FileStatus, library_id: int | None) -> list[str]:
        """当前状态和资料库范围内出现过的原因，供筛选。"""
        scope, args = _library_scope(library_id)
        where = f"WHERE {_UNPLACED if status == 'unplaced' else _IGNORED} {scope}"
        rows = self._db.execute(
            f"SELECT DISTINCT {_FILE_REASON} AS reason FROM media_file c {_FILE_JOINS} "
            f"JOIN library l ON l.id = f.library_id {where} ORDER BY reason",
            args,
        ).fetchall()
        return [row["reason"] for row in rows]

    def items(
        self, library_id: int | None, query: str | None, offset: int, limit: int
    ) -> Page[ItemView]:
        conditions: list[str] = []
        args: list[object] = []
        if library_id is not None:
            conditions.append("m.library_id = ?")
            args.append(library_id)
        if query:
            pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern += "%"
            conditions.append(
                "(m.title LIKE ? ESCAPE '\\' OR m.original_title LIKE ? ESCAPE '\\' "
                "OR m.en_title LIKE ? ESCAPE '\\')"
            )
            args.extend([pattern] * 3)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        total = self._db.execute(f"SELECT COUNT(*) FROM media_item m {where}", args).fetchone()[0]
        rows = self._db.execute(
            f"{_ITEM_SELECT} {where} ORDER BY m.added_at DESC, m.id DESC LIMIT ? OFFSET ?",
            (*args, limit, offset),
        ).fetchall()
        return Page([_item_view(row) for row in rows], total)

    def item(self, item_id: int) -> ItemDetail:
        require_item(self._db, item_id)
        row = self._db.execute(
            """
            SELECT m.overview, m.release_date, m.rating, m.genres, m.runtime, m.scraped_at,
                b.hash AS backdrop_hash, t.hash AS thumb_hash, g.hash AS logo_hash
            FROM media_item m
            LEFT JOIN image b ON b.id = m.backdrop_image_id
            LEFT JOIN image t ON t.id = m.thumb_image_id
            LEFT JOIN image g ON g.id = m.logo_image_id
            WHERE m.id = ?
            """,
            (item_id,),
        ).fetchone()
        summary = self._db.execute(f"{_ITEM_SELECT} WHERE m.id = ?", (item_id,)).fetchone()
        works = self._db.execute(
            "SELECT w.id, f.path AS folder_path, w.path, w.manual, EXISTS (SELECT 1 FROM "
            "work_suggestion s WHERE s.work_id = w.id AND s.status = 'auto_matched') AS ai_matched "
            "FROM work w "
            "JOIN library_folder f ON f.id = w.folder_id WHERE w.media_item_id = ? "
            "ORDER BY f.path, w.path",
            (item_id,),
        ).fetchall()
        versions = self._db.execute(
            """
            SELECT v.id, v.label, v.manual, c.id AS media_file_id,
                f.path AS folder_path, c.rel_path,
                c.size, c.state, s.number AS season, e.number AS episode
            FROM version v JOIN playable p ON p.id = v.playable_id
            JOIN media_file c ON c.id = v.media_file_id
            JOIN library_folder f ON f.id = c.folder_id
            LEFT JOIN episode e ON e.id = p.episode_id
            LEFT JOIN season s ON s.id = e.season_id
            WHERE p.media_item_id = ?
            ORDER BY s.number, e.number, c.size DESC, v.id
            """,
            (item_id,),
        ).fetchall()
        return ItemDetail(
            item=_item_view(summary),
            overview=row["overview"],
            release_date=row["release_date"],
            rating=row["rating"],
            genres=json.loads(row["genres"]),
            runtime=row["runtime"],
            backdrop=image_sizes(row["backdrop_hash"], "backdrop"),
            thumb=image_sizes(row["thumb_hash"], "thumb"),
            logo=image_sizes(row["logo_hash"], "logo"),
            scraped_at=row["scraped_at"],
            works=[
                ItemWork(
                    w["id"],
                    join_path(w["folder_path"], w["path"]),
                    bool(w["manual"]),
                    bool(w["ai_matched"]),
                )
                for w in works
            ],
            versions=[
                ItemVersion(
                    id=v["id"],
                    media_file_id=v["media_file_id"],
                    path=join_path(v["folder_path"], v["rel_path"]),
                    size=v["size"],
                    state=v["state"],
                    label=v["label"],
                    manual=bool(v["manual"]),
                    season=v["season"],
                    episode=v["episode"],
                )
                for v in versions
            ],
        )


def join_path(folder_path: str, rel_path: str) -> str:
    """资料库目录的 115 路径加上相对路径，管理员在 115 里能按它找到文件。"""
    return f"{folder_path.rstrip('/')}/{rel_path}" if rel_path else folder_path


_FILE_JOINS = (
    "JOIN library_folder f ON f.id = c.folder_id LEFT JOIN work w ON w.id = c.work_id "
    "LEFT JOIN version v ON v.media_file_id = c.id"
)
# 没有挂到条目上、也不在等待匹配的作品里的视频：被拆出来的，或作品匹配了但识别不出集号的
_UNPLACED = (
    "c.kind = 'video' AND c.state = 'active' AND v.id IS NULL "
    "AND (c.detached = 1 OR w.id IS NULL OR w.state = 'matched')"
)
# 与 _file_view 的原因判断保持一致
_FILE_REASON = (
    f"CASE WHEN c.ignore_reason IS NOT NULL THEN c.ignore_reason "
    f"WHEN c.detached = 1 THEN '{DETACHED_REASON}' "
    f"WHEN w.state = 'matched' AND l.kind = 'series' THEN '{NO_EPISODE_REASON}' "
    f"ELSE '{NO_ITEM_REASON}' END"
)
_IGNORED = "c.kind = 'video' AND c.state = 'ignored'"
# 视频都已经不在 115 上的作品不需要处理，等它们被清理掉
_HAS_VIDEO = (
    "EXISTS (SELECT 1 FROM media_file c WHERE c.work_id = w.id AND c.kind = 'video' "
    "AND c.state = 'active')"
)
_ITEM_SELECT = """
    SELECT m.id, m.library_id, l.name AS library_name, m.kind, m.provider_id, m.remote_id,
        m.title,
        m.original_title, m.year, m.added_at, i.hash AS poster_hash,
        (SELECT COUNT(*) FROM version v JOIN playable p ON p.id = v.playable_id
            WHERE p.media_item_id = m.id) AS version_count
    FROM media_item m JOIN library l ON l.id = m.library_id
    LEFT JOIN image i ON i.id = m.poster_image_id
"""


def _library_scope(library_id: int | None) -> tuple[str, tuple[int, ...]]:
    return ("AND f.library_id = ?", (library_id,)) if library_id is not None else ("", ())


def _candidates(data: str) -> list[CandidateView]:
    # 0027 之前保存的候选只有 tmdb_id，都来自 TMDB
    return [
        CandidateView(
            c.get("provider_id", TMDB_PROVIDER),
            str(c["remote_id"] if "remote_id" in c else c["tmdb_id"]),
            c["title"],
            c.get("original_title"),
            c.get("year"),
            c.get("score"),
            tuple(c.get("reasons", ())),
        )
        for c in json.loads(data)
    ]


def _item_ref(row: sqlite3.Row) -> ItemRef | None:
    if row["item_id"] is None:
        return None
    return ItemRef(
        row["item_id"], row["item_provider_id"], row["item_remote_id"], row["item_title"]
    )


def _file_view(row: sqlite3.Row) -> FileView:
    if row["ignore_reason"] is not None:
        reason = str(row["ignore_reason"])
    elif row["detached"]:
        reason = DETACHED_REASON
    elif row["work_state"] == "matched" and row["kind"] == "series":
        reason = NO_EPISODE_REASON
    else:
        reason = NO_ITEM_REASON
    return FileView(
        id=row["id"],
        library_id=row["library_id"],
        library_name=row["library_name"],
        kind=row["kind"],
        path=join_path(row["folder_path"], row["rel_path"]),
        size=row["size"],
        reason=reason,
        item=_item_ref(row),
    )


def _item_view(row: sqlite3.Row) -> ItemView:
    return ItemView(
        id=row["id"],
        library_id=row["library_id"],
        library_name=row["library_name"],
        kind=row["kind"],
        provider_id=row["provider_id"],
        remote_id=row["remote_id"],
        title=row["title"],
        original_title=row["original_title"],
        year=row["year"],
        poster=image_sizes(row["poster_hash"], "poster"),
        version_count=row["version_count"],
        added_at=row["added_at"],
    )
