"""播放列表接口。"""

from __future__ import annotations

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from app.api.collections import MembersIn
from app.api.schemas import Out
from app.deps import (
    AppClock,
    CurrentUser,
    Db,
    ReadDb,
)
from app.library.playlists import Playlist, PlaylistContents, PlaylistRepository

public_router = APIRouter(tags=["playlists"])


class PlaylistAddIn(BaseModel):
    item_id: int
    # 给了单集就只加这一集；不给时电影加本片，剧集按季、集顺序追加全部有可用文件的集
    episode_id: int | None = None


class PlaylistIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    shared: bool = False


class PlaylistPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    shared: bool | None = None


class PlaylistOut(Out):
    id: int
    user_id: int | None
    title: str
    # shared 一直以 0/1 返回，客户端按这个格式解析
    shared: int
    created_at: str
    updated_at: str

    @classmethod
    def of(cls, playlist: Playlist) -> PlaylistOut:
        return cls(
            id=playlist.id,
            user_id=playlist.user_id,
            title=playlist.title,
            shared=int(playlist.shared),
            created_at=playlist.created_at,
            updated_at=playlist.updated_at,
        )


class PlaylistEntryOut(Out):
    position: int
    playable_id: int
    version_id: int | None
    item_id: int
    title: str
    episode_number: int | None
    season_number: int | None
    episode_id: int | None
    episode_name: str | None


class PlaylistContentsOut(Out):
    playlist: PlaylistOut
    items: list[PlaylistEntryOut]

    @classmethod
    def of(cls, contents: PlaylistContents) -> PlaylistContentsOut:
        return cls(
            playlist=PlaylistOut.of(contents.playlist),
            items=[PlaylistEntryOut.model_validate(entry) for entry in contents.entries],
        )


class NextEntryOut(Out):
    position: int
    playable_id: int
    version_id: int
    item_id: int
    title: str


@public_router.get("/playlists")
def list_playlists(user: CurrentUser, db: ReadDb, clock: AppClock) -> list[PlaylistOut]:
    return [PlaylistOut.of(p) for p in PlaylistRepository(db, clock).list_for(user)]


@public_router.post("/playlists", status_code=201)
def create_playlist(body: PlaylistIn, user: CurrentUser, db: Db, clock: AppClock) -> PlaylistOut:
    return PlaylistOut.of(PlaylistRepository(db, clock).create(user, body.title, body.shared))


@public_router.patch("/playlists/{playlist_id}")
def patch_playlist(
    playlist_id: int, body: PlaylistPatch, user: CurrentUser, db: Db, clock: AppClock
) -> PlaylistOut:
    playlist = PlaylistRepository(db, clock).update(playlist_id, user, body.title, body.shared)
    return PlaylistOut.of(playlist)


@public_router.get("/playlists/{playlist_id}")
def get_playlist(
    playlist_id: int, user: CurrentUser, db: ReadDb, clock: AppClock
) -> PlaylistContentsOut:
    return PlaylistContentsOut.of(PlaylistRepository(db, clock).contents(playlist_id, user))


@public_router.put("/playlists/{playlist_id}/items")
def set_playlist_items(
    playlist_id: int, body: MembersIn, user: CurrentUser, db: Db, clock: AppClock
) -> PlaylistContentsOut:
    playlists = PlaylistRepository(db, clock)
    playlists.set_items(playlist_id, user, body.item_ids)
    return PlaylistContentsOut.of(playlists.contents(playlist_id, user))


@public_router.post("/playlists/{playlist_id}/items", status_code=201)
def add_playlist_item(
    playlist_id: int, body: PlaylistAddIn, user: CurrentUser, db: Db, clock: AppClock
) -> PlaylistContentsOut:
    playlists = PlaylistRepository(db, clock)
    playlists.add_item(playlist_id, user, body.item_id, body.episode_id)
    return PlaylistContentsOut.of(playlists.contents(playlist_id, user))


@public_router.get("/playlists/{playlist_id}/next")
def next_playlist_item(
    playlist_id: int,
    user: CurrentUser,
    db: ReadDb,
    clock: AppClock,
    current_playable_id: int | None = None,
) -> NextEntryOut | None:
    entry = PlaylistRepository(db, clock).next_entry(playlist_id, user, current_playable_id)
    return None if entry is None else NextEntryOut.model_validate(entry)


@public_router.delete("/playlists/{playlist_id}", status_code=204)
def delete_playlist(playlist_id: int, user: CurrentUser, db: Db, clock: AppClock) -> Response:
    PlaylistRepository(db, clock).delete(playlist_id, user)
    return Response(status_code=204)
