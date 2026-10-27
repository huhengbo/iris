"""资料库封面：自动取最近入库条目的背景图，管理员可上传覆盖，Emby 接口同样返回。"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.fake_cloud115 import FakeCloud115
from tests.fake_tmdb import FakeTmdb, image_bytes
from tests.test_browse import build_catalog
from tests.test_concurrency import multipart
from tests.test_emby import emby_get, emby_login
from tests.test_sync import fresh_admin


@pytest.fixture
def catalog(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115, tmdb: FakeTmdb
) -> dict[str, int]:
    return build_catalog(client, admin, cloud115, tmdb)


def _library(client: TestClient, headers: dict[str, str], library_id: int) -> dict[str, Any]:
    libraries = client.get("/api/libraries", headers=headers).json()
    return next(library for library in libraries if library["id"] == library_id)


def _admin_library(client: TestClient, headers: dict[str, str], library_id: int) -> dict[str, Any]:
    libraries = client.get("/api/admin/libraries", headers=headers).json()
    return next(library for library in libraries if library["id"] == library_id)


def test_cover_defaults_to_latest_backdrop_and_can_be_uploaded(
    client: TestClient, catalog: dict[str, int]
) -> None:
    admin = fresh_admin(client)
    movies = catalog["movies"]

    automatic = _library(client, admin, movies)["cover"]
    assert automatic, "自动封面应取最近入库条目的背景图"
    assert {size["width"] for size in automatic} == {780, 1280}
    assert _admin_library(client, admin, movies)["cover_custom"] is False
    assert client.get(automatic[0]["url"]).status_code == 200

    body, content = multipart(image_bytes(width=1600, height=900))
    uploaded = client.put(
        f"/api/admin/libraries/{movies}/cover", headers={**admin, **content}, content=body
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["custom"] is True
    custom = _library(client, admin, movies)["cover"]
    assert custom == uploaded.json()["cover"]
    assert custom != automatic
    assert _admin_library(client, admin, movies)["cover_custom"] is True
    # 剧集资料库不受影响
    assert _library(client, admin, catalog["series"])["cover"] != custom

    # 图片清理任务不能删掉资料库封面
    cleanup = client.post("/api/admin/images/cleanup", headers=admin)
    assert cleanup.status_code in (200, 202), cleanup.text
    assert client.get(custom[0]["url"]).status_code == 200

    reset = client.delete(f"/api/admin/libraries/{movies}/cover", headers=admin)
    assert reset.status_code == 200, reset.text
    assert reset.json() == {"cover": automatic, "custom": False}
    assert _library(client, admin, movies)["cover"] == automatic


def test_cover_upload_validation_and_permissions(
    client: TestClient, catalog: dict[str, int]
) -> None:
    admin = fresh_admin(client)
    movies = catalog["movies"]
    url = f"/api/admin/libraries/{movies}/cover"

    broken, content = multipart(b"not an image")
    rejected = client.put(url, headers={**admin, **content}, content=broken)
    assert (rejected.status_code, rejected.json()["error"]["code"]) == (400, "invalid_artwork")
    assert _admin_library(client, admin, movies)["cover_custom"] is False

    json_body = client.put(url, headers=admin, json={})
    assert json_body.status_code == 415

    body, content = multipart(image_bytes())
    missing = client.put(
        "/api/admin/libraries/9999/cover", headers={**admin, **content}, content=body
    )
    assert missing.status_code == 404
    assert client.delete("/api/admin/libraries/9999/cover", headers=admin).status_code == 404

    assert client.put(url, headers=content, content=body).status_code == 401


def test_emby_views_expose_library_cover(client: TestClient, catalog: dict[str, int]) -> None:
    admin = fresh_admin(client)
    movies = catalog["movies"]
    body, content = multipart(image_bytes(width=1600, height=900))
    uploaded = client.put(
        f"/api/admin/libraries/{movies}/cover", headers={**admin, **content}, content=body
    )
    assert uploaded.status_code == 200, uploaded.text

    login = emby_login(client)
    views = emby_get(client, login["AccessToken"], f"/Users/{login['User']['Id']}/Views")
    view = next(item for item in views["Items"] if item["Id"] == str(movies * 10 + 1))
    tag = view["ImageTags"]["Primary"]
    assert view["ImageTags"]["Thumb"] == tag
    assert view["BackdropImageTags"] == [tag]
    assert all(tag in size["url"] for size in uploaded.json()["cover"])

    # 和条目图片一样不要求令牌
    image = client.get(
        f"/Items/{view['Id']}/Images/Primary", params={"maxWidth": 800}, follow_redirects=False
    )
    assert image.status_code == 302
    assert image.headers["location"].endswith(f"{tag}_780.jpg")
    assert client.get(f"/Items/{view['Id']}/Images/Logo").status_code == 404
