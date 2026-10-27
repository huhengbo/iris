from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.db import Database
from app.library.metadata_providers import library_provider_id, save_library_provider
from app.plugins import library_plugins
from app.plugins.library_plugins import Entry
from tests.conftest import FakeClock


@pytest.fixture
def connection(tmp_path: Path, clock: FakeClock) -> Iterator[sqlite3.Connection]:
    database = Database(tmp_path / "iris.db")
    database.migrate(clock)
    with database.session() as connection:
        connection.execute(
            "INSERT INTO library (id, name, kind, sort_order, created_at, updated_at) "
            "VALUES (1, 'l', 'movie', 0, 'x', 'x')"
        )
        connection.executemany(
            "INSERT INTO plugin (id, name, version, sdk_version, provides, requires, trust, "
            "built_in, enabled, updated_at) VALUES (?, ?, '1', '>=1.0,<2.0', ?, '[]', "
            "'built_in', 1, ?, 'x')",
            [
                ("builtin.tmdb", "TMDB", '["metadata.search","metadata.details","image.fetch"]', 1),
                ("builtin.fanart", "Fanart", '["image.fetch"]', 1),
                ("builtin.assrt", "assrt", '["subtitle.download"]', 1),
            ],
        )
        yield connection


def test_defaults_without_saved_rows(connection: sqlite3.Connection) -> None:
    candidates = ["builtin.fanart", "builtin.tmdb"]
    assert library_plugins.entries(connection, 1, "image", candidates) == [
        Entry("builtin.tmdb", True),
        Entry("builtin.fanart", False),
    ]
    # 字幕默认启用全部下载器
    assert library_plugins.enabled_ids(connection, 1, "subtitle", ["b", "a"]) == ["a", "b"]
    assert library_plugins.first_enabled(connection, 1, "metadata") == "builtin.tmdb"
    assert library_plugins.first_enabled(connection, 1, "subtitle") is None


def test_saved_order_skips_uninstalled_and_appends_new(connection: sqlite3.Connection) -> None:
    library_plugins.replace(
        connection,
        1,
        "subtitle",
        [Entry("gone", True), Entry("b", True), Entry("a", False)],
        "now",
    )
    assert library_plugins.entries(connection, 1, "subtitle", ["a", "b", "c"]) == [
        Entry("b", True),
        Entry("a", False),
        Entry("c", False),
    ]
    assert library_plugins.enabled_ids(connection, 1, "subtitle", ["a", "b", "c"]) == ["b"]
    # 第一个勾选的插件即使已卸载也返回，调用方据此报「没有安装」
    assert library_plugins.first_enabled(connection, 1, "subtitle") == "gone"


def test_defaults_are_chosen_by_capability_not_plugin_id(
    connection: sqlite3.Connection,
) -> None:
    connection.execute("UPDATE plugin SET enabled = 0")
    connection.execute(
        "INSERT INTO plugin (id, name, version, sdk_version, provides, requires, trust, "
        "built_in, enabled, updated_at) VALUES "
        "('example.meta', 'Example', '1', '>=1.0,<2.0', "
        '\'["metadata.search","metadata.details","image.fetch"]\', \'[]\', '
        "'external', 0, 1, 'x')"
    )

    assert library_plugins.default_ids(connection, "metadata") == ("example.meta",)
    assert library_plugins.default_ids(connection, "image") == ("example.meta",)
    assert library_plugins.entries(connection, 1, "metadata", ["example.meta"]) == [
        Entry("example.meta", True)
    ]

    connection.execute("UPDATE plugin SET enabled = 0 WHERE id = 'example.meta'")
    connection.execute("UPDATE plugin SET enabled = 1 WHERE id = 'builtin.fanart'")
    assert library_plugins.default_ids(connection, "metadata") == ()
    assert library_plugins.default_ids(connection, "image") == ("builtin.fanart",)


def test_metadata_provider_alias_moves_plugin_first(connection: sqlite3.Connection) -> None:
    assert library_provider_id(connection, 1) == "builtin.tmdb"
    save_library_provider(connection, 1, "example.meta", "t1")
    assert library_plugins.entries(connection, 1, "metadata", ["builtin.tmdb", "example.meta"]) == [
        Entry("example.meta", True),
        Entry("builtin.tmdb", False),
    ]
    library_plugins.replace(
        connection, 1, "metadata", [Entry("example.meta", True), Entry("builtin.tmdb", True)], "t2"
    )
    save_library_provider(connection, 1, "builtin.tmdb", "t3")
    assert library_plugins.entries(connection, 1, "metadata", ["builtin.tmdb", "example.meta"]) == [
        Entry("builtin.tmdb", True),
        Entry("example.meta", True),
    ]
    assert library_provider_id(connection, 1) == "builtin.tmdb"


@pytest.fixture(autouse=True)
def _extra_builtin_plugins(plugin_env: object) -> None:
    from tests.plugin_env import PluginEnvironment, install_builtin

    assert isinstance(plugin_env, PluginEnvironment)
    install_builtin(plugin_env.root, "builtin.douban")
    install_builtin(plugin_env.root, "builtin.fanart")
    install_builtin(plugin_env.root, "builtin.assrt")
