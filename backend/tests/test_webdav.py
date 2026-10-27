from __future__ import annotations

import base64
import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import webdav as webdav_api
from app.config import AppConfig
from tests.conftest import ADMIN_PASSWORD, bearer, create_user, login, save_tmdb_key
from tests.fake_cloud115 import FakeCloud115
from tests.fake_tmdb import FakeTmdb
from tests.test_sync import create_library, sync


@pytest.fixture
def movies_fixture(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115, tmdb: FakeTmdb
) -> dict[str, Any]:
    for path in [
        "影视/电影/流浪地球 (2019)/流浪地球.2019.2160p.mkv",
        "影视/电影/流浪地球 (2019)/流浪地球.2019.chs.ass",
        "影视/电影/星际穿越 (2014)/Interstellar.2014.1080p.mp4",
    ]:
        cloud115.make_path(path)
    tmdb.add_movie(535167, "流浪地球", 2019)
    save_tmdb_key(client, admin)
    return create_library(client, admin, "电影/华语电影", "movie", cloud115.make_path("影视/电影"))


def basic(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_webdav_uses_login_credentials_and_user_switch(
    client: TestClient,
    admin: dict[str, str],
    admin_tokens: dict[str, str],
    cloud115: FakeCloud115,
    movies_fixture: dict[str, Any],
    config: AppConfig,
) -> None:
    sync(client, admin)
    default = client.get("/api/me/webdav", headers=admin)
    assert default.status_code == 200
    assert default.json() == {"enabled": True, "username": "admin", "url": None}

    assert (
        client.request(
            "PROPFIND", "/webdav", headers={**basic("admin", ADMIN_PASSWORD), "Depth": "1"}
        ).status_code
        == 207
    )
    assert client.get("/webdav", headers=basic("admin", "wrong-password")).status_code == 401
    assert client.get("/webdav").headers["www-authenticate"].startswith("Basic realm=")

    url = "/webdav/电影/华语电影/流浪地球 (2019)/流浪地球 (2019).mkv"
    assert "影视" not in url
    head = client.head(url, headers=basic("admin", ADMIN_PASSWORD))
    assert head.status_code == 200
    assert int(head.headers["content-length"]) > 0
    file_response = client.get(
        url,
        headers={**basic("admin", ADMIN_PASSWORD), "User-Agent": "Popcorn/1.0"},
        follow_redirects=False,
    )
    assert file_response.status_code == 302
    redirect = file_response.headers["location"]
    assert redirect.startswith("https://cdn.example/")
    assert file_response.headers["content-type"] == "text/html; charset=utf-8"
    assert b"Found" in file_response.content
    assert cloud115.link_requests[-1][1] == "Popcorn/1.0"

    user = create_user(client, admin_tokens, "alice", "alice-password")
    user_tokens = login(client, "alice", "alice-password")
    user_headers = bearer(user_tokens)
    # 新用户不用先打开开关就能用
    assert (
        client.get(
            url, headers=basic("alice", "alice-password"), follow_redirects=False
        ).status_code
        == 302
    )
    assert (
        client.patch("/api/me/webdav", json={"enabled": False}, headers=user_headers).json()[
            "enabled"
        ]
        is False
    )
    assert client.get("/api/me/webdav", headers=user_headers).json()["enabled"] is False
    assert client.get("/webdav", headers=basic("alice", "alice-password")).status_code == 401
    assert (
        client.patch("/api/me/webdav", json={"enabled": True}, headers=user_headers).status_code
        == 200
    )
    assert (
        client.request("PROPFIND", "/webdav", headers=basic("alice", "alice-password")).status_code
        == 207
    )
    assert user["username"] == "alice"


def test_webdav_reuses_directory_tree_for_short_period(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    movies_fixture: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sync(client, admin)

    calls = 0
    original = webdav_api._nodes

    def count_nodes(*args: Any, **kwargs: Any) -> dict[tuple[str, ...], Any]:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(webdav_api, "_nodes", count_nodes)
    headers = {**basic("admin", ADMIN_PASSWORD), "Depth": "1"}
    assert client.request("PROPFIND", "/webdav", headers=headers).status_code == 207
    assert (
        client.get(
            "/webdav/电影/华语电影/流浪地球 (2019)/流浪地球 (2019).mkv",
            headers={**basic("admin", ADMIN_PASSWORD), "User-Agent": "Popcorn/1.0"},
            follow_redirects=False,
        ).status_code
        == 302
    )
    assert calls == 1


def test_webdav_uses_standard_series_structure(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
) -> None:
    tmdb.add_series(108545, "三体", 2023, {1: 2})
    cloud115.make_path("影视/剧集/三体 (2023)/Season 1/三体.S01E01.1080p.mkv")
    library = create_library(
        client, admin, "电视剧/欧美剧", "series", cloud115.make_path("影视/剧集")
    )
    save_tmdb_key(client, admin)
    assert sync(client, admin, library["id"])["status"] == "succeeded"

    url = "/webdav/电视剧/欧美剧/三体 (2023)/Season 01/三体 - S01E01 - 第 1 集.mkv"
    response = client.get(
        url,
        headers={**basic("admin", ADMIN_PASSWORD), "User-Agent": "Popcorn/1.0"},
        follow_redirects=False,
    )
    assert response.status_code == 302, response.text
    redirect = response.headers["location"]
    assert redirect.startswith("https://cdn.example/")
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert cloud115.link_requests[-1][1] == "Popcorn/1.0"


MOVIE_URL = "/webdav/电影/华语电影/流浪地球 (2019)/流浪地球 (2019).mkv"


def test_webdav_rejects_disabled_users(
    client: TestClient,
    admin: dict[str, str],
    admin_tokens: dict[str, str],
    movies_fixture: dict[str, Any],
) -> None:
    sync(client, admin)
    alice = create_user(client, admin_tokens, "alice", "alice-password")
    credentials = basic("alice", "alice-password")
    assert client.request("PROPFIND", "/webdav", headers=credentials).status_code == 207

    response = client.patch(
        f"/api/admin/users/{alice['id']}", json={"disabled": True}, headers=admin
    )
    assert response.status_code == 200, response.text
    assert client.request("PROPFIND", "/webdav", headers=credentials).status_code == 401
    assert client.head(MOVIE_URL, headers=credentials).status_code == 401


def test_webdav_only_lists_libraries_the_user_can_see(
    client: TestClient,
    admin: dict[str, str],
    admin_tokens: dict[str, str],
    cloud115: FakeCloud115,
    movies_fixture: dict[str, Any],
) -> None:
    cloud115.make_path("影视/其他/Other (2020)/Other.2020.1080p.mkv")
    other = create_library(client, admin, "电影/其他", "movie", cloud115.make_path("影视/其他"))
    sync(client, admin)
    alice = create_user(client, admin_tokens, "alice", "alice-password")
    credentials = {**basic("alice", "alice-password"), "Depth": "infinity"}

    def listing() -> str:
        response = client.request("PROPFIND", "/webdav/电影", headers=credentials)
        return response.text if response.status_code == 207 else ""

    # 新用户默认分配当时的全部资料库（ADR 0009）；清空后什么都看不到，也取不到文件
    cleared = client.put(
        f"/api/admin/users/{alice['id']}/libraries", json={"library_ids": []}, headers=admin
    )
    assert cleared.status_code == 200, cleared.text
    assert "华语电影" not in listing()
    assert client.head(MOVIE_URL, headers=credentials).status_code == 404

    response = client.put(
        f"/api/admin/users/{alice['id']}/libraries",
        json={"library_ids": [movies_fixture["id"]]},
        headers=admin,
    )
    assert response.status_code == 200, response.text
    visible = listing()
    assert "华语电影" in visible
    assert "其他" not in visible
    assert client.head(MOVIE_URL, headers=credentials).status_code == 200
    assert other["id"] != movies_fixture["id"]
    assert client.head("/webdav/电影/其他", headers=credentials).status_code == 404


def test_webdav_follows_password_changes_immediately(
    client: TestClient,
    admin: dict[str, str],
    admin_tokens: dict[str, str],
    movies_fixture: dict[str, Any],
) -> None:
    create_user(client, admin_tokens, "alice", "alice-password")
    tokens = login(client, "alice", "alice-password")
    assert (
        client.request("PROPFIND", "/webdav", headers=basic("alice", "alice-password")).status_code
        == 207
    )

    response = client.post(
        "/api/auth/password",
        json={"current_password": "alice-password", "new_password": "new-password-1"},
        headers=bearer(tokens),
    )
    assert response.status_code == 200, response.text
    assert client.get("/webdav", headers=basic("alice", "alice-password")).status_code == 401
    assert (
        client.request("PROPFIND", "/webdav", headers=basic("alice", "new-password-1")).status_code
        == 207
    )


def test_webdav_reports_files_that_no_longer_exist_on_115(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    movies_fixture: dict[str, Any],
) -> None:
    sync(client, admin)
    credentials = basic("admin", ADMIN_PASSWORD)
    assert client.head(MOVIE_URL, headers=credentials).status_code == 200

    movie_dir = cloud115.make_path("影视/电影/流浪地球 (2019)")
    cloud115.remove(cloud115.child(movie_dir, "流浪地球.2019.2160p.mkv") or "")
    sync(client, admin)

    response = client.get(MOVIE_URL, headers=credentials, follow_redirects=False)
    assert response.status_code == 404
    assert response.text


def test_webdav_lists_and_opens_subtitle_files(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    movies_fixture: dict[str, Any],
) -> None:
    # 字幕文件名要以视频文件名开头才会被匹配到这个视频
    cloud115.make_path("影视/电影/流浪地球 (2019)/流浪地球.2019.2160p.chs.ass")
    sync(client, admin)
    credentials = basic("admin", ADMIN_PASSWORD)
    folder = "/webdav/电影/华语电影/流浪地球 (2019)"

    listing = client.request("PROPFIND", folder, headers={**credentials, "Depth": "1"})
    assert listing.status_code == 207, listing.text
    names = re.findall(r"<ns0:displayname>([^<]+)</ns0:displayname>", listing.text)
    assert names == ["流浪地球 (2019)", "流浪地球 (2019).mkv", "流浪地球 (2019).zh-Hans.ass"]
    subtitle_url = f"{folder}/流浪地球 (2019).zh-Hans.ass"

    head = client.head(subtitle_url, headers=credentials)
    assert head.status_code == 200
    opened = client.get(
        subtitle_url,
        headers={**credentials, "User-Agent": "Infuse/7.0"},
        follow_redirects=False,
    )
    assert opened.status_code == 302, opened.text
    assert opened.headers["location"].startswith("https://cdn.example/")
    assert cloud115.link_requests[-1][1] == "Infuse/7.0"


def test_webdav_lists_and_opens_nfo_files(
    client: TestClient,
    admin: dict[str, str],
    admin_tokens: dict[str, str],
    cloud115: FakeCloud115,
    movies_fixture: dict[str, Any],
) -> None:
    cloud115.make_path("影视/电影/流浪地球 (2019)/movie.nfo")
    sync(client, admin)
    credentials = basic("admin", ADMIN_PASSWORD)
    folder = "/webdav/电影/华语电影/流浪地球 (2019)"

    listing = client.request("PROPFIND", folder, headers={**credentials, "Depth": "1"})
    assert listing.status_code == 207, listing.text
    names = re.findall(r"<ns0:displayname>([^<]+)</ns0:displayname>", listing.text)
    # NFO 和视频同名，不带字幕语言
    assert "流浪地球 (2019).nfo" in names
    nfo_url = f"{folder}/流浪地球 (2019).nfo"
    opened = client.get(
        nfo_url, headers={**credentials, "User-Agent": "Infuse/7.0"}, follow_redirects=False
    )
    assert opened.status_code == 302, opened.text
    assert opened.headers["location"].startswith("https://cdn.example/")

    # 没有分配该资料库的用户看不到 NFO
    alice = create_user(client, admin_tokens, "alice", "alice-password")
    cleared = client.put(
        f"/api/admin/users/{alice['id']}/libraries", json={"library_ids": []}, headers=admin
    )
    assert cleared.status_code == 200, cleared.text
    assert client.head(nfo_url, headers=basic("alice", "alice-password")).status_code == 404


def test_webdav_names_series_nfo_in_show_directory(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
) -> None:
    tmdb.add_series(108545, "三体", 2023, {1: 2})
    cloud115.make_path("影视/剧集/三体 (2023)/Season 1/三体.S01E01.1080p.mkv")
    cloud115.make_path("影视/剧集/三体 (2023)/tvshow.nfo")
    library = create_library(
        client, admin, "电视剧/欧美剧", "series", cloud115.make_path("影视/剧集")
    )
    save_tmdb_key(client, admin)
    assert sync(client, admin, library["id"])["status"] == "succeeded"

    response = client.head(
        "/webdav/电视剧/欧美剧/三体 (2023)/tvshow.nfo", headers=basic("admin", ADMIN_PASSWORD)
    )
    assert response.status_code == 200


def test_webdav_lists_episode_nfo_next_to_video(
    client: TestClient,
    admin: dict[str, str],
    admin_tokens: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
) -> None:
    tmdb.add_series(108545, "三体", 2023, {1: 2})
    cloud115.make_path("影视/剧集/三体 (2023)/Season 1/三体.S01E01.1080p.mkv")
    cloud115.make_path("影视/剧集/三体 (2023)/Season 1/三体.S01E01.1080p.nfo")
    # 没有同名视频的孤立 NFO、季级 NFO 不显示
    cloud115.make_path("影视/剧集/三体 (2023)/Season 1/三体.S01E09.nfo")
    cloud115.make_path("影视/剧集/三体 (2023)/Season 1/season.nfo")
    library = create_library(
        client, admin, "电视剧/欧美剧", "series", cloud115.make_path("影视/剧集")
    )
    save_tmdb_key(client, admin)
    assert sync(client, admin, library["id"])["status"] == "succeeded"
    credentials = basic("admin", ADMIN_PASSWORD)
    season = "/webdav/电视剧/欧美剧/三体 (2023)/Season 01"

    listing = client.request("PROPFIND", season, headers={**credentials, "Depth": "1"})
    assert listing.status_code == 207, listing.text
    names = re.findall(r"<ns0:displayname>([^<]+)</ns0:displayname>", listing.text)
    video = next(n for n in names if n.endswith(".mkv"))
    # NFO 与规范化后的视频同名
    assert sorted(names) == sorted(["Season 01", video, video.removesuffix(".mkv") + ".nfo"])
    nfo_url = f"{season}/{video.removesuffix('.mkv')}.nfo"
    opened = client.get(
        nfo_url, headers={**credentials, "User-Agent": "Infuse/7.0"}, follow_redirects=False
    )
    assert opened.status_code == 302, opened.text
    assert opened.headers["location"].startswith("https://cdn.example/")

    # 没有分配该资料库的用户看不到
    alice = create_user(client, admin_tokens, "alice", "alice-password")
    cleared = client.put(
        f"/api/admin/users/{alice['id']}/libraries", json={"library_ids": []}, headers=admin
    )
    assert cleared.status_code == 200, cleared.text
    assert client.head(nfo_url, headers=basic("alice", "alice-password")).status_code == 404
