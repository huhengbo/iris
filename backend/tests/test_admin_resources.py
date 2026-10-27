from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig
from tests.conftest import bearer, ensure_cloud115_source, save_tmdb_key
from tests.fake_cloud115 import FakeCloud115
from tests.fake_tmdb import FakeTmdb
from tests.test_playback import PUBLIC_URL, issue, jump, save_public_url
from tests.test_sync import create_library, fresh_admin, sync

BASE = "/api/admin/resources"


def media_dir(config: AppConfig, *parts: str) -> Path:
    path = config.data_dir.joinpath("media", *parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def local_source(client: TestClient, headers: dict[str, str], root: str, name: str) -> Any:
    response = client.post(
        BASE,
        json={"plugin_id": "source.local", "name": name, "config": {"root": root}},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def local_library(
    client: TestClient, headers: dict[str, str], source_id: int, locator: str | None = None
) -> Any:
    # 测试文件很小，不按体积当成花絮
    settings = client.put("/api/admin/settings/library", json={"extras_min_mb": 0}, headers=headers)
    assert settings.status_code == 200, settings.text
    response = client.post(
        "/api/admin/libraries",
        json={
            "name": "本地电影",
            "kind": "movie",
            "roots": [{"source_id": source_id, "locator": locator}],
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_no_source_is_created_by_default(client: TestClient, admin_tokens: dict[str, str]) -> None:
    headers = bearer(admin_tokens)
    assert client.get(BASE, headers=headers).json() == []
    providers = {p["plugin_id"]: p for p in client.get(f"{BASE}/providers", headers=headers).json()}
    assert providers["source.115"]["multiple"] is False
    assert providers["source.115"]["source_count"] == 0
    local = providers["source.local"]
    assert local["multiple"] is True and local["supports_delete"] is False
    assert [field["key"] for field in local["config_fields"]] == ["root"]


def test_local_source_lifecycle(
    client: TestClient, admin_tokens: dict[str, str], config: AppConfig
) -> None:
    headers = bearer(admin_tokens)
    cloud_id = ensure_cloud115_source(client, headers)
    media_dir(config, "movies", "Film (2024)")
    outside = config.data_dir / "elsewhere"
    outside.mkdir()

    rejected = client.post(
        BASE,
        json={"plugin_id": "source.local", "name": "x", "config": {"root": str(outside)}},
        headers=headers,
    )
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "invalid_source_config"
    second_115 = client.post(BASE, json={"plugin_id": "source.115", "name": "y"}, headers=headers)
    assert second_115.json()["error"]["code"] == "resource_source_singleton"

    source = local_source(client, headers, "movies", "电影盘")
    assert source["config"]["root"] == str((config.data_dir / "media" / "movies").resolve())
    assert source["status"] == "unknown" and source["folder_count"] == 0

    listing = client.get(f"{BASE}/{source['id']}/browse", headers=headers).json()
    assert listing["locator"] is None
    assert listing["path"] == "/"
    assert listing["entries"] == [{"locator": "Film (2024)", "name": "Film (2024)"}]
    missing = client.get(f"{BASE}/{source['id']}/browse?locator=nope", headers=headers)
    assert missing.status_code == 404

    renamed = client.patch(f"{BASE}/{source['id']}", json={"name": "电影"}, headers=headers)
    assert renamed.json()["name"] == "电影"

    library = local_library(client, headers, source["id"])
    folder = library["folders"][0]
    assert (folder["source_id"], folder["source_name"], folder["plugin_id"]) == (
        source["id"],
        "电影",
        "source.local",
    )

    in_use = client.delete(f"{BASE}/{source['id']}", headers=headers)
    assert in_use.status_code == 409
    assert "本地电影" in in_use.json()["error"]["message"]
    assert (
        client.delete(f"/api/admin/libraries/{library['id']}", headers=headers).status_code == 204
    )
    assert client.delete(f"{BASE}/{source['id']}", headers=headers).status_code == 204
    # 115 来源也可以删除，没有内置来源的限制
    assert client.delete(f"{BASE}/{cloud_id}", headers=headers).status_code == 204
    assert client.get(BASE, headers=headers).json() == []


def test_local_library_syncs_plays_and_refuses_delete(
    client: TestClient, admin_tokens: dict[str, str], config: AppConfig, tmdb: FakeTmdb
) -> None:
    headers = bearer(admin_tokens)
    save_tmdb_key(client, headers)
    tmdb.add_movie(535167, "流浪地球", 2019)
    movie = media_dir(config, "流浪地球 (2019)")
    (movie / "流浪地球.2019.mkv").write_bytes(b"0123456789")
    source = local_source(client, headers, ".", "本地")
    library = local_library(client, headers, source["id"])

    # 没有接入 115 账号也能同步本地来源
    job = sync(client, headers, library["id"])
    assert job["status"] == "succeeded", job
    with sqlite3.connect(config.database_path) as connection:
        row = connection.execute(
            "SELECT state, identity, playback_locator, rel_path FROM media_file"
        ).fetchone()
    assert row == (
        "active",
        f"source:{source['id']}:流浪地球 (2019)/流浪地球.2019.mkv",
        "流浪地球 (2019)/流浪地球.2019.mkv",
        "流浪地球 (2019)/流浪地球.2019.mkv",
    )
    status = client.get(f"{BASE}/{source['id']}", headers=headers).json()
    assert status["status"] == "ok" and status["checked_at"] is not None

    # 本地文件由服务直接提供，支持 Range
    save_public_url(client, f"{PUBLIC_URL}/")
    item = client.get("/api/admin/items", headers=fresh_admin(client)).json()["items"][0]
    ticket = issue(client, fresh_admin(client), item_id=item["id"])
    played = jump(client, ticket["url"])
    assert played.status_code == 200
    assert played.content == b"0123456789"
    ranged = client.get(
        jump_path(ticket["url"]), headers={"Range": "bytes=2-4", "User-Agent": "Infuse"}
    )
    assert ranged.status_code == 206 and ranged.content == b"234"

    # 来源没有声明 delete 能力时拒绝删除，不会只删掉库里的记录
    refused = client.delete(f"/api/admin/items/{item['id']}?confirm=true", headers=headers)
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "resource_delete_unsupported"
    assert (movie / "流浪地球.2019.mkv").exists()

    # 停用来源后不能播放
    client.put(f"{BASE}/{source['id']}/enabled", json={"enabled": False}, headers=headers)
    assert jump(client, ticket["url"]).status_code == 503


def jump_path(url: str) -> str:
    return url.removeprefix(PUBLIC_URL)


def test_disabled_or_unmounted_source_does_not_mark_files_missing(
    client: TestClient,
    admin: dict[str, str],
    config: AppConfig,
    cloud115: FakeCloud115,
) -> None:
    cloud115.make_path("影视/电影/星际穿越 (2014)/Interstellar.2014.1080p.mp4")
    library = create_library(client, admin, "电影", "movie", cloud115.make_path("影视/电影"))
    movie = media_dir(config, "disk", "流浪地球 (2019)")
    (movie / "流浪地球.2019.mkv").write_bytes(b"video")
    source = local_source(client, admin, "disk", "本地")
    client.put("/api/admin/settings/library", json={"extras_min_mb": 0}, headers=admin)
    cloud_folder = {"source_id": library["folders"][0]["source_id"]}
    cloud_folder["locator"] = library["folders"][0]["locator"]
    updated = client.patch(
        f"/api/admin/libraries/{library['id']}",
        json={"roots": [cloud_folder, {"source_id": source["id"], "locator": None}]},
        headers=admin,
    )
    assert updated.status_code == 200, updated.text
    assert len(updated.json()["folders"]) == 2
    assert sync(client, admin, library["id"])["status"] == "succeeded"

    def states() -> dict[str, str]:
        with sqlite3.connect(config.database_path) as connection:
            return dict(connection.execute("SELECT rel_path, state FROM media_file").fetchall())

    assert set(states().values()) == {"active"} and len(states()) == 2

    # 停用来源：115 目录照常同步，本地目录跳过并记录问题
    client.put(f"{BASE}/{source['id']}/enabled", json={"enabled": False}, headers=admin)
    job = sync(client, admin, library["id"])
    assert job["status"] == "succeeded"
    assert any("已停用" in problem for problem in job["stats"]["problems"])
    assert set(states().values()) == {"active"}

    # 挂载丢失（目录空了）时不把已有文件标成失效
    client.put(f"{BASE}/{source['id']}/enabled", json={"enabled": True}, headers=admin)
    (movie / "流浪地球.2019.mkv").unlink()
    job = sync(client, admin, library["id"])
    assert set(states().values()) == {"active"}
    assert job["stats"]["problems"]


@pytest.fixture(autouse=True)
def _extra_builtin_plugins(plugin_env: object) -> None:
    from tests.plugin_env import PluginEnvironment, install_source_local

    assert isinstance(plugin_env, PluginEnvironment)
    install_source_local(plugin_env.root)
