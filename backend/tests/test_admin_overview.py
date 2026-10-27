"""管理首页：概况、播放动态、用户最近活动和「全部资料库」权限。"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, FakeClock, bearer, create_user, login
from tests.fake_cloud115 import FakeCloud115
from tests.fake_tmdb import FakeTmdb
from tests.test_browse import build_catalog, find, get, new_user
from tests.test_catalog import movie_library
from tests.test_playback import PUBLIC_URL, episodes, issue, progress, save_public_url
from tests.test_sync import create_library, fresh_admin, sync


@pytest.fixture
def catalog(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115, tmdb: FakeTmdb
) -> dict[str, int]:
    ids = build_catalog(client, admin, cloud115, tmdb)
    save_public_url(client, PUBLIC_URL)
    return ids


def summary(client: TestClient) -> Any:
    return get(client, fresh_admin(client), "/admin/summary")


def activity(client: TestClient) -> Any:
    return get(client, fresh_admin(client), "/admin/activity")


def test_summary_reports_setup_and_next_sync(client: TestClient) -> None:
    body = summary(client)
    assert body["setup_complete"] is False
    assert body["pending"] == {"unmatched": 0, "unplaced": 0}
    assert body["next_sync_at"] is None

    response = client.put(
        "/api/admin/sync/schedule", json={"times": ["03:00"]}, headers=fresh_admin(client)
    )
    assert response.status_code == 200, response.text
    assert summary(client)["next_sync_at"] is not None


def test_summary_is_complete_after_setup(client: TestClient, catalog: dict[str, int]) -> None:
    assert summary(client)["setup_complete"] is True


def test_pending_counts_exclude_ignored_files(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115
) -> None:
    # 没配 TMDB：作品都没匹配上；预告片按规则忽略，不算待处理
    movie_library(
        client,
        admin,
        cloud115,
        ["星际穿越 (2014)/Interstellar.2014.1080p.mp4", "星际穿越 (2014)/Interstellar.trailer.mp4"],
    )
    assert sync(client, admin)["status"] == "succeeded"

    assert get(client, admin, "/admin/pending")["ignored"] == 1
    pending = summary(client)["pending"]
    assert pending["unplaced"] == 0
    total = pending["unmatched"] + pending["unplaced"]
    health = get(client, fresh_admin(client), "/admin/health")
    check = next(c for c in health["checks"] if c["key"] == "pending")
    assert check["message"] == (f"{total} 项待处理" if total else "没有待处理内容")


def test_activity_lists_now_playing_and_recent_plays(
    client: TestClient, catalog: dict[str, int], clock: FakeClock
) -> None:
    assert activity(client) == {"now_playing": [], "recent": []}

    earth = find(client, "流浪地球")
    eps = episodes(client)
    new_user(client)
    alice_headers = bearer(login(client, "alice", "alice-password"))
    issue(client, fresh_admin(client), item_id=earth["id"], client="android")
    # 开始播放算正在播放；外部播放器之后不再上报，过了窗口就不算了
    assert [p["username"] for p in activity(client)["now_playing"]] == ["admin"]
    clock.advance(minutes=2)
    ticket = issue(client, alice_headers, episode_id=eps["S01E02"]["id"], player="builtin")
    assert progress(client, alice_headers, ticket["playable_id"], 900, 2700).status_code == 204

    body = activity(client)
    assert [(p["username"], p["title"]) for p in body["now_playing"]] == [("alice", "三体")]
    playing = body["now_playing"][0]
    assert (playing["season_number"], playing["episode_number"]) == (1, 2)
    assert (playing["position"], playing["duration"]) == (900, 2700)
    assert [(r["username"], r["title"], r["client"]) for r in body["recent"]] == [
        ("alice", "三体", "web"),
        ("admin", "流浪地球", "android"),
    ]
    assert body["recent"][1]["season_number"] is None

    # 超过一分钟没有上报就不算正在播放，最近播放仍保留
    clock.advance(minutes=2)
    body = activity(client)
    assert body["now_playing"] == []
    assert len(body["recent"]) == 2

    user = bearer(login(client, "alice", "alice-password"))
    assert client.get("/api/admin/activity", headers=user).status_code == 403


def test_last_seen_is_updated_on_login_and_refresh(
    client: TestClient, admin_tokens: dict[str, str], clock: FakeClock
) -> None:
    alice = create_user(client, admin_tokens)
    assert alice["last_seen_at"] is None

    def last_seen() -> str | None:
        users = get(client, fresh_admin(client), "/admin/users")
        value: str | None = next(u for u in users if u["username"] == "alice")["last_seen_at"]
        return value

    tokens = login(client, "alice", "alice-password")
    first = last_seen()
    assert first is not None
    clock.advance(minutes=20)
    response = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert response.status_code == 200
    second = last_seen()
    assert second is not None and second > first


def test_all_libraries_includes_later_ones(
    client: TestClient,
    admin: dict[str, str],
    admin_tokens: dict[str, str],
    cloud115: FakeCloud115,
) -> None:
    first = create_library(client, admin, "电影", "movie", cloud115.make_path("影视/电影"))
    alice = create_user(client, admin_tokens)
    url = f"/api/admin/users/{alice['id']}/libraries"
    response = client.put(url, json={"library_ids": [], "all_libraries": True}, headers=admin)
    assert response.json() == {"library_ids": [], "all_libraries": True}
    second = create_library(client, admin, "剧集", "series", cloud115.make_path("影视/剧集"))

    user = bearer(login(client, "alice", "alice-password"))
    assert [lib["id"] for lib in get(client, user, "/libraries")] == [first["id"], second["id"]]
    listed = get(client, admin, "/admin/users")
    assert next(u for u in listed if u["username"] == "alice")["all_libraries"] is True

    # 关掉后按勾选的资料库分配，并让旧令牌失效
    response = client.put(url, json={"library_ids": [second["id"]]}, headers=admin)
    assert response.json() == {"library_ids": [second["id"]], "all_libraries": False}
    assert client.get("/api/libraries", headers=user).status_code == 401
    user = bearer(login(client, "alice", "alice-password"))
    assert [lib["id"] for lib in get(client, user, "/libraries")] == [second["id"]]


def test_create_user_with_library_access(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115
) -> None:
    movies = create_library(client, admin, "电影", "movie", cloud115.make_path("影视/电影"))
    create_library(client, admin, "剧集", "series", cloud115.make_path("影视/剧集"))
    headers = bearer(login(client, ADMIN_USERNAME, ADMIN_PASSWORD))

    response = client.post(
        "/api/admin/users",
        json={"username": "bob", "password": "bob-password", "library_ids": [movies["id"]]},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    bob = response.json()
    assert bob["all_libraries"] is False
    access = get(client, headers, f"/admin/users/{bob['id']}/libraries")
    assert access == {"library_ids": [movies["id"]], "all_libraries": False}

    response = client.post(
        "/api/admin/users",
        json={"username": "carol", "password": "carol-password", "all_libraries": True},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    assert response.json()["all_libraries"] is True

    response = client.post(
        "/api/admin/users",
        json={"username": "dave", "password": "dave-password", "library_ids": [999]},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unknown_library"
    users = get(client, headers, "/admin/users")
    assert "dave" not in [u["username"] for u in users]
