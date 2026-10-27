from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from app.clock import Clock, to_iso
from app.library.media import MediaKind, media_kind

# moved：库里还有一条有效记录指向同一个来源文件，是只同步了新位置的目录时看到的移动
EnrichOutcome = Literal["added", "restored", "renamed", "moved"]


@dataclass(frozen=True)
class SnapshotDiff:
    # 从快照里消失的路径数（已标记失效），不含 NFO；新增数在补全阶段统计
    vanished: int


@dataclass(frozen=True)
class PendingFile:
    id: int
    folder_id: int
    library_id: int
    kind: MediaKind
    rel_path: str
    # 补全过的文件才有来源稳定键；从未补全过的记录消失时直接删除
    enriched: bool
    missing_at: str | None


@dataclass(frozen=True)
class FileEntry:
    """已扫描文件的来源信息，核心只保存来源给出的标识和播放定位符。"""

    identity: str
    name: str
    size: int
    playback_locator: str
    sha1: str


class MediaFileRepository:
    """来源文件的同步写入：快照比对、补全、失效和按 sha1 恢复关联（ADR 0007）。"""

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        self._db = connection
        self._clock = clock

    def apply_snapshot(
        self,
        folder_id: int,
        paths: Iterable[str],
        scope: Callable[[str], bool] | None = None,
    ) -> SnapshotDiff:
        """用来源扫描的路径和数据库按路径比对。

        新路径和重新出现的失效路径进入待补全；消失的路径标记失效，保留记录和失效时间。
        scope 是定向同步的范围：只有范围内的记录会因为不在 paths 里而失效。
        """
        paths = list(paths)
        media = _media_files(paths)
        snapshot_paths = set(paths)
        now = to_iso(self._clock.now())
        existing = {
            row["rel_path"]: row
            for row in self._db.execute(
                "SELECT id, rel_path, kind, state, identity FROM media_file WHERE folder_id = ?",
                (folder_id,),
            )
        }
        for path, kind in media.items():
            row = existing.get(path)
            if row is None:
                self._db.execute(
                    "INSERT INTO media_file (folder_id, rel_path, kind, state, created_at, "
                    "updated_at) VALUES (?, ?, ?, 'pending_enrich', ?, ?)",
                    (folder_id, path, kind, now, now),
                )
            elif row["state"] == "missing":
                self._db.execute(
                    "UPDATE media_file SET state = 'pending_enrich', updated_at = ? WHERE id = ?",
                    (now, row["id"]),
                )
        vanished = 0
        for path, row in existing.items():
            if path in media or row["state"] == "missing" or (scope and not scope(path)):
                continue
            # 过滤规则只是不把原盘文件当作可识别媒体；它们仍然存在于来源快照里，
            # 不能被误判成失效。后续同步也保留 ignored 状态，避免反复进入待处理。
            if _is_disc_like(path) and path in snapshot_paths:
                self.mark_ignored(row["id"], now)
                continue
            if row["identity"] is None:
                self._db.execute("DELETE FROM media_file WHERE id = ?", (row["id"],))
            else:
                self.mark_missing(row["id"], now)
                vanished += row["kind"] != "nfo"
        return SnapshotDiff(vanished=vanished)

    def pending(self, folder_ids: Sequence[int]) -> list[PendingFile]:
        if not folder_ids:
            return []
        marks = ", ".join("?" for _ in folder_ids)
        rows = self._db.execute(
            "SELECT c.id, c.folder_id, f.library_id, c.kind, c.rel_path, c.identity, c.missing_at "
            "FROM media_file c JOIN library_folder f ON f.id = c.folder_id "
            f"WHERE c.state = 'pending_enrich' AND c.folder_id IN ({marks}) "
            "ORDER BY c.folder_id, c.rel_path",
            tuple(folder_ids),
        ).fetchall()
        return [
            PendingFile(
                id=row["id"],
                folder_id=row["folder_id"],
                library_id=row["library_id"],
                kind=row["kind"],
                rel_path=row["rel_path"],
                enriched=row["identity"] is not None,
                missing_at=row["missing_at"],
            )
            for row in rows
        ]

    def enrich(self, file: PendingFile, entry: FileEntry, job_started_at: str) -> EnrichOutcome:
        """写入补全得到的来源信息。

        新文件如果是同一资料库里已有的文件挪过来的，就把原来那条记录挪到新路径，
        保留它的身份（播放记录挂在它上面），删掉刚建的新记录。先按来源稳定身份认，
        认不出再找 sha1 相同的失效文件（复制后删除原文件时 ID 会变）。
        """
        now = to_iso(self._clock.now())
        if file.missing_at is not None:
            self.set_active(file.id, entry, now)
            return "restored"
        lost = self._same_file(file, entry, now)
        if lost is None and entry.sha1:
            lost = self._db.execute(
                "SELECT c.id, c.missing_at FROM media_file c "
                "JOIN library_folder f ON f.id = c.folder_id "
                "WHERE f.library_id = ? AND c.state = 'missing' AND c.sha1 = ? AND c.kind = ? "
                "ORDER BY c.missing_at DESC, c.id DESC LIMIT 1",
                (file.library_id, entry.sha1, file.kind),
            ).fetchone()
        if lost is None:
            self.set_active(file.id, entry, now)
            return "added"
        self._db.execute("DELETE FROM media_file WHERE id = ?", (file.id,))
        self._db.execute(
            "UPDATE media_file SET folder_id = ?, rel_path = ? WHERE id = ?",
            (file.folder_id, file.rel_path, lost["id"]),
        )
        self.set_active(lost["id"], entry, now)
        if lost["missing_at"] is None:
            return "moved"
        # 这次同步才失效的，是在两次同步之间被改名或移动
        return "renamed" if lost["missing_at"] >= job_started_at else "restored"

    def _same_file(self, file: PendingFile, entry: FileEntry, now: str) -> sqlite3.Row | None:
        """同一资料库里指向这个来源文件的记录。

        别的资料库里的记录标记失效：文件挪出了那个资料库。
        """
        rows = self._db.execute(
            "SELECT c.id, c.missing_at, c.kind, f.library_id FROM media_file c "
            "JOIN library_folder f ON f.id = c.folder_id "
            "WHERE c.identity = ? AND c.id != ? AND c.state != 'pending_enrich'",
            (entry.identity, file.id),
        ).fetchall()
        same = None
        for row in rows:
            if row["library_id"] == file.library_id and row["kind"] == file.kind:
                same = same or row
            else:
                self.mark_missing(row["id"], now)
        return same

    def drop(self, file: PendingFile) -> bool:
        """补全时在来源上找不到这个文件。补全过的标记失效并返回 True，没补全过的直接删除。"""
        if not file.enriched:
            self._db.execute("DELETE FROM media_file WHERE id = ?", (file.id,))
            return False
        self.mark_missing(file.id, to_iso(self._clock.now()))
        return True

    def set_active(self, media_file_id: int, entry: FileEntry, now: str) -> None:
        self._db.execute(
            "UPDATE media_file SET state = 'active', identity = ?, playback_locator = ?, "
            "size = ?, sha1 = ?, missing_at = NULL, updated_at = ? WHERE id = ?",
            (
                entry.identity,
                entry.playback_locator,
                entry.size,
                entry.sha1 or None,
                now,
                media_file_id,
            ),
        )

    def item_dirs(self, item_id: int) -> list[tuple[int, str, bool]]:
        """条目的作品所在的目录：(资料库目录 ID, 相对路径, 是否是作品自己的目录)。

        作品路径下有文件时它就是作品目录；否则是散放的视频（作品路径是文件名），取它的上级目录。
        """
        rows = self._db.execute(
            "SELECT DISTINCT w.folder_id, w.path, EXISTS (SELECT 1 FROM media_file c "
            "WHERE c.folder_id = w.folder_id AND substr(c.rel_path, 1, length(w.path) + 1) "
            "= w.path || '/') AS own FROM work w WHERE w.media_item_id = ? "
            "ORDER BY w.folder_id, w.path",
            (item_id,),
        ).fetchall()
        dirs: dict[tuple[int, str], bool] = {}
        for row in rows:
            own = bool(row["own"]) and row["path"] != ""
            path = row["path"] if own else row["path"].rpartition("/")[0]
            key = (row["folder_id"], path)
            dirs[key] = dirs.get(key, False) or own
        return [(folder_id, path, own) for (folder_id, path), own in dirs.items()]

    def known_entries(self, folder_id: int) -> dict[str, sqlite3.Row]:
        """库里已经补全过的文件（有效的和被过滤规则忽略的），按相对路径索引。
        扫描时来源可以直接复用它们，不必再去访问云端。"""
        rows = self._db.execute(
            "SELECT rel_path, kind, identity, playback_locator, size, sha1 FROM media_file "
            "WHERE folder_id = ? AND state IN ('active', 'ignored')",
            (folder_id,),
        )
        return {row["rel_path"]: row for row in rows}

    def known_dirs(self, folder_id: int, prefix: str) -> set[str]:
        """prefix 下库里有未失效文件的子目录名（只取下一级）。"""
        rows = self._db.execute(
            "SELECT rel_path FROM media_file WHERE folder_id = ? AND state != 'missing' "
            "AND substr(rel_path, 1, ?) = ?",
            (folder_id, len(prefix), prefix),
        )
        names = set()
        for row in rows:
            first, nested, _ = row["rel_path"][len(prefix) :].partition("/")
            if nested:
                names.add(first)
        return names

    def has_present(self, folder_id: int) -> bool:
        """资料库目录下还有没失效的文件。"""
        row = self._db.execute(
            "SELECT 1 FROM media_file WHERE folder_id = ? AND state != 'missing' LIMIT 1",
            (folder_id,),
        ).fetchone()
        return row is not None

    def mark_missing(self, media_file_id: int, now: str) -> None:
        self._db.execute(
            "UPDATE media_file SET state = 'missing', missing_at = COALESCE(missing_at, ?), "
            "updated_at = ? WHERE id = ?",
            (now, now, media_file_id),
        )

    def mark_ignored(self, media_file_id: int, now: str) -> None:
        """过滤掉仍存在于来源快照中的原盘文件，不参与识别和失效清理。"""
        self._db.execute("DELETE FROM version WHERE media_file_id = ?", (media_file_id,))
        self._db.execute(
            "UPDATE media_file SET state = 'ignored', ignore_reason = ?, work_id = NULL, "
            "missing_at = NULL, updated_at = ? WHERE id = ?",
            ("蓝光/ISO 原盘，已忽略", now, media_file_id),
        )


def _media_files(paths: Iterable[str]) -> dict[str, MediaKind]:
    """从来源路径里挑出视频、字幕和 NFO 文件。

    扫描结果不区分文件和目录，所以凡是做过别人上级的路径都是目录。
    剧集的单集 NFO（与视频同名）也入库，只用于 WebDAV 展示；匹配仍只读 tvshow.nfo。
    """
    paths = list(paths)
    dirs = {path.rpartition("/")[0] for path in paths}
    media: dict[str, MediaKind] = {}
    for path in paths:
        if path in dirs:
            continue
        name = path.rpartition("/")[2]
        # 光盘镜像和原盘目录不是可独立识别的影视文件：跳过整个结构，避免把
        # BDMV/STREAM 下的 m2ts 拆成多部电影。独立的 m2ts 仍然允许入库。
        if _is_disc_like(path):
            continue
        kind = media_kind(name)
        if kind is not None:
            media[path] = kind
    return media


def _is_disc_like(path: str) -> bool:
    upper = path.upper()
    return path.lower().endswith(".iso") or "/BDMV/" in upper or "/VIDEO_TS/" in upper
