"""越权测试（M38 问题视图、M44 设备与会话、M45 合集）：逐个入口，包括直接用 ID 访问。"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, bearer, create_user, login
from tests.fake_cloud115 import FakeCloud115
from tests.fake_tmdb import FakeTmdb
from tests.test_browse import build_catalog
from tests.test_sync import fresh_admin

SECRET = "三体"
VISIBLE = "流浪地球"


class World:
    """电影库（alice、bob 可见）和剧集库（只有管理员可见，里面是「三体」）。"""

    def __init__(self, client: TestClient, catalog: dict[str, int]) -> None:
        self.client = client
        self.movies = catalog["movies"]
        self.series = catalog["series"]
        self.admin = fresh_admin(client)
        tokens = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
        self.alice_id = int(str(create_user(client, tokens, "alice", "alice-password")["id"]))
        self.bob_id = int(str(create_user(client, tokens, "bob", "bob-password")["id"]))
        for user_id in (self.alice_id, self.bob_id):
            response = client.put(
                f"/api/admin/users/{user_id}/libraries",
                json={"library_ids": [self.movies]},
                headers=self.admin,
            )
            assert response.status_code == 200, response.text
        self.alice = bearer(login(client, "alice", "alice-password"))
        self.bob = bearer(login(client, "bob", "bob-password"))
        self.movie_item = self.item_id(self.movies, VISIBLE)
        self.secret_item = self.item_id(self.series, SECRET)

    def item_id(self, library_id: int, title: str) -> int:
        page = self.client.get(f"/api/libraries/{library_id}/items", headers=self.admin).json()
        return int(next(i["id"] for i in page["items"] if i["title"] == title))

    def admin_collection(self, library_id: int, title: str, shared: bool = False) -> int:
        response = self.client.post(
            "/api/admin/collections",
            json={"library_id": library_id, "title": title, "shared": shared},
            headers=self.admin,
        )
        assert response.status_code == 201, response.text
        return int(response.json()["id"])


@pytest.fixture
def world(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115, tmdb: FakeTmdb
) -> World:
    return World(client, build_catalog(client, admin, cloud115, tmdb))


def assert_hidden(response: Any, *forbidden: str) -> None:
    """范围外的对象一律按不存在处理，且响应里不出现其标题。"""
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"
    for text in forbidden:
        assert text not in response.text


# ---- M38 问题视图 ----


@pytest.mark.parametrize("status", ["no_poster", "multiple_versions", "missing", "recent_metadata"])
def test_problem_views_are_admin_only(client: TestClient, world: World, status: str) -> None:
    path = f"/api/libraries/{world.movies}/items"
    assert client.get(path, params={"status": status}, headers=world.alice).status_code == 403
    assert client.get(path, params={"status": status}).status_code == 401
    assert client.get(path, params={"status": status}, headers=world.admin).status_code == 200


def test_problem_view_on_foreign_library_is_still_forbidden_for_users(
    client: TestClient, world: World
) -> None:
    response = client.get(
        f"/api/libraries/{world.series}/items", params={"status": "no_poster"}, headers=world.alice
    )
    assert response.status_code == 403
    assert SECRET not in response.text


def test_foreign_library_is_hidden_from_users(client: TestClient, world: World) -> None:
    for path in (f"/api/libraries/{world.series}", f"/api/libraries/{world.series}/items"):
        assert_hidden(client.get(path, headers=world.alice), SECRET)
    listing = client.get("/api/libraries", headers=world.alice)
    assert [lib["id"] for lib in listing.json()] == [world.movies]
    assert SECRET not in listing.text


def test_foreign_item_by_id_is_hidden_from_users(client: TestClient, world: World) -> None:
    assert_hidden(client.get(f"/api/items/{world.secret_item}", headers=world.alice), SECRET)
    assert client.get(f"/api/items/{world.secret_item}", headers=world.admin).status_code == 200
    assert client.get(f"/api/items/{world.secret_item}").status_code == 401


def test_search_does_not_leak_foreign_titles(client: TestClient, world: World) -> None:
    response = client.get("/api/search", params={"q": SECRET}, headers=world.alice)
    assert response.status_code == 200
    assert SECRET not in response.text


# ---- M44 设备与会话 ----


def login_device(client: TestClient, username: str, password: str, name: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password, "device_name": name},
    )
    assert response.status_code == 200, response.text
    body: dict[str, str] = response.json()
    return body


def my_sessions(client: TestClient, headers: dict[str, str]) -> list[dict[str, Any]]:
    response = client.get("/api/me/sessions", headers=headers)
    assert response.status_code == 200, response.text
    body: list[dict[str, Any]] = response.json()
    return body


def refresh_status(client: TestClient, tokens: dict[str, str]) -> int:
    return client.post(
        "/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    ).status_code


def test_session_endpoints_reject_anonymous_and_non_admins(
    client: TestClient, world: World
) -> None:
    for method, path in [
        ("GET", "/api/me/sessions"),
        ("DELETE", "/api/me/sessions/1"),
    ]:
        assert client.request(method, path).status_code == 401, path
    admin_paths = [
        ("GET", f"/api/admin/users/{world.bob_id}/sessions"),
        ("DELETE", f"/api/admin/users/{world.bob_id}/sessions/1"),
        ("DELETE", f"/api/admin/users/{world.bob_id}/sessions"),
    ]
    for method, path in admin_paths:
        assert client.request(method, path).status_code == 401, path
        assert client.request(method, path, headers=world.alice).status_code == 403, path


def test_user_sees_only_own_sessions(client: TestClient, world: World) -> None:
    login_device(client, "alice", "alice-password", "Alice 的电视")
    login_device(client, "bob", "bob-password", "Bob 的手机")
    names = {s["device_name"] for s in my_sessions(client, world.alice)}
    assert "Alice 的电视" in names
    assert "Bob 的手机" not in names
    assert "Alice 的电视" not in {s["device_name"] for s in my_sessions(client, world.bob)}


def test_user_cannot_revoke_someone_elses_session_by_id(client: TestClient, world: World) -> None:
    bob_tokens = login_device(client, "bob", "bob-password", "Bob 的手机")
    bob_session = my_sessions(client, world.bob)[0]["id"]

    response = client.delete(f"/api/me/sessions/{bob_session}", headers=world.alice)
    assert response.status_code in (204, 403, 404)
    assert bob_session in {s["id"] for s in my_sessions(client, world.bob)}
    assert refresh_status(client, bob_tokens) == 200


def test_user_cannot_revoke_foreign_session_through_admin_endpoint(
    client: TestClient, world: World
) -> None:
    login_device(client, "bob", "bob-password", "Bob 的手机")
    bob_session = my_sessions(client, world.bob)[0]["id"]
    path = f"/api/admin/users/{world.bob_id}/sessions/{bob_session}"
    assert client.delete(path, headers=world.alice).status_code == 403
    assert bob_session in {s["id"] for s in my_sessions(client, world.bob)}


def test_user_can_revoke_own_session_and_its_refresh_token_stops_working(
    client: TestClient, world: World
) -> None:
    tokens = login_device(client, "alice", "alice-password", "旧手机")
    target = next(s for s in my_sessions(client, world.alice) if s["device_name"] == "旧手机")

    assert client.delete(f"/api/me/sessions/{target['id']}", headers=world.alice).status_code == 204
    assert target["id"] not in {s["id"] for s in my_sessions(client, world.alice)}
    assert refresh_status(client, tokens) == 401


def test_admin_session_view_is_scoped_to_the_path_user(client: TestClient, world: World) -> None:
    login_device(client, "alice", "alice-password", "Alice 的电视")
    login_device(client, "bob", "bob-password", "Bob 的手机")
    listing = client.get(f"/api/admin/users/{world.alice_id}/sessions", headers=world.admin)
    assert listing.status_code == 200
    assert "Alice 的电视" in listing.text
    assert "Bob 的手机" not in listing.text
    assert "token" not in listing.text
    missing = client.get("/api/admin/users/99999/sessions", headers=world.admin)
    assert missing.status_code == 404


def test_admin_cannot_revoke_a_session_through_the_wrong_user_path(
    client: TestClient, world: World
) -> None:
    tokens = login_device(client, "bob", "bob-password", "Bob 的手机")
    bob_session = next(
        s["id"] for s in my_sessions(client, world.bob) if s["device_name"] == "Bob 的手机"
    )
    client.delete(f"/api/admin/users/{world.alice_id}/sessions/{bob_session}", headers=world.admin)
    assert bob_session in {s["id"] for s in my_sessions(client, world.bob)}
    assert refresh_status(client, tokens) == 200


def test_admin_can_revoke_one_session_or_all_sessions(client: TestClient, world: World) -> None:
    first = login_device(client, "bob", "bob-password", "手机")
    second = login_device(client, "bob", "bob-password", "电视")
    third = login_device(client, "bob", "bob-password", "平板")
    phone = next(s["id"] for s in my_sessions(client, world.bob) if s["device_name"] == "手机")

    one = client.delete(f"/api/admin/users/{world.bob_id}/sessions/{phone}", headers=world.admin)
    assert one.status_code == 204
    assert refresh_status(client, first) == 401

    everything = client.delete(f"/api/admin/users/{world.bob_id}/sessions", headers=world.admin)
    assert everything.status_code == 204
    assert refresh_status(client, second) == 401
    assert refresh_status(client, third) == 401
    # 只影响 bob，alice 的登录不受影响
    assert client.get("/api/me/sessions", headers=world.alice).status_code == 200


# ---- M45 合集 ----


def mix_foreign_member(config: AppConfig, collection_id: int, item_id: int) -> None:
    """模拟历史数据或并发导致的跨库成员：合集在电影库，成员却在剧集库。"""
    with sqlite3.connect(config.database_path) as connection:
        connection.execute(
            "INSERT INTO collection_item(collection_id,media_item_id,position,manual) "
            "VALUES (?,?,99,1)",
            (collection_id, item_id),
        )


def test_collection_admin_endpoints_reject_anonymous_and_non_admins(
    client: TestClient, world: World
) -> None:
    collection = world.admin_collection(world.movies, "我的合集")
    body = {"library_id": world.movies, "title": "x"}
    endpoints: list[tuple[str, str, dict[str, Any]]] = [
        ("GET", "/api/admin/collections", {}),
        ("POST", "/api/admin/collections", body),
        ("PATCH", f"/api/admin/collections/{collection}", body),
        ("DELETE", f"/api/admin/collections/{collection}", {}),
        ("PUT", f"/api/admin/collections/{collection}/items", {"item_ids": []}),
        ("POST", f"/api/admin/collections/{collection}/items", {"item_id": world.movie_item}),
        ("DELETE", f"/api/admin/collections/{collection}/items/{world.movie_item}", {}),
        ("POST", f"/api/admin/libraries/{world.movies}/collections/auto-sync", {}),
    ]
    for method, path, payload in endpoints:
        assert client.request(method, path, json=payload).status_code == 401, path
        denied = client.request(method, path, json=payload, headers=world.alice)
        assert denied.status_code == 403, (method, path)
    # 被拒绝的请求没有改动合集
    listing = client.get(f"/api/libraries/{world.movies}/collections", headers=world.alice)
    assert [c["title"] for c in listing.json()] == ["我的合集"]


def test_collections_of_foreign_library_are_hidden(client: TestClient, world: World) -> None:
    secret = world.admin_collection(world.series, "机密合集")
    assert_hidden(
        client.get(f"/api/libraries/{world.series}/collections", headers=world.alice), "机密合集"
    )
    assert_hidden(client.get(f"/api/collections/{secret}", headers=world.alice), "机密合集")
    assert client.get(f"/api/collections/{secret}", headers=world.admin).status_code == 200
    assert client.get(f"/api/collections/{secret}").status_code == 401


def test_collection_ids_do_not_reveal_existence(client: TestClient, world: World) -> None:
    secret = world.admin_collection(world.series, "机密合集")
    hidden = client.get(f"/api/collections/{secret}", headers=world.alice)
    absent = client.get("/api/collections/99999", headers=world.alice)
    assert hidden.status_code == absent.status_code == 404
    assert hidden.json()["error"]["code"] == absent.json()["error"]["code"]


def test_collection_with_foreign_members_does_not_leak_them(
    client: TestClient, world: World, config: AppConfig
) -> None:
    collection = world.admin_collection(world.movies, "混合合集")
    added = client.post(
        f"/api/admin/collections/{collection}/items",
        json={"item_id": world.movie_item},
        headers=world.admin,
    )
    assert added.status_code == 204
    mix_foreign_member(config, collection, world.secret_item)

    detail = client.get(f"/api/collections/{collection}", headers=world.alice)
    assert detail.status_code == 200
    assert [i["title"] for i in detail.json()["items"]] == [VISIBLE]
    assert SECRET not in detail.text
    listing = client.get(f"/api/libraries/{world.movies}/collections", headers=world.alice)
    assert SECRET not in listing.text
    # 管理员能看到完整成员
    full = client.get(f"/api/collections/{collection}", headers=world.admin)
    assert {i["title"] for i in full.json()["items"]} == {VISIBLE, SECRET}


def test_admin_cannot_put_foreign_library_items_into_a_collection(
    client: TestClient, world: World
) -> None:
    collection = world.admin_collection(world.movies, "电影合集")
    replaced = client.put(
        f"/api/admin/collections/{collection}/items",
        json={"item_ids": [world.secret_item, world.movie_item]},
        headers=world.admin,
    )
    assert [m["media_item_id"] for m in replaced.json()] == [world.movie_item]
    added = client.post(
        f"/api/admin/collections/{collection}/items",
        json={"item_id": world.secret_item},
        headers=world.admin,
    )
    assert added.status_code == 404


# ---- 播放列表（所有者与共享可见性）----


def create_playlist(
    client: TestClient, headers: dict[str, str], title: str, shared: bool = False
) -> int:
    response = client.post(
        "/api/playlists", json={"title": title, "shared": shared}, headers=headers
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def test_playlist_endpoints_require_login(client: TestClient, world: World) -> None:
    playlist = create_playlist(client, world.alice, "私人")
    for method, path in [
        ("GET", "/api/playlists"),
        ("POST", "/api/playlists"),
        ("GET", f"/api/playlists/{playlist}"),
        ("PATCH", f"/api/playlists/{playlist}"),
        ("DELETE", f"/api/playlists/{playlist}"),
        ("PUT", f"/api/playlists/{playlist}/items"),
        ("POST", f"/api/playlists/{playlist}/items"),
        ("GET", f"/api/playlists/{playlist}/next"),
    ]:
        assert client.request(method, path, json={}).status_code == 401, (method, path)


def test_private_playlist_is_invisible_and_immutable_for_other_users(
    client: TestClient, world: World
) -> None:
    playlist = create_playlist(client, world.alice, "Alice 私人")
    path = f"/api/playlists/{playlist}"
    assert "Alice 私人" not in client.get("/api/playlists", headers=world.bob).text
    assert_hidden(client.get(path, headers=world.bob), "Alice 私人")
    assert_hidden(client.get(f"{path}/next", headers=world.bob), "Alice 私人")
    assert_hidden(client.patch(path, json={"title": "被改"}, headers=world.bob))
    assert_hidden(client.delete(path, headers=world.bob))
    assert_hidden(client.put(f"{path}/items", json={"item_ids": []}, headers=world.bob))
    assert_hidden(
        client.post(f"{path}/items", json={"item_id": world.movie_item}, headers=world.bob)
    )
    own = client.get(path, headers=world.alice)
    assert own.json()["playlist"]["title"] == "Alice 私人"


def test_shared_playlist_is_readable_but_not_editable_by_others(
    client: TestClient, world: World
) -> None:
    playlist = create_playlist(client, world.admin, "全家共享", shared=True)
    path = f"/api/playlists/{playlist}"
    assert client.get(path, headers=world.bob).status_code == 200
    assert "全家共享" in client.get("/api/playlists", headers=world.bob).text
    assert_hidden(client.patch(path, json={"title": "被改"}, headers=world.bob))
    assert_hidden(client.delete(path, headers=world.bob))
    assert_hidden(client.put(f"{path}/items", json={"item_ids": []}, headers=world.bob))
    assert_hidden(
        client.post(f"{path}/items", json={"item_id": world.movie_item}, headers=world.bob)
    )
    assert client.get(path, headers=world.admin).json()["playlist"]["title"] == "全家共享"


def test_regular_users_cannot_create_or_publish_shared_playlists(
    client: TestClient, world: World
) -> None:
    denied = client.post(
        "/api/playlists", json={"title": "想共享", "shared": True}, headers=world.alice
    )
    assert denied.status_code == 403
    own = create_playlist(client, world.alice, "私人")
    publish = client.patch(f"/api/playlists/{own}", json={"shared": True}, headers=world.alice)
    assert publish.status_code == 403
    assert "私人" not in client.get("/api/playlists", headers=world.bob).text


def test_playlist_does_not_leak_or_accept_foreign_library_items(
    client: TestClient, world: World
) -> None:
    shared = create_playlist(client, world.admin, "管理员共享", shared=True)
    added = client.post(
        f"/api/playlists/{shared}/items", json={"item_id": world.secret_item}, headers=world.admin
    )
    assert added.status_code == 201
    assert SECRET in added.text
    assert SECRET not in client.get(f"/api/playlists/{shared}", headers=world.alice).text

    own = create_playlist(client, world.alice, "Alice 的")
    refused = client.post(
        f"/api/playlists/{own}/items", json={"item_id": world.secret_item}, headers=world.alice
    )
    assert refused.status_code in (403, 404)
    assert SECRET not in refused.text
    assert SECRET not in client.get(f"/api/playlists/{own}", headers=world.alice).text


def test_admin_collection_changes_by_id_do_not_need_user_visibility(
    client: TestClient, world: World
) -> None:
    """管理员始终能按 ID 管理任意合集；用户视角下合集改名后仍遵守可见范围。"""
    secret = world.admin_collection(world.series, "机密合集")
    renamed = client.patch(
        f"/api/admin/collections/{secret}",
        json={"library_id": world.series, "title": "机密合集改名"},
        headers=world.admin,
    )
    assert renamed.status_code == 200
    assert_hidden(client.get(f"/api/collections/{secret}", headers=world.alice), "机密合集")
    assert client.delete(f"/api/admin/collections/{secret}", headers=world.admin).status_code == 204
