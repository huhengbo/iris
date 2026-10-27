"""115 资源来源：浏览、扫描、读取、播放解析、删除、限速和熔断分组。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from iris_plugin_sdk import ResourceNotFound, ResourceRateLimited, ResourceUnavailable

from iris_source_115.errors import (
    Cloud115Error,
    Cloud115Unavailable,
    CredentialInvalid,
    DirNotFound,
    RateLimited,
)
from iris_source_115.gateway import DirListing
from iris_source_115.provider import (
    EXPORT_DIR_NAME,
    PLAYBACK_COPY_DIR,
    Cloud115ResourceProvider,
)
from iris_source_115.scheduler import ApiGroup
from iris_source_115.throttle import MAX_FETCHES_PER_HOUR
from tests.conftest import Stack, make_stack
from tests.fake_gateway import QR_COOKIE, VALID_COOKIE, FakeCloud115


def authenticated(stack: Stack) -> None:
    stack.credentials.save(VALID_COOKIE, "alipaymini", "家庭影院")


def movie_tree(gateway: FakeCloud115) -> str:
    return gateway.make_path("影视/电影/Film (2024)/Film.mkv")


def scan_root(stack: Stack, movie: str) -> object:
    return stack.provider.scan(None)


def browse_root(stack: Stack, movie: str) -> object:
    return stack.provider.open(1, {}).browse(None)


def resolve_movie(stack: Stack, movie: str) -> object:
    return stack.provider.open(1, {}).resolve(f"pc{movie}", "Infuse/1.0")


# 资源接口通往宿主的三条出口：provider._call、source._call、resolve
HOST_EXITS: tuple[Callable[[Stack, str], object], ...] = (scan_root, browse_root, resolve_movie)


def test_source_browses_with_ancestors_and_hides_its_scratch_dir(tmp_path: Path) -> None:
    stack = make_stack(tmp_path)
    authenticated(stack)
    movie = movie_tree(stack.gateway)
    stack.gateway.make_path(f"{PLAYBACK_COPY_DIR}/旧副本.mkv")
    source = stack.provider.open(1, {})

    root = source.browse(None)
    assert root.locator == "0"
    assert root.selectable is False
    assert root.path == "/"
    assert root.ancestors == ()
    assert "光圈临时目录" not in [entry.relative_path for entry in root.entries]
    assert "影视" in [entry.relative_path for entry in root.entries]

    movie_dir = stack.gateway.nodes[movie].parent
    assert movie_dir is not None
    listing = source.browse(movie_dir)
    assert listing.path == "/影视/电影/Film (2024)"
    media = stack.gateway.child("0", "影视")
    assert media is not None
    films = stack.gateway.child(media, "电影")
    assert films is not None
    assert listing.ancestors == (
        (media, "影视"),
        (films, "电影"),
        (movie_dir, "Film (2024)"),
    )
    assert listing.ancestors[-1] == (movie_dir, "Film (2024)")
    # 目录的定位符是 cid，文件的定位符是 pickcode
    entry = next(item for item in listing.entries if item.relative_path == "Film.mkv")
    assert entry.locator == f"pc{movie}"
    assert entry.playback_locator == f"pc{movie}"
    assert entry.identity == movie
    assert entry.key == movie
    assert not entry.is_dir


def test_browse_omits_non_media_files_but_keeps_directories(tmp_path: Path) -> None:
    stack = make_stack(tmp_path)
    authenticated(stack)
    folder = stack.gateway.make_path("影视")
    stack.gateway.add_file(folder, "Film.mkv")
    stack.gateway.add_file(folder, "Film.srt")
    stack.gateway.add_file(folder, "海报说明.txt")
    stack.gateway.add_dir(folder, "花絮")

    listing = stack.provider.open(1, {}).browse(folder)

    assert {entry.relative_path: entry.kind for entry in listing.entries} == {
        "Film.mkv": "video",
        "Film.srt": "subtitle",
        "花絮": "video",
    }


def test_root_and_scratch_directories_are_not_selectable(tmp_path: Path) -> None:
    stack = make_stack(tmp_path)
    authenticated(stack)
    scratch = stack.gateway.make_path("光圈临时目录")
    copies = stack.gateway.make_path(PLAYBACK_COPY_DIR)
    media = stack.gateway.make_path("影视")
    source = stack.provider.open(1, {})

    # 根目录照常返回列表，只是不能作为资料库目录；根列表仍隐藏专用目录
    root = source.browse(None)
    assert root.selectable is False
    assert "光圈临时目录" not in [entry.relative_path for entry in root.entries]
    assert source.browse("0").selectable is False
    scratch_listing = source.browse(scratch)
    assert scratch_listing.selectable is False
    assert [entry.relative_path for entry in scratch_listing.entries] == ["多端播放"]
    assert source.browse(copies).selectable is False
    assert source.browse(media).selectable is True
    assert source.scan("0") == []
    with pytest.raises(ResourceNotFound):
        source.browse("not-a-cid")
    with pytest.raises(ResourceNotFound):
        source.browse("999999")


def test_scan_is_complete_and_skips_own_scratch_dirs(tmp_path: Path) -> None:
    stack = make_stack(tmp_path)
    authenticated(stack)
    movie = movie_tree(stack.gateway)
    movie_dir = stack.gateway.nodes[movie].parent
    assert movie_dir is not None
    stack.gateway.add_file(movie_dir, "Film.srt", content=b"subtitle")
    stack.gateway.add_file("0", "删除我.mkv", content=b"temporary")
    stack.gateway.make_path(f"{PLAYBACK_COPY_DIR}/旧副本.mkv")

    entries = stack.provider.open(1, {}).scan(None)

    assert {entry.relative_path for entry in entries} == {
        "影视/电影/Film (2024)/Film.mkv",
        "影视/电影/Film (2024)/Film.srt",
        "删除我.mkv",
    }
    video = next(entry for entry in entries if entry.relative_path.endswith(".mkv"))
    assert video.locator == f"pc{movie}"
    assert video.sha1 is not None
    assert video.root_path == "/"
    assert all(not entry.relative_path.startswith(EXPORT_DIR_NAME) for entry in entries)


def test_scan_a_library_subdirectory_is_relative_to_that_directory(tmp_path: Path) -> None:
    stack = make_stack(tmp_path)
    authenticated(stack)
    movie_tree(stack.gateway)
    films = stack.gateway.make_path("影视/电影")
    stack.gateway.make_path("影视/电影/花絮/花絮.mkv")

    entries = stack.provider.open(1, {}).scan(films)

    assert {entry.relative_path for entry in entries} == {
        "Film (2024)/Film.mkv",
        "花絮/花絮.mkv",
    }
    assert {entry.root_path for entry in entries} == {"/影视/电影"}


def test_clean_exports_failure_is_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    stack = make_stack(tmp_path)
    authenticated(stack)
    export_dir = stack.provider.prepare_export_dir()
    stack.gateway.broken_dirs.add(export_dir)

    with caplog.at_level(logging.WARNING, logger="iris_source_115.provider"):
        stack.provider.clean_exports(export_dir)

    assert "failed to clean 115 export dir" in caplog.text


def test_scan_reuses_known_entries_without_listing_media_dirs(tmp_path: Path) -> None:
    stack = make_stack(tmp_path)
    authenticated(stack)
    movie_tree(stack.gateway)
    source = stack.provider.open(1, {})
    first = {entry.relative_path: entry for entry in source.scan(None)}
    stack.gateway.calls.clear()

    second = source.scan(None, known=first)

    assert {entry.relative_path: entry.locator for entry in second} == {
        path: entry.locator for path, entry in first.items()
    }
    # 只列了导出目录（清理残留），没有为了已知路径去列媒体目录
    assert stack.gateway.calls.count("list_dir") == 1
    assert "dir_path" in stack.gateway.calls


def test_scan_cleans_leftover_export_files(tmp_path: Path) -> None:
    stack = make_stack(tmp_path)
    authenticated(stack)
    movie_tree(stack.gateway)
    leftover = stack.gateway.make_path(f"{EXPORT_DIR_NAME}/上次_目录树.txt")

    stack.provider.open(1, {}).scan(None)

    assert leftover not in stack.gateway.nodes
    export_dir = stack.gateway.child("0", EXPORT_DIR_NAME)
    assert export_dir is not None
    assert stack.gateway.files_in(export_dir) == []


def test_export_busy_waits_with_progress_heartbeats(tmp_path: Path) -> None:
    progress: list[tuple[Any, str]] = []

    heartbeats: list[None] = []

    def record(until: Any, reason: str) -> None:
        progress.append((until, reason))

    stack = make_stack(tmp_path, progress=record, heartbeat=lambda: heartbeats.append(None))
    authenticated(stack)
    movie_tree(stack.gateway)
    stack.gateway.export_busy_times = 2

    stack.provider.open(1, {}).scan(None)

    assert [reason for _, reason in progress] == ["source_busy", "source_busy"]
    assert all(until == stack.clock.now() + timedelta(seconds=60) for until, _ in progress)
    # 心跳单独走 heartbeat，不把任务标记为等待
    assert heartbeats


def test_export_busy_without_progress_uses_the_sleeper(tmp_path: Path) -> None:
    stack = make_stack(tmp_path)
    authenticated(stack)
    movie_tree(stack.gateway)
    stack.gateway.export_busy_times = 1

    stack.provider.open(1, {}).scan(None)

    assert stack.sleeper[0] == 60.0


def test_read_respects_the_limit_and_reports_missing_files(tmp_path: Path) -> None:
    stack = make_stack(tmp_path)
    authenticated(stack)
    nfo = stack.gateway.make_path("影视/a.nfo")
    stack.gateway.nodes[nfo].content = b"<movie/>"
    source = stack.provider.open(1, {})
    source.scan(None)

    assert source.read(f"pc{nfo}", 100) == b"<movie/>"
    with pytest.raises(ResourceUnavailable):
        source.read(f"pc{nfo}", 3)
    with pytest.raises(ResourceNotFound):
        source.read("pc-missing", 100)


def test_delete_uses_the_file_id_behind_the_locator(tmp_path: Path) -> None:
    stack = make_stack(tmp_path)
    authenticated(stack)
    victim = stack.gateway.make_path("影视/删除我.mkv")
    source = stack.provider.open(1, {})
    entries = {entry.relative_path: entry for entry in source.scan(None)}

    source.delete(entries["影视/删除我.mkv"].locator)

    assert victim not in stack.gateway.nodes
    with pytest.raises(ResourceNotFound):
        source.read(entries["影视/删除我.mkv"].locator, 100)


def test_resolve_caches_links_per_user_agent_until_expiry(tmp_path: Path) -> None:
    stack = make_stack(tmp_path)
    authenticated(stack)
    movie = movie_tree(stack.gateway)
    source = stack.provider.open(1, {})

    first = source.resolve(f"pc{movie}", "Infuse/1.0")
    assert first.url is not None and first.url.startswith("https://cdn.fake115.test/")
    assert source.resolve(f"pc{movie}", "Infuse/1.0").url == first.url
    assert len(stack.gateway.link_requests) == 1

    other = source.resolve(f"pc{movie}", "VLC/3.0")
    assert other.url != first.url
    assert len(stack.gateway.link_requests) == 2

    # 缓存比直链早 5 分钟过期：假直链 2 小时有效，1 小时 55 分起重新取链
    stack.clock.advance(minutes=114)
    assert source.resolve(f"pc{movie}", "Infuse/1.0").url == first.url
    assert len(stack.gateway.link_requests) == 2
    stack.clock.advance(minutes=1)
    assert source.resolve(f"pc{movie}", "Infuse/1.0").url != first.url
    assert len(stack.gateway.link_requests) == 3


def test_resolve_reports_gone_and_rate_limited_files(tmp_path: Path) -> None:
    stack = make_stack(tmp_path)
    authenticated(stack)
    movie = movie_tree(stack.gateway)
    source = stack.provider.open(1, {})

    stack.gateway.errors_once["direct_link"] = Cloud115Unavailable()
    with pytest.raises(ResourceUnavailable):
        source.resolve(f"pc{movie}", "Infuse/1.0")
    with pytest.raises(ResourceNotFound):
        source.resolve("pc-missing", "Infuse/1.0")
    stack.gateway.errors_once["direct_link"] = RateLimited()
    with pytest.raises(ResourceRateLimited) as error:
        source.resolve(f"pc{movie}", "Infuse/1.0")
    assert error.value.status_code == 503
    assert error.value.retry_after == 30 * 60


def test_rate_limit_without_progress_reaches_the_host_with_its_resume_time(
    tmp_path: Path,
) -> None:
    stack = make_stack(tmp_path)
    authenticated(stack)
    source = stack.provider.open(1, {})
    stack.gateway.errors_once["list_dir"] = RateLimited()

    # browse 没有 progress：风控必须翻译成宿主的限流异常，不能漏成插件内部错误
    with pytest.raises(ResourceRateLimited) as error:
        source.browse(None)

    assert error.value.status_code == 503
    assert error.value.code == "resource_rate_limited"
    assert error.value.retry_after == 30 * 60


@pytest.mark.parametrize(
    "make_error",
    [RateLimited, DirNotFound, CredentialInvalid, Cloud115Unavailable],
    ids=["rate-limited", "dir-not-found", "credential-invalid", "unavailable"],
)
def test_all_host_exits_translate_the_same_internal_error(
    tmp_path: Path, make_error: Callable[[], Cloud115Error]
) -> None:
    signatures: list[tuple[object, ...]] = []
    for index, exit_call in enumerate(HOST_EXITS):
        stack = make_stack(tmp_path / str(index))
        authenticated(stack)
        movie = movie_tree(stack.gateway)
        stack.gateway.error = make_error()

        with pytest.raises((ResourceNotFound, ResourceRateLimited, ResourceUnavailable)) as caught:
            exit_call(stack, movie)
        error = caught.value
        signatures.append(
            (
                type(error),
                str(error),
                getattr(error, "fatal", None),
                getattr(error, "status_code", None),
                getattr(error, "code", None),
                getattr(error, "retry_after", None),
            )
        )

    assert len(set(signatures)) == 1


def test_multiple_playback_disabled_shares_the_link(tmp_path: Path) -> None:
    stack = make_stack(tmp_path)
    authenticated(stack)
    movie = movie_tree(stack.gateway)
    source = stack.provider.open(1, {})

    first = source.resolve(f"pc{movie}", "UA", throttle_key="token-a")
    second = source.resolve(f"pc{movie}", "UA", throttle_key="token-b")

    assert second.url == first.url
    assert "copy_file" not in stack.gateway.calls


def test_multiple_playback_copies_for_a_second_session(
    tmp_path: Path, deferred: list[tuple[float, Any]]
) -> None:
    stack = make_stack(tmp_path)
    authenticated(stack)
    movie = movie_tree(stack.gateway)
    stack.provider.use_same_playback(lambda: True)
    source = stack.provider.open(1, {})

    first = source.resolve(f"pc{movie}", "UA", throttle_key="token-a")
    # 同一会话再请求命中自己的缓存，不复制
    assert source.resolve(f"pc{movie}", "UA", throttle_key="token-a").url == first.url
    assert "copy_file" not in stack.gateway.calls

    second = source.resolve(f"pc{movie}", "UA", throttle_key="token-b")
    assert second.url != first.url
    assert stack.gateway.calls.count("copy_file") == 1
    original, copy = (locator for locator, _ in stack.gateway.link_requests)
    assert copy != original
    assert len(copy_files(stack.gateway)) == 1

    # 第二台设备再请求命中自己的缓存，不再复制
    assert source.resolve(f"pc{movie}", "UA", throttle_key="token-b").url == second.url
    assert stack.gateway.calls.count("copy_file") == 1

    # 取完直链几秒后删掉副本
    assert [delay for delay, _ in deferred] == [5.0]
    deferred.pop()[1]()
    assert copy_files(stack.gateway) == []


def test_copy_link_retries_until_the_copy_is_ready(
    tmp_path: Path, deferred: list[tuple[float, Any]]
) -> None:
    stack = make_stack(tmp_path)
    authenticated(stack)
    movie = movie_tree(stack.gateway)
    stack.provider.use_same_playback(lambda: True)
    source = stack.provider.open(1, {})
    source.resolve(f"pc{movie}", "UA", throttle_key="token-a")
    stack.gateway.copy_not_ready_times = 2
    stack.sleeper.clear()

    source.resolve(f"pc{movie}", "UA", throttle_key="token-b")

    assert stack.sleeper == [0.5, 1.0]
    assert stack.gateway.calls.count("copy_file") == 1


def test_failed_copy_falls_back_to_the_original_link(
    tmp_path: Path, deferred: list[tuple[float, Any]]
) -> None:
    stack = make_stack(tmp_path)
    authenticated(stack)
    movie = movie_tree(stack.gateway)
    stack.provider.use_same_playback(lambda: True)
    source = stack.provider.open(1, {})
    source.resolve(f"pc{movie}", "UA", throttle_key="token-a")
    stack.gateway.link_requests.clear()
    stack.gateway.errors_once["copy_file"] = Cloud115Unavailable()

    second = source.resolve(f"pc{movie}", "UA", throttle_key="token-b")

    assert second.url is not None
    assert copy_files(stack.gateway) == []
    ((locator, _),) = stack.gateway.link_requests
    assert locator == f"pc{movie}"


def test_leftover_copies_are_cleaned_instead_of_guessed(
    tmp_path: Path, deferred: list[tuple[float, Any]]
) -> None:
    stack = make_stack(tmp_path)
    authenticated(stack)
    movie = movie_tree(stack.gateway)
    stack.provider.use_same_playback(lambda: True)
    source = stack.provider.open(1, {})
    source.resolve(f"pc{movie}", "UA", throttle_key="token-a")
    stack.gateway.make_path(f"{PLAYBACK_COPY_DIR}/旧副本.mkv")

    source.resolve(f"pc{movie}", "UA", throttle_key="token-b")

    # 认不准哪个是新副本：两个都删掉，这次用原文件
    assert len(deferred) == 2
    for _, action in deferred:
        action()
    assert copy_files(stack.gateway) == []


def test_link_fetches_are_throttled_per_token(tmp_path: Path) -> None:
    stack = make_stack(tmp_path)
    authenticated(stack)
    movie = movie_tree(stack.gateway)
    source = stack.provider.open(1, {})
    stack.gateway.link_requests.clear()

    for index in range(MAX_FETCHES_PER_HOUR):
        source.resolve(f"pc{movie}", f"UA-{index}", throttle_key="token-a")

    with pytest.raises(ResourceRateLimited) as error:
        source.resolve(f"pc{movie}", "UA-final", throttle_key="token-a")
    assert error.value.retry_after is not None and error.value.retry_after > 0
    assert len(stack.gateway.link_requests) == MAX_FETCHES_PER_HOUR


def test_rate_limited_resolve_fails_fast_without_calling_115_again(tmp_path: Path) -> None:
    stack = make_stack(tmp_path)
    authenticated(stack)
    movie = movie_tree(stack.gateway)
    source = stack.provider.open(1, {})
    stack.gateway.errors_once["direct_link"] = RateLimited()

    with pytest.raises(ResourceRateLimited):
        source.resolve(f"pc{movie}", "UA")
    calls = len(stack.gateway.calls)

    # 熔断期间立即失败，不再向 115 取链
    with pytest.raises(ResourceRateLimited):
        source.resolve(f"pc{movie}", "UA")
    assert len(stack.gateway.calls) == calls


def test_invalid_credential_resolve_is_unavailable(tmp_path: Path) -> None:
    stack = make_stack(tmp_path)
    authenticated(stack)
    movie = movie_tree(stack.gateway)
    source = stack.provider.open(1, {})
    stack.gateway.valid_cookies.discard(VALID_COOKIE)

    with pytest.raises(ResourceUnavailable) as error:
        source.resolve(f"pc{movie}", "UA")
    assert error.value.fatal is True
    assert error.value.status_code == 409
    assert error.value.code == "resource_credential_invalid"
    credential = stack.credentials.meta()
    assert credential is not None
    assert credential.invalid_at is not None


def test_stale_request_failure_does_not_invalidate_the_new_cookie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = make_stack(tmp_path)
    authenticated(stack)
    source = stack.provider.open(1, {})

    def list_dir_after_cookie_change(cookie: str, cid: str) -> DirListing:
        # 旧 Cookie 的请求还在路上，管理员已经通过插件页换了新 Cookie
        stack.service.save_cookie(QR_COOKIE)
        raise CredentialInvalid()

    monkeypatch.setattr(stack.gateway, "list_dir", list_dir_after_cookie_change)

    with pytest.raises(ResourceUnavailable) as error:
        source.browse(None)
    assert error.value.fatal is True

    # 失败的是旧 Cookie，不能把刚保存的新 Cookie 标成失效
    stored = stack.credentials.cookie()
    assert stored is not None
    assert stored[0] == QR_COOKIE
    assert stored[1].invalid_at is None
    assert stack.provider.status({}).state == "ok"


def test_web_breaker_does_not_block_playback(tmp_path: Path) -> None:
    stack = make_stack(tmp_path)
    authenticated(stack)
    movie = movie_tree(stack.gateway)
    source = stack.provider.open(1, {})
    stack.service.scheduler.breakers[ApiGroup.WEB].trip()

    # 同步那组接口被拦，取直链走 App 版接口，照常播放
    assert source.resolve(f"pc{movie}", "UA").url is not None
    with pytest.raises(ResourceRateLimited):
        source.browse(None)


def test_app_breaker_does_not_block_the_web_api(tmp_path: Path) -> None:
    stack = make_stack(tmp_path)
    authenticated(stack)
    movie = movie_tree(stack.gateway)
    source = stack.provider.open(1, {})

    assert source.browse(None).path == "/"
    stack.gateway.errors_once["direct_link"] = RateLimited()
    with pytest.raises(ResourceRateLimited):
        source.resolve(f"pc{movie}", "UA")

    # 网页版接口不受影响
    assert source.browse(None).path == "/"
    status = stack.provider.status({})
    assert status.state == "limited"
    assert status.resume_at is not None


def test_status_reports_missing_invalid_limited_and_ok(tmp_path: Path) -> None:
    stack = make_stack(tmp_path)
    provider = stack.provider

    missing = provider.status({})
    assert missing.state == "failed"
    assert missing.page == "account"

    authenticated(stack)
    assert provider.status({}).state == "ok"

    stack.credentials.mark_invalid(VALID_COOKIE)
    invalid = provider.status({})
    assert invalid.state == "failed"
    assert invalid.page == "account"
    assert invalid.message is not None and "登录已失效" in invalid.message

    with pytest.raises(ResourceUnavailable) as error:
        provider.check({})
    assert error.value.fatal is True
    assert error.value.code == "resource_credential_invalid"

    stack.credentials.save(VALID_COOKIE, "alipaymini", "家庭影院")
    resume_at = stack.service.scheduler.breakers[ApiGroup.APP].trip()
    limited = provider.status({})
    assert limited.state == "limited"
    assert limited.page == "account"
    assert limited.resume_at == resume_at.isoformat()


def test_check_without_credential_is_fatal(tmp_path: Path) -> None:
    stack = make_stack(tmp_path)

    with pytest.raises(ResourceUnavailable) as error:
        stack.provider.check({})
    assert error.value.fatal is True
    assert error.value.status_code == 409
    assert error.value.code == "resource_not_configured"


def test_provider_capabilities_are_declared(tmp_path: Path) -> None:
    stack = make_stack(tmp_path)
    provider: Cloud115ResourceProvider = stack.provider

    assert provider.id == "source.115"
    assert provider.multiple is False
    assert provider.capabilities == {"status", "changes", "delete", "known_scan"}
    assert provider.config_fields == ()
    assert provider.validate_config({}) == {}


def copy_files(gateway: FakeCloud115) -> list[str]:
    folder = gateway.child("0", EXPORT_DIR_NAME)
    playback = gateway.child(folder, "多端播放") if folder else None
    return gateway.files_in(playback) if playback else []


def test_export_busy_for_too_long_fails_the_folder(tmp_path: Path) -> None:
    def advance(until: Any, reason: str) -> None:
        if until is not None:
            stack.clock.current = until

    stack = make_stack(tmp_path, progress=advance)
    authenticated(stack)
    stack.gateway.export_busy_times = 1000

    with pytest.raises(ResourceUnavailable, match="其他导出任务 30 分钟内没有结束"):
        stack.provider.open(1, {}).scan(None)


def test_slow_export_is_polled_until_done(tmp_path: Path) -> None:
    heartbeats: list[None] = []
    stack = make_stack(tmp_path, heartbeat=lambda: heartbeats.append(None))
    authenticated(stack)
    film = stack.gateway.make_path("影视/电影/Film (2024)")
    stack.gateway.add_file(film, "Film.mkv")
    stack.gateway.export_polls = 3

    entries = stack.provider.open(1, {}).scan(None)

    assert [entry.relative_path for entry in entries] == ["影视/电影/Film (2024)/Film.mkv"]
    assert len(heartbeats) >= 3
    assert stack.sleeper


def test_scan_waits_out_an_open_breaker(tmp_path: Path) -> None:
    progress: list[tuple[Any, str]] = []

    def advance(until: Any, reason: str) -> None:
        progress.append((until, reason))
        if until is not None:
            stack.clock.current = until

    stack = make_stack(tmp_path, progress=advance)
    authenticated(stack)
    film = stack.gateway.make_path("影视/电影/Film (2024)")
    stack.gateway.add_file(film, "Film.mkv")
    stack.service.scheduler.breakers[ApiGroup.WEB].trip()

    entries = stack.provider.open(1, {}).scan(None)

    assert entries
    assert progress[0][1] == "rate_limited"
