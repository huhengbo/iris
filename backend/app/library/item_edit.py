"""管理员对条目的直接编辑：查看详情、手动改元数据、取消匹配、合并电影条目。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from dataclasses import fields as dataclass_fields

from app.clock import Clock, to_iso
from app.errors import AppError, NotFound
from app.library.watchlist import drop_watched_for_item

# 允许手动修改的条目列；列名会拼进 SQL，所以只接受这个白名单
EDITABLE_FIELDS = frozenset(
    {
        "title",
        "sort_title",
        "original_title",
        "en_title",
        "release_date",
        "tagline",
        "overview",
        "rating",
        "genres",
        "studios",
        "countries",
        "poster_path",
        "backdrop_path",
        "thumb_path",
        "logo_path",
    }
)
_JSON_ARRAY_FIELDS = frozenset({"genres", "studios", "countries"})


@dataclass(frozen=True)
class ItemRecord:
    """media_item 表的一行；JSON 列保持数据库里的文本。"""

    id: int
    library_id: int
    kind: str
    provider_id: str
    remote_id: str
    title: str
    original_title: str | None
    en_title: str | None
    year: int | None
    release_date: str | None
    overview: str | None
    rating: float | None
    genres: str
    runtime: int | None
    poster_path: str | None
    poster_image_id: int | None
    backdrop_path: str | None
    backdrop_image_id: int | None
    thumb_path: str | None
    thumb_image_id: int | None
    logo_path: str | None
    logo_image_id: int | None
    scraped_at: str
    added_at: str
    sort_title: str | None
    tagline: str | None
    studios: str
    countries: str
    tmdb_collection_id: int | None
    content_rating: str | None


@dataclass(frozen=True)
class VersionInfo:
    id: int
    label: str
    attributes: str
    rel_path: str
    size: int | None
    state: str
    identity: str | None
    duration: int | None
    width: int | None
    height: int | None
    video_codec: str | None
    hdr: str | None
    audio: str | None
    subtitles: str | None


@dataclass(frozen=True)
class SubtitleInfo:
    id: int
    label: str
    language: str | None
    forced: int
    rel_path: str


@dataclass(frozen=True)
class FieldInfo:
    field: str
    source: str
    locked: int
    updated_at: str


@dataclass(frozen=True)
class ItemInfo:
    item: ItemRecord
    versions: list[VersionInfo]
    subtitles: list[SubtitleInfo]
    metadata: list[FieldInfo]


class ItemEditRepository:
    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        self._db = connection
        self._clock = clock

    def require(self, item_id: int) -> None:
        if self._db.execute("SELECT 1 FROM media_item WHERE id = ?", (item_id,)).fetchone() is None:
            raise NotFound("条目不存在")

    def title(self, item_id: int) -> str:
        row = self._db.execute("SELECT title FROM media_item WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise NotFound("条目不存在")
        return str(row["title"])

    def info(self, item_id: int) -> ItemInfo:
        item = self._db.execute("SELECT * FROM media_item WHERE id = ?", (item_id,)).fetchone()
        if item is None:
            raise NotFound("条目不存在")
        versions = self._db.execute(
            """SELECT v.id, v.label, v.attributes, c.rel_path, c.size, c.state, c.identity,
            mi.duration, mi.width, mi.height, mi.video_codec, mi.hdr, mi.audio, mi.subtitles
            FROM version v JOIN media_file c ON c.id = v.media_file_id
            LEFT JOIN media_info mi ON mi.media_file_id = c.id
            JOIN playable p ON p.id = v.playable_id
            WHERE p.media_item_id = ? ORDER BY v.id""",
            (item_id,),
        ).fetchall()
        subtitles = self._db.execute(
            """SELECT s.id, s.label, s.language, s.forced, c.rel_path
            FROM subtitle s JOIN media_file c ON c.id = s.media_file_id
            JOIN version v ON v.id = s.version_id JOIN playable p ON p.id = v.playable_id
            WHERE p.media_item_id = ? ORDER BY s.id""",
            (item_id,),
        ).fetchall()
        fields = self._db.execute(
            """SELECT field, source, locked, updated_at FROM metadata_field
            WHERE entity_kind = 'item' AND entity_id = ? ORDER BY field""",
            (item_id,),
        ).fetchall()
        return ItemInfo(
            # media_item 还有补缺用的列，ItemRecord 只取自己声明过的
            item=ItemRecord(
                **{field.name: item[field.name] for field in dataclass_fields(ItemRecord)}
            ),
            versions=[VersionInfo(**dict(row)) for row in versions],
            subtitles=[SubtitleInfo(**dict(row)) for row in subtitles],
            metadata=[FieldInfo(**dict(row)) for row in fields],
        )

    def patch_metadata(
        self, item_id: int, values: Mapping[str, str | None], locked: Mapping[str, bool]
    ) -> None:
        """写入手动修改的字段，默认锁定，之后的刷新不会覆盖。"""
        unknown = set(values) - EDITABLE_FIELDS
        if unknown:
            raise AppError(
                400, "invalid_metadata_field", f"不支持的字段: {', '.join(sorted(unknown))}"
            )
        self.require(item_id)
        now = to_iso(self._clock.now())
        for field, value in values.items():
            stored = value
            if field in _JSON_ARRAY_FIELDS and isinstance(value, str):
                try:
                    stored = _compact_json(json.loads(value))
                except json.JSONDecodeError:
                    raise AppError(
                        400, "invalid_metadata_value", f"字段 {field} 必须是 JSON 数组"
                    ) from None
            self._db.execute(f"UPDATE media_item SET {field} = ? WHERE id = ?", (stored, item_id))
            self._db.execute(
                "INSERT INTO metadata_field(entity_kind, entity_id, field, source, locked, "
                "updated_at) VALUES ('item', ?, ?, 'manual', ?, ?) "
                "ON CONFLICT(entity_kind, entity_id, field) DO UPDATE SET source='manual', "
                "locked=excluded.locked, updated_at=excluded.updated_at",
                (item_id, field, int(bool(locked.get(field, True))), now),
            )

    def unmatch(self, item_id: int) -> int:
        """作品回到待处理列表，条目删除。返回受影响的作品数。"""
        self.require(item_id)
        works = self._db.execute(
            "SELECT COUNT(*) FROM work WHERE media_item_id = ?", (item_id,)
        ).fetchone()[0]
        self._db.execute(
            "UPDATE work SET media_item_id = NULL, state = 'unmatched', manual = 0, "
            "updated_at = ? WHERE media_item_id = ?",
            (to_iso(self._clock.now()), item_id),
        )
        self._db.execute("DELETE FROM media_item WHERE id = ?", (item_id,))
        return int(works)

    def check_merge(self, target_id: int, source_ids: Sequence[int]) -> None:
        """只允许把同一资料库里的电影并入另一部电影；剧集有季集冲突，要逐个改挂版本。"""
        if target_id in source_ids or len(set(source_ids)) != len(source_ids):
            raise AppError(400, "invalid_merge", "目标条目不能出现在来源列表中")
        target = self._db.execute(
            "SELECT id,library_id,kind FROM media_item WHERE id = ?", (target_id,)
        ).fetchone()
        if target is None:
            raise NotFound("目标条目不存在")
        marks = ",".join("?" * len(source_ids))
        sources = self._db.execute(
            f"SELECT id,library_id,kind FROM media_item WHERE id IN ({marks})",
            tuple(source_ids),
        ).fetchall()
        if len(sources) != len(source_ids) or any(
            row["library_id"] != target["library_id"] or row["kind"] != target["kind"]
            for row in sources
        ):
            raise AppError(409, "merge_kind_mismatch", "只能合并同一资料库内的同类条目")
        if target["kind"] != "movie":
            raise AppError(
                409, "merge_series_unsupported", "剧集合并需要先处理季集冲突，请逐个改挂版本"
            )

    def merge(self, target_id: int, source_ids: Sequence[int]) -> None:
        """把来源条目的版本、播放进度和作品并入目标条目，再删除来源条目。"""
        target_playable = self._movie_playable(target_id)
        for source_id in source_ids:
            source_playable = self._movie_playable(source_id)
            if source_playable is not None and target_playable is not None:
                self._db.execute(
                    "INSERT INTO playback_state(user_id, playable_id, position, duration, watched, "
                    "last_played_at, last_version_id, updated_at) "
                    "SELECT user_id, ?, position, duration, watched, last_played_at, "
                    "last_version_id, updated_at FROM playback_state WHERE playable_id = ? "
                    "ON CONFLICT(user_id, playable_id) DO UPDATE SET "
                    "watched = MAX(watched, excluded.watched), "
                    "last_played_at = COALESCE(excluded.last_played_at, "
                    "playback_state.last_played_at)",
                    (target_playable, source_playable),
                )
                self._db.execute(
                    "UPDATE version SET playable_id = ? WHERE playable_id = ?",
                    (target_playable, source_playable),
                )
                self._db.execute("DELETE FROM playable WHERE id = ?", (source_playable,))
            elif source_playable is not None:
                self._db.execute(
                    "UPDATE playable SET media_item_id = ? WHERE id = ?",
                    (target_id, source_playable),
                )
        marks = ",".join("?" * len(source_ids))
        self._db.execute(
            f"UPDATE work SET media_item_id = ? WHERE media_item_id IN ({marks})",
            (target_id, *source_ids),
        )
        self._db.execute(f"DELETE FROM media_item WHERE id IN ({marks})", tuple(source_ids))
        # 合并会把来源条目的已看状态并到目标条目上，看完的目标也要移出稍后观看
        drop_watched_for_item(self._db, target_id)

    def _movie_playable(self, item_id: int) -> int | None:
        row = self._db.execute(
            "SELECT id FROM playable WHERE media_item_id = ? AND episode_id IS NULL", (item_id,)
        ).fetchone()
        return None if row is None else int(row["id"])


def _compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
