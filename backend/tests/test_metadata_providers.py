"""资料库选用元数据插件（ADR 0022）：外置插件刮削和资料库设置校验。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from iris_plugin_sdk import MetadataNotFound, MetadataRecord

from app.config import AppConfig
from tests.conftest import FakeClock, SyncRunner, cloud115_roots, make_app
from tests.fake_cloud115 import FakeCloud115
from tests.test_sync import fresh_admin, sync

ADMIN = "/api/admin"
PLUGIN_ID = "org.example.meta"


@pytest.fixture
def plugin(config: AppConfig) -> Any:
    from tests.fake_plugins import install_metadata_plugin

    return install_metadata_plugin(config.plugins_dir, PLUGIN_ID, "示例元数据")


@pytest.fixture
def configured_plugin(plugin: Any, client: TestClient) -> Any:
    plugin.provider.records["m-1"] = MetadataRecord(
        provider_id=PLUGIN_ID,
        remote_id="m-1",
        title="示例电影",
        year=2020,
        overview="外置插件刮削的简介",
        poster="posters/m-1.jpg",
    )
    plugin.provider.set_page_url_template("https://meta.example.org/subject/{remote_id}")
    return plugin


@pytest.fixture
def client(
    config: AppConfig,
    clock: FakeClock,
    sync_runner: SyncRunner,
    plugin: Any,
) -> Iterator[TestClient]:
    from tests.plugin_env import bind_controls

    app = make_app(config, clock, sync_runner)
    with TestClient(app) as test_client:
        bind_controls(config.plugins_dir, test_client)
        yield test_client


def request(client: TestClient, method: str, path: str, body: Any = None, status: int = 200) -> Any:
    response = client.request(method, f"{ADMIN}{path}", json=body, headers=fresh_admin(client))
    assert response.status_code == status, response.text
    return response.json() if response.content else None


def movie_library(
    client: TestClient, cloud115: FakeCloud115, provider: str | None, name: str = "电影"
) -> dict[str, Any]:
    cloud115.make_path(f"{name}/示例电影 (2020)/示例电影.2020.1080p.mkv")
    body: dict[str, Any] = {
        "name": name,
        "kind": "movie",
        "roots": cloud115_roots(client, cloud115.make_path(name)),
    }
    if provider is not None:
        body["metadata_provider"] = provider
    library: dict[str, Any] = request(client, "POST", "/libraries", body, status=201)
    return library


def test_metadata_providers_list_built_in_first(
    client: TestClient, admin: dict[str, str], tmdb: Any
) -> None:
    tmdb.reason = "还没有配置 TMDB Key，在插件 › TMDB 元数据里填写"
    providers = request(client, "GET", "/libraries/metadata-providers")

    assert providers == [
        {
            "id": "builtin.tmdb",
            "name": "TMDB 元数据",
            "built_in": True,
            "enabled": True,
            "unavailable_reason": "还没有配置 TMDB Key，在插件 › TMDB 元数据里填写",
        },
        {
            "id": PLUGIN_ID,
            "name": "示例元数据",
            "built_in": True,
            "enabled": True,
            "unavailable_reason": None,
        },
    ]


def test_library_scraped_by_external_plugin(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    config: AppConfig,
    configured_plugin: Any,
) -> None:
    library = movie_library(client, cloud115, PLUGIN_ID)
    assert library["metadata_provider"] == PLUGIN_ID

    # 没配置 TMDB 也不影响选了其他插件的资料库
    body = sync(client, admin)

    assert body["status"] == "succeeded", body
    assert body["stats"]["matched"] == 1
    listed = request(client, "GET", "/items")["items"]
    assert [(i["title"], i["provider_id"], i["remote_id"]) for i in listed] == [
        ("示例电影", PLUGIN_ID, "m-1")
    ]
    detail = request(client, "GET", f"/items/{listed[0]['id']}")
    assert detail["provider_name"] == "示例元数据"
    assert detail["external_url"] == "https://meta.example.org/subject/m-1"
    assert request(client, "GET", "/pending")["metadata_problem"] is None
    with sqlite3.connect(config.database_path) as connection:
        poster = connection.execute("SELECT poster_image_id FROM media_item").fetchone()[0]
    assert poster is not None

    found = request(
        client, "GET", f"/metadata/search?kind=movie&query=示例&library_id={library['id']}"
    )
    assert [(r["provider_id"], r["remote_id"], r["url"]) for r in found] == [
        (PLUGIN_ID, "m-1", "https://meta.example.org/subject/m-1")
    ]


def test_unready_plugin_only_holds_its_own_library(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115, configured_plugin: Any
) -> None:
    plugin = configured_plugin
    plugin.provider.reason = "示例元数据还没有登录"
    movie_library(client, cloud115, PLUGIN_ID)

    body = sync(client, admin)

    assert body["status"] == "succeeded", body
    assert body["stats"]["waiting"] == 1
    pending = request(client, "GET", "/pending")
    assert pending["metadata_problem"] == "示例元数据还没有登录"
    assert pending["metadata_action"] == f"/settings/manage/plugins/{PLUGIN_ID}"

    plugin.provider.reason = None
    assert sync(client, admin)["stats"]["matched"] == 1


def test_search_not_found_leaves_work_unmatched_without_failing_sync(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115, configured_plugin: Any
) -> None:
    # MetaTube 这类插件搜不到时回 404：只是这一部没有结果，不能让整次同步失败
    configured_plugin.provider.error = MetadataNotFound("info not found")
    movie_library(client, cloud115, PLUGIN_ID)

    body = sync(client, admin)

    assert body["status"] == "succeeded", body
    assert body["stats"]["matched"] == 0
    assert body["stats"]["unmatched"] == 1
    assert body["stats"]["waiting"] == 0


def test_library_metadata_provider_is_validated(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115
) -> None:
    cloud115.make_path("电影/示例电影 (2020)/示例电影.2020.mkv")
    request(
        client,
        "POST",
        "/libraries",
        {
            "name": "电影",
            "kind": "movie",
            "roots": cloud115_roots(client, "1"),
            "metadata_provider": "nope",
        },
        status=400,
    )
    library = movie_library(client, cloud115, None)
    assert library["metadata_provider"] == "builtin.tmdb"

    updated = request(
        client, "PATCH", f"/libraries/{library['id']}", {"metadata_provider": PLUGIN_ID}
    )
    assert updated["metadata_provider"] == PLUGIN_ID
    settings = request(client, "GET", f"/libraries/{library['id']}/settings")
    assert settings["metadata_provider"] == PLUGIN_ID

    request(client, "PUT", f"/plugins/{PLUGIN_ID}/enabled", {"enabled": False})
    request(
        client,
        "PUT",
        f"/libraries/{library['id']}/settings/metadata_provider",
        {"value": PLUGIN_ID},
        status=409,
    )
    request(
        client,
        "PUT",
        f"/libraries/{library['id']}/settings/metadata_provider",
        {"value": "builtin.tmdb"},
    )
    assert request(client, "GET", "/libraries")[0]["metadata_provider"] == "builtin.tmdb"
