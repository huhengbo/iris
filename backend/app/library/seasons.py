"""季和单集的手动元数据编辑、刷新定位与删除。"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from app.clock import Clock, to_iso
from app.errors import AppError, NotFound

ChildKind = Literal["season", "episode"]

# 允许手动修改的列；列名会拼进 SQL，所以只接受这个白名单
_EDITABLE_FIELDS: dict[ChildKind, frozenset[str]] = {
    "season": frozenset({"name", "overview", "air_date", "poster_path"}),
    "episode": frozenset({"name", "overview", "air_date", "still_path"}),
}


@dataclass(frozen=True)
class SeasonTarget:
    """刷新一季需要的信息：这一季在元数据插件里的位置。"""

    season_id: int
    number: int
    provider_id: str
    remote_id: str


class SeasonRepository:
    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        self._db = connection
        self._clock = clock

    def patch_metadata(
        self,
        kind: ChildKind,
        entity_id: int,
        values: Mapping[str, str | None],
        locked: Mapping[str, bool],
    ) -> None:
        """写入手动修改的字段，默认锁定，之后的刷新不会覆盖。"""
        if self._db.execute(f"SELECT id FROM {kind} WHERE id = ?", (entity_id,)).fetchone() is None:
            raise NotFound("季或单集不存在")
        if set(values) - _EDITABLE_FIELDS[kind]:
            raise AppError(400, "invalid_metadata_field", "不支持的字段")
        now = to_iso(self._clock.now())
        for field, value in values.items():
            self._db.execute(f"UPDATE {kind} SET {field} = ? WHERE id = ?", (value, entity_id))
            self._db.execute(
                "INSERT INTO metadata_field(entity_kind,entity_id,field,source,locked,updated_at) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(entity_kind,entity_id,field) DO UPDATE SET "
                "source='manual',locked=excluded.locked,updated_at=excluded.updated_at",
                (kind, entity_id, field, "manual", int(locked.get(field, True)), now),
            )

    def season_target(self, season_id: int) -> SeasonTarget:
        row = self._db.execute(
            "SELECT s.id,s.number,m.provider_id,m.remote_id FROM season s "
            "JOIN media_item m ON m.id=s.media_item_id WHERE s.id = ?",
            (season_id,),
        ).fetchone()
        if row is None:
            raise NotFound("季不存在")
        return SeasonTarget(row["id"], int(row["number"]), row["provider_id"], row["remote_id"])

    def episode_target(self, episode_id: int) -> SeasonTarget:
        """单集没有单独的刷新接口，刷新它所在的整季。"""
        row = self._db.execute(
            "SELECT s.id AS season_id,s.number,m.provider_id,m.remote_id FROM episode e "
            "JOIN season s ON s.id=e.season_id JOIN media_item m ON m.id=s.media_item_id "
            "WHERE e.id = ?",
            (episode_id,),
        ).fetchone()
        if row is None:
            raise NotFound("单集不存在")
        return SeasonTarget(
            int(row["season_id"]), int(row["number"]), row["provider_id"], row["remote_id"]
        )

    def season_identities(self, season_id: int) -> list[str]:
        """这一季所有视频和字幕在来源上的文件标识。"""
        if self._db.execute("SELECT 1 FROM season WHERE id = ?", (season_id,)).fetchone() is None:
            raise NotFound("季不存在")
        rows = self._db.execute(
            "SELECT DISTINCT c.identity FROM episode e JOIN playable p ON p.episode_id=e.id "
            "JOIN version v ON v.playable_id=p.id JOIN media_file c ON c.id=v.media_file_id "
            "WHERE e.season_id=? AND c.identity IS NOT NULL "
            "UNION SELECT DISTINCT c.identity FROM episode e JOIN playable p ON p.episode_id=e.id "
            "JOIN version v ON v.playable_id=p.id JOIN subtitle s ON s.version_id=v.id "
            "JOIN media_file c ON c.id=s.media_file_id "
            "WHERE e.season_id=? AND c.identity IS NOT NULL",
            (season_id, season_id),
        ).fetchall()
        return [str(row[0]) for row in rows]

    def episode_identities(self, episode_id: int) -> list[str]:
        """这一集所有视频和字幕在来源上的文件标识。"""
        if self._db.execute("SELECT 1 FROM episode WHERE id = ?", (episode_id,)).fetchone() is None:
            raise NotFound("单集不存在")
        rows = self._db.execute(
            "SELECT DISTINCT c.identity FROM playable p JOIN version v ON v.playable_id=p.id "
            "JOIN media_file c ON c.id=v.media_file_id "
            "WHERE p.episode_id=? AND c.identity IS NOT NULL "
            "UNION SELECT DISTINCT c.identity FROM playable p JOIN version v ON v.playable_id=p.id "
            "JOIN subtitle s ON s.version_id=v.id JOIN media_file c ON c.id=s.media_file_id "
            "WHERE p.episode_id=? AND c.identity IS NOT NULL",
            (episode_id, episode_id),
        ).fetchall()
        return [str(row[0]) for row in rows]

    def delete_season(self, season_id: int) -> None:
        self._db.execute("DELETE FROM season WHERE id = ?", (season_id,))

    def delete_episode(self, episode_id: int) -> None:
        self._db.execute("DELETE FROM episode WHERE id = ?", (episode_id,))
