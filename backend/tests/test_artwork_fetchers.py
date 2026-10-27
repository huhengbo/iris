"""图片获取器：按列里的提供方下载、image.fetch 与 metadata.images 的差异、G2 选图。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from iris_plugin_sdk import ImageLookupRequest, RemoteImage

from app.config import AppConfig
from app.library import merge
from app.priority import Priority
from tests.conftest import FakeClock, SyncRunner, make_app
from tests.fake_plugins import (
    FakeImagePlugin,
    install_image_plugin,
    install_metadata_plugin,
)
from tests.plugin_env import bind_controls

MAIN = "test.main"
IMAGES = "test.images"


@pytest.fixture
def main(config: AppConfig) -> Any:
    return install_metadata_plugin(config.plugins_dir, MAIN, "假元数据")


@pytest.fixture
def images(config: AppConfig) -> Any:
    return install_image_plugin(config.plugins_dir, IMAGES, "假图片")


@pytest.fixture
def client(
    config: AppConfig,
    clock: FakeClock,
    sync_runner: SyncRunner,
    main: Any,
    images: Any,
) -> Iterator[TestClient]:
    app = make_app(config, clock, sync_runner)
    with TestClient(app) as test_client:
        bind_controls(config.plugins_dir, test_client)
        yield test_client


def test_pick_image_uses_language_tiers_then_score_then_order() -> None:
    poster = [
        RemoteImage("poster", "other", language="fr", score=9),
        RemoteImage("poster", "english", language="en", score=9),
        RemoteImage("poster", "no-text", score=1),
        RemoteImage("poster", "chinese", language="zh", score=1),
        RemoteImage("poster", "chinese-2", language="zh-CN", score=1),
    ]
    picked = merge.pick_image(poster, "poster", "zh-CN")
    assert picked is not None and picked.source == "chinese"

    backdrop = [
        RemoteImage("backdrop", "chinese", language="zh", score=99),
        RemoteImage("backdrop", "no-text", score=1),
        RemoteImage("backdrop", "english", language="en", score=50),
    ]
    picked = merge.pick_image(backdrop, "backdrop", "zh-CN")
    assert picked is not None and picked.source == "no-text"

    # 同一档按 score 降序，score 为空排最后；都相同时保持原顺序
    same = [
        RemoteImage("logo", "low", language="zh", score=1),
        RemoteImage("logo", "high", language="zh", score=8),
        RemoteImage("logo", "none", language="zh"),
        RemoteImage("logo", "high-2", language="zh", score=8),
    ]
    picked = merge.pick_image(same, "logo", "zh-CN")
    assert picked is not None and picked.source == "high"

    # 只有其他语言也算「有这类图」，作为最后一档
    only_other = [RemoteImage("thumb", "fr", language="fr", score=2)]
    picked = merge.pick_image(only_other, "thumb", "zh-CN")
    assert picked is not None and picked.source == "fr"
    assert merge.pick_image([], "thumb", "zh-CN") is None


def test_image_source_distinguishes_listing_and_download(client: TestClient) -> None:
    providers = client.app.state.container.metadata.providers  # type: ignore[attr-defined]

    fetcher = providers.image_source(IMAGES)
    assert fetcher is not None and fetcher.can_list
    assert fetcher.unavailable_reason() is None
    assert (
        fetcher.images(
            ImageLookupRequest(kind="movie", external_ids={"tmdb": "1"}), Priority.INTERACTIVE
        )
        == []
    )

    # 只提供 metadata.images 的插件能下载已知来源，但不能列图
    downloader = providers.image_source(MAIN)
    assert downloader is not None and not downloader.can_list
    assert downloader.image("poster.jpg", 500, Priority.INTERACTIVE)
    assert providers.image_source("nope") is None


def test_artwork_pending_resolves_column_provider_and_downloads(
    client: TestClient, config: AppConfig, images: FakeImagePlugin
) -> None:
    with sqlite3.connect(config.database_path) as connection:
        connection.execute(
            "INSERT INTO library (id, name, kind, sort_order, created_at, updated_at) "
            "VALUES (1, '电影', 'movie', 0, 'x', 'x')"
        )
        connection.execute(
            "INSERT INTO media_item (id, library_id, kind, provider_id, remote_id, title, "
            "poster_path, poster_provider, backdrop_path, scraped_at, added_at) "
            "VALUES (10, 1, 'movie', 'test.main', 'm-1', '示例', 'p.jpg', ?, "
            "'b.jpg', 'x', 'x')",
            (IMAGES,),
        )

    container = client.app.state.container  # type: ignore[attr-defined]
    tasks = container.metadata.artwork.pending([1])
    poster = next(task for task in tasks if task.column == "poster")
    # poster 用列里的提供方，backdrop 没有指定时沿用条目自己的插件
    assert poster.provider_id == IMAGES
    backdrop = next(task for task in tasks if task.column == "backdrop")
    assert backdrop.provider_id == MAIN

    container.metadata.artwork.download(poster, Priority.INTERACTIVE)

    with sqlite3.connect(config.database_path) as connection:
        image_id = connection.execute(
            "SELECT poster_image_id FROM media_item WHERE id = 10"
        ).fetchone()[0]
    assert image_id is not None
    assert [source for source, _ in images.provider.downloads] == ["p.jpg"]


def test_blocked_image_provider_is_reported_and_skipped(
    client: TestClient, config: AppConfig, images: FakeImagePlugin
) -> None:
    with sqlite3.connect(config.database_path) as connection:
        connection.execute(
            "INSERT INTO library (id, name, kind, sort_order, created_at, updated_at) "
            "VALUES (1, '电影', 'movie', 0, 'x', 'x')"
        )
        connection.execute(
            "INSERT INTO media_item (id, library_id, kind, provider_id, remote_id, title, "
            "poster_path, poster_provider, scraped_at, added_at) "
            "VALUES (10, 1, 'movie', 'test.main', 'm-1', '示例', 'p.jpg', ?, 'x', 'x')",
            (IMAGES,),
        )
    images.provider.reason = "Fanart.tv 还没有填写 API Key"
    container = client.app.state.container  # type: ignore[attr-defined]

    blocked = container.metadata.filler.blocked_image_providers(
        container.metadata.providers, [IMAGES]
    )

    assert blocked == {IMAGES: "Fanart.tv 还没有填写 API Key"}
    assert container.metadata.artwork.pending([1])
