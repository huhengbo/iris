from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import (
    FakeClock,
    SyncRunner,
    bearer,
    cloud115_roots,
    create_user,
    ensure_cloud115_source,
    login,
    make_app,
)
from tests.fake_cloud115 import FakeCloud115
from tests.fake_plugins import install_resource_plugin
from tests.plugin_env import bind_controls
from tests.test_sync import fresh_admin

BASE = "/api/admin/libraries"


@pytest.fixture
def admin(client: TestClient, admin_tokens: dict[str, str]) -> dict[str, str]:
    return bearer(admin_tokens)


@pytest.fixture
def tree(cloud115: FakeCloud115) -> dict[str, str]:
    """115 上的目录：/影视/电影、/影视/电影/动画、/影视/剧集、/影视/纪录片。"""
    return {
        "影视": cloud115.make_path("影视"),
        "电影": cloud115.make_path("影视/电影"),
        "动画": cloud115.make_path("影视/电影/动画"),
        "剧集": cloud115.make_path("影视/剧集"),
        "纪录片": cloud115.make_path("影视/纪录片"),
    }


def create(client: TestClient, admin: dict[str, str], name: str, kind: str, *cids: str) -> Any:
    return client.post(
        BASE,
        json={"name": name, "kind": kind, "roots": cloud115_roots(client, *cids)},
        headers=admin,
    )


def error_code(response: Any) -> str:
    code: str = response.json()["error"]["code"]
    return code


def test_create_library_with_115_folders(
    client: TestClient, admin: dict[str, str], tree: dict[str, str]
) -> None:
    response = create(client, admin, "电影", "movie", tree["电影"], tree["纪录片"])

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "电影"
    assert body["kind"] == "movie"
    assert [(f["locator"], f["path"]) for f in body["folders"]] == [
        (tree["电影"], "/影视/电影"),
        (tree["纪录片"], "/影视/纪录片"),
    ]
    assert body["video_count"] == 0
    listed = client.get(BASE, headers=admin).json()
    assert [library["name"] for library in listed] == ["电影"]


@pytest.mark.parametrize(
    ("folder", "conflict"),
    [("电影", "same folder"), ("动画", "inside"), ("影视", "contains")],
)
def test_overlapping_folders_are_rejected(
    client: TestClient, admin: dict[str, str], tree: dict[str, str], folder: str, conflict: str
) -> None:
    assert create(client, admin, "电影", "movie", tree["电影"]).status_code == 201

    response = create(client, admin, "其他", "movie", tree[folder])

    assert response.status_code == 409, conflict
    assert error_code(response) == "library_folder_overlap"
    assert "资料库「电影」的目录「/影视/电影」" in response.json()["error"]["message"]
    assert [library["name"] for library in client.get(BASE, headers=admin).json()] == ["电影"]


def test_overlapping_folders_within_one_request_are_rejected(
    client: TestClient, admin: dict[str, str], tree: dict[str, str]
) -> None:
    response = create(client, admin, "电影", "movie", tree["电影"], tree["动画"])

    assert response.status_code == 409
    assert error_code(response) == "library_folder_overlap"


def test_missing_folders_are_rejected(
    client: TestClient, admin: dict[str, str], tree: dict[str, str]
) -> None:
    missing = create(client, admin, "电影", "movie", "99999")
    assert missing.status_code == 400
    assert error_code(missing) == "invalid_folder"
    assert create(client, admin, "电影", "movie").status_code == 422
    assert client.get(BASE, headers=admin).json() == []


def test_library_names_are_unique(
    client: TestClient, admin: dict[str, str], tree: dict[str, str]
) -> None:
    assert create(client, admin, "电影", "movie", tree["电影"]).status_code == 201

    response = create(client, admin, " 电影 ", "movie", tree["剧集"])

    assert response.status_code == 409
    assert error_code(response) == "library_name_taken"


def test_update_name_and_folders(
    client: TestClient, admin: dict[str, str], tree: dict[str, str], cloud115: FakeCloud115
) -> None:
    library = create(client, admin, "电影", "movie", tree["电影"]).json()
    cloud115.calls.clear()

    response = client.patch(
        f"{BASE}/{library['id']}",
        json={
            "name": "院线电影",
            "roots": cloud115_roots(client, tree["电影"], tree["纪录片"]),
        },
        headers=admin,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "院线电影"
    assert body["kind"] == "movie"
    assert [f["path"] for f in body["folders"]] == ["/影视/电影", "/影视/纪录片"]
    # 已有的目录不重新访问 115，只浏览新增的目录
    assert cloud115.calls == ["browse"]

    response = client.patch(
        f"{BASE}/{library['id']}",
        json={"roots": cloud115_roots(client, tree["纪录片"])},
        headers=admin,
    )
    assert [f["path"] for f in response.json()["folders"]] == ["/影视/纪录片"]
    # 移出的目录可以被别的资料库使用
    assert create(client, admin, "动画", "movie", tree["动画"]).status_code == 201


def test_kind_cannot_be_changed(
    client: TestClient, admin: dict[str, str], tree: dict[str, str]
) -> None:
    library = create(client, admin, "电影", "movie", tree["电影"]).json()

    client.patch(f"{BASE}/{library['id']}", json={"kind": "series"}, headers=admin)

    assert client.get(BASE, headers=admin).json()[0]["kind"] == "movie"


def test_reorder_and_delete(
    client: TestClient, admin: dict[str, str], tree: dict[str, str]
) -> None:
    movies = create(client, admin, "电影", "movie", tree["电影"]).json()
    shows = create(client, admin, "剧集", "series", tree["剧集"]).json()
    docs = create(client, admin, "纪录片", "movie", tree["纪录片"]).json()

    response = client.put(
        f"{BASE}/order", json={"ids": [docs["id"], movies["id"], shows["id"]]}, headers=admin
    )
    assert response.status_code == 200
    assert [library["name"] for library in response.json()] == ["纪录片", "电影", "剧集"]
    partial = client.put(f"{BASE}/order", json={"ids": [docs["id"]]}, headers=admin)
    assert error_code(partial) == "invalid_order"

    assert client.delete(f"{BASE}/{movies['id']}", headers=admin).status_code == 204
    assert [library["name"] for library in client.get(BASE, headers=admin).json()] == [
        "纪录片",
        "剧集",
    ]
    assert client.delete(f"{BASE}/{movies['id']}", headers=admin).status_code == 404


def test_folders_cannot_change_while_syncing(
    client: TestClient,
    admin: dict[str, str],
    tree: dict[str, str],
    sync_runner: SyncRunner,
) -> None:
    library = create(client, admin, "电影", "movie", tree["电影"]).json()
    sync_runner.deferred = True
    assert client.post("/api/admin/sync", json={}, headers=admin).status_code == 202

    patch = client.patch(
        f"{BASE}/{library['id']}",
        json={"roots": cloud115_roots(client, tree["纪录片"])},
        headers=admin,
    )
    delete = client.delete(f"{BASE}/{library['id']}", headers=admin)
    rename = client.patch(f"{BASE}/{library['id']}", json={"name": "院线"}, headers=admin)

    assert patch.status_code == 409 and error_code(patch) == "sync_running"
    assert delete.status_code == 409 and error_code(delete) == "sync_running"
    assert rename.status_code == 200
    sync_runner.run_pending()
    assert client.delete(f"{BASE}/{library['id']}", headers=admin).status_code == 204


def resource_source_id(client: TestClient, headers: dict[str, str]) -> int:
    return ensure_cloud115_source(client, headers)


def test_dir_browser_lists_subdirectories(
    client: TestClient, admin: dict[str, str], tree: dict[str, str]
) -> None:
    create(client, admin, "动画", "movie", tree["动画"])
    source_id = resource_source_id(client, admin)

    root = client.get(f"/api/admin/resources/{source_id}/browse", headers=admin).json()
    assert root["locator"] is None
    assert root["path"] == "/"
    assert [entry["name"] for entry in root["entries"]] == ["影视"]
    assert root["entries"][0]["locator"] == tree["影视"]

    response = client.get(
        f"/api/admin/resources/{source_id}/browse",
        params={"locator": tree["影视"]},
        headers=admin,
    )
    body = response.json()
    assert body["locator"] == tree["影视"]
    assert body["path"] == "/影视"
    assert [entry["name"] for entry in body["entries"]] == ["电影", "剧集", "纪录片"]

    inside = client.get(
        f"/api/admin/resources/{source_id}/browse",
        params={"locator": tree["动画"]},
        headers=admin,
    )
    assert inside.json()["entries"] == []


def test_dir_browser_rejects_missing_or_malformed_ids(
    client: TestClient, admin: dict[str, str]
) -> None:
    source_id = resource_source_id(client, admin)
    missing = client.get(
        f"/api/admin/resources/{source_id}/browse",
        params={"locator": "424242"},
        headers=admin,
    )
    assert missing.status_code == 404
    malformed = client.get(
        f"/api/admin/resources/{source_id}/browse",
        params={"locator": "../x"},
        headers=admin,
    )
    assert malformed.status_code == 404


def test_only_admins_manage_libraries(
    client: TestClient, admin: dict[str, str], admin_tokens: dict[str, str], tree: dict[str, str]
) -> None:
    create_user(client, admin_tokens)
    user = bearer(login(client, "alice", "alice-password"))

    assert client.get(BASE, headers=user).status_code == 403
    assert create(client, user, "电影", "movie", tree["电影"]).status_code == 403
    source_id = resource_source_id(client, admin)
    assert client.get(f"/api/admin/resources/{source_id}/browse", headers=user).status_code == 403
    assert client.get(BASE).status_code == 401


def test_update_name_subtitle_language_and_folders_in_one_request(
    client: TestClient, admin: dict[str, str], tree: dict[str, str]
) -> None:
    """名称、字幕语言、目录一起改：设置服务自己开写事务，不能嵌在改名的事务里等死。"""
    library = create(client, admin, "电影", "movie", tree["电影"]).json()

    response = client.patch(
        f"{BASE}/{library['id']}",
        json={
            "name": "院线电影",
            "subtitle_language": "zh",
            "roots": cloud115_roots(client, tree["电影"], tree["纪录片"]),
        },
        headers=admin,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["name"], body["subtitle_language"]) == ("院线电影", "zh")
    assert [f["path"] for f in body["folders"]] == ["/影视/电影", "/影视/纪录片"]
    assert client.get(BASE, headers=admin).json()[0]["subtitle_language"] == "zh"


def test_unselectable_folder_is_rejected(
    config: Any,
    clock: FakeClock,
    sync_runner: SyncRunner,
) -> None:
    """插件说某个目录不能作为资料库目录时，宿主直接拒绝，不做来源特判。"""
    plugin = install_resource_plugin(config.plugins_dir)
    with TestClient(make_app(config, clock, sync_runner)) as app:
        bind_controls(config.plugins_dir, app)
        plugin.provider.selectable = False
        admin = fresh_admin(app)
        source = app.post(
            "/api/admin/resources",
            json={"plugin_id": "test.source", "name": "假来源"},
            headers=admin,
        ).json()
        listing = app.get(f"/api/admin/resources/{source['id']}/browse", headers=admin).json()
        assert listing["selectable"] is False

        response = app.post(
            BASE,
            json={
                "name": "电影",
                "kind": "movie",
                "roots": [{"source_id": source["id"], "locator": "root"}],
            },
            headers=admin,
        )
        assert response.status_code == 400
        assert error_code(response) == "library_folder_not_selectable"
        assert response.json()["error"]["message"] == "不能把这个目录作为资料库目录"

        plugin.provider.selectable = True
        created = app.post(
            BASE,
            json={
                "name": "电影",
                "kind": "movie",
                "roots": [{"source_id": source["id"], "locator": "root"}],
            },
            headers=admin,
        )
        assert created.status_code == 201, created.text
