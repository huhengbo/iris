"""影视目录：把云端文件归到作品、过滤附加内容、按匹配结果挂到条目的版本上（ADR 0012、0016）。

只读写数据库，不访问 115 和 TMDB。同步时先 assign_works（识别、分组、过滤），匹配 TMDB 之后
再 place_folder（建季、集、可播放条目和版本，关联字幕，清理空壳）。两步都可以重复执行。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from iris_plugin_sdk import MediaContext

from app.clock import Clock, to_iso
from app.errors import NotFound
from app.library.nfo import nfo_for_work
from app.library.recognize import LibraryKind, ParsedName, Recognized, recognize
from app.library.subtitle_plugin import (
    LocalSubtitleCandidate,
    LocalSubtitleRequest,
    SubtitleMatcher,
)

DISC_EXTRA_REASON = "原盘中的非正片片段"


@dataclass(frozen=True)
class NfoTask:
    """需要下载读取的 NFO：作品还在等待匹配，而它的 NFO 没读过或者变了。"""

    work_id: int
    identity: str
    sha1: str


@dataclass(frozen=True)
class AssignResult:
    nfo_tasks: list[NfoTask]
    # 这次新标为附加内容而忽略的视频数
    ignored: int


@dataclass(frozen=True)
class FolderInfo:
    id: int
    library_id: int
    kind: LibraryKind
    # 资料库目录自己的名字，作品目录就是资料库目录时用它识别片名
    root_name: str


@dataclass(frozen=True)
class Target:
    """文件要挂到的位置：电影的条目，或剧集某一季的某一集。"""

    media_item_id: int
    season: int | None = None
    episode: int | None = None


class CatalogRepository:
    def __init__(
        self,
        connection: sqlite3.Connection,
        clock: Clock,
        subtitles: SubtitleMatcher | None = None,
    ) -> None:
        self._db = connection
        self._clock = clock
        # 宿主内置的外挂字幕匹配；为 None 时 place_folder 不改动已有的字幕关联
        self._subtitles = subtitles

    def folder(self, folder_id: int) -> FolderInfo:
        row = self._db.execute(
            "SELECT f.id, f.library_id, l.kind, f.path FROM library_folder f "
            "JOIN library l ON l.id = f.library_id WHERE f.id = ?",
            (folder_id,),
        ).fetchone()
        if row is None:
            raise NotFound("资料库目录不存在")
        return FolderInfo(row["id"], row["library_id"], row["kind"], folder_name(row["path"]))

    # ---- 识别与分组 ----

    def assign_works(
        self, folder_id: int, kind: LibraryKind, root_name: str, extras_min_bytes: int
    ) -> AssignResult:
        """识别资料库目录下的视频：附加内容标为忽略，其余按作品分组。

        失效的文件保持原样（版本保留，恢复后还能播放）。管理员指定过位置或拆出来的文件不动。
        """
        now = to_iso(self._clock.now())
        rows = self._files(folder_id)
        videos = [r for r in rows if r["kind"] == "video" and r["state"] in ("active", "ignored")]
        recognized = {r["id"]: recognize(kind, r["rel_path"], root_name) for r in videos}
        largest = _largest_per_disc(videos, recognized)
        ignored = 0
        work_videos: dict[int, list[str]] = {}
        for row in videos:
            if row["detached"] or row["version_manual"]:
                continue
            rec = recognized[row["id"]]
            reason = rec.extra
            if reason is None and rec.disc is not None and largest.get(rec.disc) != row["id"]:
                reason = DISC_EXTRA_REASON
            if (
                reason is None
                and kind == "movie"
                and rec.disc is None
                and (row["size"] or 0) < extras_min_bytes
            ):
                reason = f"小于 {extras_min_bytes // (1024 * 1024)} MB"
            if reason is not None:
                if row["state"] != "ignored":
                    ignored += 1
                self._db.execute(
                    "UPDATE media_file SET state = 'ignored', ignore_reason = ?, work_id = NULL, "
                    "updated_at = ? WHERE id = ?",
                    (reason, now, row["id"]),
                )
                if row["version_id"] is not None:
                    self._db.execute("DELETE FROM version WHERE id = ?", (row["version_id"],))
                continue
            work_id = self.ensure_work(folder_id, rec.work_path, rec.name, now)
            work_videos.setdefault(work_id, []).append(row["rel_path"])
            if row["state"] == "ignored" or row["work_id"] != work_id:
                self._db.execute(
                    "UPDATE media_file SET state = 'active', ignore_reason = NULL, work_id = ?, "
                    "updated_at = ? WHERE id = ?",
                    (work_id, now, row["id"]),
                )
        # 没有文件的作品（文件被删除、改名到别的作品或成了附加内容）
        self._db.execute(
            "DELETE FROM work WHERE folder_id = ? AND id NOT IN "
            "(SELECT work_id FROM media_file WHERE work_id IS NOT NULL)",
            (folder_id,),
        )
        return AssignResult(self._nfo_tasks(kind, rows, work_videos), ignored)

    def ensure_work(self, folder_id: int, path: str, name: ParsedName, now: str) -> int:
        row = self._db.execute(
            "SELECT id, title, alt_title, year, tmdb_hint, state, media_item_id, manual "
            "FROM work WHERE folder_id = ? AND path = ?",
            (folder_id, path),
        ).fetchone()
        if row is None:
            cursor = self._db.execute(
                "INSERT INTO work (folder_id, path, title, alt_title, year, tmdb_hint, state, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                (folder_id, path, name.title, name.alt_title, name.year, name.tmdb_id, now, now),
            )
            assert cursor.lastrowid is not None
            return cursor.lastrowid
        parsed = (name.title, name.alt_title, name.year, name.tmdb_id)
        changed = parsed != (row["title"], row["alt_title"], row["year"], row["tmdb_hint"])
        # 没匹配上的作品识别结果变了（识别规则更新、目录改了名）、目录里新加了 {tmdb-ID} 标记，
        # 或者匹配到的条目被删了，都重新匹配；管理员指定的不动
        lost = row["state"] == "matched" and row["media_item_id"] is None
        new_hint = name.tmdb_id is not None and name.tmdb_id != row["tmdb_hint"]
        if not row["manual"] and (lost or new_hint or (changed and row["state"] != "matched")):
            self._db.execute(
                "UPDATE work SET title = ?, alt_title = ?, year = ?, tmdb_hint = ?, "
                "state = 'pending', reason = NULL, updated_at = ? WHERE id = ?",
                (*parsed, now, row["id"]),
            )
        elif changed:
            self._db.execute(
                "UPDATE work SET title = ?, alt_title = ?, year = ?, tmdb_hint = ?, "
                "updated_at = ? WHERE id = ?",
                (*parsed, now, row["id"]),
            )
        return int(row["id"])

    def set_nfo(self, work_id: int, sha1: str, tmdb_id: int | None, imdb_id: str | None) -> None:
        self._db.execute(
            "UPDATE work SET nfo_sha1 = ?, nfo_tmdb_id = ?, nfo_imdb_id = ? WHERE id = ?",
            (sha1, tmdb_id, imdb_id, work_id),
        )

    def _nfo_tasks(
        self, kind: LibraryKind, rows: list[sqlite3.Row], work_videos: dict[int, list[str]]
    ) -> list[NfoTask]:
        nfos = {
            r["rel_path"]: r
            for r in rows
            if r["kind"] == "nfo" and r["state"] == "active" and r["identity"] and r["sha1"]
        }
        if not nfos or not work_videos:
            return []
        marks = ", ".join("?" for _ in work_videos)
        works = self._db.execute(
            f"SELECT id, path, nfo_sha1 FROM work WHERE id IN ({marks}) "
            "AND state = 'pending' AND manual = 0",
            tuple(work_videos),
        ).fetchall()
        tasks = []
        for work in works:
            path = nfo_for_work(kind, work["path"], work_videos[work["id"]], list(nfos))
            if path is not None and nfos[path]["sha1"] != work["nfo_sha1"]:
                tasks.append(NfoTask(work["id"], nfos[path]["identity"], nfos[path]["sha1"]))
        return tasks

    # ---- 挂到条目 ----

    def place_folder(self, folder_id: int, kind: LibraryKind, root_name: str) -> None:
        """按作品的匹配结果给视频建版本，关联字幕，最后清理没有内容的条目。"""
        now = to_iso(self._clock.now())
        rows = self._files(folder_id)
        items = {
            r["id"]: r["media_item_id"]
            for r in self._db.execute(
                "SELECT id, media_item_id FROM work WHERE folder_id = ? AND state = 'matched'",
                (folder_id,),
            )
        }
        placed: dict[str, int] = {}
        for row in rows:
            if row["kind"] != "video":
                continue
            rec = recognize(kind, row["rel_path"], root_name)
            if row["version_manual"]:
                # 位置是管理员指定的不动；体积可能在重新补全后变了，标签跟着更新
                self._db.execute(
                    "UPDATE version SET label = ? WHERE id = ?",
                    (rec.version.label(row["size"]), row["version_id"]),
                )
                placed[row["rel_path"]] = row["version_id"]
                continue
            if row["state"] == "missing":
                # 失效文件的版本保留，恢复后还能播放
                if row["version_id"] is not None:
                    placed[row["rel_path"]] = row["version_id"]
                continue
            target = None
            item_id = items.get(row["work_id"]) if row["state"] == "active" else None
            if item_id is not None and not row["detached"]:
                if kind == "movie":
                    target = Target(item_id)
                elif rec.episode is not None:
                    season = rec.season if rec.season is not None else 1
                    target = Target(item_id, season, rec.episode)
            if target is None:
                if row["version_id"] is not None:
                    self._db.execute("DELETE FROM version WHERE id = ?", (row["version_id"],))
                continue
            placed[row["rel_path"]] = self.put_version(row, rec, target, manual=False, now=now)
        self._attach_subtitles(rows, placed)
        self.prune()

    def put_version(
        self, file: sqlite3.Row, rec: Recognized, target: Target, *, manual: bool, now: str
    ) -> int:
        """把文件挂到目标位置：已有版本时改挂并更新属性，没有时新建。返回版本 ID。"""
        playable_id = self._playable(target, now)
        attributes = json.dumps(rec.version.to_json(), ensure_ascii=False)
        label = rec.version.label(file["size"])
        if file["version_id"] is not None:
            self._db.execute(
                "UPDATE version SET playable_id = ?, attributes = ?, label = ?, manual = ? "
                "WHERE id = ?",
                (playable_id, attributes, label, int(manual), file["version_id"]),
            )
            return int(file["version_id"])
        cursor = self._db.execute(
            "INSERT INTO version (playable_id, media_file_id, attributes, label, manual, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (playable_id, file["id"], attributes, label, int(manual), now),
        )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def prune(self) -> None:
        """删除没有版本的可播放条目，以及因此变空的集、季和不再被作品引用的条目。"""
        self._db.execute("DELETE FROM playable WHERE id NOT IN (SELECT playable_id FROM version)")
        self._db.execute(
            "DELETE FROM episode WHERE id NOT IN "
            "(SELECT episode_id FROM playable WHERE episode_id IS NOT NULL)"
        )
        self._db.execute("DELETE FROM season WHERE id NOT IN (SELECT season_id FROM episode)")
        self._db.execute(
            "DELETE FROM media_item WHERE id NOT IN (SELECT media_item_id FROM playable) "
            "AND id NOT IN (SELECT media_item_id FROM work WHERE media_item_id IS NOT NULL)"
        )

    def file(self, media_file_id: int) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self._db.execute(
            f"{_FILES} WHERE c.id = ?", (media_file_id,)
        ).fetchone()
        return row

    def _playable(self, target: Target, now: str) -> int:
        if target.season is None or target.episode is None:
            row = self._db.execute(
                "SELECT id FROM playable WHERE media_item_id = ? AND episode_id IS NULL",
                (target.media_item_id,),
            ).fetchone()
            if row is not None:
                return int(row["id"])
            return self._insert_playable(target.media_item_id, None, now)
        season = self._db.execute(
            "SELECT id FROM season WHERE media_item_id = ? AND number = ?",
            (target.media_item_id, target.season),
        ).fetchone()
        if season is None:
            cursor = self._db.execute(
                "INSERT INTO season (media_item_id, number) VALUES (?, ?)",
                (target.media_item_id, target.season),
            )
            season_id = cursor.lastrowid
        else:
            season_id = season["id"]
        episode = self._db.execute(
            "SELECT id FROM episode WHERE season_id = ? AND number = ?",
            (season_id, target.episode),
        ).fetchone()
        if episode is None:
            cursor = self._db.execute(
                "INSERT INTO episode (season_id, number) VALUES (?, ?)",
                (season_id, target.episode),
            )
            episode_id = cursor.lastrowid
            # 新的一集：下次刮削时只刷新这一季（ADR 0016）
            self._db.execute("UPDATE season SET scraped_at = NULL WHERE id = ?", (season_id,))
        else:
            episode_id = episode["id"]
        row = self._db.execute(
            "SELECT id FROM playable WHERE episode_id = ?", (episode_id,)
        ).fetchone()
        if row is not None:
            return int(row["id"])
        return self._insert_playable(target.media_item_id, episode_id, now)

    def _insert_playable(self, media_item_id: int, episode_id: Any, now: str) -> int:
        cursor = self._db.execute(
            "INSERT INTO playable (media_item_id, episode_id, created_at) VALUES (?, ?, ?)",
            (media_item_id, episode_id, now),
        )
        assert cursor.lastrowid is not None
        if episode_id is not None:
            # 剧集有新的一集：按「最近添加」排序时顶到前面
            self._db.execute(
                "UPDATE media_item SET added_at = ? WHERE id = ? AND added_at < ?",
                (now, media_item_id, now),
            )
        return cursor.lastrowid

    def _attach_subtitles(self, rows: list[sqlite3.Row], placed: dict[str, int]) -> None:
        if self._subtitles is None:
            return
        files = tuple(
            r["rel_path"] for r in rows if r["kind"] == "subtitle" and r["state"] == "active"
        )
        # 字幕文件 → 最具体（视频名最长）的候选及其视频
        best: dict[str, tuple[float, str, LocalSubtitleCandidate]] = {}
        for video in placed:
            request = LocalSubtitleRequest(MediaContext(title="", filename=video), files=files)
            for candidate in self._subtitles.search(request):
                score = candidate.score or 0.0
                if candidate.remote_id not in best or score > best[candidate.remote_id][0]:
                    best[candidate.remote_id] = (score, video, candidate)
        for row in rows:
            if row["kind"] != "subtitle":
                continue
            found = best.get(row["rel_path"])
            if found is None:
                self._db.execute("DELETE FROM subtitle WHERE media_file_id = ?", (row["id"],))
                continue
            _, video, candidate = found
            self._db.execute(
                """
                INSERT INTO subtitle (version_id, media_file_id, language, label, forced,
                    is_default) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (media_file_id) DO UPDATE SET version_id = excluded.version_id,
                    language = excluded.language, label = excluded.label,
                    forced = excluded.forced, is_default = excluded.is_default
                """,
                (
                    placed[video],
                    row["id"],
                    candidate.language or None,
                    candidate.label,
                    int(candidate.forced),
                    int(candidate.default),
                ),
            )

    def _files(self, folder_id: int) -> list[sqlite3.Row]:
        return self._db.execute(
            f"{_FILES} WHERE c.folder_id = ? ORDER BY c.rel_path", (folder_id,)
        ).fetchall()


_FILES = """
    SELECT c.id, c.folder_id, c.rel_path, c.kind, c.state, c.size, c.sha1, c.identity,
        c.work_id, c.detached, v.id AS version_id, v.manual AS version_manual
    FROM media_file c LEFT JOIN version v ON v.media_file_id = c.id
"""


def _largest_per_disc(
    videos: list[sqlite3.Row], recognized: dict[int, Recognized]
) -> dict[str, int]:
    """每张蓝光原盘里最大的流文件，当作正片。"""
    best: dict[str, tuple[int, int]] = {}
    for row in videos:
        disc = recognized[row["id"]].disc
        if disc is None:
            continue
        size = row["size"] or 0
        if disc not in best or size > best[disc][0]:
            best[disc] = (size, row["id"])
    return {disc: media_file_id for disc, (_, media_file_id) in best.items()}


def folder_name(path: str) -> str:
    """资料库目录路径的最后一段。"""
    return path.rstrip("/").rpartition("/")[2]
