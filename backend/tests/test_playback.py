"""第六阶段验收：播放签发、播放跳转、已看标记和最近播放。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote, urlsplit

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from app.api.playback import RedactPlayTokens
from app.playback.throttle import MAX_REQUESTS_PER_MINUTE
from tests.conftest import ADMIN_PASSWORD, FakeClock, bearer, login
from tests.fake_cloud115 import FakeCloud115
from tests.fake_tmdb import FakeTmdb
from tests.test_browse import build_catalog, find, get, new_user, set_access
from tests.test_sync import fresh_admin, sync

PUBLIC_URL = "https://movie.example.com"
INFUSE = "Infuse-Direct/7.8"
VLC = "VLC/3.6.0 LibVLC/3.0.21"


@pytest.fixture
def catalog(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115, tmdb: FakeTmdb
) -> dict[str, int]:
    ids = build_catalog(client, admin, cloud115, tmdb)
    save_public_url(client, f"{PUBLIC_URL}/")
    return ids


def save_public_url(client: TestClient, url: str | None) -> Response:
    return client.put(
        "/api/admin/settings/server",
        json={"name": "光圈", "public_url": url},
        headers=fresh_admin(client),
    )


def issue(client: TestClient, headers: dict[str, str], /, **body: Any) -> dict[str, Any]:
    response = client.post("/api/play", json=body, headers=headers)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def jump(client: TestClient, url: str, user_agent: str = INFUSE, method: str = "GET") -> Response:
    """像播放器一样访问跳转地址，不跟随 302。"""
    assert url.startswith(f"{PUBLIC_URL}/play/")
    return client.request(
        method, urlsplit(url).path, headers={"User-Agent": user_agent}, follow_redirects=False
    )


def movie(client: TestClient, title: str = "流浪地球") -> dict[str, Any]:
    detail: dict[str, Any] = get(client, fresh_admin(client), f"/items/{find(client, title)['id']}")
    return detail


def episodes(client: TestClient) -> dict[str, dict[str, Any]]:
    """三体的每一集，键是 S01E02 这样的编号。"""
    headers = fresh_admin(client)
    detail = get(client, headers, f"/items/{find(client, '三体')['id']}")
    result = {}
    for season in detail["seasons"]:
        for episode in get(client, headers, f"/seasons/{season['id']}")["episodes"]:
            result[f"S{season['number']:02d}E{episode['number']:02d}"] = episode
    return result


def alice_with(client: TestClient, library_ids: list[int]) -> tuple[dict[str, object], Any]:
    alice = new_user(client)
    set_access(client, alice["id"], library_ids)
    return alice, lambda: bearer(login(client, "alice", "alice-password"))


# ---- 对外访问地址 ----


def test_public_url_is_validated_and_normalized(client: TestClient, admin: dict[str, str]) -> None:
    for bad in [
        "movie.example.com",
        "ftp://movie.example.com",
        "https://u:p@movie.example.com",
        "https://movie.example.com/?a=1",
        "https://movie.example.com/#x",
        "https://",
    ]:
        response = save_public_url(client, bad)
        assert response.status_code == 400, bad
        assert response.json()["error"]["code"] == "invalid_public_url"

    saved = save_public_url(client, " https://movie.example.com/cinema/ ")
    assert saved.json()["server"]["public_url"] == "https://movie.example.com/cinema"
    # 只改名称时保持原值
    renamed = client.put(
        "/api/admin/settings/server", json={"name": "家里"}, headers=fresh_admin(client)
    )
    assert renamed.json()["server"]["public_url"] == "https://movie.example.com/cinema"
    assert save_public_url(client, "").json()["server"]["public_url"] is None


def test_issue_requires_public_url(client: TestClient, catalog: dict[str, int]) -> None:
    save_public_url(client, None)
    response = client.post(
        "/api/play", json={"item_id": find(client, "流浪地球")["id"]}, headers=fresh_admin(client)
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "public_url_not_configured"


# ---- 签发 ----


def test_issue_picks_default_version_and_builds_launch_links(
    client: TestClient, catalog: dict[str, int], clock: FakeClock
) -> None:
    earth = movie(client)
    big, small = earth["versions"]
    assert big["size"] > small["size"]

    ticket = issue(client, fresh_admin(client), item_id=earth["id"])
    # 第一次播放用最大的版本
    assert ticket["version_id"] == big["id"]
    assert ticket["title"] == "流浪地球"
    assert ticket["filename"] == "流浪地球.2019.2160p.WEB-DL.mkv"
    assert ticket["url"].startswith(f"{PUBLIC_URL}/play/")
    assert ticket["url"].endswith("/" + quote("流浪地球.2019.2160p.WEB-DL.mkv"))
    expires_at = datetime.fromisoformat(ticket["expires_at"])
    assert expires_at - clock.now() == timedelta(hours=24)
    assert ticket["subtitles"] == []
    assert ticket["launch"]["infuse"] == (
        f"infuse://x-callback-url/play?url={quote(ticket['url'], safe='')}"
    )

    chosen = issue(client, fresh_admin(client), version_id=small["id"], client="ios")
    assert chosen["version_id"] == small["id"]
    [subtitle] = chosen["subtitles"]
    assert subtitle["label"] == "简体中文"
    assert subtitle["language"] == "zh-Hans"
    assert subtitle["url"].endswith("/" + quote("流浪地球.2019.1080p.BluRay.chs.srt"))
    encoded_sub = quote(subtitle["url"], safe="")
    launch = chosen["launch"]
    assert launch["infuse"].endswith(f"&sub={encoded_sub}")
    assert launch["vlc"] == (
        f"vlc-x-callback://x-callback-url/stream?url={quote(chosen['url'], safe='')}"
        f"&sub={encoded_sub}"
    )
    assert launch["nplayer"] == f"nplayer-{chosen['url']}"
    assert launch["senplayer"].startswith("senplayer://x-callback-url/play?url=")
    path = urlsplit(chosen["url"]).path
    assert launch["android"] == (
        f"intent://movie.example.com{path}#Intent;scheme=https;type=video/*;"
        f"S.title={quote('流浪地球', safe='')};end"
    )

    # 之后不指定版本时沿用上次的版本
    assert issue(client, fresh_admin(client), item_id=earth["id"])["version_id"] == small["id"]


def test_user_subtitle_preference_overrides_library_default(
    client: TestClient,
    catalog: dict[str, int],
    cloud115: FakeCloud115,
) -> None:
    cloud115.make_path("影视/电影/流浪地球 (2019)/流浪地球.2019.1080p.BluRay.en.srt")
    sync(client, fresh_admin(client), catalog["movies"])

    earth = movie(client)
    small = min(earth["versions"], key=lambda version: version["size"])
    library = catalog["movies"]
    admin = fresh_admin(client)
    saved = client.patch(
        f"/api/admin/libraries/{library}",
        json={"subtitle_language": "en"},
        headers=admin,
    )
    assert saved.status_code == 200, saved.text

    library_default = issue(client, admin, version_id=small["id"])
    assert library_default["subtitles"][0]["language"] == "en"
    assert library_default["selected_subtitle_id"] == library_default["subtitles"][0]["id"]

    _, alice_headers = alice_with(client, [library])
    preference = client.patch(
        "/api/me/preferences",
        json={"subtitle_language": "zh-Hans"},
        headers=alice_headers(),
    )
    assert preference.status_code == 200, preference.text
    user_default = issue(client, alice_headers(), version_id=small["id"])
    assert user_default["subtitles"][0]["language"] == "zh-Hans"

    chosen = user_default["subtitles"][-1]
    explicit = client.post(
        "/api/play",
        json={"version_id": small["id"], "subtitle_id": chosen["id"]},
        headers=alice_headers(),
    )
    assert explicit.status_code == 200, explicit.text
    assert explicit.json()["selected_subtitle_id"] == chosen["id"]
    assert explicit.json()["subtitles"][0]["id"] == chosen["id"]


def test_issue_episode_and_reject_series_item(client: TestClient, catalog: dict[str, int]) -> None:
    episode = episodes(client)["S01E02"]
    ticket = issue(client, fresh_admin(client), episode_id=episode["id"])
    assert ticket["title"] == "三体 S01E02"
    assert ticket["version_id"] == episode["versions"][0]["id"]

    series = client.post(
        "/api/play", json={"item_id": find(client, "三体")["id"]}, headers=fresh_admin(client)
    )
    assert series.status_code == 400
    assert series.json()["error"]["code"] == "episode_required"
    both = client.post(
        "/api/play",
        json={"item_id": 1, "episode_id": episode["id"]},
        headers=fresh_admin(client),
    )
    assert both.status_code == 422
    assert client.post("/api/play", json={}, headers=fresh_admin(client)).status_code == 422


def test_out_of_scope_versions_cannot_be_issued(
    client: TestClient, catalog: dict[str, int]
) -> None:
    episode = episodes(client)["S01E01"]
    series_id = find(client, "三体")["id"]
    earth = movie(client)
    _, alice = alice_with(client, [catalog["movies"]])
    headers = alice()
    for body in [
        {"version_id": episode["versions"][0]["id"]},
        {"episode_id": episode["id"]},
        {"item_id": series_id},
        {"version_id": 999999},
        {"item_id": 999999},
    ]:
        response = client.post("/api/play", json=body, headers=headers)
        assert response.status_code == 404, body
        assert response.json()["error"]["code"] == "not_found"
    # 范围内的照常签发
    assert issue(client, headers, item_id=earth["id"])["version_id"] == earth["versions"][0]["id"]


def test_issue_requires_login(client: TestClient, catalog: dict[str, int]) -> None:
    response = client.post("/api/play", json={"item_id": find(client, "流浪地球")["id"]})
    assert response.status_code == 401


# ---- 跳转 ----


def test_redirect_fetches_link_with_player_ua_and_caches_per_ua(
    client: TestClient, catalog: dict[str, int], cloud115: FakeCloud115, clock: FakeClock
) -> None:
    ticket = issue(client, fresh_admin(client), item_id=find(client, "流浪地球")["id"])
    cloud115.link_requests.clear()

    first = jump(client, ticket["url"], INFUSE)
    assert first.status_code == 302
    assert first.headers["location"].startswith("https://cdn.example/")
    assert first.headers["cache-control"] == "no-store"
    # 取链用的就是播放器的 UA
    assert [ua for _, ua in cloud115.link_requests] == [INFUSE]

    # 同一个 UA 命中缓存，HEAD 也一样
    assert jump(client, ticket["url"], INFUSE).headers["location"] == first.headers["location"]
    head = jump(client, ticket["url"], INFUSE, method="HEAD")
    assert head.status_code == 302
    assert head.headers["location"] == first.headers["location"]
    assert len(cloud115.link_requests) == 1

    # 不同的 UA 各取各的
    other = jump(client, ticket["url"], VLC)
    assert other.status_code == 302
    assert other.headers["location"] != first.headers["location"]
    assert [ua for _, ua in cloud115.link_requests] == [INFUSE, VLC]

    # 缓存按 UA 命中：同一个 UA 不重复取链
    assert jump(client, ticket["url"], INFUSE).headers["location"] == first.headers["location"]
    assert [ua for _, ua in cloud115.link_requests] == [INFUSE, VLC]


def test_access_log_hides_play_tokens() -> None:
    def access_line(path: str) -> str:
        # 参数和 uvicorn 访问日志一致
        args = ("1.2.3.4:5678", "GET", path, "1.1", 302)
        record = logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 0, '%s - "%s %s HTTP/%s" %d', args, None
        )
        assert RedactPlayTokens().filter(record)
        return record.getMessage()

    assert access_line("/play/secret.token/a.mkv") == (
        '1.2.3.4:5678 - "GET /play/***/a.mkv HTTP/1.1" 302'
    )
    assert "secret" not in access_line("/play/secret.token")
    assert "/api/items/1" in access_line("/api/items/1")


def test_app_logs_hide_credentials_tokens_and_links(
    client: TestClient,
    catalog: dict[str, int],
    cloud115: FakeCloud115,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # httpx 的客户端日志会打出完整请求地址；这里只检查应用自己的日志
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    caplog.set_level(logging.DEBUG)
    tokens = login(client, "admin", ADMIN_PASSWORD)
    headers = bearer(tokens)
    assert sync(client, headers)["status"] == "succeeded"
    ticket = issue(client, headers, item_id=find(client, "流浪地球")["id"])
    link = jump(client, ticket["url"]).headers["location"]
    cloud115.control.call("source", "fail_next", {"method": "resolve", "count": 1})
    assert jump(client, ticket["url"], VLC).status_code == 503

    play_token = urlsplit(ticket["url"]).path.split("/")[2]
    secrets = [
        ADMIN_PASSWORD,
        tokens["access_token"],
        tokens["refresh_token"],
        play_token,
        link,
    ]
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "play GET redirected" in logged
    for secret in secrets:
        assert secret not in logged


def test_subtitle_redirect(
    client: TestClient, catalog: dict[str, int], cloud115: FakeCloud115
) -> None:
    small = movie(client)["versions"][1]
    [subtitle] = issue(client, fresh_admin(client), version_id=small["id"])["subtitles"]
    response = jump(client, subtitle["url"])
    assert response.status_code == 302
    assert quote("流浪地球.2019.1080p.BluRay.chs.srt") in response.headers["location"]


def test_redirect_fails_after_expiry(
    client: TestClient, catalog: dict[str, int], clock: FakeClock
) -> None:
    ticket = issue(client, fresh_admin(client), item_id=find(client, "流浪地球")["id"])
    clock.advance(hours=23, minutes=59)
    assert jump(client, ticket["url"]).status_code == 302
    clock.advance(minutes=1)
    expired = jump(client, ticket["url"])
    assert expired.status_code == 410
    assert "过期" in expired.text


@pytest.mark.parametrize("change", ["disable", "reset_password", "own_password", "access"])
def test_redirect_fails_after_user_changes(
    client: TestClient, catalog: dict[str, int], change: str
) -> None:
    alice, login_alice = alice_with(client, [catalog["movies"]])
    headers = login_alice()
    ticket = issue(client, headers, item_id=find(client, "流浪地球")["id"])
    assert jump(client, ticket["url"]).status_code == 302

    admin = fresh_admin(client)
    if change == "disable":
        response = client.patch(
            f"/api/admin/users/{alice['id']}", json={"disabled": True}, headers=admin
        )
    elif change == "reset_password":
        response = client.post(
            f"/api/admin/users/{alice['id']}/password",
            json={"new_password": "another-password"},
            headers=admin,
        )
    elif change == "own_password":
        response = client.post(
            "/api/auth/password",
            json={"current_password": "alice-password", "new_password": "another-password"},
            headers=headers,
        )
    else:
        response = client.put(
            f"/api/admin/users/{alice['id']}/libraries", json={"library_ids": []}, headers=admin
        )
    assert response.status_code == 200, response.text

    # 已缓存的直链也不能再拿到
    denied = jump(client, ticket["url"])
    assert denied.status_code == 401
    assert "location" not in denied.headers


def test_redirect_rejects_forged_tokens(
    client: TestClient, catalog: dict[str, int], admin_tokens: dict[str, str]
) -> None:
    ticket = issue(client, fresh_admin(client), item_id=find(client, "流浪地球")["id"])
    path = urlsplit(ticket["url"]).path
    token = path.split("/")[2]
    body, _, signature = token.partition(".")
    for bad in [
        f"{body}.{signature[:-2]}AA",
        f"{body[:-2]}AA.{signature}",
        "garbage",
        # 登录令牌签名用的子密钥不同，不能当播放令牌用
        admin_tokens["access_token"],
    ]:
        response = client.get(f"/play/{bad}/a.mkv", follow_redirects=False)
        assert response.status_code == 401, bad
        assert response.headers["content-type"].startswith("text/plain")


def test_redirect_reports_missing_files(
    client: TestClient, catalog: dict[str, int], cloud115: FakeCloud115
) -> None:
    earth = movie(client)
    big, small = earth["versions"]
    gone = issue(client, fresh_admin(client), version_id=big["id"])
    stale = issue(client, fresh_admin(client), version_id=small["id"])
    folder = cloud115.make_path("影视/电影/流浪地球 (2019)")

    # 来源上已经没有，但还没同步：取链时才发现
    cloud115.remove(cloud115.child(folder, "流浪地球.2019.2160p.WEB-DL.mkv") or "")
    response = jump(client, gone["url"])
    assert response.status_code == 404

    # 同步后文件标记为失效，不再去来源取链
    cloud115.remove(cloud115.child(folder, "流浪地球.2019.1080p.BluRay.mkv") or "")
    assert sync(client, fresh_admin(client))["status"] == "succeeded"
    before = len(cloud115.link_requests)
    assert jump(client, stale["url"]).status_code == 404
    assert len(cloud115.link_requests) == before


def test_rate_limited_returns_503_with_retry_after(
    client: TestClient, catalog: dict[str, int], cloud115: FakeCloud115
) -> None:
    ticket = issue(client, fresh_admin(client), item_id=find(client, "流浪地球")["id"])
    cloud115.control.call("source", "fail_next", {"method": "resolve", "count": 1})
    limited = jump(client, ticket["url"])
    assert limited.status_code == 503
    assert int(limited.headers["retry-after"]) == 30 * 60


def test_unavailable_source_returns_503(
    client: TestClient, catalog: dict[str, int], cloud115: FakeCloud115
) -> None:
    ticket = issue(client, fresh_admin(client), item_id=find(client, "流浪地球")["id"])
    cloud115.control.call("source", "set_resolve_error", {"error": "unavailable"})

    response = jump(client, ticket["url"])

    assert response.status_code == 503
    assert "登录已失效" in response.text


def test_redirect_is_throttled_per_token(
    client: TestClient, catalog: dict[str, int], cloud115: FakeCloud115
) -> None:
    big, small = movie(client)["versions"]
    ticket = issue(client, fresh_admin(client), version_id=big["id"])
    for _ in range(MAX_REQUESTS_PER_MINUTE):
        assert jump(client, ticket["url"]).status_code == 302
    throttled = jump(client, ticket["url"])
    assert throttled.status_code == 429
    assert int(throttled.headers["retry-after"]) >= 1
    # 别的令牌不受影响
    other = issue(client, fresh_admin(client), version_id=small["id"])
    assert jump(client, other["url"]).status_code == 302


def test_watched_marks_cascade_and_stay_per_user(
    client: TestClient, catalog: dict[str, int]
) -> None:
    _, login_alice = alice_with(client, [catalog["movies"], catalog["series"]])
    series = find(client, "三体")
    admin = fresh_admin(client)
    assert client.put(f"/api/items/{series['id']}/watched", headers=admin).status_code == 204

    assert find(client, "三体")["watched"] is True
    assert all(e["watched"] for e in episodes(client).values())
    # 别人的标记互不影响
    alice = login_alice()
    assert get(client, alice, "/search", q="三体")["items"][0]["watched"] is False

    # 取消一季：整部剧不再算已看
    season_one = get(client, admin, f"/items/{series['id']}")["seasons"][0]
    assert (
        client.delete(f"/api/seasons/{season_one['id']}/watched", headers=admin).status_code == 204
    )
    marks = {code: e["watched"] for code, e in episodes(client).items()}
    assert marks == {"S01E01": False, "S01E02": False, "S02E01": True, "S00E01": True}
    assert find(client, "三体")["watched"] is False

    # 单集
    s01e01 = episodes(client)["S01E01"]
    assert client.put(f"/api/episodes/{s01e01['id']}/watched", headers=admin).status_code == 204
    assert episodes(client)["S01E01"]["watched"] is True

    # 电影
    earth = find(client, "流浪地球")
    assert client.put(f"/api/items/{earth['id']}/watched", headers=admin).status_code == 204
    detail = get(client, admin, f"/items/{earth['id']}")
    assert detail["watched"] is True
    assert client.delete(f"/api/items/{earth['id']}/watched", headers=admin).status_code == 204
    assert get(client, admin, f"/items/{earth['id']}")["watched"] is False


def test_watched_marks_respect_scope(client: TestClient, catalog: dict[str, int]) -> None:
    _, login_alice = alice_with(client, [catalog["movies"]])
    alice = login_alice()
    series = find(client, "三体")
    season = get(client, fresh_admin(client), f"/items/{series['id']}")["seasons"][0]
    episode = episodes(client)["S01E01"]
    for path in [
        f"/api/items/{series['id']}/watched",
        f"/api/seasons/{season['id']}/watched",
        f"/api/episodes/{episode['id']}/watched",
        "/api/items/999999/watched",
    ]:
        assert client.put(path, headers=alice).status_code == 404, path
        assert client.delete(path, headers=alice).status_code == 404, path


def test_wall_filters_by_watched(client: TestClient, catalog: dict[str, int]) -> None:
    admin = fresh_admin(client)
    earth = find(client, "流浪地球")
    client.put(f"/api/items/{earth['id']}/watched", headers=admin)
    path = f"/libraries/{catalog['movies']}/items"
    watched = get(client, admin, path, watched="true")
    assert [i["title"] for i in watched["items"]] == ["流浪地球"]
    assert watched["total"] == 1
    unwatched = get(client, admin, path, watched="false", sort="name")
    assert sorted(i["title"] for i in unwatched["items"]) == ["星际穿越", "疯狂的外星人"]
    assert get(client, admin, path)["total"] == 3


# ---- 下一集与最近播放 ----


def test_next_episode_follows_plays_and_marks(client: TestClient, catalog: dict[str, int]) -> None:
    series_id = find(client, "三体")["id"]
    eps = episodes(client)

    def next_code() -> str:
        ref = get(client, fresh_admin(client), f"/items/{series_id}")["next_episode"]
        return f"S{ref['season_number']:02d}E{ref['number']:02d}"

    assert next_code() == "S01E01"
    issue(client, fresh_admin(client), episode_id=eps["S01E01"]["id"])
    # 播放过但没标记已看：接着看这一集
    assert next_code() == "S01E01"
    client.put(f"/api/episodes/{eps['S01E01']['id']}/watched", headers=fresh_admin(client))
    assert next_code() == "S01E02"
    # 跳着看了第二季，看完后往后已没有正片，回到前面没看的；特别篇不参与推荐
    issue(client, fresh_admin(client), episode_id=eps["S02E01"]["id"])
    client.put(f"/api/episodes/{eps['S02E01']['id']}/watched", headers=fresh_admin(client))
    assert next_code() == "S01E02"
    client.put(f"/api/items/{series_id}/watched", headers=fresh_admin(client))
    assert next_code() == "S01E01"


def test_home_lists_recent_plays_per_user(
    client: TestClient, catalog: dict[str, int], clock: FakeClock
) -> None:
    alice, login_alice = alice_with(client, [catalog["movies"], catalog["series"]])
    eps = episodes(client)
    earth = find(client, "流浪地球")
    alice_headers = login_alice()
    issue(client, alice_headers, episode_id=eps["S01E01"]["id"])
    clock.advance(minutes=1)
    issue(client, alice_headers, item_id=earth["id"])
    clock.advance(minutes=1)
    issue(client, alice_headers, episode_id=eps["S01E02"]["id"])

    recent = get(client, login_alice(), "/home")["recent"]
    # 每部作品一条，按最近播放排序；剧集带最近播放的那一集
    assert [r["item"]["title"] for r in recent] == ["三体", "流浪地球"]
    assert recent[0]["episode"]["number"] == 2
    assert recent[0]["episode"]["season_number"] == 1
    assert recent[1]["episode"] is None
    assert recent[0]["played_at"] > recent[1]["played_at"]
    # 管理员没有播放过
    assert get(client, fresh_admin(client), "/home")["recent"] == []

    # 失去资料库权限后，那里的播放记录不再出现
    set_access(client, alice["id"], [catalog["movies"]])
    assert [r["item"]["title"] for r in get(client, login_alice(), "/home")["recent"]] == [
        "流浪地球"
    ]


def test_issue_records_play_events(client: TestClient, catalog: dict[str, int]) -> None:
    earth = find(client, "流浪地球")
    issue(client, fresh_admin(client), item_id=earth["id"], client="android")
    issue(client, fresh_admin(client), item_id=earth["id"])
    with client.app.state.container.database.session() as connection:  # type: ignore[attr-defined]
        rows = connection.execute("SELECT client FROM play_event ORDER BY id").fetchall()
    assert [row["client"] for row in rows] == ["android", "web"]


# ---- 内置播放器 ----


def test_builtin_player_works_without_public_url(
    client: TestClient, catalog: dict[str, int]
) -> None:
    save_public_url(client, None)
    small = movie(client)["versions"][1]
    ticket = issue(
        client, fresh_admin(client), version_id=small["id"], client="android", player="builtin"
    )
    assert ticket["url"] is None and ticket["launch"] is None
    assert ticket["path"].startswith("/play/")
    assert ticket["path"].endswith("/" + quote(ticket["filename"]))
    [subtitle] = ticket["subtitles"]
    assert subtitle["url"] is None and subtitle["path"].startswith("/play/")
    # 路径直接拼在 App 连的服务器地址后面就能跳转
    response = client.get(ticket["path"], headers={"User-Agent": "mpv"}, follow_redirects=False)
    assert response.status_code == 302
    assert (
        client.get(
            subtitle["path"], headers={"User-Agent": "mpv"}, follow_redirects=False
        ).status_code
        == 302
    )

    # 配置了对外访问地址时两种都给，url 就是对外访问地址加 path
    save_public_url(client, PUBLIC_URL)
    both = issue(client, fresh_admin(client), version_id=small["id"], player="builtin")
    assert both["url"] == PUBLIC_URL + both["path"]
    assert both["subtitles"][0]["url"] == PUBLIC_URL + both["subtitles"][0]["path"]
    assert both["launch"] is not None


def progress(
    client: TestClient, headers: dict[str, str], playable_id: int, position: int, duration: int
) -> Response:
    return client.put(
        f"/api/playables/{playable_id}/progress",
        json={"position": position, "duration": duration},
        headers=headers,
    )


def test_progress_resumes_and_marks_watched(client: TestClient, catalog: dict[str, int]) -> None:
    admin = fresh_admin(client)
    earth = movie(client)
    ticket = issue(client, admin, item_id=earth["id"], player="builtin")
    assert ticket["position"] is None
    playable = ticket["playable_id"]

    assert progress(client, admin, playable, 1800, 7200).status_code == 204
    assert movie(client)["progress"] == {"position": 1800, "duration": 7200}
    assert issue(client, admin, item_id=earth["id"], player="builtin")["position"] == 1800
    recent = get(client, admin, "/home")["recent"][0]
    assert recent["progress"] == {"position": 1800, "duration": 7200}

    # 开头 1 分钟内不记续播位置
    assert progress(client, admin, playable, 30, 7200).status_code == 204
    assert movie(client)["progress"] is None
    # 看到 90% 算看完：标记已看并清掉位置
    progress(client, admin, playable, 6600, 7200)
    detail = movie(client)
    assert detail["watched"] is True and detail["progress"] is None
    # 重看到一半停下：记位置但不取消已看
    progress(client, admin, playable, 3600, 7200)
    detail = movie(client)
    assert detail["watched"] is True
    assert detail["progress"] == {"position": 3600, "duration": 7200}
    # 手动标记从头开始，清掉位置
    client.delete(f"/api/items/{earth['id']}/watched", headers=admin)
    detail = movie(client)
    assert detail["watched"] is False and detail["progress"] is None


def test_episode_progress_shows_on_season_and_next_episode(
    client: TestClient, catalog: dict[str, int]
) -> None:
    admin = fresh_admin(client)
    series_id = find(client, "三体")["id"]
    eps = episodes(client)
    ticket = issue(client, admin, episode_id=eps["S01E02"]["id"], player="builtin")
    progress(client, admin, ticket["playable_id"], 900, 2700)

    assert episodes(client)["S01E02"]["progress"] == {"position": 900, "duration": 2700}
    assert episodes(client)["S01E01"]["progress"] is None
    ref = get(client, admin, f"/items/{series_id}")["next_episode"]
    assert ref["number"] == 2 and ref["progress"] == {"position": 900, "duration": 2700}
    recent = get(client, admin, "/home")["recent"][0]
    assert recent["episode"]["progress"] == recent["progress"]

    # 这一集看完后推荐下一集
    progress(client, admin, ticket["playable_id"], 2650, 2700)
    assert episodes(client)["S01E02"]["watched"] is True
    ref = get(client, admin, f"/items/{series_id}")["next_episode"]
    assert (ref["season_number"], ref["number"]) == (2, 1)


def test_progress_is_validated_and_scoped(client: TestClient, catalog: dict[str, int]) -> None:
    eps = episodes(client)
    ticket = issue(client, fresh_admin(client), episode_id=eps["S01E01"]["id"])
    _, login_alice = alice_with(client, [catalog["movies"]])
    alice = login_alice()
    assert progress(client, alice, ticket["playable_id"], 600, 2700).status_code == 404
    assert progress(client, alice, 999999, 600, 2700).status_code == 404
    admin = fresh_admin(client)
    assert progress(client, admin, ticket["playable_id"], -1, 2700).status_code == 422
    assert progress(client, admin, ticket["playable_id"], 10, 0).status_code == 422
    assert (
        client.put(
            f"/api/playables/{ticket['playable_id']}/progress",
            json={"position": 10, "duration": 20},
        ).status_code
        == 401
    )


# ---- 流信息 ----

MEDIA_INFO: dict[str, Any] = {
    "duration": 7680,
    "width": 3840,
    "height": 2160,
    "video_codec": "hevc",
    "hdr": "dolby_vision",
    "audio": [
        {"language": "chi", "title": "国语", "codec": "eac3", "channels": 6, "default": True},
        {"language": "eng", "codec": "truehd", "channels": 8},
    ],
    "subtitles": [
        {"language": "chi", "title": "简体", "codec": "subrip", "default": True},
        {"language": "eng", "codec": "hdmv_pgs_subtitle", "forced": True},
    ],
}


def report_media(
    client: TestClient, headers: dict[str, str], version_id: int, body: dict[str, Any]
) -> Response:
    return client.put(f"/api/versions/{version_id}/media-info", json=body, headers=headers)


def test_media_info_reported_by_player_shows_on_versions(
    client: TestClient, catalog: dict[str, int]
) -> None:
    admin = fresh_admin(client)
    big, small = movie(client)["versions"]
    assert big["media"] is None

    assert report_media(client, admin, big["id"], MEDIA_INFO).status_code == 204
    big, small = movie(client)["versions"]
    assert big["media"] == {
        **MEDIA_INFO,
        "audio": [
            MEDIA_INFO["audio"][0],
            {"language": "eng", "title": None, "codec": "truehd", "channels": 8, "default": False},
        ],
        "subtitles": [
            {**MEDIA_INFO["subtitles"][0], "forced": False},
            {
                "language": "eng",
                "title": None,
                "codec": "hdmv_pgs_subtitle",
                "forced": True,
                "default": False,
            },
        ],
    }
    # 只挂在上报的那个版本上
    assert small["media"] is None

    # 再次上报覆盖；什么都没读到也能报
    assert report_media(client, admin, big["id"], {}).status_code == 204
    assert movie(client)["versions"][0]["media"] == {
        "duration": None,
        "width": None,
        "height": None,
        "video_codec": None,
        "hdr": None,
        "audio": [],
        "subtitles": [],
    }

    # 单集的版本在季详情里带出来
    episode_version = episodes(client)["S01E01"]["versions"][0]
    assert report_media(client, admin, episode_version["id"], MEDIA_INFO).status_code == 204
    assert episodes(client)["S01E01"]["versions"][0]["media"]["width"] == 3840


def test_media_info_is_dropped_after_file_changes(
    client: TestClient, catalog: dict[str, int]
) -> None:
    admin = fresh_admin(client)
    version = movie(client)["versions"][0]
    report_media(client, admin, version["id"], MEDIA_INFO)
    with client.app.state.container.database.session() as connection:  # type: ignore[attr-defined]
        connection.execute(
            "UPDATE media_file SET size = size + 1 "
            "WHERE id = (SELECT media_file_id FROM version WHERE id = ?)",
            (version["id"],),
        )
    assert movie(client)["versions"][0]["media"] is None

    # 下一次播放重新上报后又有了
    report_media(client, admin, version["id"], MEDIA_INFO)
    assert movie(client)["versions"][0]["media"]["video_codec"] == "hevc"


def test_media_info_is_validated_and_scoped(client: TestClient, catalog: dict[str, int]) -> None:
    version = episodes(client)["S01E01"]["versions"][0]
    _, login_alice = alice_with(client, [catalog["movies"]])
    alice = login_alice()
    assert report_media(client, alice, version["id"], MEDIA_INFO).status_code == 404
    admin = fresh_admin(client)
    assert report_media(client, admin, 999999, MEDIA_INFO).status_code == 404
    bad_bodies: list[dict[str, Any]] = [
        {"hdr": "hdr12"},
        {"width": 0},
        {"duration": -5},
        {"audio": [{"channels": 0}]},
        {"audio": [{"language": "x" * 17}]},
        {"subtitles": [{}] * 129},
    ]
    for bad in bad_bodies:
        assert report_media(client, admin, version["id"], bad).status_code == 422, bad
    assert client.put(f"/api/versions/{version['id']}/media-info", json={}).status_code == 401


def test_plugin_local_path_must_stay_inside_media_dir(
    client: TestClient, catalog: dict[str, int], cloud115: FakeCloud115, config: Any
) -> None:
    ticket = issue(client, fresh_admin(client), item_id=find(client, "流浪地球")["id"])
    outside = config.data_dir / "outside.mkv"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_bytes(b"video")

    cloud115.control.call("source", "set_resolve_path", {"path": str(outside)})
    response = jump(client, ticket["url"])
    assert response.status_code == 404
    assert "文件不存在或已失效" in response.text

    inside = config.media_dir / "movie.mkv"
    inside.parent.mkdir(parents=True, exist_ok=True)
    inside.write_bytes(b"video")
    cloud115.control.call("source", "set_resolve_path", {"path": str(inside)})
    served = jump(client, ticket["url"])
    assert served.status_code == 200
    assert served.content == b"video"
