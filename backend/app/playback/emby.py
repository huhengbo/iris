"""Emby 播放用的版本查询：MediaSource、外挂字幕和时长（ADR 0024）。

只做直接播放，不转码；版本和文件的可用性判定与内置播放器一致。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app.auth.users import User
from app.errors import NotFound
from app.library.browse_models import MediaInfo
from app.library.browse_rows import row_media_info
from app.playback.service import PlaybackService, _basename


@dataclass(frozen=True)
class EmbySubtitleFile:
    id: int
    label: str
    language: str | None
    forced: bool
    rel_path: str
    playback_locator: str | None
    folder_id: int
    format: str = "srt"
    source: str = "external"


@dataclass(frozen=True)
class EmbyVersionSource:
    version_id: int
    label: str
    rel_path: str
    size: int | None
    media: MediaInfo | None
    # 媒体信息里的秒数；没有时用集或条目的分钟数折算
    runtime: int | None
    subtitles: list[EmbySubtitleFile]

    @property
    def duration(self) -> int | None:
        if self.media is not None and self.media.duration is not None:
            return self.media.duration
        return self.runtime

    @property
    def filename(self) -> str:
        return _basename(self.rel_path)

    @property
    def container(self) -> str:
        return Path(self.rel_path).suffix.lstrip(".").lower()


class EmbyPlayback:
    """按可播放条目列出 MediaSource；排序由调用方把默认版本提到第一。"""

    def __init__(self, connection: sqlite3.Connection, user: User) -> None:
        self._db = connection
        self._user = user

    def versions(self, playable_id: int) -> list[EmbyVersionSource]:
        rows = self._db.execute(
            """
            SELECT v.id, v.label, c.rel_path, c.size,
                mi.media_file_id, mi.duration AS media_duration,
                mi.width AS media_width, mi.height AS media_height,
                mi.video_codec AS media_video_codec, mi.hdr AS media_hdr,
                mi.audio AS media_audio, mi.subtitles AS media_subtitles,
                e.runtime AS episode_runtime, m.runtime AS item_runtime
            FROM version v JOIN media_file c ON c.id = v.media_file_id
            JOIN playable p ON p.id = v.playable_id
            JOIN media_item m ON m.id = p.media_item_id
            LEFT JOIN episode e ON e.id = p.episode_id
            LEFT JOIN media_info mi ON mi.media_file_id = c.id
                AND mi.size IS c.size AND mi.sha1 IS c.sha1
            WHERE v.playable_id = ? AND c.state = 'active' AND c.playback_locator IS NOT NULL
            ORDER BY c.size IS NULL, c.size DESC, v.id
            """,
            (playable_id,),
        ).fetchall()
        if not rows:
            raise NotFound("没有可以播放的文件")
        result: list[EmbyVersionSource] = []
        for row in rows:
            media = row_media_info(row)
            minutes = row["episode_runtime"] or row["item_runtime"]
            result.append(
                EmbyVersionSource(
                    version_id=row["id"],
                    label=row["label"],
                    rel_path=row["rel_path"],
                    size=row["size"],
                    media=media,
                    runtime=int(minutes) * 60 if minutes is not None else None,
                    subtitles=self._subtitles(row["id"]),
                )
            )
        return result

    def default_version(self, playback: PlaybackService, playable_id: int) -> int:
        """沿用内置播放器的默认版本规则：上次用过的可用版本，否则最大的。"""
        return playback.default_version(self._db, self._user, playable_id)

    def subtitle_at(self, version_id: int, index: int) -> EmbySubtitleFile:
        subtitles = self.subtitle_files(version_id)
        if index < 0 or index >= len(subtitles):
            raise NotFound("字幕不存在")
        return subtitles[index]

    def subtitle_files(self, version_id: int) -> list[EmbySubtitleFile]:
        rows = self._db.execute(
            """
            SELECT s.id, s.label, s.language, s.forced, s.source,
                c.rel_path, c.playback_locator, c.folder_id, o.format
            FROM subtitle s
            LEFT JOIN media_file c ON c.id = s.media_file_id
            LEFT JOIN online_subtitle o ON o.subtitle_id = s.id
            WHERE s.version_id = ?
                AND (s.source = 'online' OR (c.state = 'active' AND c.playback_locator IS NOT NULL))
            ORDER BY s.is_default DESC, s.forced, s.id
            """,
            (version_id,),
        ).fetchall()
        return [
            EmbySubtitleFile(
                id=row["id"],
                label=row["label"],
                language=row["language"],
                forced=bool(row["forced"]),
                rel_path=row["rel_path"] or "",
                playback_locator=row["playback_locator"],
                folder_id=row["folder_id"] or 0,
                format=row["format"] or _extension(row["rel_path"] or ""),
                source=row["source"],
            )
            for row in rows
        ]

    def _subtitles(self, version_id: int) -> list[EmbySubtitleFile]:
        return self.subtitle_files(version_id)


def _extension(rel_path: str) -> str:
    suffix = rel_path.rpartition(".")[2]
    return suffix.lower() if suffix and suffix != rel_path else "srt"
