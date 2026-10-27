"""WebDAV 目录树需要的文件清单：用户可见资料库里已同步的视频、字幕和 NFO。"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal, cast

from app.library.nfo import nfo_for_work
from app.library.recognize import LibraryKind, normalize


@dataclass(frozen=True)
class MediaFile:
    id: int
    rel_path: str
    size: int | None
    updated_at: str
    library_name: str
    media_kind: str
    title: str
    year: int | None
    playable_id: int
    episode_number: int | None
    season_number: int | None
    episode_name: str | None
    version_label: str | None
    # 只有字幕文件有
    subtitle_language: str | None


@dataclass(frozen=True)
class NfoFile:
    id: int
    rel_path: str
    size: int | None
    updated_at: str
    library_name: str
    media_kind: str
    title: str
    year: int | None


@dataclass(frozen=True)
class OnlineSubtitleFile:
    """在线字幕以及它绑定的视频；WebDAV 列在视频同一个目录里。"""

    subtitle_id: int
    version_id: int
    label: str
    language: str | None
    format: str
    size: int
    created_at: str
    video: MediaFile


_COLUMNS = """
    c.id, c.rel_path, c.size, c.updated_at, l.name AS library_name, m.kind AS media_kind,
    m.title, m.year, p.id AS playable_id, e.number AS episode_number,
    se.number AS season_number, e.name AS episode_name, v.label AS version_label
"""
_VIDEO_JOIN = "JOIN version v ON v.media_file_id = c.id"
_SUBTITLE_JOIN = "JOIN subtitle s ON s.media_file_id = c.id JOIN version v ON v.id = s.version_id"
_COMMON_JOINS = """
    JOIN playable p ON p.id = v.playable_id
    JOIN media_item m ON m.id = p.media_item_id
    LEFT JOIN episode e ON e.id = p.episode_id
    LEFT JOIN season se ON se.id = e.season_id
    JOIN library_folder f ON f.id = c.folder_id
    JOIN library l ON l.id = f.library_id
"""


class WebDavFileQueries:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def library_names(self, library_ids: Sequence[int]) -> list[str]:
        """按给定顺序返回存在的资料库名称。"""
        names: list[str] = []
        for library_id in library_ids:
            row = self._db.execute(
                "SELECT name FROM library WHERE id = ?", (library_id,)
            ).fetchone()
            if row is not None:
                names.append(str(row["name"]))
        return names

    def videos(self, library_ids: Sequence[int]) -> list[MediaFile]:
        return self._files(library_ids, "video", _VIDEO_JOIN, "NULL")

    def subtitles(self, library_ids: Sequence[int]) -> list[MediaFile]:
        return self._files(library_ids, "subtitle", _SUBTITLE_JOIN, "s.language")

    def online_subtitles(self, library_ids: Sequence[int]) -> list[OnlineSubtitleFile]:
        """在线字幕和它所属版本对应的视频，按视频的目录和顺序排列。"""
        marks = ", ".join("?" for _ in library_ids)
        rows = self._db.execute(
            f"""
            SELECT s.id, s.version_id, s.label, s.language, o.format, o.size, o.created_at,
                {_COLUMNS}, NULL AS subtitle_language
            FROM subtitle s
            JOIN online_subtitle o ON o.subtitle_id = s.id
            JOIN version v ON v.id = s.version_id
            JOIN media_file c ON c.id = v.media_file_id
            {_COMMON_JOINS}
            WHERE s.source = 'online' AND c.state = 'active' AND c.identity IS NOT NULL
                AND c.kind = 'video' AND l.id IN ({marks})
            ORDER BY l.sort_order, l.id, m.title, p.id, s.id
            """,
            tuple(library_ids),
        ).fetchall()
        return [
            OnlineSubtitleFile(
                subtitle_id=int(row[0]),
                version_id=int(row[1]),
                label=str(row[2]),
                language=row[3],
                format=str(row[4]),
                size=int(row[5]),
                created_at=str(row[6]),
                video=MediaFile(*row[7:]),
            )
            for row in rows
        ]

    def _files(
        self,
        library_ids: Sequence[int],
        kind: Literal["video", "subtitle"],
        first_join: str,
        language: str,
    ) -> list[MediaFile]:
        marks = ", ".join("?" for _ in library_ids)
        rows = self._db.execute(
            f"""
            SELECT {_COLUMNS}, {language} AS subtitle_language
            FROM media_file c
            {first_join}
            {_COMMON_JOINS}
            WHERE c.state = 'active' AND c.identity IS NOT NULL AND c.kind = '{kind}'
                AND l.id IN ({marks})
            ORDER BY l.sort_order, l.id, m.title, p.id, c.id
            """,
            tuple(library_ids),
        ).fetchall()
        return [MediaFile(*row) for row in rows]

    def episode_nfos(self, library_ids: Sequence[int]) -> list[MediaFile]:
        """剧集里和视频同名的单集 NFO，带着对应视频的归属信息；找不到视频的孤立 NFO 不返回。"""
        marks = ", ".join("?" for _ in library_ids)
        videos = self._db.execute(
            f"""
            SELECT {_COLUMNS}, NULL AS subtitle_language, c.folder_id
            FROM media_file c {_VIDEO_JOIN} {_COMMON_JOINS}
            WHERE c.state = 'active' AND c.identity IS NOT NULL AND c.kind = 'video'
                AND m.kind = 'series' AND l.id IN ({marks})
            ORDER BY l.sort_order, l.id, m.title, p.id, c.id
            """,
            tuple(library_ids),
        ).fetchall()
        nfos = {
            (r["folder_id"], r["rel_path"].lower()): r
            for r in self._db.execute(
                f"""
                SELECT c.id, c.folder_id, c.rel_path, c.size, c.updated_at FROM media_file c
                JOIN library_folder f ON f.id = c.folder_id
                WHERE c.state = 'active' AND c.identity IS NOT NULL AND c.kind = 'nfo'
                    AND f.library_id IN ({marks})
                """,
                tuple(library_ids),
            )
        }
        result: list[MediaFile] = []
        for row in videos:
            video = MediaFile(*row[:-1])
            stem = video.rel_path.rpartition(".")[0]
            nfo = nfos.get((row["folder_id"], f"{stem}.nfo".lower()))
            if nfo is not None:
                result.append(
                    replace(
                        video,
                        id=nfo["id"],
                        rel_path=nfo["rel_path"],
                        size=nfo["size"],
                        updated_at=nfo["updated_at"],
                    )
                )
        return result

    def nfos(self, library_ids: Sequence[int]) -> list[NfoFile]:
        """已匹配作品对应的 NFO。入库时 NFO 不记录归属，这里按同步时的规则重新定位。"""
        marks = ", ".join("?" for _ in library_ids)
        works = self._db.execute(
            f"""
            SELECT w.id, w.folder_id, w.path, l.name AS library_name, l.kind AS library_kind,
                m.kind AS media_kind, m.title, m.year
            FROM work w
            JOIN media_item m ON m.id = w.media_item_id
            JOIN library_folder f ON f.id = w.folder_id
            JOIN library l ON l.id = f.library_id
            WHERE w.state = 'matched' AND l.id IN ({marks})
            ORDER BY l.sort_order, l.id, m.title, w.id
            """,
            tuple(library_ids),
        ).fetchall()
        folders = {int(w["folder_id"]) for w in works}
        nfo_rows: dict[int, dict[str, sqlite3.Row]] = {}
        video_paths: dict[int, list[str]] = {}
        for folder_id in folders:
            rows = self._db.execute(
                """
                SELECT id, rel_path, size, updated_at, kind, work_id FROM media_file
                WHERE folder_id = ? AND state = 'active' AND identity IS NOT NULL
                    AND kind IN ('video', 'nfo')
                """,
                (folder_id,),
            ).fetchall()
            nfo_rows[folder_id] = {r["rel_path"]: r for r in rows if r["kind"] == "nfo"}
            for r in rows:
                if r["kind"] == "video" and r["work_id"] is not None:
                    video_paths.setdefault(int(r["work_id"]), []).append(r["rel_path"])
        # 每个根目录的 NFO 只按目录分一次组；nfo_for_work 只会查作品目录和单文件作品所在目录，
        # 每个作品只传这两处的 NFO。整份传进去会让耗时随 作品数 × NFO 数 增长（大库 20 秒以上）
        nfo_by_dir: dict[int, dict[str, list[str]]] = {}
        for folder_id, rows_by_path in nfo_rows.items():
            grouped: dict[str, list[str]] = {}
            for rel_path in rows_by_path:
                grouped.setdefault(normalize(rel_path.rpartition("/")[0]), []).append(rel_path)
            nfo_by_dir[folder_id] = grouped
        result: list[NfoFile] = []
        for w in works:
            candidates = nfo_rows[int(w["folder_id"])]
            videos = video_paths.get(int(w["id"]), [])
            directories = {w["path"]}
            directories.update(
                normalize(v.rpartition("/")[0]) for v in videos if normalize(v) == w["path"]
            )
            by_dir = nfo_by_dir[int(w["folder_id"])]
            nearby = [path for directory in directories for path in by_dir.get(directory, [])]
            path = nfo_for_work(cast(LibraryKind, w["library_kind"]), w["path"], videos, nearby)
            if path is not None:
                n = candidates[path]
                result.append(
                    NfoFile(
                        n["id"],
                        path,
                        n["size"],
                        n["updated_at"],
                        w["library_name"],
                        w["media_kind"],
                        w["title"],
                        w["year"],
                    )
                )
        return result
