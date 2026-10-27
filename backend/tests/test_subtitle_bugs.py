"""三个既有字幕 bug 的回归测试。"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import bearer, login
from tests.fake_cloud115 import FakeCloud115
from tests.fake_tmdb import FakeTmdb
from tests.test_browse import build_catalog, find, get
from tests.test_playback import PUBLIC_URL, save_public_url
from tests.test_sync import fresh_admin, sync


@pytest.fixture
def catalog(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115, tmdb: FakeTmdb
) -> dict[str, int]:
    ids = build_catalog(client, admin, cloud115, tmdb)
    save_public_url(client, f"{PUBLIC_URL}/")
    return ids


def detail(client: TestClient, title: str) -> dict[str, Any]:
    found = find(client, title)
    body: dict[str, Any] = get(client, fresh_admin(client), f"/items/{found['id']}")
    return body


def issue(client: TestClient, headers: dict[str, str], version_id: int) -> dict[str, Any]:
    response = client.post("/api/play", json={"version_id": version_id}, headers=headers)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_preference_zh_tw_maps_to_hant(
    client: TestClient, catalog: dict[str, int], cloud115: FakeCloud115
) -> None:
    cloud115.make_path("影视/电影/流浪地球 (2019)/流浪地球.2019.1080p.BluRay.cht.srt")
    assert sync(client, fresh_admin(client), catalog["movies"])["status"] == "succeeded"

    earth = detail(client, "流浪地球")
    small = min(earth["versions"], key=lambda version: version["size"])
    languages = {subtitle["language"] for subtitle in small["subtitles"]}
    assert {"zh-Hans", "zh-Hant"} <= languages
    headers = bearer(login(client, "admin", "admin-password-for-tests"))

    def choose(preference: str) -> str:
        response = client.patch(
            "/api/me/preferences",
            json={"subtitle_language": preference},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        return str(issue(client, headers, small["id"])["subtitles"][0]["language"])

    # 客户端偏好用 zh-CN / zh-TW，字幕里是 zh-Hans / zh-Hant
    assert choose("zh-TW") == "zh-Hant"
    assert choose("zh-CN") == "zh-Hans"


def test_library_default_preference_uses_the_version_media_item(
    client: TestClient,
    catalog: dict[str, int],
    cloud115: FakeCloud115,
    config: Any,
) -> None:
    cloud115.make_path("影视/剧集/三体 (2023)/Season 1/三体.S01E01.1080p.chs.srt")
    cloud115.make_path("影视/剧集/三体 (2023)/Season 1/三体.S01E01.1080p.eng.srt")
    assert sync(client, fresh_admin(client), catalog["series"])["status"] == "succeeded"
    response = client.patch(
        f"/api/admin/libraries/{catalog['series']}",
        json={"subtitle_language": "en"},
        headers=fresh_admin(client),
    )
    assert response.status_code == 200, response.text

    with sqlite3.connect(config.database_path) as connection:
        row = connection.execute(
            """
            SELECT v.id FROM version v JOIN subtitle s ON s.version_id = v.id
            WHERE s.media_file_id IN (
                SELECT id FROM media_file WHERE rel_path LIKE '%三体.S01E01%'
                    AND rel_path LIKE '%.srt'
            ) LIMIT 1
            """
        ).fetchone()
        assert row is not None
        version_id = int(row[0])
        max_media_item = int(connection.execute("SELECT MAX(id) FROM media_item").fetchone()[0])
    # 这个版本的 id 不是 media_item id；旧实现把 version_id 当成 media_item_id 查资料库
    assert version_id > max_media_item

    ticket = issue(client, fresh_admin(client), version_id)
    assert [subtitle["language"] for subtitle in ticket["subtitles"]][:2] == ["en", "zh-Hans"]


def test_missing_external_subtitle_does_not_hide_the_version(
    client: TestClient, catalog: dict[str, int], config: Any
) -> None:
    before = detail(client, "流浪地球")
    assert len(before["versions"]) == 2
    small = min(before["versions"], key=lambda version: version["size"])
    assert [subtitle["language"] for subtitle in small["subtitles"]] == ["zh-Hans"]

    with sqlite3.connect(config.database_path) as connection:
        connection.execute("UPDATE media_file SET state = 'missing' WHERE rel_path LIKE '%chs.srt'")
        connection.commit()

    after = detail(client, "流浪地球")
    assert {version["id"] for version in after["versions"]} == {
        version["id"] for version in before["versions"]
    }
    small_after = next(version for version in after["versions"] if version["id"] == small["id"])
    assert small_after["subtitles"] == []
