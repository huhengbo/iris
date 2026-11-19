from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from iris_plugin_sdk import ResourceEntry

from app.clock import Clock, to_iso
from app.library.media import MediaKind, media_kind

# moved：库里还有一条有效记录指向同一个来源文件，是只同步了新位置的目录时看到的移动
EnrichOutcome = Literal["added", "restored", "renamed", "moved"]


def iso_time(epoch: int | None) -> str | None:
    """来源给的 Unix 秒转成库里统一的 ISO 时间；空值或不合理的值（0、负数）按没有处理。"""
    if not epoch or epoch <= 0:
        return None
    return to_iso(datetime.fromtimestamp(epoch, UTC))


def epoch_time(value: str | None) -> int | None:
    return int(datetime.fromisoformat(value).timestamp()) if value else None


@dataclass(frozen=True)
class SnapshotDiff:
    # 从快照里消失的路径数（已标记失效），不含 NFO；新增数在补全阶段统计
    vanished: int
    # 同一路径上换了文件的记录数，已经就地刷新了身份和播放定位符
    refreshed: int = 0


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
    # 来源给出的创建时间（ISO），没有时不覆盖库里已有的值
    created_at: str | None = None


class MediaFileRepository:
    """来源文件的同步写入：快照比对、补全、失效和按 sha1 恢复关联（ADR 0007）。"""

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        self._db = connection
        self._clock = clock

    def apply_snapshot(
        self,
        folder_id: int,
        entries: Sequence[ResourceEntry],
        scope: Callable[[str], bool] | None = None,
    ) -> SnapshotDiff:
        """用来源扫描出的条目和数据库按路径比对。

        新路径和重新出现的失效路径进入待补全；消失的路径标记失效，保留记录和失效时间。
        scope 是定向同步的范围：只有范围内的记录会因为不在快照里而失效。

        同一个路径上的文件被换掉时（重传、替换、重新转存），来源给的稳定身份变了，
        这里就地刷新身份和播放定位符：路径没变，旧定位符在来源上已经失效，不刷新就会
        一直播 404。
        """
        located = {entry.relative_path: entry for entry in entries}
        media = _media_files(located)
        snapshot_paths = set(located)
        now = to_iso(self._clock.now())
        existing = {
            row["rel_path"]: row
            for row in self._db.execute(
                "SELECT id, rel_path, kind, state, identity FROM media_file WHERE folder_id = ?",
                (folder_id,),
            )
        }
        refreshed = 0
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
            elif row["state"] == "active" and _replaced(row, located[path]):
                self.set_active(row["id"], _file_entry(located[path]), now)
                refreshed += 1
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
        return SnapshotDiff(vanished=vanished, refreshed=refreshed)

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
            "size = ?, sha1 = ?, missing_at = NULL, updated_at = ?, "
            "source_created_at = COALESCE(?, source_created_at) WHERE id = ?",
            (
                entry.identity,
                entry.playback_locator,
                entry.size,
                entry.sha1 or None,
                now,
                entry.created_at,
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
            "SELECT rel_path, kind, identity, playback_locator, size, sha1, source_created_at "
            "FROM media_file WHERE folder_id = ? AND state IN ('active', 'ignored')",
            (folder_id,),
        )
        return {row["rel_path"]: row for row in rows}

    def record_source_times(self, folder_id: int, times: dict[str, str]) -> None:
        """扫描拿到的来源创建时间写回已有记录（旧记录的回填，也跟随来源上的变化）。"""
        self._db.executemany(
            "UPDATE media_file SET source_created_at = ? "
            "WHERE folder_id = ? AND rel_path = ? AND source_created_at IS NOT ?",
            [(created, folder_id, path, created) for path, created in times.items()],
        )

    def refresh_added_at(self, library_ids: Iterable[int]) -> None:
        """条目的“添加时间”取它在用文件的最新来源创建时间；来源没给时间的条目保持原值。"""
        library_ids = list(library_ids)
        marks = ", ".join("?" for _ in library_ids) or "NULL"
        self._db.execute(
            f"""
            UPDATE media_item SET added_at = latest.created FROM (
                SELECT p.media_item_id AS item_id, MAX(f.source_created_at) AS created
                FROM playable p
                JOIN version v ON v.playable_id = p.id
                JOIN media_file f ON f.id = v.media_file_id
                WHERE f.state = 'active' AND f.source_created_at IS NOT NULL
                GROUP BY p.media_item_id
            ) AS latest
            WHERE media_item.id = latest.item_id AND media_item.added_at <> latest.created
              AND media_item.library_id IN ({marks})
            """,
            library_ids,
        )

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


def _replaced(row: sqlite3.Row, entry: ResourceEntry) -> bool:
    """同一个路径上是不是换了文件：来源给出的稳定身份和库里记的不一样。

    来源没有身份（或库里还没补全过）时判断不了，交给原来的补全流程。
    """
    identity = entry.identity
    return bool(identity and row["identity"] and identity != row["identity"])


def _file_entry(entry: ResourceEntry) -> FileEntry:
    return FileEntry(
        identity=str(entry.identity),
        name=entry.relative_path.rpartition("/")[2],
        size=entry.size or 0,
        playback_locator=entry.playback_locator or entry.locator,
        sha1=entry.sha1 or "",
        created_at=iso_time(entry.created_at),
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
