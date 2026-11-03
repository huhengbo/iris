"""语义索引：按向量模型建索引、增量更新、换模型重建、按可见范围召回（M55，ADR 0030）。"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.ai.semantic import SemanticIndex
from app.ai.types import AiError
from app.config import AppConfig
from app.container import AppContainer
from tests.conftest import FakeClock, save_tmdb_key
from tests.fake_ai import FakeAi
from tests.fake_cloud115 import FakeCloud115
from tests.fake_tmdb import FakeTmdb
from tests.test_ai_providers import add_provider, container
from tests.test_catalog import movie_library
from tests.test_sync import fresh_admin, sync

ADMIN = "/api/admin"
FILMS = {"太空漫游": 1, "爱情故事": 2, "战争之王": 3}


@pytest.fixture
def indexed_library(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
) -> AppContainer:
    save_tmdb_key(client, admin)
    provider = add_provider(client, {"access_token": admin["Authorization"].split()[1]})
    response = client.put(
        f"{ADMIN}/ai/roles",
        json={"embedding": {"provider_id": provider["id"], "model": "bge"}},
        headers=admin,
    )
    assert response.status_code == 200, response.text
    for title, tmdb_id in FILMS.items():
        tmdb.add_movie(tmdb_id, title, 2020)
    movie_library(client, admin, cloud115, [f"{t} (2020)/{t}.2020.mkv" for t in FILMS])
    assert sync(client, admin)["stats"]["matched"] == 3
    return container(client)


def build(index: SemanticIndex) -> int:
    total = 0
    while done := index.sync():
        total += done
    return total


def item_ids(config: AppConfig) -> dict[str, int]:
    with sqlite3.connect(config.database_path) as connection:
        return {t: i for i, t in connection.execute("SELECT id, title FROM media_item")}


def search(app: AppContainer, text: str, visible: set[int] | None = None, limit: int = 5) -> Any:
    def accept(ids: Sequence[int]) -> list[int]:
        return [i for i in ids if visible is None or i in visible]

    with app.database.read_session(vector=True) as connection:
        return app.semantic.search(connection, text, accept, limit)


def test_index_is_built_in_batches_and_answers_by_meaning(
    indexed_library: AppContainer, config: AppConfig, fake_ai: FakeAi
) -> None:
    app = indexed_library
    assert app.semantic.status().state == "building"

    assert build(app.semantic) == 3

    status = app.semantic.status()
    assert (status.state, status.total, status.indexed, status.model) == ("ready", 3, 3, "bge")
    ids = item_ids(config)
    assert search(app, "太空 太空 冒险")[0] == ids["太空漫游"]
    assert search(app, "爱情 爱情")[0] == ids["爱情故事"]
    # 索引文本包含标题、类型和简介
    assert "标题：太空漫游" in fake_ai.embedded[0][0] and "类型：电影" in fake_ai.embedded[0][0]
    assert "简介：太空漫游的简介" in fake_ai.embedded[0][0]
    # 已是最新时不再调用模型
    calls = len(fake_ai.embedded)
    assert app.semantic.sync() == 0 and len(fake_ai.embedded) == calls


def test_results_are_limited_to_the_visible_items(
    indexed_library: AppContainer, config: AppConfig
) -> None:
    app = indexed_library
    build(app.semantic)
    ids = item_ids(config)
    only_love = {ids["爱情故事"]}

    assert search(app, "太空 太空", only_love) == [ids["爱情故事"]]
    assert search(app, "太空", set()) == []
    assert len(search(app, "太空", None, limit=2)) == 2


def test_changing_the_embedding_model_rebuilds_the_index(
    client: TestClient, indexed_library: AppContainer, admin: dict[str, str], fake_ai: FakeAi
) -> None:
    app = indexed_library
    build(app.semantic)
    first = len(fake_ai.embedded)
    provider_id = app.ai.roles()["embedding"].provider_id  # type: ignore[union-attr]

    client.put(
        f"{ADMIN}/ai/roles",
        json={"embedding": {"provider_id": provider_id, "model": "bge-large"}},
        headers=fresh_admin(client),
    )
    # 换模型后旧索引立刻不可用于召回，重建完成前不会混用
    assert app.semantic.usable() is False
    assert build(app.semantic) == 3

    assert len(fake_ai.embedded) > first
    assert app.semantic.status().model == "bge-large"
    assert app.semantic.usable() is True


def test_new_edited_and_deleted_items_are_kept_in_sync(
    client: TestClient,
    indexed_library: AppContainer,
    config: AppConfig,
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
    admin: dict[str, str],
    fake_ai: FakeAi,
) -> None:
    app = indexed_library
    build(app.semantic)
    ids = item_ids(config)
    embedded = len(fake_ai.embedded)

    # 刮削时间变了但文本没变：只更新记录，不再调用模型
    with sqlite3.connect(config.database_path) as connection:
        connection.execute(
            "UPDATE media_item SET scraped_at = '2030-01-01T00:00:00+00:00' WHERE id = ?",
            (ids["战争之王"],),
        )
    assert app.semantic.sync() == 1
    assert len(fake_ai.embedded) == embedded
    assert app.semantic.sync() == 0

    # 简介变了：重新生成
    with sqlite3.connect(config.database_path) as connection:
        connection.execute(
            "UPDATE media_item SET overview = '一部喜剧 喜剧', scraped_at = '2031-01-01' "
            "WHERE id = ?",
            (ids["战争之王"],),
        )
    assert app.semantic.sync() == 1
    assert len(fake_ai.embedded) == embedded + 1
    assert search(app, "喜剧 喜剧 喜剧")[0] == ids["战争之王"]

    # 条目删除后，向量也被清理
    with sqlite3.connect(config.database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("DELETE FROM media_item WHERE id = ?", (ids["爱情故事"],))
    app.semantic.sync()
    assert ids["爱情故事"] not in search(app, "爱情 爱情", limit=10)
    assert app.semantic.status().total == 2


def test_embedding_failures_are_reported_and_retried_later(
    indexed_library: AppContainer, fake_ai: FakeAi, clock: FakeClock
) -> None:
    app = indexed_library
    fake_ai.embed_error = AiError("auth", "Key 无效")
    assert app.semantic.sync() == 0
    status = app.semantic.status()
    assert (status.state, status.error) == ("error", "Key 无效")

    fake_ai.embed_error = None
    assert app.semantic.sync() == 0  # 退避期内
    clock.advance(minutes=6)
    assert app.semantic.sync() == 3
    assert app.semantic.status().state == "ready"


def test_missing_extension_only_disables_semantic_search(
    client: TestClient, indexed_library: AppContainer, admin: dict[str, str]
) -> None:
    app = indexed_library
    build(app.semantic)
    assert client.get("/api/system/info").json()["ai"]["semantic"] is False  # 还没有聊天模型
    provider_id = app.ai.roles()["embedding"].provider_id  # type: ignore[union-attr]
    client.put(
        f"{ADMIN}/ai/roles",
        json={"chat": {"provider_id": provider_id, "model": "c"}},
        headers=fresh_admin(client),
    )
    assert client.get("/api/system/info").json()["ai"] == {
        "chat": True,
        "ask_search": True,
        "semantic": True,
    }

    app.database._vector_loadable = False

    assert app.semantic.status().state == "unavailable"
    assert app.semantic.sync() == 0
    assert client.get("/api/system/info").json()["ai"] == {
        "chat": True,
        "ask_search": True,
        "semantic": False,
    }


def test_status_and_rebuild_endpoints(
    client: TestClient, indexed_library: AppContainer, admin: dict[str, str]
) -> None:
    app = indexed_library
    status = client.get(f"{ADMIN}/ai/semantic", headers=fresh_admin(client)).json()
    assert status == {"state": "building", "model": "bge", "total": 3, "indexed": 0, "error": None}
    build(app.semantic)
    assert (
        client.get(f"{ADMIN}/ai/semantic", headers=fresh_admin(client)).json()["state"] == "ready"
    )
    assert (
        client.post(f"{ADMIN}/ai/semantic/rebuild", headers=fresh_admin(client)).status_code == 204
    )
    assert client.get(f"{ADMIN}/ai/semantic", headers=fresh_admin(client)).json()["indexed"] == 0
    assert build(app.semantic) == 3
