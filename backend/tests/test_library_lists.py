from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.auth.devices import DeviceSessionRepository
from app.auth.preferences import PreferenceRepository
from app.auth.users import User
from app.db import Database
from app.errors import AppError, NotFound
from app.library.collections import CollectionDraft, CollectionRepository
from app.library.playlists import PlaylistRepository
from tests.conftest import FakeClock

NOW = "2026-01-01T00:00:00Z"


@pytest.fixture
def db(tmp_path: Path, clock: FakeClock) -> Iterator[sqlite3.Connection]:
    database = Database(tmp_path / "unit.sqlite")
    database.migrate(clock)
    with database.session() as connection:
        yield connection


def add_user(db: sqlite3.Connection, user_id: int, role: str = "user") -> User:
    db.execute(
        "INSERT INTO user(id,username,password_hash,role,all_libraries,created_at,updated_at) "
        "VALUES (?,?,?,?,0,?,?)",
        (user_id, f"user{user_id}", "x", role, NOW, NOW),
    )
    return User(
        id=user_id,
        username=f"user{user_id}",
        role="admin" if role == "admin" else "user",
        disabled=False,
        token_version=1,
        created_at=NOW,
    )


def add_library(db: sqlite3.Connection, library_id: int, *users: User) -> None:
    db.execute(
        "INSERT INTO library(id,name,kind,sort_order,created_at,updated_at) VALUES (?,?,?,?,?,?)",
        (library_id, f"lib{library_id}", "movie", library_id, NOW, NOW),
    )
    for user in users:
        db.execute("INSERT INTO library_access VALUES (?,?)", (user.id, library_id))


def add_movie(db: sqlite3.Connection, item_id: int, library_id: int) -> int:
    """返回条目对应的可播放项 ID。"""
    db.execute(
        "INSERT INTO media_item(id,library_id,kind,provider_id,remote_id,title,scraped_at,added_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (item_id, library_id, "movie", "p", str(item_id), f"电影{item_id}", NOW, NOW),
    )
    cursor = db.execute(
        "INSERT INTO playable(media_item_id,created_at) VALUES (?,?)", (item_id, NOW)
    )
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def add_session(db: sqlite3.Connection, user_id: int, token: str, last_seen: str | None) -> int:
    cursor = db.execute(
        "INSERT INTO auth_session(user_id,token_hash,token_version,created_at,expires_at,"
        "last_seen_at) VALUES (?,?,1,?,?,?)",
        (user_id, token, NOW, NOW, last_seen),
    )
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def draft(library_id: int, **overrides: object) -> CollectionDraft:
    fields: dict[str, object] = {
        "library_id": library_id,
        "title": "系列",
        "sort_title": None,
        "overview": None,
        "poster": None,
        "sort_mode": "release",
        "smart_filter": None,
    }
    return CollectionDraft(**{**fields, **overrides})  # type: ignore[arg-type]


def test_preferences_default_and_normalized_save(db: sqlite3.Connection, clock: FakeClock) -> None:
    add_user(db, 1)
    preferences = PreferenceRepository(db, clock)
    assert preferences.get(1).subtitle_language is None

    assert preferences.save(1, "  zh  ").subtitle_language == "zh"
    assert preferences.get(1).subtitle_language == "zh"
    assert preferences.save(1, "   ").subtitle_language is None
    assert preferences.get(1).subtitle_language is None
    assert preferences.save(1, None).subtitle_language is None


def test_device_sessions_order_and_revoke_scope(db: sqlite3.Connection) -> None:
    add_user(db, 1)
    add_user(db, 2)
    old = add_session(db, 1, "a", "2026-01-01T00:00:00Z")
    recent = add_session(db, 1, "b", "2026-02-01T00:00:00Z")
    never = add_session(db, 1, "c", None)
    other = add_session(db, 2, "d", None)
    sessions = DeviceSessionRepository(db)

    assert [s.id for s in sessions.list_by_activity(1)] == [recent, old, never]
    assert [s.id for s in sessions.list_by_creation(1)] == [never, recent, old]

    sessions.revoke(1, other)
    assert [s.id for s in sessions.list_by_creation(2)] == [other]
    sessions.revoke(1, recent)
    assert [s.id for s in sessions.list_by_creation(1)] == [never, old]
    sessions.revoke_all(1)
    assert sessions.list_by_creation(1) == []
    assert [s.id for s in sessions.list_by_creation(2)] == [other]


def test_collection_draft_filter_and_missing_library(
    db: sqlite3.Connection, clock: FakeClock
) -> None:
    add_library(db, 1)
    collections = CollectionRepository(db, clock)

    with pytest.raises(NotFound, match="资料库不存在"):
        collections.create(draft(99))
    empty = collections.create(draft(1, smart_filter={}))
    assert empty.smart_filter is None
    smart = collections.create(draft(1, smart_filter={"genre": "科幻"}))
    assert smart.smart_filter == '{"genre":"科幻"}'
    assert not smart.automatic
    with pytest.raises(NotFound, match="合集不存在"):
        collections.update(999, draft(1))
    with pytest.raises(NotFound, match="合集不存在"):
        collections.delete(999)


def test_collection_members_stay_inside_library(db: sqlite3.Connection, clock: FakeClock) -> None:
    add_library(db, 1)
    add_library(db, 2)
    add_movie(db, 10, 1)
    add_movie(db, 11, 1)
    add_movie(db, 20, 2)
    collections = CollectionRepository(db, clock)
    collection = collections.create(draft(1))

    members = collections.set_members(collection.id, [11, 20, 10, 11, 999])
    # 顺序按库里查到的先后，与请求里的顺序无关；重复和越界的 ID 被丢掉
    assert [(m.media_item_id, m.position, m.manual) for m in members] == [
        (10, 0, True),
        (11, 1, True),
    ]

    with pytest.raises(NotFound, match="条目不存在"):
        collections.add_member(collection.id, 20)
    collections.add_member(collection.id, 10)
    assert [m.media_item_id for m in collections.members(collection.id)] == [10, 11]

    with pytest.raises(NotFound, match="条目不在合集里"):
        collections.remove_member(collection.id, 20)
    collections.remove_member(collection.id, 11)
    collections.add_member(collection.id, 11)
    assert [(m.media_item_id, m.position) for m in collections.members(collection.id)] == [
        (10, 0),
        (11, 1),
    ]


def test_collection_visibility_follows_library_access(
    db: sqlite3.Connection, clock: FakeClock
) -> None:
    alice = add_user(db, 1)
    add_library(db, 1, alice)
    add_library(db, 2)
    collections = CollectionRepository(db, clock)
    visible = collections.create(draft(1))
    hidden = collections.create(draft(2))

    assert [c.id for c in collections.list_visible(1, alice)] == [visible.id]
    assert collections.require_visible(visible.id, alice).id == visible.id
    with pytest.raises(NotFound, match="资料库不存在"):
        collections.list_visible(2, alice)
    with pytest.raises(NotFound, match="合集不存在"):
        collections.require_visible(hidden.id, alice)


def test_automatic_collections_keep_manual_members(
    db: sqlite3.Connection, clock: FakeClock
) -> None:
    add_library(db, 1)
    for item_id in (10, 11, 12):
        add_movie(db, item_id, 1)
    db.execute("UPDATE media_item SET tmdb_collection_id = 7 WHERE id IN (10, 11)")
    collections = CollectionRepository(db, clock)

    with pytest.raises(NotFound, match="资料库不存在"):
        collections.sync_automatic(99)
    first = collections.sync_automatic(1)
    assert (first.created, first.updated, first.groups) == (1, 0, 1)

    [auto] = collections.list_all(1)
    assert auto.automatic and auto.tmdb_collection_id == 7
    collections.add_member(auto.id, 12)
    second = collections.sync_automatic(1)
    assert (second.created, second.updated, second.groups) == (0, 1, 1)
    assert {m.media_item_id: m.manual for m in collections.members(auto.id)} == {
        10: False,
        11: False,
        12: True,
    }


def test_playlist_permissions(db: sqlite3.Connection, clock: FakeClock) -> None:
    owner = add_user(db, 1)
    other = add_user(db, 2)
    admin = add_user(db, 3, "admin")
    playlists = PlaylistRepository(db, clock)

    with pytest.raises(AppError) as forbidden:
        playlists.create(owner, "共享", shared=True)
    assert forbidden.value.status_code == 403
    mine = playlists.create(owner, "我的", shared=False)
    shared = playlists.create(admin, "大家的", shared=True)

    assert [p.id for p in playlists.list_for(owner)] == [shared.id, mine.id]
    assert [p.id for p in playlists.list_for(other)] == [shared.id]
    with pytest.raises(NotFound):
        playlists.require_visible(mine.id, other)
    # 管理员能看也能改别人的私有列表，但共享列表对普通用户只读
    assert playlists.require_visible(mine.id, admin).id == mine.id
    assert playlists.require_editable(mine.id, admin).id == mine.id
    assert playlists.require_visible(shared.id, other).id == shared.id
    with pytest.raises(NotFound):
        playlists.require_editable(shared.id, other)
    with pytest.raises(NotFound):
        playlists.delete(mine.id, other)


def test_playlist_update_rules(db: sqlite3.Connection, clock: FakeClock) -> None:
    owner = add_user(db, 1)
    admin = add_user(db, 3, "admin")
    playlists = PlaylistRepository(db, clock)
    mine = playlists.create(owner, "我的", shared=False)

    clock.advance(hours=1)
    unchanged = playlists.update(mine.id, owner, None, None)
    assert unchanged == mine
    renamed = playlists.update(mine.id, owner, "  新名字 ", None)
    assert renamed.title == "新名字" and renamed.updated_at > mine.updated_at
    with pytest.raises(AppError) as forbidden:
        playlists.update(mine.id, owner, None, True)
    assert forbidden.value.status_code == 403
    assert playlists.update(mine.id, admin, None, True).shared
    assert playlists.update(mine.id, admin, None, False).shared is False


def test_playlist_items_respect_library_access(db: sqlite3.Connection, clock: FakeClock) -> None:
    owner = add_user(db, 1)
    add_library(db, 1, owner)
    add_library(db, 2)
    mine = add_movie(db, 10, 1)
    foreign = add_movie(db, 20, 2)
    playlists = PlaylistRepository(db, clock)
    playlist = playlists.create(owner, "我的", shared=False)

    playlists.set_items(playlist.id, owner, [foreign, mine, mine, 999])
    assert [e.playable_id for e in playlists.contents(playlist.id, owner).entries] == [mine]

    # 已在列表里的不重复追加；看不到的资料库里的条目按没有可播放版本处理
    playlists.add_item(playlist.id, owner, 10, None)
    assert [e.position for e in playlists.contents(playlist.id, owner).entries] == [0]
    with pytest.raises(NotFound, match="条目没有可播放版本"):
        playlists.add_item(playlist.id, owner, 20, None)
    with pytest.raises(NotFound, match="单集不存在"):
        playlists.add_item(playlist.id, owner, 10, 12345)
    # 电影没有可用文件，不会出现在「下一个」里
    assert playlists.next_entry(playlist.id, owner, None) is None
