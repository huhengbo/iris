"""自动下载字幕：资料库下载器顺序、语言判断、退避、预算、取消和计划任务调度。"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from iris_plugin_sdk import (
    MediaContext,
    SubtitleCandidate,
    SubtitleUnavailable,
)

from app.container import AppContainer
from app.ops import maintenance
from app.ops.maintenance import run_due
from app.ops.tasks import TaskRepository, TrackedTask
from app.plugins import library_plugins
from app.plugins.library_plugins import Entry
from app.subtitles import auto
from app.subtitles.auto import SubtitleAutoDownloader
from app.subtitles.languages import covers, track_language
from tests.conftest import FakeClock
from tests.fake_cloud115 import VIDEO_SIZE, FakeCloud115
from tests.fake_tmdb import FakeTmdb
from tests.test_browse import build_catalog
from tests.test_sync import create_library, fresh_admin, sync

AUTO_LIBRARY = "自动字幕"


def _srt(text: str) -> str:
    return f"1\n00:00:01,000 --> 00:00:02,000\n{text}\n"


def candidate(
    plugin_id: str,
    remote_id: str,
    *,
    language: str = "zh-Hans",
    score: float | None = None,
    downloads: int | None = None,
) -> SubtitleCandidate:
    return SubtitleCandidate(
        provider_id=plugin_id,
        remote_id=remote_id,
        label=remote_id,
        language=language,
        format="srt",
        score=score,
        downloads=downloads,
    )


class SearchLog:
    def __init__(self, control: Any) -> None:
        self._control = control

    def _read(self) -> list[str | None]:
        return list(self._control.call("subtitle", "search_languages"))

    def __iter__(self) -> Any:
        return iter(self._read())

    def __len__(self) -> int:
        return len(self._read())

    def __getitem__(self, index: int) -> str | None:
        return self._read()[index]

    def __eq__(self, other: object) -> bool:
        return self._read() == list(other) if isinstance(other, list) else NotImplemented

    def __contains__(self, value: object) -> bool:
        return value in self._read()

    def __repr__(self) -> str:
        return repr(self._read())

    def clear(self) -> None:
        self._control.call("subtitle", "clear_calls")


class FakeDownloader:
    """按语言返回候选的字幕下载器控制；状态在插件子进程里。"""

    def __init__(self, plugin_id: str, results: dict[str, list[SubtitleCandidate]] | None = None):
        self.id = plugin_id
        self._results = results or {}
        self._control: Any = None

    def attach(self, control: Any) -> None:
        self._control = control
        self._control.call(
            "subtitle",
            "set_results",
            {"results": {key: list(value) for key, value in self._results.items()}},
        )

    @property
    def searches(self) -> SearchLog:
        return SearchLog(self._control)

    @property
    def media(self) -> list[MediaContext]:
        return list(self._control.call("subtitle", "media_requests"))

    @property
    def downloads(self) -> list[SubtitleCandidate]:
        return list(self._control.call("subtitle", "download_candidates"))

    @property
    def unavailable(self) -> str | None:
        return None

    @unavailable.setter
    def unavailable(self, value: str | None) -> None:
        self._control.call("subtitle", "set_reason", {"reason": value})

    @property
    def search_error(self) -> Exception | None:
        return None

    @search_error.setter
    def search_error(self, value: Exception | None) -> None:
        if value is None:
            self._control.call("subtitle", "set_error", {"error": None})
            return
        if isinstance(value, SubtitleUnavailable):
            self._control.call(
                "subtitle",
                "set_error",
                {
                    "error": "unavailable",
                    "message": value.message,
                    "retry_after": value.retry_after,
                },
            )
            return
        self._control.call("subtitle", "set_error", {"error": "failed", "message": str(value)})


def container(client: TestClient) -> AppContainer:
    return cast(AppContainer, cast(FastAPI, client.app).state.container)


def install(client: TestClient, provider: FakeDownloader) -> FakeDownloader:
    from tests.conftest import fake_control

    app_container = container(client)
    app_container.plugins.set_enabled(provider.id, True)
    control = fake_control(app_container.config, provider.id)
    provider.attach(control)
    return provider


@pytest.fixture
def _subtitle_plugins(plugin_env: Any) -> None:
    from tests.plugin_env import FakePluginSpec, install_fake_plugin

    for plugin_id in ("test.auto.a", "test.auto.b", "test.auto.c"):
        install_fake_plugin(
            plugin_env.root,
            FakePluginSpec(
                plugin_id,
                f"假下载器 {plugin_id}",
                ("subtitle.search", "subtitle.download"),
            ),
        )


@pytest.fixture
def client(
    _subtitle_plugins: None,
    config: Any,
    clock: FakeClock,
    sync_runner: Any,
    plugin_env: Any,
) -> Any:
    from tests.conftest import make_app
    from tests.plugin_env import bind_controls

    with TestClient(make_app(config, clock, sync_runner)) as test_client:
        bind_controls(plugin_env.root, test_client)
        yield test_client


@pytest.fixture(autouse=True)
def _hide_unused_downloaders(client: TestClient) -> None:
    for plugin_id in ("test.auto.a", "test.auto.b", "test.auto.c"):
        container(client).plugins.set_enabled(plugin_id, False)


@pytest.fixture
def catalog(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115, tmdb: FakeTmdb
) -> dict[str, int]:
    return build_catalog(client, admin, cloud115, tmdb)


@pytest.fixture
def library(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115, catalog: dict[str, int]
) -> int:
    """单独的资料库：一部电影两个版本，1080p 带一条简体中文外挂字幕。"""
    root = f"影视/{AUTO_LIBRARY}"
    for path in (
        "流浪地球 (2019)/流浪地球.2019.1080p.BluRay.mkv",
        "流浪地球 (2019)/流浪地球.2019.1080p.BluRay.chs.srt",
    ):
        cloud115.make_path(f"{root}/{path}")
    cloud115.add_file(
        cloud115.make_path(f"{root}/流浪地球 (2019)"),
        "流浪地球.2019.2160p.WEB-DL.mkv",
        size=VIDEO_SIZE * 3,
    )
    created = create_library(client, admin, AUTO_LIBRARY, "movie", cloud115.make_path(root))
    assert sync(client, admin)["status"] == "succeeded"
    library_id: int = created["id"]
    return library_id


def versions(app_container: AppContainer, library_id: int) -> dict[str, int]:
    """资料库里的两个版本：subtitled 带外挂字幕，clean 没有。"""
    with app_container.database.read_session() as connection:
        rows = connection.execute(
            """
            SELECT v.id, c.rel_path
            FROM version v
            JOIN playable p ON p.id = v.playable_id
            JOIN media_item m ON m.id = p.media_item_id
            JOIN media_file c ON c.id = v.media_file_id
            WHERE m.library_id = ? ORDER BY v.id
            """,
            (library_id,),
        ).fetchall()
    found: dict[str, int] = {}
    for row in rows:
        key = "clean" if "2160p" in str(row["rel_path"]) else "subtitled"
        found[key] = int(row["id"])
    assert set(found) == {"clean", "subtitled"}
    return found


def set_languages(
    app_container: AppContainer,
    library_id: int,
    languages: list[str],
    *,
    skip_if_audio: bool = False,
) -> None:
    with app_container.database.session() as connection:
        for key, value in (
            ("subtitle_download_languages", json.dumps(languages, ensure_ascii=False)),
            ("subtitle_skip_if_audio", "true" if skip_if_audio else "false"),
        ):
            connection.execute(
                "INSERT INTO library_setting (library_id, key, value, updated_at) "
                "VALUES (?, ?, ?, 'now') ON CONFLICT (library_id, key) DO UPDATE SET "
                "value = excluded.value, updated_at = excluded.updated_at",
                (library_id, key, value),
            )


def set_downloaders(app_container: AppContainer, library_id: int, ids: list[str]) -> None:
    with app_container.database.session() as connection:
        library_plugins.replace(
            connection,
            library_id,
            "subtitle",
            [Entry(plugin_id, True) for plugin_id in ids],
            "now",
        )


def save_media_info(
    app_container: AppContainer,
    version_id: int,
    *,
    subtitles: list[dict[str, object]] | None = None,
    audio: list[dict[str, object]] | None = None,
    stale: bool = False,
) -> None:
    with app_container.database.session() as connection:
        row = connection.execute(
            "SELECT c.id, c.size, c.sha1 FROM version v "
            "JOIN media_file c ON c.id = v.media_file_id WHERE v.id = ?",
            (version_id,),
        ).fetchone()
        assert row is not None
        size = int(row["size"] or 0) + (1 if stale else 0)
        connection.execute(
            "INSERT INTO media_info (media_file_id, size, sha1, audio, subtitles, reported_at) "
            "VALUES (?, ?, ?, ?, ?, 'now') ON CONFLICT (media_file_id) DO UPDATE SET "
            "size = excluded.size, sha1 = excluded.sha1, audio = excluded.audio, "
            "subtitles = excluded.subtitles, reported_at = excluded.reported_at",
            (
                row["id"],
                size,
                row["sha1"],
                json.dumps(audio or []),
                json.dumps(subtitles or []),
            ),
        )


def run_auto(
    app_container: AppContainer, clock: FakeClock, cancel: Callable[[], bool] | None = None
) -> dict[str, int]:
    with app_container.database.session() as connection:
        task = TaskRepository(connection, clock).create("test:subtitle_auto")
    handle = TrackedTask(task.id, app_container.database, clock)
    return app_container.subtitle_auto.run(handle, cancel or (lambda: False))


def online_subtitles(app_container: AppContainer, version_id: int) -> list[sqlite3.Row]:
    with app_container.database.read_session() as connection:
        return connection.execute(
            "SELECT s.language, o.provider_id, o.remote_id, o.origin, o.created_by "
            "FROM online_subtitle o JOIN subtitle s ON s.id = o.subtitle_id "
            "WHERE s.version_id = ?",
            (version_id,),
        ).fetchall()


def auto_searches(app_container: AppContainer, version_id: int) -> dict[str, str]:
    with app_container.database.read_session() as connection:
        return {
            str(row["language"]): str(row["outcome"])
            for row in connection.execute(
                "SELECT language, outcome FROM subtitle_auto_search WHERE version_id = ?",
                (version_id,),
            ).fetchall()
        }


# ---- 语言 ----


def test_track_language_normalizes_iso_codes_and_titles() -> None:
    assert track_language("chi", None) == "zh"
    assert track_language("zho", None) == "zh"
    assert track_language("eng", None) == "en"
    assert track_language("jpn", None) == "ja"
    assert track_language("kor", None) == "ko"
    assert track_language("chs", None) == "zh-Hans"
    assert track_language("cht", None) == "zh-Hant"
    assert track_language("zh-Hans", None) == "zh-Hans"
    assert track_language("zh-CN", None) == "zh-Hans"
    assert track_language(None, "简体中文") == "zh-Hans"
    assert track_language(None, "繁體中文") == "zh-Hant"
    assert track_language(None, "English SDH") == "en"
    assert track_language(None, "日语") == "ja"
    assert track_language(None, None) is None


def test_covers_broad_chinese_but_not_cross_variant() -> None:
    assert covers("zh", "zh-Hans")
    assert covers("zh", "zh-Hant")
    assert covers("zh-Hans", "zh")
    assert not covers("zh-Hans", "zh-Hant")
    assert not covers("zh-Hant", "zh-Hans")
    assert covers("en", "en")
    assert not covers("en", "ja")


# ---- 资料库下载器列表 ----


def test_subtitle_downloaders_follow_library_list(
    client: TestClient, library: int, catalog: dict[str, int]
) -> None:
    install(client, FakeDownloader("test.auto.a"))
    install(client, FakeDownloader("test.auto.b"))
    app_container = container(client)
    # 没保存过列表时默认启用全部下载器，按 id 排序
    assert [item[0] for item in app_container.plugins.subtitle_downloaders()] == [
        "builtin.assrt",
        "test.auto.a",
        "test.auto.b",
    ]
    assert [item[0] for item in app_container.plugins.subtitle_downloaders(library)] == [
        "builtin.assrt",
        "test.auto.a",
        "test.auto.b",
    ]

    set_downloaders(app_container, library, ["test.auto.b", "test.auto.a"])
    assert [item[0] for item in app_container.plugins.subtitle_downloaders(library)] == [
        "test.auto.b",
        "test.auto.a",
    ]


def test_providers_and_manual_search_use_library_order(
    client: TestClient, library: int, catalog: dict[str, int]
) -> None:
    install(client, FakeDownloader("test.auto.a", {"zh-Hans": [candidate("test.auto.a", "a")]}))
    install(client, FakeDownloader("test.auto.b", {"zh-Hans": [candidate("test.auto.b", "b")]}))
    app_container = container(client)
    set_downloaders(app_container, library, ["test.auto.b", "test.auto.a"])
    version_id = versions(app_container, library)["clean"]
    admin = fresh_admin(client)

    filtered = client.get(
        "/api/subtitles/providers", params={"version_id": version_id}, headers=admin
    )
    assert filtered.status_code == 200, filtered.text
    assert [item["id"] for item in filtered.json()["providers"]] == ["test.auto.b", "test.auto.a"]

    everything = client.get("/api/subtitles/providers", headers=admin)
    assert [item["id"] for item in everything.json()["providers"]] == [
        "builtin.assrt",
        "test.auto.a",
        "test.auto.b",
    ]

    missing = client.get("/api/subtitles/providers", params={"version_id": 999_999}, headers=admin)
    assert missing.status_code == 404

    found = client.get(
        f"/api/versions/{version_id}/subtitles/search",
        headers=admin,
        params={"language": "zh-Hans"},
    )
    assert found.status_code == 200, found.text
    assert [item["id"] for item in found.json()["providers"]] == ["test.auto.b", "test.auto.a"]
    assert [item["remote_id"] for item in found.json()["results"]] == ["b", "a"]

    # 资料库没有勾选任何下载器时手动搜索报错
    with app_container.database.session() as connection:
        library_plugins.replace(
            connection,
            library,
            "subtitle",
            [Entry("test.auto.a", False), Entry("test.auto.b", False)],
            "now",
        )
    blocked = client.get(f"/api/versions/{version_id}/subtitles/search", headers=admin)
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "subtitle_provider_unavailable"


# ---- 自动下载 ----


def test_auto_skips_libraries_without_languages_or_downloaders(
    client: TestClient, library: int, clock: FakeClock
) -> None:
    downloader = install(
        client, FakeDownloader("test.auto.a", {"zh-Hans": [candidate("test.auto.a", "one.srt")]})
    )
    app_container = container(client)
    # 没有配置语言时什么也不做
    counters = run_auto(app_container, clock)
    assert counters["libraries"] == 0
    assert downloader.searches == []

    set_languages(app_container, library, ["zh-Hans"])
    with app_container.database.session() as connection:
        library_plugins.replace(
            connection, library, "subtitle", [Entry("test.auto.a", False)], "now"
        )
    counters = run_auto(app_container, clock)
    assert counters["libraries"] == 0
    assert downloader.searches == []


def test_auto_skips_external_subtitles_and_runs_once_per_language(
    client: TestClient, library: int, clock: FakeClock
) -> None:
    downloader = install(
        client, FakeDownloader("test.auto.a", {"zh-Hans": [candidate("test.auto.a", "one.srt")]})
    )
    app_container = container(client)
    set_downloaders(app_container, library, ["test.auto.a"])
    set_languages(app_container, library, ["zh-Hans"])
    found = versions(app_container, library)

    counters = run_auto(app_container, clock)

    assert counters["downloaded"] == 1
    # 1080p 版本已有外挂简体中文字幕，不再搜索
    assert downloader.searches == ["zh-Hans"]
    rows = online_subtitles(app_container, found["clean"])
    assert len(rows) == 1
    assert (rows[0]["origin"], rows[0]["created_by"]) == ("auto", None)
    assert online_subtitles(app_container, found["subtitled"]) == []

    # 已经有在线字幕的版本不再重复下载
    again = run_auto(app_container, clock)
    assert again["downloaded"] == 0
    assert downloader.searches == ["zh-Hans"]


def test_auto_skips_embedded_and_audio_languages(
    client: TestClient, library: int, clock: FakeClock
) -> None:
    downloader = install(client, FakeDownloader("test.auto.a", {}))
    app_container = container(client)
    set_downloaders(app_container, library, ["test.auto.a"])
    found = versions(app_container, library)
    save_media_info(
        app_container,
        found["clean"],
        subtitles=[{"language": "chi", "title": "简体中文"}],
        audio=[{"language": "jpn", "title": "日语"}],
    )
    # 内封字幕算已有；音轨默认不算
    set_languages(app_container, library, ["zh-Hans", "ja"])
    counters = run_auto(app_container, clock)
    assert counters["searches"] == 2
    assert downloader.searches == ["ja", "ja"]
    assert "zh-Hans" not in auto_searches(app_container, found["clean"])

    # 打开「音轨是该语言时跳过」后，带日语原声的版本不再搜索日语
    set_languages(app_container, library, ["zh-Hans", "ja"], skip_if_audio=True)
    downloader.searches.clear()
    clock.advance(days=8)  # 越过上一轮「没有结果」的退避
    counters = run_auto(app_container, clock)
    assert counters["searches"] == 1
    assert downloader.searches == ["ja"]
    assert auto_searches(app_container, found["clean"]) == {"ja": "none"}


def test_auto_ignores_stale_media_info(client: TestClient, library: int, clock: FakeClock) -> None:
    downloader = install(
        client, FakeDownloader("test.auto.a", {"zh-Hans": [candidate("test.auto.a", "one.srt")]})
    )
    app_container = container(client)
    set_downloaders(app_container, library, ["test.auto.a"])
    set_languages(app_container, library, ["zh-Hans"])
    found = versions(app_container, library)
    # 文件换过之后 size 对不上，上报过的内封字幕不能算已有
    save_media_info(
        app_container,
        found["clean"],
        subtitles=[{"language": "chi", "title": "简体中文"}],
        stale=True,
    )
    counters = run_auto(app_container, clock)
    assert counters["downloaded"] == 1
    assert downloader.searches == ["zh-Hans"]


def test_auto_takes_highest_score_from_first_downloader_with_results(
    client: TestClient, library: int, clock: FakeClock
) -> None:
    empty = install(client, FakeDownloader("test.auto.a", {}))
    ranked = install(
        client,
        FakeDownloader(
            "test.auto.b",
            {
                "zh-Hans": [
                    candidate("test.auto.b", "no-score.srt"),
                    candidate("test.auto.b", "popular.srt", score=9.0, downloads=5),
                    candidate("test.auto.b", "rarer.srt", score=9.0, downloads=2),
                    candidate("test.auto.b", "english.srt", language="en", score=99.0),
                ]
            },
        ),
    )
    later = install(
        client, FakeDownloader("test.auto.c", {"zh-Hans": [candidate("test.auto.c", "c.srt")]})
    )
    app_container = container(client)
    set_downloaders(app_container, library, ["test.auto.a", "test.auto.b", "test.auto.c"])
    set_languages(app_container, library, ["zh-Hans"])

    counters = run_auto(app_container, clock)

    assert counters["downloaded"] == 1
    assert empty.searches == ["zh-Hans"]
    assert ranked.searches == ["zh-Hans"]
    # 第一个有结果的下载器里取最高分，后面的下载器不再问
    assert later.searches == []
    assert [item.remote_id for item in ranked.downloads] == ["popular.srt"]


def test_auto_records_none_and_failed_backoff(
    client: TestClient, library: int, clock: FakeClock
) -> None:
    downloader = install(client, FakeDownloader("test.auto.a", {}))
    app_container = container(client)
    set_downloaders(app_container, library, ["test.auto.a"])
    set_languages(app_container, library, ["zh-Hans"])
    version_id = versions(app_container, library)["clean"]

    run_auto(app_container, clock)
    assert auto_searches(app_container, version_id) == {"zh-Hans": "none"}
    downloader.searches.clear()
    # 7 天内不再搜同一个空结果
    clock.advance(days=1)
    run_auto(app_container, clock)
    assert downloader.searches == []

    clock.advance(days=7)
    run_auto(app_container, clock)
    assert downloader.searches == ["zh-Hans"]

    # 下载器出错记 failed，1 天后就会重试
    clock.advance(days=8)
    downloader.search_error = RuntimeError("boo")
    run_auto(app_container, clock)
    assert auto_searches(app_container, version_id) == {"zh-Hans": "failed"}
    attempts = len(downloader.searches)
    clock.advance(hours=12)
    run_auto(app_container, clock)
    assert len(downloader.searches) == attempts
    clock.advance(hours=13)
    run_auto(app_container, clock)
    assert len(downloader.searches) == attempts + 1
    assert auto_searches(app_container, version_id) == {"zh-Hans": "failed"}


def test_auto_stops_at_search_budget(
    client: TestClient, library: int, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    downloader = install(client, FakeDownloader("test.auto.a", {}))
    app_container = container(client)
    set_downloaders(app_container, library, ["test.auto.a"])
    set_languages(app_container, library, ["zh-Hans", "ja"])
    monkeypatch.setattr(auto, "MAX_SEARCHES", 2)

    counters = run_auto(app_container, clock)

    assert counters["searches"] == 2
    assert len(downloader.searches) == 2


def test_auto_can_be_cancelled(client: TestClient, library: int, clock: FakeClock) -> None:
    downloader = install(client, FakeDownloader("test.auto.a", {}))
    app_container = container(client)
    set_downloaders(app_container, library, ["test.auto.a"])
    set_languages(app_container, library, ["zh-Hans", "ja"])

    counters = run_auto(app_container, clock, lambda: len(downloader.searches) >= 1)

    assert counters["searches"] == 1
    assert len(downloader.searches) == 1


def test_auto_marks_failed_when_version_hits_online_limit(
    client: TestClient, library: int, clock: FakeClock
) -> None:
    downloader = install(
        client, FakeDownloader("test.auto.a", {"zh-Hans": [candidate("test.auto.a", "one.srt")]})
    )
    app_container = container(client)
    set_downloaders(app_container, library, ["test.auto.a"])
    set_languages(app_container, library, ["zh-Hans"])
    version_id = versions(app_container, library)["clean"]
    with app_container.database.session() as connection:
        for index in range(20):
            cursor = connection.execute(
                "INSERT INTO subtitle (version_id, media_file_id, source, language, label) "
                "VALUES (?, NULL, 'online', 'en', ?)",
                (version_id, f"已有的 {index}"),
            )
            connection.execute(
                "INSERT INTO online_subtitle (subtitle_id, provider_id, remote_id, format, sha256, "
                "size, content, original_encoding, origin, created_at) "
                "VALUES (?, 'test.other', ?, 'srt', ?, 1, X'78', 'utf-8', 'manual', 'now')",
                (cursor.lastrowid, f"other/{index}", f"{index:064d}"),
            )

    counters = run_auto(app_container, clock)

    assert counters["downloaded"] == 0
    assert counters["failed"] == 1
    assert auto_searches(app_container, version_id) == {"zh-Hans": "failed"}
    assert downloader.searches == ["zh-Hans"]


def test_auto_stops_using_a_rate_limited_downloader_for_the_run(
    client: TestClient, library: int, clock: FakeClock
) -> None:
    limited = install(client, FakeDownloader("test.auto.a", {}))
    limited.search_error = SubtitleUnavailable("限流", retry_after=60)

    fallback = install(
        client,
        FakeDownloader(
            "test.auto.b",
            {
                "zh-Hans": [candidate("test.auto.b", "zh-Hans.srt", language="zh-Hans")],
                "ja": [candidate("test.auto.b", "ja.srt", language="ja")],
            },
        ),
    )
    app_container = container(client)
    set_downloaders(app_container, library, ["test.auto.a", "test.auto.b"])
    set_languages(app_container, library, ["zh-Hans", "ja"])

    counters = run_auto(app_container, clock)

    # 第一个下载器要求退避后，本次运行不再问它；后面的语言直接问下一个下载器
    assert limited.searches == ["ja"]
    assert fallback.searches == ["ja", "zh-Hans", "ja"]
    assert counters["downloaded"] == 3


def test_auto_uses_external_ids_table_when_present(
    client: TestClient, library: int, clock: FakeClock
) -> None:
    downloader = install(client, FakeDownloader("test.auto.a", {}))
    app_container = container(client)
    set_downloaders(app_container, library, ["test.auto.a"])
    set_languages(app_container, library, ["zh-Hans"])
    version_id = versions(app_container, library)["clean"]

    run_auto(app_container, clock)
    # 0039 之前没有外部 ID 表：按主插件记录推算 TMDB ID
    assert downloader.media[-1].tmdb_id == 535167
    assert downloader.media[-1].imdb_id is None

    with app_container.database.session() as connection:
        media_item_id = connection.execute(
            "SELECT p.media_item_id FROM version v JOIN playable p ON p.id = v.playable_id "
            "WHERE v.id = ?",
            (version_id,),
        ).fetchone()[0]
        connection.executemany(
            "INSERT OR REPLACE INTO media_item_external_id (media_item_id, scheme, value) "
            "VALUES (?, ?, ?)",
            [(media_item_id, "imdb", "tt2332883"), (media_item_id, "tmdb", "999999")],
        )
    # 上一轮记了「没有结果」，退避过后才会再问一次
    clock.advance(days=8)
    run_auto(app_container, clock)
    assert downloader.media[-1].imdb_id == "tt2332883"
    assert downloader.media[-1].tmdb_id == 999999


# ---- 计划任务 ----


class FakeAutoDownloader:
    """会一直等到测试放行的假下载器，用来观察调度线程。"""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def run(self, handle: TrackedTask, cancel: Callable[[], bool]) -> dict[str, int]:
        self.calls += 1
        self.started.set()
        self.release.wait(10)
        return {"downloaded": 0}


def _wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("等待超时")


def test_maintenance_runs_subtitle_download_in_a_thread_without_blocking(
    client: TestClient, clock: FakeClock, library: int
) -> None:
    app_container = container(client)
    fake = FakeAutoDownloader()
    with app_container.database.session() as connection:
        connection.execute("UPDATE scheduled_task SET enabled = 1 WHERE key = 'cleanup_sessions'")
        connection.execute(
            "INSERT INTO auth_session (user_id, token_hash, token_version, created_at, expires_at) "
            "VALUES ((SELECT id FROM user LIMIT 1), 'expired', 1, '2020-01-01T00:00:00+00:00', "
            "'2020-01-02T00:00:00+00:00')"
        )

    # 假下载器一直不返回，维护循环仍然跑完其他任务并立即返回
    run_due(
        app_container.database,
        app_container.backups,
        app_container.images,
        app_container.metadata,
        cast(SubtitleAutoDownloader, fake),
        clock,
    )
    assert fake.started.is_set()
    assert not fake.release.is_set()

    with app_container.database.read_session() as connection:
        tasks = TaskRepository(connection, clock).list(kind="scheduled:download_subtitles")
        assert [task.status for task in tasks] == ["running"]
        expired = connection.execute(
            "SELECT COUNT(*) FROM auth_session WHERE token_hash = 'expired'"
        ).fetchone()[0]
        assert expired == 0

    # 在跑的时候再 tick 不会重入
    run_due(
        app_container.database,
        app_container.backups,
        app_container.images,
        app_container.metadata,
        cast(SubtitleAutoDownloader, fake),
        clock,
    )
    assert fake.calls == 1

    fake.release.set()
    _wait_until(
        lambda: _subtitle_task_status(app_container, clock) not in ("running", "cancel_requested")
    )
    with app_container.database.read_session() as connection:
        task = TaskRepository(connection, clock).list(kind="scheduled:download_subtitles")[0]
        assert (task.status, task.result["downloaded"]) == ("succeeded", 0)
        row = connection.execute(
            "SELECT last_run_at, last_task_id FROM scheduled_task WHERE key = 'download_subtitles'"
        ).fetchone()
    assert row["last_run_at"] is not None and row["last_task_id"] == task.id


def _subtitle_task_status(app_container: AppContainer, clock: FakeClock) -> str:
    with app_container.database.read_session() as connection:
        tasks = TaskRepository(connection, clock).list(kind="scheduled:download_subtitles")
    return tasks[0].status if tasks else "missing"


def _task_status(client: TestClient, task_id: int) -> str:
    response = client.get(f"/api/admin/tasks/{task_id}", headers=fresh_admin(client))
    assert response.status_code == 200, response.text
    status: str = response.json()["status"]
    return status


def test_manual_run_returns_task_id_and_rejects_reentry(
    client: TestClient, admin: dict[str, str], clock: FakeClock, library: int
) -> None:
    lock = maintenance._subtitle_lock
    if not lock.acquire(blocking=False):
        pytest.skip("下载字幕线程还在收尾")
    try:
        blocked = client.post("/api/admin/scheduled-tasks/download_subtitles/run", headers=admin)
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "task_already_running"
    finally:
        lock.release()

    first = client.post("/api/admin/scheduled-tasks/download_subtitles/run", headers=admin)
    assert first.status_code == 202, first.text
    task_id = first.json()["task_id"]
    _wait_until(lambda: _task_status(client, task_id) not in ("running", "cancel_requested"))
    assert _task_status(client, task_id) == "succeeded"


@pytest.fixture(autouse=True)
def _extra_builtin_plugins(plugin_env: object) -> None:
    from tests.plugin_env import PluginEnvironment, install_builtin

    assert isinstance(plugin_env, PluginEnvironment)
    install_builtin(plugin_env.root, "builtin.assrt")
