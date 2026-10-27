"""在线字幕端到端：搜索、下载、播放/Emby/WebDAV、权限、限流和备份。"""

from __future__ import annotations

import base64
import sqlite3
import xml.etree.ElementTree as ElementTree
from typing import Any, cast
from urllib.parse import quote, unquote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from iris_plugin_sdk import (
    MediaContext,
)

from tests.conftest import bearer, create_user, login
from tests.fake_cloud115 import FakeCloud115
from tests.fake_tmdb import FakeTmdb
from tests.test_browse import build_catalog, find, get
from tests.test_emby import emby_get, emby_login, enc, token_headers
from tests.test_playback import PUBLIC_URL, save_public_url
from tests.test_sync import fresh_admin

PLUGIN_ID = "test.online"
FAILING_ID = "test.failing"
UNAVAILABLE_ID = "test.unavailable"


class SubtitleControl:
    def __init__(self, control: Any) -> None:
        self._control = control

    @property
    def mode(self) -> str:
        return "ok"

    @mode.setter
    def mode(self, value: str) -> None:
        self._control.call("subtitle", "set_error", {"error": value if value == "failed" else None})
        self._control.call(
            "subtitle",
            "set_reason",
            {"reason": "测试插件还没有配置" if value == "unavailable" else None},
        )

    @property
    def same_content(self) -> bool:
        return False

    @same_content.setter
    def same_content(self, value: bool) -> None:
        self._control.call("subtitle", "set_same_content", {"value": value})

    def _calls(self) -> list[str]:
        return [str(item) for item in self._control.call("subtitle", "calls")]

    @property
    def search_calls(self) -> int:
        return self._calls().count("search")

    @property
    def download_calls(self) -> int:
        return self._calls().count("download")

    @property
    def media_requests(self) -> list[MediaContext]:
        return list(self._control.call("subtitle", "media_requests"))


class FakeOnlinePlugin:
    plugin_id = PLUGIN_ID

    def __init__(self, control: Any) -> None:
        self.provider = SubtitleControl(control)


class FailingPlugin(FakeOnlinePlugin):
    plugin_id = FAILING_ID


class UnavailablePlugin(FakeOnlinePlugin):
    plugin_id = UNAVAILABLE_ID


def _srt(remote_id: str) -> str:
    return f"1\n00:00:01,000 --> 00:00:02,000\n{remote_id}\n"


@pytest.fixture
def catalog(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115, tmdb: FakeTmdb
) -> dict[str, int]:
    ids = build_catalog(client, admin, cloud115, tmdb)
    save_public_url(client, f"{PUBLIC_URL}/")
    return ids


@pytest.fixture(autouse=True)
def _subtitle_plugins(plugin_env: Any) -> None:
    from tests.plugin_env import FakePluginSpec, install_fake_plugin

    for plugin_id, name in (
        (PLUGIN_ID, "测试在线字幕"),
        (FAILING_ID, "会失败的字幕插件"),
        (UNAVAILABLE_ID, "未配置的字幕插件"),
    ):
        install_fake_plugin(
            plugin_env.root,
            FakePluginSpec(plugin_id, name, ("subtitle.search", "subtitle.download")),
        )


@pytest.fixture
def plugin(client: TestClient, catalog: dict[str, int]) -> FakeOnlinePlugin:
    from tests.conftest import fake_control

    app = cast(FastAPI, client.app)
    return FakeOnlinePlugin(fake_control(app.state.container.config, PLUGIN_ID))


def movie(client: TestClient, title: str = "流浪地球") -> dict[str, Any]:
    found = find(client, title)
    detail: dict[str, Any] = get(client, fresh_admin(client), f"/items/{found['id']}")
    return detail


def search(client: TestClient, version_id: int, headers: dict[str, str], **params: Any) -> Any:
    return client.get(
        f"/api/versions/{version_id}/subtitles/search", params=params, headers=headers
    )


def download(
    client: TestClient,
    version_id: int,
    headers: dict[str, str],
    *,
    provider_id: str = PLUGIN_ID,
    remote_id: str = "remote/one.srt",
    label: str = "测试在线字幕",
) -> Any:
    return client.post(
        f"/api/versions/{version_id}/subtitles",
        json={
            "provider_id": provider_id,
            "remote_id": remote_id,
            "language": "zh-Hans",
            "label": label,
            "format": "srt",
            "release": "Fake.Release",
        },
        headers=headers,
    )


def test_providers_search_download_and_playback(
    client: TestClient, plugin: FakeOnlinePlugin, catalog: dict[str, int]
) -> None:
    admin = fresh_admin(client)
    providers = client.get("/api/subtitles/providers", headers=admin)
    assert providers.status_code == 200, providers.text
    body = providers.json()
    assert body["allowed"] is True
    by_id = {item["id"]: item for item in body["providers"]}
    assert by_id[PLUGIN_ID] == {
        "id": PLUGIN_ID,
        "name": "测试在线字幕",
        "ready": True,
        "reason": None,
    }
    assert by_id["builtin.assrt"]["ready"] is False
    assert by_id["builtin.assrt"]["reason"] == "还没有填写 API Token"
    # 本地字幕插件只提供 subtitle.search，不算下载器
    assert "builtin.local-subtitles" not in by_id

    earth = movie(client)
    big = earth["versions"][0]
    found = search(client, big["id"], admin, language="zh-Hans")
    assert found.status_code == 200, found.text
    result = found.json()
    assert result["version_id"] == big["id"]
    assert result["language"] == "zh-Hans"
    statuses = {item["id"]: item for item in result["providers"]}
    assert statuses[PLUGIN_ID]["status"] == "ok"
    assert statuses["builtin.assrt"]["status"] == "unavailable"
    assert [item["remote_id"] for item in result["results"]] == ["remote/one.srt"]
    candidate = result["results"][0]
    assert candidate == {
        "provider_id": PLUGIN_ID,
        "provider_name": "测试在线字幕",
        "remote_id": "remote/one.srt",
        "label": "测试在线字幕",
        "release": "Fake.Release.1080p",
        "language": "zh-Hans",
        "format": "srt",
        "forced": False,
        "hearing_impaired": False,
        "score": 1.5,
        "downloads": 7,
        "uploaded_at": "2024-01-02 03:04:05",
        "downloaded": False,
    }

    created = download(client, big["id"], admin)
    assert created.status_code == 201, created.text
    created_body = created.json()
    assert created_body["created"] is True
    subtitle = created_body["subtitle"]
    assert subtitle["source"] == "online"
    assert subtitle["label"] == "测试在线字幕"
    assert subtitle["language"] == "zh-Hans"
    assert created_body["play"]["path"].startswith("/play/")
    assert created_body["play"]["url"].startswith(f"{PUBLIC_URL}/play/")
    assert unquote(created_body["play"]["path"]).endswith("/流浪地球.2019.2160p.WEB-DL.zh-Hans.srt")

    duplicate = download(client, big["id"], admin)
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["created"] is False
    assert duplicate.json()["subtitle"]["id"] == subtitle["id"]

    # 搜索里标记已下载
    again = search(client, big["id"], admin, language="zh-Hans").json()
    assert again["results"][0]["downloaded"] is True

    # 详情里看到 source
    detail = movie(client)
    version = next(item for item in detail["versions"] if item["id"] == big["id"])
    assert version["subtitles"][0]["source"] == "online"

    # 播放签发和直接取内容
    ticket = client.post("/api/play", json={"version_id": big["id"]}, headers=admin)
    assert ticket.status_code == 200, ticket.text
    play_subtitle = ticket.json()["subtitles"][0]
    assert play_subtitle["source"] == "online"
    assert unquote(play_subtitle["path"]).endswith("/流浪地球.2019.2160p.WEB-DL.zh-Hans.srt")
    content = client.get(play_subtitle["path"])
    assert content.status_code == 200, content.text
    assert content.headers["content-type"] == "application/x-subrip; charset=utf-8"
    assert content.headers["cache-control"] == "no-store"
    assert "remote/one.srt" in content.text

    # Emby 把在线字幕列成 Stream.<format>
    login_body = emby_login(client)
    token = login_body["AccessToken"]
    info = emby_get(client, token, f"/Items/{enc(earth['id'], 2)}/PlaybackInfo")
    media_source = next(
        source for source in info["MediaSources"] if source["Id"] == enc(big["id"], 6)
    )
    external = [stream for stream in media_source["MediaStreams"] if stream.get("IsExternal")]
    assert len(external) == 1
    assert external[0]["Codec"] == "srt"
    assert external[0]["DeliveryUrl"].split("?", 1)[0].endswith("Stream.srt")
    streamed = client.get(external[0]["DeliveryUrl"], headers=token_headers(token))
    assert streamed.status_code == 200
    assert "remote/one.srt" in streamed.text


def test_permission_and_provider_reason_visibility(
    client: TestClient,
    plugin: FakeOnlinePlugin,
    catalog: dict[str, int],
    admin_tokens: dict[str, str],
) -> None:
    alice = create_user(client, admin_tokens, "alice", "alice-password")
    tokens = login(client, "alice", "alice-password")
    headers = bearer(tokens)
    big = movie(client)["versions"][0]

    providers = client.get("/api/subtitles/providers", headers=headers).json()
    assert providers["allowed"] is True
    assert all(item["reason"] is None for item in providers["providers"])

    denied = client.patch(
        f"/api/admin/users/{alice['id']}",
        json={"allow_subtitle_download": False},
        headers=fresh_admin(client),
    )
    assert denied.status_code == 200, denied.text
    assert denied.json()["allow_subtitle_download"] is False
    # 权限变化不把已登录用户踢下线
    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["allow_subtitle_download"] is False

    blocked = search(client, big["id"], headers)
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "subtitle_download_not_allowed"
    assert download(client, big["id"], headers).status_code == 403
    assert client.get("/api/subtitles/providers", headers=headers).json()["allowed"] is False

    # 管理员始终允许
    assert search(client, big["id"], fresh_admin(client)).status_code == 200

    allowed = client.patch(
        f"/api/admin/users/{alice['id']}",
        json={"allow_subtitle_download": True},
        headers=fresh_admin(client),
    )
    assert allowed.json()["allow_subtitle_download"] is True
    assert search(client, big["id"], headers).status_code == 200


def test_search_rate_limit(
    client: TestClient, plugin: FakeOnlinePlugin, catalog: dict[str, int]
) -> None:
    admin = fresh_admin(client)
    version_id = movie(client)["versions"][0]["id"]
    for index in range(10):
        response = search(client, version_id, admin)
        assert response.status_code == 200, (index, response.text)
    limited = search(client, version_id, admin)
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "subtitle_rate_limited"


def test_provider_failures_are_isolated_without_token_leaks(
    client: TestClient, catalog: dict[str, int]
) -> None:
    from tests.conftest import fake_control

    config = client.app.state.container.config  # type: ignore[attr-defined]
    failing = FailingPlugin(fake_control(config, FAILING_ID))
    unavailable = UnavailablePlugin(fake_control(config, UNAVAILABLE_ID))
    failing.provider.mode = "failed"
    unavailable.provider.mode = "unavailable"
    big = movie(client)["versions"][0]
    response = search(client, big["id"], fresh_admin(client))
    assert response.status_code == 200, response.text
    assert "secret-token-should-not-leak" not in response.text
    statuses = {item["id"]: item for item in response.json()["providers"]}
    assert statuses["test.failing"]["status"] == "failed"
    assert statuses["test.failing"]["message"] == "字幕站请求失败，请稍后再试"
    assert statuses["test.unavailable"]["status"] == "unavailable"
    assert statuses["test.unavailable"]["message"] == "测试插件还没有配置"

    failed = download(
        client, big["id"], fresh_admin(client), provider_id="test.failing", remote_id="x/y.srt"
    )
    assert failed.status_code == 502
    assert failed.json()["error"]["code"] == "subtitle_provider_failed"
    missing = download(
        client, big["id"], fresh_admin(client), provider_id="test.unavailable", remote_id="x/y.srt"
    )
    assert missing.status_code == 409
    assert missing.json()["error"]["code"] == "subtitle_provider_unavailable"
    unknown = download(
        client, big["id"], fresh_admin(client), provider_id="nope", remote_id="x/y.srt"
    )
    assert unknown.status_code == 409
    assert unknown.json()["error"]["code"] == "subtitle_provider_unavailable"


def test_download_limit_and_online_delete(
    client: TestClient, plugin: FakeOnlinePlugin, catalog: dict[str, int], clock: Any
) -> None:
    admin = fresh_admin(client)
    version_id = movie(client)["versions"][0]["id"]
    ids = []
    for index in range(20):
        response = download(
            client,
            version_id,
            admin,
            remote_id=f"remote/{index}.srt",
            label=f"字幕 {index}",
        )
        assert response.status_code == 201, (index, response.text)
        ids.append(response.json()["subtitle"]["id"])
    # 20 次下载正好用满下载限流窗口
    clock.advance(minutes=11)
    over = download(client, version_id, admin, remote_id="remote/over.srt", label="超出")
    assert over.status_code == 409
    assert over.json()["error"]["code"] == "subtitle_limit_reached"

    listing = client.get(f"/api/admin/versions/{version_id}/subtitles", headers=fresh_admin(client))
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["version"]["filename"] == "流浪地球.2019.2160p.WEB-DL.mkv"
    online = [item for item in body["subtitles"] if item["source"] == "online"]
    assert len(online) == 20
    assert online[0]["online"]["created_by"]["username"] == "admin"
    assert online[0]["online"]["provider_name"] == "测试在线字幕"
    assert online[0]["format"] == "srt"
    assert online[0]["default"] is False
    assert body["embedded"] is None

    deleted = client.delete(f"/api/admin/subtitles/{ids[0]}", headers=fresh_admin(client))
    assert deleted.status_code == 204
    # 删掉一条后可以再下载
    replacement = download(
        client, version_id, admin, remote_id="remote/replacement.srt", label="替补"
    )
    assert replacement.status_code == 201, replacement.text


def test_webdav_lists_and_serves_online_subtitles(
    client: TestClient, plugin: FakeOnlinePlugin, catalog: dict[str, int]
) -> None:
    admin = fresh_admin(client)
    big = movie(client)["versions"][0]
    credentials = {
        "Authorization": "Basic " + base64.b64encode(b"admin:admin-password-for-tests").decode()
    }

    directory = "/webdav/电影/流浪地球 (2019)"

    def listing() -> ElementTree.Element:
        response = client.request(
            "PROPFIND",
            quote(directory, safe="/"),
            headers={**credentials, "Depth": "1"},
        )
        assert response.status_code == 207, response.text
        return ElementTree.fromstring(response.text)

    before = listing()
    hrefs = [
        unquote(element.text or "") for element in before.iter() if element.tag.endswith("href")
    ]
    video = next(href for href in hrefs if href.endswith(".mkv") and "2160p" in href)
    online_name = video.rpartition(".")[0].rpartition("/")[2] + ".zh-Hans.srt"
    assert online_name not in "\n".join(hrefs)

    created = download(client, big["id"], admin)
    assert created.status_code == 201, created.text

    # 目录树缓存必须立刻失效
    after = listing()
    hrefs = [
        unquote(element.text or "") for element in after.iter() if element.tag.endswith("href")
    ]
    assert any(href.endswith("/" + online_name) for href in hrefs)

    url = quote(video.rpartition("/")[0] + "/" + online_name, safe="/")
    fetched = client.get(url, headers=credentials)
    assert fetched.status_code == 200, fetched.text
    assert "remote/one.srt" in fetched.text
    head = client.head(url, headers=credentials)
    assert head.status_code == 200
    assert int(head.headers["content-length"]) > 0


def test_backup_round_trip_keeps_online_subtitle(
    client: TestClient, plugin: FakeOnlinePlugin, catalog: dict[str, int]
) -> None:
    admin = fresh_admin(client)
    big = movie(client)["versions"][0]
    created = download(client, big["id"], admin)
    assert created.status_code == 201, created.text
    subtitle_id = created.json()["subtitle"]["id"]

    backup = client.post("/api/admin/backups", headers=fresh_admin(client))
    assert backup.status_code == 201, backup.text
    name = backup.json()["name"]

    assert (
        client.delete(
            f"/api/admin/subtitles/{subtitle_id}", headers=fresh_admin(client)
        ).status_code
        == 204
    )
    assert not [
        item
        for item in client.get(
            f"/api/admin/versions/{big['id']}/subtitles", headers=fresh_admin(client)
        ).json()["subtitles"]
        if item["source"] == "online"
    ]

    restored = client.post(
        f"/api/admin/backups/{name}/restore?confirm=true", headers=fresh_admin(client)
    )
    assert restored.status_code == 200, restored.text
    restored_list = client.get(
        f"/api/admin/versions/{big['id']}/subtitles", headers=fresh_admin(client)
    )
    online = [item for item in restored_list.json()["subtitles"] if item["source"] == "online"]
    assert [item["id"] for item in online] == [subtitle_id]

    # 内容也一起恢复，能直接播放
    ticket = client.post("/api/play", json={"version_id": big["id"]}, headers=fresh_admin(client))
    path = ticket.json()["subtitles"][0]["path"]
    assert "remote/one.srt" in client.get(path).text


def test_content_is_stored_compressed_in_sqlite(
    client: TestClient, plugin: FakeOnlinePlugin, catalog: dict[str, int], config: Any
) -> None:
    big = movie(client)["versions"][0]
    created = download(client, big["id"], fresh_admin(client))
    assert created.status_code == 201, created.text
    with sqlite3.connect(config.database_path) as connection:
        row = connection.execute(
            "SELECT content, original_encoding, size FROM online_subtitle"
        ).fetchone()
    assert row is not None
    assert row[0][:2] == b"\x78\x9c" or row[0][:1] == b"\x78"
    assert row[1] == "utf-8"
    assert row[2] == len(_srt("remote/one.srt").encode())


def test_media_context_uses_series_title_and_ids(
    client: TestClient, plugin: FakeOnlinePlugin, catalog: dict[str, int]
) -> None:
    """在线插件收到的上下文：剧集带季集号、文件名和 TMDB ID。"""
    series = find(client, "三体")
    detail = get(client, fresh_admin(client), f"/items/{series['id']}")
    season = detail["seasons"][0]
    episode = get(client, fresh_admin(client), f"/seasons/{season['id']}")["episodes"][0]
    response = search(client, episode["versions"][0]["id"], fresh_admin(client))
    assert response.status_code == 200, response.text
    captured = plugin.provider.media_requests
    assert captured
    media = captured[0]
    assert media.kind == "series"
    assert media.season == season["number"] and media.episode == episode["number"]
    assert media.title == f"三体 S{season['number']:02d}E{episode['number']:02d}"
    assert media.tmdb_id == 108545
    assert media.filename and media.filename.endswith(".mkv")


def test_admin_external_subtitle_requires_confirm_and_deletes_source_file(
    client: TestClient, catalog: dict[str, int]
) -> None:
    admin = fresh_admin(client)
    earth = movie(client)
    small = min(earth["versions"], key=lambda version: version["size"])
    listing = client.get(f"/api/admin/versions/{small['id']}/subtitles", headers=admin)
    assert listing.status_code == 200, listing.text
    [external] = [item for item in listing.json()["subtitles"] if item["source"] == "external"]
    assert external["external"]["deletable"] is True
    assert external["external"]["rel_path"].endswith(".chs.srt")
    assert external["format"] == "srt"

    refused = client.delete(f"/api/admin/subtitles/{external['id']}", headers=admin)
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "delete_confirmation_required"

    deleted = client.delete(
        f"/api/admin/subtitles/{external['id']}?confirm=true", headers=fresh_admin(client)
    )
    assert deleted.status_code == 204, deleted.text
    after = client.get(
        f"/api/admin/versions/{small['id']}/subtitles", headers=fresh_admin(client)
    ).json()
    assert after["subtitles"] == []


def test_same_content_from_different_remote_ids_is_deduplicated(
    client: TestClient, plugin: FakeOnlinePlugin, catalog: dict[str, int]
) -> None:
    plugin.provider.same_content = True
    admin = fresh_admin(client)
    version_id = movie(client)["versions"][0]["id"]
    first = download(client, version_id, admin, remote_id="remote/a.srt", label="字幕 A")
    assert first.status_code == 201, first.text
    second = download(client, version_id, admin, remote_id="remote/b.srt", label="字幕 B")
    assert second.status_code == 200, second.text
    assert second.json()["created"] is False
    assert second.json()["subtitle"]["id"] == first.json()["subtitle"]["id"]
    listing = client.get(
        f"/api/admin/versions/{version_id}/subtitles", headers=fresh_admin(client)
    ).json()
    assert len([item for item in listing["subtitles"] if item["source"] == "online"]) == 1


@pytest.fixture(autouse=True)
def _extra_builtin_plugins(plugin_env: object) -> None:
    from tests.plugin_env import PluginEnvironment, install_builtin

    assert isinstance(plugin_env, PluginEnvironment)
    install_builtin(plugin_env.root, "builtin.assrt")
    install_builtin(plugin_env.root, "builtin.fanart")
    install_builtin(plugin_env.root, "builtin.douban")
