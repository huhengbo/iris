"""视频流信息的上报：内置播放器打开文件后把容器头里读到的信息交上来（ADR 0019）。

流信息属于文件本身，所有能看到这个版本的用户共用；后报的覆盖先报的。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict

from app.auth.users import User
from app.clock import Clock, to_iso
from app.errors import NotFound
from app.library.browse import visible_library_ids
from app.library.browse_models import MediaInfo


def save_media_info(
    connection: sqlite3.Connection, clock: Clock, user: User, version_id: int, info: MediaInfo
) -> None:
    """范围外或文件已失效的版本当作不存在。"""
    library_ids = visible_library_ids(connection, user)
    scope = ", ".join("?" for _ in library_ids) or "NULL"
    row = connection.execute(
        f"""
        SELECT c.id, c.size, c.sha1
        FROM version v JOIN media_file c ON c.id = v.media_file_id
        JOIN playable p ON p.id = v.playable_id
        JOIN media_item m ON m.id = p.media_item_id
        WHERE v.id = ? AND m.library_id IN ({scope}) AND c.state = 'active'
        """,
        (version_id, *library_ids),
    ).fetchone()
    if row is None:
        raise NotFound("这个版本不存在或已失效")
    connection.execute(
        """
        INSERT INTO media_info (media_file_id, size, sha1, duration, width, height, video_codec,
            hdr, audio, subtitles, reported_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (media_file_id) DO UPDATE SET
            size = excluded.size, sha1 = excluded.sha1, duration = excluded.duration,
            width = excluded.width, height = excluded.height,
            video_codec = excluded.video_codec, hdr = excluded.hdr, audio = excluded.audio,
            subtitles = excluded.subtitles, reported_at = excluded.reported_at
        """,
        (
            row["id"],
            row["size"],
            row["sha1"],
            info.duration,
            info.width,
            info.height,
            info.video_codec,
            info.hdr,
            json.dumps([asdict(t) for t in info.audio], ensure_ascii=False),
            json.dumps([asdict(t) for t in info.subtitles], ensure_ascii=False),
            to_iso(clock.now()),
        ),
    )
