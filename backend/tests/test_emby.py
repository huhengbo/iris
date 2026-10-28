"""Emby 兼容接口：登录、浏览、播放、进度、收藏和可见范围（ADR 0024）。"""

from __future__ import annotations

import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME
from tests.fake_cloud115 import FakeCloud115
from tests.fake_tmdb import FakeTmdb
from tests.test_browse import build_catalog, find, get, new_user, set_access
from tests.test_playback import PUBLIC_URL, save_public_url
from tests.test_sync import fresh_admin


@pytest.fixture
def catalog(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115, tmdb: FakeTmdb
) -> dict[str, int]:
    ids = build_catalog(client, admin, cloud115, tmdb)
    save_public_url(client, f"{PUBLIC_URL}/")
    return ids


AUTH = {
    "X-Emby-Authorization": (
        'MediaBrowser Client="Infuse", Device="iPhone", DeviceId="infuse-1", Version="7.8"'
    )
}


def emby_login(
    client: TestClient,
    username: str = ADMIN_USERNAME,
    password: str = ADMIN_PASSWORD,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    response = client.post(
        "/emby/users/authenticatebyname",
        json={"Username": username, "Pw": password},
        headers=headers or AUTH,
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def token_headers(token: str) -> dict[str, str]:
    return {"X-Emby-Token": token}


def emby_get(client: TestClient, token: str, path: str, **params: Any) -> Any:
    response = client.get(path, params=params, headers=token_headers(token))
    assert response.status_code == 200, response.text
    return response.json()


def test_login_and_browse(client: TestClient, catalog: dict[str, int]) -> None:
    body = emby_login(client)
    token = body["AccessToken"]
    uid = body["User"]["Id"]
    assert body["User"]["Policy"]["IsAdministrator"] is False

    views = emby_get(client, token, f"/Users/{uid}/Views")
    assert [item["Name"] for item in views["Items"]] == ["电影", "剧集"]
    assert [item["CollectionType"] for item in views["Items"]] == ["movies", "tvshows"]
    assert all(item["Type"] == "CollectionFolder" for item in views["Items"])

    root = emby_get(client, token, f"/Users/{uid}/Items")
    assert [item["Id"] for item in root["Items"]] == [item["Id"] for item in views["Items"]]

    page = emby_get(client, token, f"/Users/{uid}/Items", Recursive="true")
    assert page["TotalRecordCount"] == 4
    assert {item["Type"] for item in page["Items"]} == {"Movie", "Series"}

    movies = emby_get(
        client,
        token,
        f"/Users/{uid}/Items",
        ParentId=views["Items"][0]["Id"],
        IncludeItemTypes="Movie",
        SortBy="SortName",
        Recursive="true",
    )
    assert [item["Name"] for item in movies["Items"]] == ["星际穿越", "流浪地球", "疯狂的外星人"]
    for item in movies["Items"]:
        assert None not in item.values()
        assert None not in item["UserData"].values()
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{7}Z", item["DateCreated"])
        if "PremiereDate" in item:
            assert item["PremiereDate"].endswith("T00:00:00.0000000Z")


def enc(internal_id: int, type_bit: int) -> str:
    return str(internal_id * 10 + type_bit)


def series_episodes(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    detail = get(client, headers, f"/items/{find(client, '三体')['id']}")
    result = {}
    for season in detail["seasons"]:
        for episode in get(client, headers, f"/seasons/{season['id']}")["episodes"]:
            result[f"S{season['number']:02d}E{episode['number']:02d}"] = episode
    return result


def test_playback_info_and_stream(
    client: TestClient, catalog: dict[str, int], cloud115: FakeCloud115
) -> None:
    body = emby_login(client)
    token = body["AccessToken"]
    earth = find(client, "流浪地球")
    item_id = enc(earth["id"], 2)
    detail = get(client, fresh_admin(client), f"/items/{earth['id']}")

    info = emby_get(client, token, f"/Items/{item_id}/PlaybackInfo")
    assert info["PlaySessionId"]
    sources = info["MediaSources"]
    assert len(sources) == 2
    assert sources[0]["Id"] == enc(detail["versions"][0]["id"], 6)
    assert sources[0]["IsDefault"] is True
    assert sources[0]["Protocol"] == "File"
    assert sources[0]["Container"] == "mkv"
    assert sources[0]["SupportsDirectPlay"] is True
    assert sources[0]["SupportsDirectStream"] is True
    assert sources[0]["SupportsTranscoding"] is False
    assert token in sources[0]["DirectStreamUrl"]
    assert f"MediaSourceId={sources[0]['Id']}" in sources[0]["DirectStreamUrl"]

    stream = client.get(sources[0]["DirectStreamUrl"], follow_redirects=False)
    assert stream.status_code == 302, stream.text
    assert stream.headers["location"].startswith("https://cdn.example/")
    head = client.head(sources[0]["DirectStreamUrl"], follow_redirects=False)
    assert head.status_code == 302

    # 直接拼 /Videos/{id}/stream 也走同一个解析，大小写由中间件归一化
    fallback = client.get(
        f"/Videos/{item_id}/stream?api_key={token}",
        headers={"User-Agent": "Infuse-Direct/7.8"},
        follow_redirects=False,
    )
    assert fallback.status_code == 302
    assert f"/Videos/{item_id}/stream" in sources[0]["DirectStreamUrl"]
    for path in (f"/Videos/{item_id}/stream.mkv", f"/Videos/{item_id}/original.mkv"):
        response = client.get(
            path,
            params={"api_key": token, "MediaSourceId": sources[0]["Id"]},
            follow_redirects=False,
        )
        assert response.status_code == 302, path


def test_playback_info_includes_reported_media_streams(
    client: TestClient, catalog: dict[str, int]
) -> None:
    from tests.test_playback import MEDIA_INFO

    admin = fresh_admin(client)
    body = emby_login(client)
    token = body["AccessToken"]
    earth = find(client, "流浪地球")
    detail = get(client, admin, f"/items/{earth['id']}")
    small = detail["versions"][1]
    reported = client.put(f"/api/versions/{small['id']}/media-info", json=MEDIA_INFO, headers=admin)
    assert reported.status_code == 204, reported.text

    info = emby_get(client, token, f"/Items/{enc(earth['id'], 2)}/PlaybackInfo")
    source = next(s for s in info["MediaSources"] if s["Id"] == enc(small["id"], 6))
    streams = source["MediaStreams"]
    assert {stream["Type"] for stream in streams} == {"Video", "Audio", "Subtitle"}
    video = next(stream for stream in streams if stream["Type"] == "Video")
    assert (video["Codec"], video["Width"], video["Height"]) == ("hevc", 3840, 2160)
    external = [stream for stream in streams if stream.get("IsExternal")]
    assert len(external) == 1
    assert external[0]["DeliveryMethod"] == "External"
    assert "api_key=" in external[0]["DeliveryUrl"]
    assert external[0]["DeliveryUrl"].endswith("Stream.srt?api_key=" + token)
    assert source["RunTimeTicks"] == MEDIA_INFO["duration"] * 10_000_000


def test_device_token_lifecycle(client: TestClient, catalog: dict[str, int]) -> None:
    body = emby_login(client)
    token = body["AccessToken"]
    uid = body["User"]["Id"]
    session_id = body["SessionInfo"]["Id"]
    admin = fresh_admin(client)

    sessions = client.get("/api/me/sessions", headers=admin).json()
    device = next(s for s in sessions if s["kind"] == "emby")
    assert (device["id"], device["client_name"]) == (int(session_id), "Infuse")

    # 设备令牌只能走 Emby 接口，不能换访问令牌，也不能当普通 Bearer
    assert client.post("/api/auth/refresh", json={"refresh_token": token}).status_code == 401
    assert client.get("/api/home", headers={"Authorization": f"Bearer {token}"}).status_code == 401

    # 撤销后立刻 401
    assert client.delete(f"/api/me/sessions/{session_id}", headers=admin).status_code == 204
    assert client.get(f"/Users/{uid}/Views", headers=token_headers(token)).status_code == 401


def test_device_login_replaces_same_device_and_respects_user_changes(
    client: TestClient, catalog: dict[str, int]
) -> None:
    first = emby_login(client)
    second = emby_login(client)
    admin = fresh_admin(client)
    emby_sessions = [
        session
        for session in client.get("/api/me/sessions", headers=admin).json()
        if session["kind"] == "emby"
    ]
    assert len(emby_sessions) == 1
    # 同一 DeviceId 再次登录后旧令牌失效
    assert (
        client.get(
            f"/Users/{first['User']['Id']}/Views", headers=token_headers(first["AccessToken"])
        ).status_code
        == 401
    )
    assert (
        client.get(
            f"/Users/{second['User']['Id']}/Views", headers=token_headers(second["AccessToken"])
        ).status_code
        == 200
    )

    alice = new_user(client)
    set_access(client, alice["id"], [catalog["movies"]])
    login_body = emby_login(client, "alice", "alice-password")
    alice_token = login_body["AccessToken"]
    alice_uid = login_body["User"]["Id"]
    assert (
        client.get(f"/Users/{alice_uid}/Views", headers=token_headers(alice_token)).status_code
        == 200
    )

    # 修改可见范围：token_version 递增，设备令牌立即失效
    set_access(client, alice["id"], [])
    assert (
        client.get(f"/Users/{alice_uid}/Views", headers=token_headers(alice_token)).status_code
        == 401
    )

    login_body = emby_login(client, "alice", "alice-password")
    alice_token = login_body["AccessToken"]
    # 禁用用户
    assert (
        client.patch(
            f"/api/admin/users/{alice['id']}",
            json={"disabled": True},
            headers=fresh_admin(client),
        ).status_code
        == 200
    )
    assert (
        client.get(f"/Users/{alice_uid}/Views", headers=token_headers(alice_token)).status_code
        == 401
    )


def test_login_failure_and_token_sources(client: TestClient, catalog: dict[str, int]) -> None:
    bad = client.post(
        "/Users/AuthenticateByName",
        json={"Username": ADMIN_USERNAME, "Pw": "wrong-password"},
        headers=AUTH,
    )
    assert bad.status_code == 401
    # 兼容旧客户端的 Password 字段
    old = client.post(
        "/Users/AuthenticateByName",
        json={"Username": ADMIN_USERNAME, "Password": ADMIN_PASSWORD},
        headers=AUTH,
    )
    assert old.status_code == 200, old.text
    token = old.json()["AccessToken"]
    uid = old.json()["User"]["Id"]

    for headers in [
        {"X-Emby-Token": token},
        {
            "X-Emby-Authorization": (
                f'MediaBrowser Client="Infuse", Device="iPhone", DeviceId="d", '
                f'Version="7.8", Token="{token}"'
            )
        },
        {"Authorization": f'MediaBrowser Token="{token}"'},
    ]:
        assert client.get(f"/Users/{uid}/Views", headers=headers).status_code == 200
    assert client.get(f"/Users/{uid}/Views", params={"api_key": token}).status_code == 200
    assert client.get(f"/Users/{uid}/Views", params={"X-Emby-Token": token}).status_code == 200
    assert client.get(f"/Users/{uid}/Views").status_code == 401
    assert client.get("/Users/999999/Views", headers=token_headers(token)).status_code == 403


def test_progress_marks_watched_and_reports_client(
    client: TestClient, catalog: dict[str, int]
) -> None:
    admin = fresh_admin(client)
    login_body = emby_login(client)
    token = login_body["AccessToken"]
    series = get(client, admin, f"/items/{find(client, '三体')['id']}")
    season_id = series["seasons"][0]["id"]
    episode = series_episodes(client, admin)["S01E01"]
    item_id = enc(episode["id"], 4)
    info = emby_get(client, token, f"/Items/{item_id}/PlaybackInfo")
    source = info["MediaSources"][0]
    duration = source["RunTimeTicks"] // 10_000_000
    report = {
        "ItemId": item_id,
        "MediaSourceId": source["Id"],
        "PositionTicks": 0,
    }
    assert (
        client.post("/emby/sessions/playing", json=report, headers=token_headers(token)).status_code
        == 204
    )

    report["PositionTicks"] = int(duration * 0.9) * 10_000_000
    progress = client.post(
        "/emby/Sessions/Playing/Progress", json=report, headers=token_headers(token)
    )
    assert progress.status_code == 204, progress.text

    season = get(client, admin, f"/seasons/{season_id}")
    shown = next(e for e in season["episodes"] if e["id"] == episode["id"])
    assert shown["watched"] is True

    with client.app.state.container.database.session() as connection:  # type: ignore[attr-defined]
        event = connection.execute(
            "SELECT client, device_id, mode, ended_at, watched_seconds FROM play_event "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert (event["client"], event["device_id"], event["mode"]) == (
        "Infuse",
        "infuse-1",
        "external",
    )
    assert event["ended_at"] is not None and event["watched_seconds"] == int(duration * 0.9)

    # 正在播放的客户端名来自最近一条播放事件
    playing = client.get("/api/admin/activity", headers=admin).json()["now_playing"]
    assert playing and playing[0]["client"] == "Infuse"

    stopped = client.post(
        "/emby/Sessions/Playing/Stopped",
        json={**report, "PositionTicks": 600 * 10_000_000},
        headers=token_headers(token),
    )
    assert stopped.status_code == 204
    with client.app.state.container.database.session() as connection:  # type: ignore[attr-defined]
        event = connection.execute(
            "SELECT ended_at, watched_seconds FROM play_event ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert event["ended_at"] is not None and event["watched_seconds"] == 600


def test_favorites_and_played_items(client: TestClient, catalog: dict[str, int]) -> None:
    admin = fresh_admin(client)
    login_body = emby_login(client)
    token = login_body["AccessToken"]
    uid = login_body["User"]["Id"]
    earth = find(client, "流浪地球")
    item_id = enc(earth["id"], 2)

    added = client.post(f"/Users/{uid}/FavoriteItems/{item_id}", headers=token_headers(token))
    assert added.status_code == 200, added.text
    assert added.json()["IsFavorite"] is True
    page = emby_get(client, token, f"/Users/{uid}/Items", IsFavorite="true")
    assert [item["Name"] for item in page["Items"]] == ["流浪地球"]
    assert client.get(f"/api/items/{earth['id']}", headers=admin).json()["favorite"] is True

    removed = client.delete(f"/Users/{uid}/FavoriteItems/{item_id}", headers=token_headers(token))
    assert removed.status_code == 200 and removed.json()["IsFavorite"] is False
    assert client.get(f"/api/items/{earth['id']}", headers=admin).json()["favorite"] is False

    # 季不能收藏
    detail = get(client, admin, f"/items/{find(client, '三体')['id']}")
    season_id = enc(detail["seasons"][0]["id"], 3)
    assert (
        client.post(
            f"/Users/{uid}/FavoriteItems/{season_id}", headers=token_headers(token)
        ).status_code
        == 400
    )

    # 已看：单集、整季、整部剧
    episode = series_episodes(client, admin)["S01E01"]
    played = client.post(
        f"/Users/{uid}/PlayedItems/{enc(episode['id'], 4)}", headers=token_headers(token)
    )
    assert played.status_code == 200 and played.json()["Played"] is True
    assert (
        get(client, admin, f"/seasons/{detail['seasons'][0]['id']}")["episodes"][0]["watched"]
        is True
    )

    season_played = client.post(
        f"/Users/{uid}/PlayedItems/{season_id}", headers=token_headers(token)
    )
    assert season_played.status_code == 200 and season_played.json()["Played"] is True
    series_id = enc(find(client, "三体")["id"], 2)
    series_played = client.post(
        f"/Users/{uid}/PlayedItems/{series_id}", headers=token_headers(token)
    )
    assert series_played.status_code == 200 and series_played.json()["Played"] is True
    assert find(client, "三体")["watched"] is True

    unplayed = client.delete(f"/Users/{uid}/PlayedItems/{series_id}", headers=token_headers(token))
    assert unplayed.status_code == 200 and unplayed.json()["Played"] is False
    assert find(client, "三体")["watched"] is False

    # 人物收藏
    person = get(client, admin, f"/items/{earth['id']}")["cast"][0]
    person_id = enc(person["person_id"], 5)
    result = client.post(f"/Users/{uid}/FavoriteItems/{person_id}", headers=token_headers(token))
    assert result.status_code == 200 and result.json()["IsFavorite"] is True


def test_items_filters_sort_and_paging(client: TestClient, catalog: dict[str, int]) -> None:
    body = emby_login(client)
    token = body["AccessToken"]
    uid = body["User"]["Id"]
    earth = find(client, "流浪地球")
    series = find(client, "三体")

    page = emby_get(
        client,
        token,
        f"/Users/{uid}/Items",
        IncludeItemTypes="Movie",
        SortBy="ProductionYear",
        SortOrder="Descending",
        StartIndex=1,
        Limit=1,
    )
    assert page["TotalRecordCount"] == 3
    assert page["StartIndex"] == 1
    assert len(page["Items"]) == 1
    assert page["Items"][0]["ProductionYear"] == 2019

    assert [
        item["Name"]
        for item in emby_get(
            client, token, f"/Users/{uid}/Items", IncludeItemTypes="Movie", SearchTerm="流浪"
        )["Items"]
    ] == ["流浪地球"]
    assert [
        item["Name"]
        for item in emby_get(
            client, token, f"/Users/{uid}/Items", IncludeItemTypes="Movie", Genres="科幻"
        )["Items"]
    ] == ["星际穿越", "流浪地球"]
    assert [
        item["Name"]
        for item in emby_get(
            client, token, f"/Users/{uid}/Items", IncludeItemTypes="Movie", Years=2014
        )["Items"]
    ] == ["星际穿越"]
    assert [
        item["Name"]
        for item in emby_get(
            client,
            token,
            f"/Users/{uid}/Items",
            IncludeItemTypes="Movie,Series",
            Ids=f"{enc(earth['id'], 2)},{enc(series['id'], 2)}",
        )["Items"]
    ] == ["三体", "流浪地球"]

    # 用户路径不能访问别人
    assert (
        client.get(f"/Users/{int(uid) + 1}/Items", headers=token_headers(token)).status_code == 403
    )


def test_middleware_rewrites_root_paths_and_unknown_emby_paths_404(
    client: TestClient, catalog: dict[str, int]
) -> None:
    assert client.get("/System/Info/Public").status_code == 200
    assert client.get("/system/info/public").status_code == 200
    assert client.get("/EMBY/System/Info/Public").status_code == 200
    login = client.post(
        "/Users/AuthenticateByName",
        json={"Username": ADMIN_USERNAME, "Pw": ADMIN_PASSWORD},
        headers=AUTH,
    )
    assert login.status_code == 200
    token = login.json()["AccessToken"]
    uid = login.json()["User"]["Id"]
    assert client.get(f"/users/{uid}/views", headers=token_headers(token)).status_code == 200
    for path in ["/Emby/NoSuchThing", "/Playback/NoSuchThing", "/Items/1/NoSuchThing"]:
        assert client.get(path, headers=token_headers(token)).status_code == 404, path
    # 前端页面路由不受影响：这里没有构建产物，回落路由返回前端未构建的 404
    assert client.get("/library/3").status_code == 404
    assert client.get("/item/3").status_code == 404


def test_visible_scope_filters_browse_and_playback(
    client: TestClient, catalog: dict[str, int]
) -> None:
    alice = new_user(client)
    set_access(client, alice["id"], [catalog["movies"]])
    body = emby_login(client, "alice", "alice-password")
    token = body["AccessToken"]
    uid = body["User"]["Id"]
    views = emby_get(client, token, f"/Users/{uid}/Views")
    assert [item["Name"] for item in views["Items"]] == ["电影"]
    page = emby_get(client, token, f"/Users/{uid}/Items", Recursive="true")
    assert {item["Type"] for item in page["Items"]} == {"Movie"}
    assert page["TotalRecordCount"] == 3
    series = find(client, "三体")
    assert (
        client.get(f"/Items/{enc(series['id'], 2)}", headers=token_headers(token)).status_code
        == 404
    )
    assert (
        client.get(
            f"/Items/{enc(series['id'], 2)}/PlaybackInfo", headers=token_headers(token)
        ).status_code
        == 404
    )


def test_shows_next_up_seasons_episodes_resume_and_latest(
    client: TestClient, catalog: dict[str, int]
) -> None:
    admin = fresh_admin(client)
    login_body = emby_login(client)
    token = login_body["AccessToken"]
    uid = login_body["User"]["Id"]
    series = find(client, "三体")
    series_id = enc(series["id"], 2)

    seasons = emby_get(client, token, f"/Shows/{series_id}/Seasons")
    assert [(item["IndexNumber"], item["Type"]) for item in seasons["Items"]] == [
        (1, "Season"),
        (2, "Season"),
        (0, "Season"),
    ]
    season_id = seasons["Items"][0]["Id"]
    episodes = emby_get(client, token, f"/Shows/{series_id}/Episodes", SeasonId=season_id)
    assert [item["Name"] for item in episodes["Items"]] == ["第 1 集", "第 2 集"]
    assert all(item["SeriesId"] == series_id for item in episodes["Items"])
    assert all(item["MediaSources"] == [] for item in episodes["Items"])

    # 网易爆米花直接拿列表里的播放源播放，不请求 PlaybackInfo
    with_sources = emby_get(
        client,
        token,
        f"/Shows/{series_id}/Episodes",
        SeasonId=season_id,
        Fields="Overview,MediaSources,Path",
    )
    listed = with_sources["Items"][0]["MediaSources"]
    assert len(listed) == 1
    assert token in listed[0]["DirectStreamUrl"]
    stream = client.get(listed[0]["DirectStreamUrl"], follow_redirects=False)
    assert stream.status_code == 302, stream.text

    next_up = emby_get(client, token, "/Shows/NextUp")
    assert [item["Name"] for item in next_up["Items"]] == ["第 1 集"]
    episode_id = enc(series_episodes(client, admin)["S01E01"]["id"], 4)
    assert (
        client.post(
            f"/Users/{uid}/PlayedItems/{episode_id}", headers=token_headers(token)
        ).status_code
        == 200
    )
    next_up = emby_get(client, token, "/Shows/NextUp")
    assert [item["Name"] for item in next_up["Items"]] == ["第 2 集"]

    # 续播和最近添加：S01E01 已标记看完，用 S01E02 上报续播
    resume_episode = enc(series_episodes(client, admin)["S01E02"]["id"], 4)
    body = emby_get(client, token, f"/Items/{resume_episode}/PlaybackInfo")
    source = body["MediaSources"][0]
    report = {
        "ItemId": resume_episode,
        "MediaSourceId": source["Id"],
        "PositionTicks": 600 * 10_000_000,
    }
    assert (
        client.post("/emby/sessions/playing", json=report, headers=token_headers(token)).status_code
        == 204
    )
    assert (
        client.post(
            "/emby/sessions/playing/progress", json=report, headers=token_headers(token)
        ).status_code
        == 204
    )
    resume = emby_get(client, token, f"/Users/{uid}/Items/Resume")
    assert [item["Id"] for item in resume["Items"]] == [resume_episode]
    assert resume["Items"][0]["UserData"]["PlaybackPositionTicks"] == 600 * 10_000_000

    latest = emby_get(
        client, token, f"/Users/{uid}/Items/Latest", ParentId=enc(catalog["movies"], 1)
    )
    assert latest and all(item["Type"] in ("Movie", "Series") for item in latest)


def test_images_and_empty_endpoints(client: TestClient, catalog: dict[str, int]) -> None:
    login_body = emby_login(client)
    token = login_body["AccessToken"]
    earth = find(client, "流浪地球")
    item_id = enc(earth["id"], 2)

    image = client.get(
        f"/Items/{item_id}/Images/Primary",
        params={"maxWidth": 400},
        headers=token_headers(token),
        follow_redirects=False,
    )
    assert image.status_code == 302
    assert "_342.jpg" in image.headers["location"]
    assert client.get(image.headers["location"]).status_code == 200
    indexed = client.get(
        f"/Items/{item_id}/Images/Primary/0",
        params={"maxWidth": 400},
        headers=token_headers(token),
        follow_redirects=False,
    )
    assert indexed.status_code == 302
    large = client.get(
        f"/Items/{item_id}/Images/Primary",
        params={"maxWidth": 900},
        headers=token_headers(token),
        follow_redirects=False,
    )
    assert "_780.jpg" in large.headers["location"]
    backdrop = client.get(
        f"/Items/{item_id}/Images/Backdrop",
        headers=token_headers(token),
        follow_redirects=False,
    )
    assert backdrop.status_code == 302
    anonymous = client.get(f"/emby/Items/{item_id}/Images/Primary", follow_redirects=False)
    assert anonymous.status_code == 302

    person = get(client, fresh_admin(client), f"/items/{earth['id']}")["cast"][0]
    profile = client.get(
        f"/Items/{enc(person['person_id'], 5)}/Images/Primary",
        params={"maxWidth": 200},
        headers=token_headers(token),
        follow_redirects=False,
    )
    assert profile.status_code == 302
    assert "_185.jpg" in profile.headers["location"]
    assert (
        client.get(f"/Items/{item_id}/Images/NoSuchType", headers=token_headers(token)).status_code
        == 404
    )

    assert client.get(f"/Items/{item_id}/Intros", headers=token_headers(token)).json() == []
    assert emby_get(client, token, f"/Items/{item_id}/Similar")["TotalRecordCount"] == 0
    theme = emby_get(client, token, f"/Items/{item_id}/ThemeMedia")
    assert theme["ThemeVideosResult"]["TotalRecordCount"] == 0
    assert emby_get(client, token, "/Genres")["TotalRecordCount"] == 0
    assert emby_get(client, token, "/Persons")["TotalRecordCount"] == 0
    preferences = emby_get(client, token, "/DisplayPreferences/home")
    assert preferences["CustomPrefs"] == {}


def test_local_file_stream_and_subtitle(
    client: TestClient,
    admin: dict[str, str],
    config: Any,
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
) -> None:
    from tests.test_admin_resources import local_library, local_source, media_dir

    build_catalog(client, admin, cloud115, tmdb)
    directory = media_dir(config, "本地", "星际穿越 (2014)")
    (directory / "Interstellar.2014.1080p.mp4").write_bytes(b"local-video-bytes")
    (directory / "Interstellar.2014.1080p.chs.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHi\n", encoding="utf-8"
    )
    source = local_source(client, admin, "本地", "本地来源")
    library = local_library(client, admin, source["id"])
    from tests.test_sync import sync

    assert sync(client, admin)["status"] == "succeeded"

    items = client.get(f"/api/libraries/{library['id']}/items", headers=admin).json()["items"]
    local_item = next(item for item in items if item["title"] == "星际穿越")
    login_body = emby_login(client)
    token = login_body["AccessToken"]
    info = emby_get(client, token, f"/Items/{enc(local_item['id'], 2)}/PlaybackInfo")
    [media_source] = info["MediaSources"]
    subtitles = [stream for stream in media_source["MediaStreams"] if stream.get("IsExternal")]
    assert len(subtitles) == 1

    stream = client.get(media_source["DirectStreamUrl"])
    assert stream.status_code == 200
    assert stream.content == b"local-video-bytes"
    ranged = client.get(media_source["DirectStreamUrl"], headers={"Range": "bytes=0-4"})
    assert ranged.status_code == 206
    assert ranged.content == b"local"

    subtitle = client.get(subtitles[0]["DeliveryUrl"])
    assert subtitle.status_code == 200
    assert "Hi" in subtitle.text

    # 网易爆米花拉字幕不带令牌：请求过播放的版本放行，其他版本仍然 401
    anonymous = subtitles[0]["DeliveryUrl"].split("?", 1)[0]
    assert anonymous.startswith("/Videos/")
    assert "Hi" in client.get(anonymous).text
    other_version = anonymous.replace(f"/{media_source['Id']}/", f"/{enc(999999, 6)}/")
    assert client.get(other_version).status_code == 401
    # 客户端在 /emby 后面再拼一次 /emby 也能访问
    assert client.get("/emby" + media_source["DirectStreamUrl"]).status_code == 200
    # 设备注销后，之前登记的版本也不再放行
    assert client.post("/Sessions/Logout", headers=token_headers(token)).status_code == 204
    assert client.get(anonymous).status_code == 401


def test_stream_is_throttled_per_device_and_revocation_blocks_it(
    client: TestClient, catalog: dict[str, int]
) -> None:
    login_body = emby_login(client)
    token = login_body["AccessToken"]
    uid = login_body["User"]["Id"]
    session_id = login_body["SessionInfo"]["Id"]
    earth = find(client, "流浪地球")
    info = emby_get(client, token, f"/Items/{enc(earth['id'], 2)}/PlaybackInfo")
    url = info["MediaSources"][0]["DirectStreamUrl"]

    from app.playback.throttle import MAX_REQUESTS_PER_MINUTE

    for _ in range(MAX_REQUESTS_PER_MINUTE):
        assert client.get(url, follow_redirects=False).status_code == 302
    throttled = client.get(url, follow_redirects=False)
    assert throttled.status_code == 429
    assert throttled.headers["retry-after"]

    # 撤销设备后串流地址立即 401
    admin = fresh_admin(client)
    assert client.delete(f"/api/me/sessions/{session_id}", headers=admin).status_code == 204
    assert client.get(url, follow_redirects=False).status_code == 401
    assert client.get(f"/Users/{uid}/Views", headers=token_headers(token)).status_code == 401


def test_item_details_and_filters(client: TestClient, catalog: dict[str, int]) -> None:
    admin = fresh_admin(client)
    login_body = emby_login(client)
    token = login_body["AccessToken"]
    uid = login_body["User"]["Id"]
    earth = find(client, "流浪地球")
    series = find(client, "三体")

    movie = emby_get(client, token, f"/Users/{uid}/Items/{enc(earth['id'], 2)}")
    assert movie["Type"] == "Movie"
    assert len(movie["MediaSources"]) == 2
    assert movie["UserData"]["Played"] is False
    assert movie["MediaSources"][0]["SupportsTranscoding"] is False

    show = emby_get(client, token, f"/Users/{uid}/Items/{enc(series['id'], 2)}")
    assert show["Type"] == "Series"
    assert show["ChildCount"] == 3

    person = get(client, admin, f"/items/{earth['id']}")["cast"][0]
    person_item = emby_get(client, token, f"/Users/{uid}/Items/{enc(person['person_id'], 5)}")
    assert person_item["Type"] == "Person"
    assert person_item["Overview"] == "中国内地男演员、导演。"

    people = emby_get(client, token, f"/Users/{uid}/Items", IncludeItemTypes="Person")
    assert "吴京" in [item["Name"] for item in people["Items"]]
    assert all(item["Type"] == "Person" for item in people["Items"])
    by_person = emby_get(
        client,
        token,
        f"/Users/{uid}/Items",
        IncludeItemTypes="Movie",
        PersonIds=enc(person["person_id"], 5),
    )
    assert [item["Name"] for item in by_person["Items"]] == ["流浪地球", "疯狂的外星人"]

    assert (
        client.post(
            f"/Users/{uid}/PlayedItems/{enc(earth['id'], 2)}", headers=token_headers(token)
        ).status_code
        == 200
    )
    played = emby_get(
        client, token, f"/Users/{uid}/Items", IncludeItemTypes="Movie", Filters="IsPlayed"
    )
    assert [item["Name"] for item in played["Items"]] == ["流浪地球"]
    unplayed = emby_get(
        client, token, f"/Users/{uid}/Items", IncludeItemTypes="Movie", Filters="IsUnplayed"
    )
    assert sorted(item["Name"] for item in unplayed["Items"]) == ["星际穿越", "疯狂的外星人"]

    # POST 也可以取播放信息
    info = client.post(
        f"/Items/{enc(earth['id'], 2)}/PlaybackInfo",
        headers=token_headers(token),
        json={},
    )
    assert info.status_code == 200 and len(info.json()["MediaSources"]) == 2
    # 版本不属于这个条目时 404
    other = get(client, admin, f"/items/{find(client, '星际穿越')['id']}")["versions"][0]
    bad = client.get(
        f"/Videos/{enc(earth['id'], 2)}/stream",
        params={"api_key": token, "MediaSourceId": enc(other["id"], 6)},
        follow_redirects=False,
    )
    assert bad.status_code == 404


def test_session_logout_and_public_endpoints(client: TestClient, catalog: dict[str, int]) -> None:
    assert client.get("/Users/Public").json() == []
    assert client.get("/Branding/Configuration").json() == {}
    assert client.get("/System/Ping").status_code == 200
    assert client.post("/System/Ping").status_code == 200

    login_body = emby_login(client)
    token = login_body["AccessToken"]
    uid = login_body["User"]["Id"]
    assert emby_get(client, token, "/Users/Me")["Name"] == ADMIN_USERNAME
    assert client.post("/Sessions/Capabilities", headers=token_headers(token)).status_code == 204
    assert (
        client.post("/Sessions/Capabilities/Full", headers=token_headers(token)).status_code == 204
    )

    assert client.post("/Sessions/Logout", headers=token_headers(token)).status_code == 204
    assert client.get(f"/Users/{uid}/Views", headers=token_headers(token)).status_code == 401


def test_acceptance_flow_public_login_views_items_playback_stream(
    client: TestClient, catalog: dict[str, int]
) -> None:
    """和验收步骤一致：公开信息 → 登录 → 资料库 → 条目 → 播放信息 → 302。"""
    assert client.get("/System/Info/Public").json()["Version"] == "4.8.0.0"
    login = client.post(
        "/Users/AuthenticateByName",
        json={"Username": ADMIN_USERNAME, "Pw": ADMIN_PASSWORD},
        headers=AUTH,
    )
    assert login.status_code == 200, login.text
    token = login.json()["AccessToken"]
    uid = login.json()["User"]["Id"]

    views = client.get(f"/Users/{uid}/Views", headers=token_headers(token)).json()
    library_id = views["Items"][0]["Id"]
    items = client.get(
        f"/Users/{uid}/Items",
        params={"ParentId": library_id, "IncludeItemTypes": "Movie", "Recursive": "true"},
        headers=token_headers(token),
    ).json()
    movie = next(item for item in items["Items"] if item["Name"] == "流浪地球")
    info = client.get(f"/Items/{movie['Id']}/PlaybackInfo", headers=token_headers(token)).json()
    stream = client.get(info["MediaSources"][0]["DirectStreamUrl"], follow_redirects=False)
    assert stream.status_code == 302
    assert stream.headers["location"].startswith("https://cdn.example/")


def _emby_device(client: TestClient) -> dict[str, Any]:
    sessions = client.get("/api/me/sessions", headers=fresh_admin(client)).json()
    device: dict[str, Any] = next(s for s in sessions if s["kind"] == "emby")
    return device


@pytest.mark.parametrize(
    "headers",
    [
        {
            "Authorization": (
                'Emby Client="Popcorn", Device="%E5%AE%A2%E5%8E%85%E7%94%B5%E8%A7%86", '
                'DeviceId="tv-1", Version="2.1"'
            )
        },
        {
            "X-Emby-Client": "Popcorn",
            "X-Emby-Device-Name": "%E5%AE%A2%E5%8E%85%E7%94%B5%E8%A7%86",
            "X-Emby-Device-Id": "tv-1",
            "X-Emby-Client-Version": "2.1",
        },
    ],
)
def test_login_reads_client_info_from_other_header_styles(
    client: TestClient, catalog: dict[str, int], headers: dict[str, str]
) -> None:
    emby_login(client, headers=headers)

    device = _emby_device(client)
    assert (device["client_name"], device["device_name"], device["client_version"]) == (
        "Popcorn",
        "客厅电视",
        "2.1",
    )


def test_session_without_client_info_is_filled_by_later_requests(
    client: TestClient, catalog: dict[str, int]
) -> None:
    body = emby_login(client, headers={"X-Emby-Authorization": "MediaBrowser"})
    assert _emby_device(client)["device_name"] is None

    uid = body["User"]["Id"]
    response = client.get(
        f"/Users/{uid}/Views",
        params={
            "X-Emby-Client": "Popcorn",
            "X-Emby-Device-Name": "iPad",
            "X-Emby-Device-Id": "ipad-1",
        },
        headers=token_headers(body["AccessToken"]),
    )

    assert response.status_code == 200
    device = _emby_device(client)
    assert (device["client_name"], device["device_name"]) == ("Popcorn", "iPad")


def test_client_name_falls_back_to_the_user_agent(
    client: TestClient, catalog: dict[str, int]
) -> None:
    # 网易爆米花的真实请求头：授权头里没有 Client，只在 UA 里写 Filmly
    emby_login(
        client,
        headers={
            "X-Emby-Authorization": (
                'Emby UserId="1", Device="23127PN0CC", DeviceId="a0e4f52cba081ee1", '
                'Version="2.12.13"'
            ),
            "User-Agent": "Filmly/2.12.13-443",
        },
    )

    device = _emby_device(client)
    assert (device["client_name"], device["device_name"], device["client_version"]) == (
        "网易爆米花",
        "23127PN0CC",
        "2.12.13",
    )


def test_generic_user_agents_are_not_taken_as_client_names(
    client: TestClient, catalog: dict[str, int]
) -> None:
    emby_login(
        client,
        headers={"X-Emby-Authorization": 'Emby Device="TV"', "User-Agent": "okhttp/4.12.0"},
    )

    assert _emby_device(client)["client_name"] is None


@pytest.fixture(autouse=True)
def _extra_builtin_plugins(plugin_env: object) -> None:
    from tests.plugin_env import PluginEnvironment, install_source_local

    assert isinstance(plugin_env, PluginEnvironment)
    install_source_local(plugin_env.root)


def test_plugins_and_packages_are_empty_json_arrays(
    client: TestClient, catalog: dict[str, int]
) -> None:
    token = emby_login(client)["AccessToken"]
    for path in ("/Plugins", "/emby/Plugins", "/Packages", "/emby/Packages"):
        response = client.get(path, headers=token_headers(token))
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert response.json() == []


def test_virtual_folders_lists_visible_libraries(
    client: TestClient, catalog: dict[str, int]
) -> None:
    token = emby_login(client)["AccessToken"]
    folders = emby_get(client, token, "/Library/VirtualFolders")
    views = emby_get(client, token, "/Library/MediaFolders")["Items"]
    assert [folder["Name"] for folder in folders] == ["电影", "剧集"]
    assert [folder["ItemId"] for folder in folders] == [item["Id"] for item in views]
    assert [folder["CollectionType"] for folder in folders] == ["movies", "tvshows"]
    assert all(folder["Locations"] == [] for folder in folders)


def test_library_item_detail(client: TestClient, catalog: dict[str, int]) -> None:
    body = emby_login(client)
    token, uid = body["AccessToken"], body["User"]["Id"]
    views = emby_get(client, token, f"/Users/{uid}/Views")["Items"]
    for view in views:
        detail = emby_get(client, token, f"/Users/{uid}/Items/{view['Id']}")
        assert detail["Id"] == view["Id"]
        assert detail["Type"] == "CollectionFolder"
    missing = client.get(f"/Users/{uid}/Items/999991", headers=token_headers(token))
    assert missing.status_code == 404


def test_plugin_ui_paths_are_not_rewritten_to_emby() -> None:
    from app.emby.middleware import rewrite_path

    assert rewrite_path("/Plugins") == "/emby/plugins"
    assert rewrite_path("/plugins/test.ui/ui/index.html") == "/plugins/test.ui/ui/index.html"


def test_empty_lists_and_unknown_emby_paths_are_json(
    client: TestClient, catalog: dict[str, int]
) -> None:
    token = emby_login(client)["AccessToken"]
    for path in ("/Studios", "/Years", "/emby/Genres"):
        assert emby_get(client, token, path) == {"Items": [], "TotalRecordCount": 0}
    for path in ("/Collections", "/Playlists/x", "/LiveTv/Info", "/Artists", "/Devices"):
        response = client.get(path, headers=token_headers(token))
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")
