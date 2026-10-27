"""浏览查询共用的 SQL 片段和行到数据类的转换。"""

from __future__ import annotations

import json
import sqlite3

from app.library.browse_models import (
    AudioTrack,
    Credit,
    EmbeddedSubtitle,
    EpisodeRef,
    ItemCard,
    MediaInfo,
    Progress,
)
from app.library.images import image_sizes

# 条目至少有一个可用文件
PLAYABLE = (
    "EXISTS (SELECT 1 FROM playable pp JOIN version vv ON vv.playable_id = pp.id "
    "WHERE pp.media_item_id = m.id AND EXISTS ("
    "SELECT 1 FROM media_file cc WHERE cc.id = vv.media_file_id AND cc.state = 'active'))"
)
# 条目每个有可用文件的可播放条目都被当前用户标记了已看；参数是用户 ID，要和 PLAYABLE 合用。
# 状态前的 + 不能删：没有它 SQLite 会从「全部 active 文件」出发再反查条目，耗时随条目数 × 文件数增长
WATCHED = (
    "NOT EXISTS (SELECT 1 FROM playable wp JOIN version wv ON wv.playable_id = wp.id "
    "JOIN media_file wc ON wc.id = wv.media_file_id "
    "WHERE wp.media_item_id = m.id AND +wc.state = 'active' AND NOT EXISTS ("
    "SELECT 1 FROM playback_state ws WHERE ws.user_id = ? AND ws.playable_id = wp.id "
    "AND ws.watched = 1))"
)
# 单集（别名 p 的可播放条目）至少有一个可用文件
EPISODE_PLAYABLE = (
    "EXISTS (SELECT 1 FROM version vv WHERE vv.playable_id = p.id AND EXISTS ("
    "SELECT 1 FROM media_file cc WHERE cc.id = vv.media_file_id AND cc.state = 'active'))"
)
CARD_SELECT_FIELDS = """
    SELECT m.id, m.library_id, m.kind, m.title, m.original_title, m.year, m.rating, m.genres,
        m.runtime, pi.hash AS poster_hash, bi.hash AS backdrop_hash
"""
CARD_FROM = """
    FROM media_item m
    LEFT JOIN image pi ON pi.id = m.poster_image_id
    LEFT JOIN image bi ON bi.id = m.backdrop_image_id
"""
CARD_SELECT = CARD_SELECT_FIELDS + CARD_FROM


def row_card(row: sqlite3.Row, watched: bool) -> ItemCard:
    return ItemCard(
        id=row["id"],
        library_id=row["library_id"],
        kind=row["kind"],
        title=row["title"],
        original_title=row["original_title"],
        year=row["year"],
        rating=row["rating"],
        genres=json.loads(row["genres"]),
        runtime=row["runtime"],
        poster=image_sizes(row["poster_hash"], "poster"),
        backdrop=image_sizes(row["backdrop_hash"], "backdrop"),
        watched=watched,
    )


def row_episode_ref(row: sqlite3.Row, progress: Progress | None) -> EpisodeRef:
    return EpisodeRef(
        row["id"], row["season_id"], row["season_number"], row["number"], row["name"], progress
    )


def row_progress(row: sqlite3.Row) -> Progress | None:
    """行里的 position、duration 都有值才算有续播位置。"""
    if row["position"] is None or row["duration"] is None:
        return None
    return Progress(row["position"], row["duration"])


def row_media_info(row: sqlite3.Row) -> MediaInfo | None:
    """版本查询里带出的流信息列。JSON 是上报时按接口校验过再写入的。"""
    if row["media_file_id"] is None:
        return None
    return MediaInfo(
        duration=row["media_duration"],
        width=row["media_width"],
        height=row["media_height"],
        video_codec=row["media_video_codec"],
        hdr=row["media_hdr"],
        audio=[AudioTrack(**track) for track in json.loads(row["media_audio"])],
        subtitles=[EmbeddedSubtitle(**track) for track in json.loads(row["media_subtitles"])],
    )


def row_credit(row: sqlite3.Row) -> Credit:
    return Credit(
        person_id=row["id"],
        name=row["name"],
        character=row["character"],
        profile=image_sizes(row["profile_hash"], "profile"),
    )
