from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.clock import Clock, to_iso


@dataclass(frozen=True)
class PlaybackPreference:
    subtitle_language: str | None


class PreferenceRepository:
    """用户的播放偏好。没保存过时返回全空的默认值。"""

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        self._db = connection
        self._clock = clock

    def get(self, user_id: int) -> PlaybackPreference:
        row = self._db.execute(
            "SELECT subtitle_language FROM user_playback_preference WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return PlaybackPreference(subtitle_language=None if row is None else row[0])

    def save(self, user_id: int, subtitle_language: str | None) -> PlaybackPreference:
        """空白字符串等同于清除偏好，客户端不必区分「没填」和「填了空格」。"""
        language = subtitle_language.strip() if subtitle_language else None
        if language == "":
            language = None
        self._db.execute(
            """
            INSERT INTO user_playback_preference (user_id, subtitle_language, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET subtitle_language = excluded.subtitle_language,
                updated_at = excluded.updated_at
            """,
            (user_id, language, to_iso(self._clock.now())),
        )
        return PlaybackPreference(subtitle_language=language)
