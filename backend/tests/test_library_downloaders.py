"""资料库下载器列表接口：读取、整体替换、校验和与旧 metadata_provider 的兼容。"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig
from tests.conftest import FakeClock, SyncRunner, cloud115_roots, make_app
from tests.fake_cloud115 import FakeCloud115
from tests.fake_plugins import install_image_plugin, install_metadata_plugin
from tests.plugin_env import bind_controls
from tests.test_sync import fresh_admin

ADMIN = "/api/admin"
MAIN = "test.main"
FILL = "test.fill"
IMAGES = "test.images"


@pytest.fixture
def main(config: AppConfig) -> Any:
    return install_metadata_plugin(config.plugins_dir, MAIN, "主元数据")


@pytest.fixture
def fill(config: AppConfig) -> Any:
    return install_metadata_plugin(config.plugins_dir, FILL, "补缺插件", search=False)


@pytest.fixture
def images(config: AppConfig) -> Any:
    return install_image_plugin(config.plugins_dir, IMAGES, "假图片")


@pytest.fixture
def client(
    config: AppConfig,
    clock: FakeClock,
    sync_runner: SyncRunner,
    main: Any,
    fill: Any,
    images: Any,
) -> Iterator[TestClient]:
    app = make_app(config, clock, sync_runner)
    with TestClient(app) as test_client:
        bind_controls(config.plugins_dir, test_client)
        yield test_client


def request(client: TestClient, method: str, path: str, body: Any = None, status: int = 200) -> Any:
    response = client.request(method, f"{ADMIN}{path}", json=body, headers=fresh_admin(client))
    assert response.status_code == status, response.text
    return response.json() if response.content else None


def movie_library(client: TestClient, cloud115: FakeCloud115, name: str = "电影") -> dict[str, Any]:
    cloud115.make_path(f"{name}/示例电影 (2020)/示例电影.2020.1080p.mkv")
    library: dict[str, Any] = request(
        client,
        "POST",
        "/libraries",
        {
            "name": name,
            "kind": "movie",
            "roots": cloud115_roots(client, cloud115.make_path(name)),
        },
        status=201,
    )
    return library


def test_default_downloader_lists(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115
) -> None:
    library = movie_library(client, cloud115)

    body = request(client, "GET", f"/libraries/{library['id']}/downloaders")

    assert [(entry["id"], entry["enabled"]) for entry in body["metadata"]] == [
        ("builtin.tmdb", True),
        ("builtin.douban", False),
        (FILL, False),
        (MAIN, False),
    ]
    # 没有 image.fetch 的插件进不了图片列表；图片默认只启用 TMDB
    assert [(entry["id"], entry["enabled"]) for entry in body["images"]] == [
        ("builtin.tmdb", True),
        ("builtin.fanart", False),
        (IMAGES, False),
    ]
    assert {entry["id"] for entry in body["subtitles"]} == {"builtin.assrt"}
    assert all(entry["enabled"] for entry in body["subtitles"])
    assert body["subtitle_languages"] == []
    assert body["subtitle_skip_if_audio"] is False
    metadata = body["metadata"]
    assert metadata[0]["plugin_enabled"] is True
    assert metadata[0]["can_match"] is True
    assert metadata[1]["can_match"] is False
    assert metadata[2]["can_match"] is False


def test_replace_images_and_subtitle_settings(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115
) -> None:
    library = movie_library(client, cloud115)

    body = request(
        client,
        "PUT",
        f"/libraries/{library['id']}/downloaders",
        {
            "images": [{"id": IMAGES, "enabled": True}],
            "subtitles": [],
            "subtitle_languages": ["zh-CN", "en", "zh-TW"],
            "subtitle_skip_if_audio": True,
        },
    )

    assert [(entry["id"], entry["enabled"]) for entry in body["images"]] == [
        (IMAGES, True),
        ("builtin.fanart", False),
        ("builtin.tmdb", False),
    ]
    # 空列表代表一个也不勾选，不会回落到「字幕默认全部启用」
    assert body["subtitles"] and all(not entry["enabled"] for entry in body["subtitles"])
    assert body["subtitle_languages"] == ["zh-Hans", "en", "zh-Hant"]
    assert body["subtitle_skip_if_audio"] is True

    again = request(client, "GET", f"/libraries/{library['id']}/downloaders")
    assert again == body


def test_downloader_validation_errors(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115
) -> None:
    library = movie_library(client, cloud115)
    path = f"/libraries/{library['id']}/downloaders"

    request(client, "PUT", path, {"images": [{"id": "nope"}]}, status=400)
    # 能力不符：图片获取器不能放进元数据列表
    request(client, "PUT", path, {"metadata": [{"id": IMAGES}]}, status=400)
    request(
        client,
        "PUT",
        path,
        {"images": [{"id": IMAGES}, {"id": IMAGES}]},
        status=400,
    )
    response = request(client, "PUT", path, {"metadata": []}, status=400)
    assert response["error"]["code"] == "metadata_downloader_required"
    response = request(client, "PUT", path, {"metadata": [{"id": FILL}]}, status=400)
    assert response["error"]["code"] == "primary_cannot_match"

    # 单独停用 TMDB 后，不能把它放在第一位
    request(client, "PUT", "/plugins/builtin.tmdb/enabled", {"enabled": False})
    response = request(client, "PUT", path, {"metadata": [{"id": "builtin.tmdb"}]}, status=409)
    assert response["error"]["code"] == "metadata_provider_disabled"

    request(
        client,
        "PUT",
        path,
        {"subtitle_languages": ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k"]},
        status=400,
    )
    response = request(client, "PUT", path, {"subtitle_languages": ["not a language"]}, status=400)
    assert response["error"]["code"] == "invalid_subtitle_language"

    for key in ("subtitle_download_languages", "subtitle_skip_if_audio"):
        response = request(
            client, "PUT", f"/libraries/{library['id']}/settings/{key}", {"value": "[]"}, status=400
        )
        assert response["error"]["code"] == "library_setting_reserved"


def test_metadata_provider_alias_updates_the_first_downloader(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115
) -> None:
    cloud115.make_path("电影/示例电影 (2020)/示例电影.2020.1080p.mkv")
    library = request(
        client,
        "POST",
        "/libraries",
        {
            "name": "电影",
            "kind": "movie",
            "roots": cloud115_roots(client, cloud115.make_path("电影")),
            "metadata_provider": MAIN,
        },
        status=201,
    )
    assert library["metadata_provider"] == MAIN
    body = request(client, "GET", f"/libraries/{library['id']}/downloaders")
    assert body["metadata"][0]["id"] == MAIN and body["metadata"][0]["enabled"]

    # 补缺插件不能当主插件
    response = request(
        client, "PATCH", f"/libraries/{library['id']}", {"metadata_provider": FILL}, status=400
    )
    assert response["error"]["code"] == "primary_cannot_match"

    # 通用设置路由仍按旧别名工作
    request(
        client, "PUT", f"/libraries/{library['id']}/settings/metadata_provider", {"value": MAIN}
    )
    settings = request(client, "GET", f"/libraries/{library['id']}/settings")
    assert settings["metadata_provider"] == MAIN

    listed = request(client, "GET", "/libraries/metadata-providers")
    assert [item["id"] for item in listed] == ["builtin.tmdb", MAIN]


@pytest.fixture(autouse=True)
def _extra_builtin_plugins(plugin_env: object) -> None:
    from tests.plugin_env import PluginEnvironment, install_builtin

    assert isinstance(plugin_env, PluginEnvironment)
    install_builtin(plugin_env.root, "builtin.douban")
    install_builtin(plugin_env.root, "builtin.fanart")
    install_builtin(plugin_env.root, "builtin.assrt")
