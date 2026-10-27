from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from app.auth.users import User
from app.clock import Clock, to_iso
from app.errors import AppError, NotFound
from app.library.browse import visible_library_ids

_COLUMNS = "id, user_id, title, shared, created_at, updated_at"


@dataclass(frozen=True)
class Playlist:
    id: int
    user_id: int | None
    title: str
    shared: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class PlaylistEntry:
    position: int
    playable_id: int
    # 没有可用文件的版本不算，此时为空
    version_id: int | None
    item_id: int
    title: str
    episode_number: int | None
    season_number: int | None
    episode_id: int | None
    episode_name: str | None


@dataclass(frozen=True)
class PlaylistContents:
    playlist: Playlist
    entries: list[PlaylistEntry]


@dataclass(frozen=True)
class NextEntry:
    position: int
    playable_id: int
    version_id: int
    item_id: int
    title: str


class PlaylistRepository:
    """播放列表及其条目。

    普通用户只能改自己的列表；共享列表所有人可看，但创建和设为共享仅限管理员。
    列表里的条目只展示当前用户有权访问的资料库，别人加进去而自己看不到的内容不会泄露。
    """

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        self._db = connection
        self._clock = clock

    def list_for(self, user: User) -> list[Playlist]:
        rows = self._db.execute(
            f"SELECT {_COLUMNS} FROM playlist WHERE shared = 1 OR user_id = ? ORDER BY title",
            (user.id,),
        ).fetchall()
        return [_to_playlist(row) for row in rows]

    def create(self, user: User, title: str, shared: bool) -> Playlist:
        if shared and not user.is_admin:
            raise AppError(403, "forbidden", "只有管理员可以创建共享播放列表")
        now = to_iso(self._clock.now())
        cursor = self._db.execute(
            "INSERT INTO playlist(user_id,title,shared,created_at,updated_at) VALUES (?,?,?,?,?)",
            (user.id, title, int(shared), now, now),
        )
        assert cursor.lastrowid is not None
        return self._require(cursor.lastrowid)

    def update(
        self, playlist_id: int, user: User, title: str | None, shared: bool | None
    ) -> Playlist:
        self.require_editable(playlist_id, user)
        if shared and not user.is_admin:
            raise AppError(403, "forbidden", "只有管理员可以创建共享播放列表")
        fields: list[str] = []
        args: list[Any] = []
        if title is not None:
            fields.append("title=?")
            args.append(title.strip())
        if shared is not None:
            fields.append("shared=?")
            args.append(int(shared))
        if fields:
            fields.append("updated_at=?")
            args.append(to_iso(self._clock.now()))
            self._db.execute(
                f"UPDATE playlist SET {', '.join(fields)} WHERE id=?", (*args, playlist_id)
            )
        return self._require(playlist_id)

    def delete(self, playlist_id: int, user: User) -> None:
        self.require_editable(playlist_id, user)
        self._db.execute("DELETE FROM playlist WHERE id = ?", (playlist_id,))

    def require_visible(self, playlist_id: int, user: User) -> Playlist:
        playlist = self._find(playlist_id)
        if playlist is None or not (
            playlist.shared or playlist.user_id == user.id or user.is_admin
        ):
            raise NotFound("播放列表不存在")
        return playlist

    def require_editable(self, playlist_id: int, user: User) -> Playlist:
        """能改的一定看得到；看得到但不是自己的（管理员除外）按不存在处理。"""
        playlist = self._find(playlist_id)
        if playlist is None or not (playlist.user_id == user.id or user.is_admin):
            raise NotFound("播放列表不存在")
        return playlist

    def contents(self, playlist_id: int, user: User) -> PlaylistContents:
        playlist = self.require_visible(playlist_id, user)
        library_ids = visible_library_ids(self._db, user)
        if not library_ids:
            return PlaylistContents(playlist, [])
        marks = ",".join("?" for _ in library_ids)
        rows = self._db.execute(
            "SELECT pi.position,p.id AS playable_id,"
            "(SELECT v.id FROM version v JOIN media_file c ON c.id=v.media_file_id "
            "WHERE v.playable_id=p.id AND c.state='active' ORDER BY v.id LIMIT 1) AS version_id,"
            "m.id AS item_id,m.title,e.number AS episode_number,s.number AS season_number,"
            "p.episode_id,e.name AS episode_name "
            "FROM playlist_item pi JOIN playable p ON p.id=pi.playable_id "
            "JOIN media_item m ON m.id=p.media_item_id "
            "LEFT JOIN episode e ON e.id=p.episode_id LEFT JOIN season s ON s.id=e.season_id "
            f"WHERE pi.playlist_id=? AND m.library_id IN ({marks}) ORDER BY pi.position",
            (playlist_id, *library_ids),
        ).fetchall()
        entries = [
            PlaylistEntry(
                position=row["position"],
                playable_id=row["playable_id"],
                version_id=row["version_id"],
                item_id=row["item_id"],
                title=row["title"],
                episode_number=row["episode_number"],
                season_number=row["season_number"],
                episode_id=row["episode_id"],
                episode_name=row["episode_name"],
            )
            for row in rows
        ]
        return PlaylistContents(playlist, entries)

    def set_items(self, playlist_id: int, user: User, playable_ids: list[int]) -> None:
        """整体替换条目。只收用户能访问的资料库里的可播放项，重复和无效的 ID 直接忽略。"""
        self.require_editable(playlist_id, user)
        library_ids = visible_library_ids(self._db, user)
        library_marks = ",".join("?" for _ in library_ids) or "NULL"
        marks = ",".join("?" * len(playable_ids)) or "NULL"
        valid = [
            int(row[0])
            for row in self._db.execute(
                "SELECT p.id FROM playable p JOIN media_item m ON m.id=p.media_item_id "
                f"WHERE p.id IN ({marks}) AND m.library_id IN ({library_marks})",
                (*playable_ids, *library_ids),
            ).fetchall()
        ]
        self._db.execute("DELETE FROM playlist_item WHERE playlist_id = ?", (playlist_id,))
        self._db.executemany(
            "INSERT INTO playlist_item(playlist_id,playable_id,position) VALUES (?,?,?)",
            [(playlist_id, p, n) for n, p in enumerate(dict.fromkeys(valid))],
        )
        self._touch(playlist_id)

    def add_item(self, playlist_id: int, user: User, item_id: int, episode_id: int | None) -> None:
        """追加到末尾，已在列表里的跳过。

        给了单集就只加这一集；不给时电影加本片，剧集按季、集顺序追加全部有可用文件的集。
        """
        self.require_editable(playlist_id, user)
        library_ids = visible_library_ids(self._db, user)
        library_marks = ",".join("?" for _ in library_ids) or "NULL"
        item = self._db.execute(
            f"SELECT kind FROM media_item WHERE id = ? AND library_id IN ({library_marks})",
            (item_id, *library_ids),
        ).fetchone()
        if episode_id is not None:
            episode = self._db.execute(
                "SELECT 1 FROM episode e JOIN season s ON s.id=e.season_id "
                "WHERE e.id = ? AND s.media_item_id = ?",
                (episode_id, item_id),
            ).fetchone()
            if item is None or episode is None:
                raise NotFound("单集不存在")
            rows = self._db.execute(
                "SELECT id FROM playable WHERE episode_id = ?", (episode_id,)
            ).fetchall()
        elif item is None or item["kind"] == "movie":
            rows = self._db.execute(
                "SELECT p.id FROM playable p JOIN media_item m ON m.id=p.media_item_id "
                f"WHERE p.media_item_id = ? AND m.library_id IN ({library_marks}) "
                "ORDER BY p.id LIMIT 1",
                (item_id, *library_ids),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT p.id FROM playable p JOIN episode e ON e.id=p.episode_id "
                "JOIN season s ON s.id=e.season_id WHERE s.media_item_id = ? AND EXISTS ("
                "SELECT 1 FROM version v JOIN media_file c ON c.id=v.media_file_id "
                "WHERE v.playable_id=p.id AND c.state='active') ORDER BY s.number, e.number",
                (item_id,),
            ).fetchall()
        if not rows:
            raise NotFound("条目没有可播放版本")
        existing = {
            int(row[0])
            for row in self._db.execute(
                "SELECT playable_id FROM playlist_item WHERE playlist_id = ?", (playlist_id,)
            ).fetchall()
        }
        position = self._db.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM playlist_item WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchone()[0]
        added = [int(row[0]) for row in rows if int(row[0]) not in existing]
        self._db.executemany(
            "INSERT INTO playlist_item(playlist_id,playable_id,position) VALUES (?,?,?)",
            [(playlist_id, playable_id, position + n) for n, playable_id in enumerate(added)],
        )
        self._touch(playlist_id)

    def next_entry(
        self, playlist_id: int, user: User, current_playable_id: int | None
    ) -> NextEntry | None:
        """当前项之后第一个有可用文件的条目；没给当前项时取列表里第一个。"""
        self.require_visible(playlist_id, user)
        library_ids = visible_library_ids(self._db, user)
        if not library_ids:
            return None
        marks = ",".join("?" for _ in library_ids)
        if current_playable_id is None:
            where = ""
            args: tuple[Any, ...] = (playlist_id, *library_ids)
        else:
            where = (
                "AND pi.position > "
                "(SELECT position FROM playlist_item WHERE playlist_id=? AND playable_id=?)"
            )
            args = (playlist_id, playlist_id, current_playable_id, *library_ids)
        row = self._db.execute(
            "SELECT pi.position,p.id AS playable_id,v.id AS version_id,m.id AS item_id,m.title "
            "FROM playlist_item pi JOIN playable p ON p.id=pi.playable_id "
            "JOIN media_item m ON m.id=p.media_item_id "
            "JOIN version v ON v.playable_id=p.id "
            "JOIN media_file c ON c.id=v.media_file_id AND c.state='active' "
            f"WHERE pi.playlist_id=? {where} AND m.library_id IN ({marks}) "
            "ORDER BY pi.position,v.id LIMIT 1",
            args,
        ).fetchone()
        if row is None:
            return None
        return NextEntry(
            position=row["position"],
            playable_id=row["playable_id"],
            version_id=row["version_id"],
            item_id=row["item_id"],
            title=row["title"],
        )

    def _touch(self, playlist_id: int) -> None:
        self._db.execute(
            "UPDATE playlist SET updated_at = ? WHERE id = ?",
            (to_iso(self._clock.now()), playlist_id),
        )

    def _find(self, playlist_id: int) -> Playlist | None:
        row = self._db.execute(
            f"SELECT {_COLUMNS} FROM playlist WHERE id = ?", (playlist_id,)
        ).fetchone()
        return None if row is None else _to_playlist(row)

    def _require(self, playlist_id: int) -> Playlist:
        playlist = self._find(playlist_id)
        if playlist is None:
            raise NotFound("播放列表不存在")
        return playlist


def _to_playlist(row: sqlite3.Row) -> Playlist:
    return Playlist(
        id=row["id"],
        user_id=row["user_id"],
        title=row["title"],
        shared=bool(row["shared"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
