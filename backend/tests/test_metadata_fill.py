"""元数据补缺：只按外部 ID 找条目、填空/fallback 字段、按来源存评分、按 G2 选图。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from iris_plugin_sdk import MetadataRecord, MetadataUnavailable, RemoteImage

from app.config import AppConfig
from app.library.fill import Filler
from app.priority import Priority
from tests.conftest import FakeClock, SyncRunner, cloud115_roots, make_app
from tests.fake_cloud115 import FakeCloud115
from tests.fake_plugins import (
    FakeImagePlugin,
    FakeMetadataPlugin,
    install_image_plugin,
    install_metadata_plugin,
)
from tests.plugin_env import bind_controls
from tests.test_sync import fresh_admin, sync

ADMIN = "/api/admin"
MAIN = "test.main"
FILL = "test.fill"
IMAGES = "test.images"
IMAGES2 = "test.images2"


@pytest.fixture
def main(config: AppConfig) -> Any:
    return install_metadata_plugin(config.plugins_dir, MAIN, "假元数据")


@pytest.fixture
def fill(config: AppConfig) -> Any:
    return install_metadata_plugin(config.plugins_dir, FILL, "假补缺", search=False)


@pytest.fixture
def images(config: AppConfig) -> Any:
    return install_image_plugin(config.plugins_dir, IMAGES, "假图片")


@pytest.fixture
def images2(config: AppConfig) -> Any:
    return install_image_plugin(config.plugins_dir, IMAGES2, "第二图片")


@pytest.fixture
def client(
    config: AppConfig,
    clock: FakeClock,
    sync_runner: SyncRunner,
    main: Any,
    fill: Any,
    images: Any,
    images2: Any,
) -> Iterator[TestClient]:
    app = make_app(config, clock, sync_runner)
    with TestClient(app) as test_client:
        bind_controls(config.plugins_dir, test_client)
        yield test_client


def request(client: TestClient, method: str, path: str, body: Any = None, status: int = 200) -> Any:
    response = client.request(method, f"{ADMIN}{path}", json=body, headers=fresh_admin(client))
    assert response.status_code == status, response.text
    return response.json() if response.content else None


def main_record(**overrides: Any) -> MetadataRecord:
    values: dict[str, Any] = {
        "provider_id": MAIN,
        "remote_id": "m-1",
        "title": "示例电影",
        "kind": "movie",
        "original_title": "Original Title",
        "overview": "English overview",
        "year": 2020,
        "release_date": "2020-01-01",
        "rating": 7.0,
        "genres": ("剧情",),
        "runtime": 120,
        "poster": "main/poster.jpg",
        "external_ids": {"tmdb": "101", "imdb": "tt101"},
        "fallback_fields": frozenset({"overview"}),
    }
    values.update(overrides)
    return MetadataRecord(**values)


def fill_record(**overrides: Any) -> MetadataRecord:
    values: dict[str, Any] = {
        "provider_id": FILL,
        "remote_id": "d-1",
        "title": "豆瓣标题",
        "overview": "中文简介",
        "tagline": "一句话",
        "year": 2020,
        "runtime": 121,
        "studios": ("制片厂",),
        "countries": ("中国",),
        "content_rating": "PG-13",
        "rating": 8.6,
        "poster": "fill/poster.jpg",
        "external_ids": {"douban": "d-1"},
    }
    values.update(overrides)
    return MetadataRecord(**values)


def prepare(
    client: TestClient,
    cloud115: FakeCloud115,
    main: FakeMetadataPlugin,
    fill: FakeMetadataPlugin,
    *,
    image_plugins: list[str] | None = None,
    library_name: str = "电影",
) -> dict[str, Any]:
    cloud115.make_path(f"{library_name}/示例电影 (2020)/示例电影.2020.1080p.mkv")
    main.provider.records["m-1"] = main_record()
    fill.provider.find_results[("tmdb", "101")] = None
    fill.provider.find_results[("imdb", "tt101")] = "d-1"
    fill.provider.records["d-1"] = fill_record()
    library: dict[str, Any] = request(
        client,
        "POST",
        "/libraries",
        {
            "name": library_name,
            "kind": "movie",
            "roots": cloud115_roots(client, cloud115.make_path(library_name)),
            "metadata_provider": MAIN,
        },
        status=201,
    )
    body: dict[str, Any] = {
        "metadata": [
            {"id": MAIN, "enabled": True},
            {"id": FILL, "enabled": True},
        ]
    }
    if image_plugins:
        body["images"] = [{"id": plugin_id, "enabled": True} for plugin_id in image_plugins]
    request(client, "PUT", f"/libraries/{library['id']}/downloaders", body)
    return library


def item_row(config: AppConfig) -> sqlite3.Row:
    connection = sqlite3.connect(config.database_path)
    connection.row_factory = sqlite3.Row
    try:
        row: sqlite3.Row = connection.execute("SELECT * FROM media_item").fetchone()
        return row
    finally:
        connection.close()


def source_rows(config: AppConfig) -> dict[str, tuple[str | None, float | None]]:
    with sqlite3.connect(config.database_path) as connection:
        return {
            provider_id: (remote_id, rating)
            for provider_id, remote_id, rating in connection.execute(
                "SELECT provider_id, remote_id, rating FROM media_item_source"
            )
        }


def external_ids(config: AppConfig) -> dict[str, str]:
    with sqlite3.connect(config.database_path) as connection:
        return {
            scheme: value
            for scheme, value in connection.execute(
                "SELECT scheme, value FROM media_item_external_id"
            )
        }


def test_sync_fills_fields_images_and_ratings(
    client: TestClient,
    admin: dict[str, str],
    config: AppConfig,
    cloud115: FakeCloud115,
    main: FakeMetadataPlugin,
    fill: FakeMetadataPlugin,
    images: FakeImagePlugin,
) -> None:
    prepare(client, cloud115, main, fill, image_plugins=[IMAGES])
    images.provider.available = [
        RemoteImage("poster", "p-fr", language="fr", score=9),
        RemoteImage("poster", "p-en", language="en", score=8),
        RemoteImage("poster", "p-zh", language="zh", score=1),
        RemoteImage("backdrop", "b-zh", language="zh", score=9),
        RemoteImage("backdrop", "b-none", score=50),
        RemoteImage("thumb", "t-fr", language="fr", score=2),
    ]

    body = sync(client, admin)

    assert body["status"] == "succeeded", body
    row = item_row(config)
    # fallback 的 overview 被补缺替换，其他已有字段和列表字段不覆盖
    assert row["overview"] == "中文简介"
    assert row["tagline"] == "一句话"
    assert row["original_title"] == "Original Title"
    assert row["genres"] == '["剧情"]'
    assert row["studios"] == '["制片厂"]'
    assert row["rating"] == 7.0
    assert row["filled_at"] is not None
    assert row["fallback_fields"] == '["overview"]'
    # 图片按 G2：poster 元数据语言优先，backdrop 无文字优先，thumb 只有其他语言也采用
    assert (row["poster_path"], row["poster_provider"]) == ("p-zh", IMAGES)
    assert (row["backdrop_path"], row["backdrop_provider"]) == ("b-none", IMAGES)
    assert (row["thumb_path"], row["thumb_provider"]) == ("t-fr", IMAGES)
    # 补缺只按外部 ID 查，绝不用 search
    assert fill.provider.search_calls == []
    assert fill.provider.find_calls == [
        ("movie", "tmdb", "101"),
        ("movie", "imdb", "tt101"),
    ]
    assert images.provider.image_requests[0].language == "zh-CN"
    assert images.provider.image_requests[0].external_ids == {
        "tmdb": "101",
        "imdb": "tt101",
        "douban": "d-1",
    }
    assert external_ids(config) == {"tmdb": "101", "imdb": "tt101", "douban": "d-1"}
    assert source_rows(config) == {MAIN: ("m-1", 7.0), FILL: ("d-1", 8.6)}
    # 获取器的图片真正下载了
    assert {source for source, _ in images.provider.downloads} >= {"p-zh", "b-none", "t-fr"}

    detail = client.get(f"/api/items/{row['id']}", headers=fresh_admin(client)).json()
    assert [
        (rating["source"], rating["label"], rating["value"]) for rating in detail["ratings"]
    ] == [
        (MAIN, "假元数据", 7.0),
        (FILL, "假补缺", 8.6),
    ]


def test_no_external_id_sends_no_requests(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    main: FakeMetadataPlugin,
    fill: FakeMetadataPlugin,
    images: FakeImagePlugin,
) -> None:
    prepare(client, cloud115, main, fill, image_plugins=[IMAGES])
    main.provider.records["m-1"] = replace(
        main.provider.records["m-1"], external_ids={}, fallback_fields=frozenset()
    )

    sync(client, admin)

    assert fill.provider.find_calls == []
    assert fill.provider.search_calls == []
    assert images.provider.image_requests == []


def test_mismatched_external_id_is_discarded(
    client: TestClient,
    admin: dict[str, str],
    config: AppConfig,
    cloud115: FakeCloud115,
    main: FakeMetadataPlugin,
    fill: FakeMetadataPlugin,
) -> None:
    prepare(client, cloud115, main, fill)
    fill.provider.records["d-1"] = fill_record(external_ids={"imdb": "tt999"})

    sync(client, admin)

    row = item_row(config)
    assert row["overview"] == "English overview"
    assert row["tagline"] is None
    assert source_rows(config)[FILL] == (None, None)


def test_negative_cache_skips_recent_misses(
    client: TestClient,
    admin: dict[str, str],
    config: AppConfig,
    clock: FakeClock,
    cloud115: FakeCloud115,
    main: FakeMetadataPlugin,
    fill: FakeMetadataPlugin,
) -> None:
    library = prepare(client, cloud115, main, fill)
    sync(client, admin)
    item = item_row(config)
    filler: Filler = client.app.state.container.metadata.filler  # type: ignore[attr-defined]

    # 第一次没找到，写负缓存
    fill.provider.find_results.clear()
    fill.provider.records.clear()
    filler.fill_item(item["id"], Priority.INTERACTIVE)
    calls = list(fill.provider.find_calls)
    assert len(calls) == 2

    # 7 天内的再次补缺不再请求
    fill.provider.find_results[("imdb", "tt101")] = "d-1"
    fill.provider.records["d-1"] = fill_record(overview="后来补上的简介")
    filler.fill_item(item["id"], Priority.INTERACTIVE)
    assert len(fill.provider.find_calls) == 2
    assert item_row(config)["overview"] == "中文简介"

    # 过了负缓存时间会重新查
    clock.advance(days=8)
    filler.fill_item(item["id"], Priority.INTERACTIVE)
    assert len(fill.provider.find_calls) == 4
    assert item_row(config)["overview"] == "后来补上的简介"
    assert library["id"]


def test_unavailable_fill_plugin_does_not_block_matching(
    client: TestClient,
    admin: dict[str, str],
    config: AppConfig,
    cloud115: FakeCloud115,
    main: FakeMetadataPlugin,
    fill: FakeMetadataPlugin,
) -> None:
    prepare(client, cloud115, main, fill)
    fill.provider.reason = "豆瓣需要填写 Cookie"

    body = sync(client, admin)

    assert body["status"] == "succeeded"
    assert body["stats"]["matched"] == 1
    assert any("豆瓣需要填写 Cookie" in problem for problem in body["stats"]["problems"])
    assert item_row(config)["overview"] == "English overview"


def test_refresh_item_uses_a_single_write_transaction(
    client: TestClient,
    admin: dict[str, str],
    config: AppConfig,
    cloud115: FakeCloud115,
    main: FakeMetadataPlugin,
    fill: FakeMetadataPlugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare(client, cloud115, main, fill)
    sync(client, admin)
    container = client.app.state.container  # type: ignore[attr-defined]
    database = container.database
    calls: list[int] = []
    original = database.session

    @contextmanager
    def counted() -> Iterator[sqlite3.Connection]:
        calls.append(1)
        with original() as connection:
            yield connection

    monkeypatch.setattr(database, "session", counted)
    main.provider.records["m-1"] = replace(
        main.provider.records["m-1"], overview="English overview 2"
    )

    container.metadata.scraper.refresh_item(item_row(config)["id"], Priority.INTERACTIVE)

    assert calls == [1]


def test_first_fetcher_with_each_type_wins(
    client: TestClient,
    admin: dict[str, str],
    config: AppConfig,
    cloud115: FakeCloud115,
    main: FakeMetadataPlugin,
    fill: FakeMetadataPlugin,
    images: FakeImagePlugin,
    images2: FakeImagePlugin,
) -> None:
    prepare(client, cloud115, main, fill, image_plugins=[IMAGES, IMAGES2])
    # 第一个获取器只有 backdrop；第二个的 poster 才被选中
    images.provider.available = [RemoteImage("backdrop", "b-1", language="en", score=1)]
    images2.provider.available = [
        RemoteImage("poster", "p-2", language="en", score=1),
        RemoteImage("backdrop", "b-2", language="zh", score=99),
    ]

    sync(client, admin)

    row = item_row(config)
    assert (row["poster_path"], row["poster_provider"]) == ("p-2", IMAGES2)
    assert (row["backdrop_path"], row["backdrop_provider"]) == ("b-1", IMAGES)


def test_locked_fields_and_images_are_kept(
    client: TestClient,
    admin: dict[str, str],
    config: AppConfig,
    cloud115: FakeCloud115,
    main: FakeMetadataPlugin,
    fill: FakeMetadataPlugin,
    images: FakeImagePlugin,
) -> None:
    prepare(client, cloud115, main, fill, image_plugins=[IMAGES])
    sync(client, admin)
    item = item_row(config)
    with sqlite3.connect(config.database_path) as connection:
        for field in ("overview", "poster_path"):
            connection.execute(
                "INSERT OR REPLACE INTO metadata_field"
                "(entity_kind, entity_id, field, source, locked, updated_at) "
                "VALUES ('item', ?, ?, 'manual', 1, 'x')",
                (item["id"], field),
            )
        connection.execute(
            "UPDATE media_item SET overview = '手动简介', poster_path = 'locked/poster.jpg', "
            "poster_provider = NULL WHERE id = ?",
            (item["id"],),
        )
    fill.provider.records["d-1"] = fill_record(overview="新的中文简介", en_title="English Title")
    images.provider.available = [RemoteImage("poster", "p-new", score=10)]

    filler: Filler = client.app.state.container.metadata.filler  # type: ignore[attr-defined]
    filler.fill_item(item["id"], Priority.INTERACTIVE)

    row = item_row(config)
    assert row["overview"] == "手动简介"
    assert row["poster_path"] == "locked/poster.jpg"
    assert row["en_title"] == "English Title"


def test_metadata_image_fallback_when_no_fetcher_has_the_type(
    client: TestClient,
    admin: dict[str, str],
    config: AppConfig,
    cloud115: FakeCloud115,
    main: FakeMetadataPlugin,
    fill: FakeMetadataPlugin,
    images: FakeImagePlugin,
) -> None:
    prepare(client, cloud115, main, fill, image_plugins=[IMAGES])
    main.provider.records["m-1"] = replace(main.provider.records["m-1"], poster=None)
    images.provider.available = [RemoteImage("backdrop", "b-1", score=1)]

    sync(client, admin)

    row = item_row(config)
    # 没有获取器提供 poster 且条目当前无图时，回退到补缺记录的图，来源记为补缺插件
    assert (row["poster_path"], row["poster_provider"]) == ("fill/poster.jpg", FILL)


def test_pending_uses_image_provider_and_admin_selects_current_image(
    client: TestClient,
    admin: dict[str, str],
    config: AppConfig,
    cloud115: FakeCloud115,
    main: FakeMetadataPlugin,
    fill: FakeMetadataPlugin,
    images: FakeImagePlugin,
) -> None:
    prepare(client, cloud115, main, fill, image_plugins=[IMAGES])
    images.provider.available = [RemoteImage("poster", "p-zh", language="zh", score=1)]
    sync(client, admin)
    container = client.app.state.container  # type: ignore[attr-defined]
    item = item_row(config)
    with sqlite3.connect(config.database_path) as connection:
        connection.execute(
            "UPDATE media_item SET poster_image_id = NULL WHERE id = ?", (item["id"],)
        )

    tasks = container.metadata.artwork.pending([item["library_id"]])
    poster = next(task for task in tasks if task.column == "poster")
    assert (poster.provider_id, poster.path) == (IMAGES, "p-zh")

    artwork = request(client, "GET", f"/items/{item['id']}/artwork/poster")
    assert artwork["current"] == "p-zh"
    before = len(images.provider.downloads)
    # 选中「当前图」时用当前图的提供方下载，而不是条目所属的主插件
    updated = request(
        client,
        "PUT",
        f"/items/{item['id']}/artwork/poster",
        {"source": "provider", "value": "p-zh"},
    )
    assert updated["current"] == "p-zh"
    assert len(images.provider.downloads) == before + 1
    assert images.provider.downloads[-1][0] == "p-zh"


def test_filling_phase_runs_between_matching_and_scraping(
    client: TestClient,
    admin: dict[str, str],
    config: AppConfig,
    cloud115: FakeCloud115,
    main: FakeMetadataPlugin,
    fill: FakeMetadataPlugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.sync import run as sync_run

    prepare(client, cloud115, main, fill)
    phases: list[str] = []
    original = sync_run.SyncRun._progress

    def record(self: Any, phase: Any, done: int, total: int) -> None:
        phases.append(str(phase))
        original(self, phase, done, total)

    monkeypatch.setattr(sync_run.SyncRun, "_progress", record)
    sync(client, admin)

    assert phases.index("matching") < phases.index("filling") < phases.index("images")

    # 定向同步也有 filling 阶段
    item = item_row(config)
    with sqlite3.connect(config.database_path) as connection:
        connection.execute("UPDATE media_item SET filled_at = NULL WHERE id = ?", (item["id"],))
    phases.clear()
    response = client.post(f"/api/admin/items/{item['id']}/sync", headers=fresh_admin(client))
    assert response.status_code == 202, response.text
    assert "filling" in phases


def test_filling_phase_precedes_season_scraping(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    main: FakeMetadataPlugin,
    fill: FakeMetadataPlugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.sync import run as sync_run

    main.provider.records["s-1"] = MetadataRecord(
        provider_id=MAIN,
        remote_id="s-1",
        title="示例剧",
        kind="series",
        year=2020,
        rating=8.0,
        external_ids={"tmdb": "202"},
    )
    cloud115.make_path("剧集/示例剧 (2020)/Season 1/示例剧.S01E01.mkv")
    library = request(
        client,
        "POST",
        "/libraries",
        {
            "name": "剧集",
            "kind": "series",
            "roots": cloud115_roots(client, cloud115.make_path("剧集")),
            "metadata_provider": MAIN,
        },
        status=201,
    )
    request(
        client,
        "PUT",
        f"/libraries/{library['id']}/downloaders",
        {"metadata": [{"id": MAIN, "enabled": True}, {"id": FILL, "enabled": True}]},
    )
    phases: list[str] = []
    original = sync_run.SyncRun._progress

    def record(self: Any, phase: Any, done: int, total: int) -> None:
        phases.append(str(phase))
        original(self, phase, done, total)

    monkeypatch.setattr(sync_run.SyncRun, "_progress", record)
    sync(client, admin)

    assert phases.index("matching") < phases.index("filling") < phases.index("scraping")


def test_transient_failure_retries_next_sync_and_skips_plugin_for_the_run(
    client: TestClient,
    admin: dict[str, str],
    config: AppConfig,
    cloud115: FakeCloud115,
    main: FakeMetadataPlugin,
    fill: FakeMetadataPlugin,
) -> None:
    prepare(client, cloud115, main, fill)
    fill.provider.error = MetadataUnavailable("豆瓣拒绝了这次请求")

    body = sync(client, admin)

    assert body["status"] == "succeeded"
    # 第一个外部 ID 就报不可用，不再试 imdb；条目留到下次同步再补
    assert len(fill.provider.find_calls) == 1
    row = item_row(config)
    assert row["filled_at"] is None
    assert row["overview"] == "English overview"

    # 同一轮里报过不可用的插件，后面的条目不再请求
    filler: Filler = client.app.state.container.metadata.filler  # type: ignore[attr-defined]
    blocked = {FILL}
    plan = filler.fill_item(row["id"], Priority.BACKGROUND, blocked=blocked)
    assert plan.incomplete
    assert len(fill.provider.find_calls) == 1

    fill.provider.error = None
    sync(client, admin)

    row = item_row(config)
    assert row["filled_at"] is not None
    assert row["overview"] == "中文简介"


def test_primary_source_keeps_its_own_missing_rating(
    client: TestClient,
    admin: dict[str, str],
    config: AppConfig,
    cloud115: FakeCloud115,
    main: FakeMetadataPlugin,
    fill: FakeMetadataPlugin,
) -> None:
    prepare(client, cloud115, main, fill)
    main.provider.records["m-1"] = replace(main.provider.records["m-1"], rating=None)

    sync(client, admin)

    # 条目评分用补缺来源补上，但主插件那一行仍是它自己的（没有）评分
    assert item_row(config)["rating"] == 8.6
    assert source_rows(config) == {MAIN: ("m-1", None), FILL: ("d-1", 8.6)}
