"""结构迁移：全新建库和执行 0002 后的来源中立结构。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from iris_plugin_sdk import ResourceListing

from app.db import Database, apply_migrations, load_migrations
from app.library.repository import FolderRef
from tests.conftest import FakeClock

NOW = "2026-01-01T00:00:00+00:00"


def test_fresh_database_gets_the_baseline_schema(tmp_path: Path, clock: FakeClock) -> None:
    database = Database(tmp_path / "fresh.db")
    database.migrate(clock)

    raw = sqlite3.connect(database.path)
    tables = {row[0] for row in raw.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"library_hub", "library_plugin", "user_favorite", "user_watchlist"} <= tables
    assert {"online_subtitle", "media_item_external_id", "media_item_source"} <= tables
    assert {"plugin_repository", "plugin_install"} <= tables
    assert "home_hub" not in tables
    assert raw.execute(
        "SELECT enabled, interval FROM scheduled_task WHERE key = 'download_subtitles'"
    ).fetchone() == (1, 86400)
    raw.close()


def test_migrations_are_baseline_then_source_neutral() -> None:
    assert [(m.version, m.name) for m in load_migrations()] == [
        (1, "baseline"),
        (2, "source_neutral"),
        (3, "source_busy"),
        (4, "plugin_repositories"),
        (5, "collection_artwork"),
        (6, "admin_library_access"),
    ]


def _columns(raw: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in raw.execute(f"PRAGMA table_info({table})")}


def _settings(raw: sqlite3.Connection) -> dict[str, str]:
    return {str(row[0]): str(row[1]) for row in raw.execute("SELECT key, value FROM setting")}


def test_source_neutral_migration_renames_and_converts_scope_keys(
    tmp_path: Path, clock: FakeClock
) -> None:
    database = Database(tmp_path / "upgrade.db")
    baseline = [m for m in load_migrations() if m.version == 1]
    connection = sqlite3.connect(database.path)
    try:
        apply_migrations(connection, baseline, clock)
        connection.executescript(
            f"""
            INSERT INTO plugin (id, name, version, updated_at)
            VALUES ('source.115', '115 网盘', '1', '{NOW}'),
                   ('source.local', '本地存储', '1', '{NOW}'),
                   ('builtin.local-subtitles', '本地外挂字幕', '1', '{NOW}');

            INSERT INTO resource_source
                (id, plugin_id, name, config, status, created_at, updated_at)
            VALUES (1, 'source.115', '115 网盘', '{{}}', 'unknown', '{NOW}', '{NOW}'),
                   (2, 'source.local', '本地存储', '{{}}', 'unknown', '{NOW}', '{NOW}');

            INSERT INTO resource_root (id, source_id, locator, display_path, created_at)
            VALUES (1, 1, '12', '/影视/电影', '{NOW}'),
                   (2, 2, 'movies', '/movies', '{NOW}'),
                   (3, 2, 'a/b', '/a/b', '{NOW}');

            INSERT INTO library (id, name, kind, sort_order, created_at, updated_at)
            VALUES (1, '电影', 'movie', 1, '{NOW}', '{NOW}');

            INSERT INTO user
                (id, username, password_hash, role, created_at, updated_at)
            VALUES (1, 'admin', 'hash', 'admin', '{NOW}', '{NOW}');

            INSERT INTO library_folder
                (id, library_id, cid, cid_path, path, created_at, resource_root_id)
            VALUES (1, 1, '12', '/0/12/', '/影视/电影', '{NOW}', 1),
                   (2, 1, 'resource:2', '/source/2/movies/', '/movies', '{NOW}', 2),
                   (3, 1, 'resource:3', '/source/2/a/b/', '/a/b', '{NOW}', 3);

            INSERT INTO media_item
                (id, library_id, kind, provider_id, remote_id, title, scraped_at, added_at)
            VALUES (1, 1, 'movie', 'tmdb', '1', '电影', '{NOW}', '{NOW}'),
                   (2, 1, 'movie', 'tmdb', '2', '另一部', '{NOW}', '{NOW}');

            INSERT INTO playable (id, media_item_id, created_at) VALUES (1, 1, '{NOW}');

            INSERT INTO playback_state
                (user_id, playable_id, position, duration, updated_at)
            VALUES (1, 1, 42, 100, '{NOW}');

            INSERT INTO cloud_file
                (id, folder_id, rel_path, kind, state, file_id, pickcode, size, sha1,
                 created_at, updated_at)
            VALUES (1, 1, '电影.mkv', 'video', 'active', '115-file-id', '115-pickcode',
                    100, 'sha1-115', '{NOW}', '{NOW}'),
                   (2, 2, 'local.mkv', 'video', 'active', 'local-file-id', 'local-pickcode',
                    200, 'sha1-local', '{NOW}', '{NOW}');

            INSERT INTO version (id, playable_id, cloud_file_id, label, created_at)
            VALUES (1, 1, 1, '正片', '{NOW}');

            INSERT INTO subtitle (id, version_id, cloud_file_id, source, label)
            VALUES (1, 1, 1, 'external', '简体中文');

            INSERT INTO media_info (cloud_file_id, size, sha1, reported_at)
            VALUES (1, 100, 'sha1-115', '{NOW}');

            INSERT INTO plugin_secret (plugin_id, name, value, updated_at)
            VALUES ('source.115', 'cookie', 'encrypted', '{NOW}');

            UPDATE plugin SET config = '{{"api_key_configured": true}}'
            WHERE id = 'source.115';

            INSERT INTO setting (key, value, is_secret, updated_at)
            VALUES ('cloud115.cookie', 'encrypted', 1, '{NOW}'),
                   ('cloud115.breaker', '{{}}', 0, '{NOW}'),
                   ('cloud115.breaker.app', '{{}}', 0, '{NOW}'),
                   ('life.enabled', '1', 0, '{NOW}'),
                   ('life.interval', '300', 0, '{NOW}'),
                   ('life.state', '{{}}', 0, '{NOW}'),
                   ('tmdb.key', 'encrypted', 1, '{NOW}'),
                   ('tmdb.proxy', 'http://proxy', 0, '{NOW}'),
                   ('tmdb.validation', '{{}}', 0, '{NOW}'),
                   ('plugin.proxy', 'http://global-proxy', 0, '{NOW}'),
                   ('server.id', 'server-id', 0, '{NOW}');
            """
        )
        connection.commit()
    finally:
        connection.close()

    database.migrate(clock)

    raw = sqlite3.connect(database.path)
    try:
        tables = {
            str(row[0])
            for row in raw.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "media_file" in tables
        assert "cloud_file" not in tables
        assert _columns(raw, "media_file") >= {
            "identity",
            "playback_locator",
            "size",
            "sha1",
            "state",
        }
        assert _columns(raw, "media_file").isdisjoint({"file_id", "pickcode"})
        assert _columns(raw, "library_folder") == {
            "id",
            "library_id",
            "scope_key",
            "path",
            "created_at",
            "resource_root_id",
        }
        for table in ("version", "subtitle", "media_info"):
            assert "media_file_id" in _columns(raw, table)
            assert "cloud_file_id" not in _columns(raw, table)

        indexes = {
            str(row[0])
            for row in raw.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'media_file'"
            )
        }
        assert {
            "media_file_state",
            "media_file_sha1",
            "media_file_work",
            "media_file_folder_state_kind",
            "media_file_identity",
        } <= indexes
        assert not any(name.startswith("cloud_file_") for name in indexes)

        assert raw.execute(
            "SELECT identity, playback_locator, size, sha1 FROM media_file ORDER BY id"
        ).fetchall() == [
            ("115-file-id", "115-pickcode", 100, "sha1-115"),
            ("local-file-id", "local-pickcode", 200, "sha1-local"),
        ]
        assert raw.execute("SELECT media_file_id FROM version WHERE id = 1").fetchone() == (1,)
        assert raw.execute("SELECT media_file_id FROM subtitle WHERE id = 1").fetchone() == (1,)
        assert raw.execute("SELECT media_file_id FROM media_info").fetchone() == (1,)
        assert raw.execute(
            "SELECT position, duration FROM playback_state WHERE user_id = 1 AND playable_id = 1"
        ).fetchone() == (42, 100)

        # 和新代码 FolderRef.of(source_id, browse(locator)) 生成的值一致：
        # 115 去掉根目录 0；本地来源用逐级累积的相对路径（a/b → a/a/b）
        assert raw.execute(
            "SELECT id, scope_key, path FROM library_folder ORDER BY id"
        ).fetchall() == [
            (1, "/source/1/12/", "/影视/电影"),
            (2, "/source/2/movies/", "/movies"),
            (3, "/source/2/a/a/b/", "/a/b"),
        ]

        assert "plugin_secret" not in tables
        assert "config" not in _columns(raw, "plugin")
        assert [row[0] for row in raw.execute("SELECT id FROM plugin ORDER BY id")] == [
            "source.115",
            "source.local",
        ]

        settings = _settings(raw)
        assert {
            "cloud115.cookie",
            "cloud115.breaker",
            "cloud115.breaker.app",
            "life.enabled",
            "life.interval",
            "life.state",
            "tmdb.key",
            "tmdb.proxy",
            "tmdb.validation",
        }.isdisjoint(settings)
        assert settings["plugin.proxy"] == "http://global-proxy"
        assert settings["server.id"] == "server-id"

        # 约束和触发器的 SQL 也要跟着改名，不能在 CHECK 或外键里留下旧列名
        schema_sql = "\n".join(
            str(row[0] or "")
            for row in raw.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
            )
        )
        ddl = "\n".join(
            line for line in schema_sql.splitlines() if not line.strip().startswith("--")
        )
        assert "cloud_file" not in ddl.lower()
        assert "cid_path" not in ddl.lower()

        assert raw.execute("PRAGMA foreign_key_check").fetchall() == []
        assert raw.execute(
            "SELECT version, name FROM schema_migration ORDER BY version"
        ).fetchall() == [
            (1, "baseline"),
            (2, "source_neutral"),
            (3, "source_busy"),
            (4, "plugin_repositories"),
            (5, "collection_artwork"),
            (6, "admin_library_access"),
        ]
    finally:
        raw.close()


def test_migrated_scope_keys_match_provider_listings(tmp_path: Path, clock: FakeClock) -> None:
    """迁移算出的 scope_key 必须和 FolderRef.of(browse(locator)) 对同一目录生成的值相同。"""
    cid = "12"
    cid_path = "/0/12/"

    database = Database(tmp_path / "scope.db")
    baseline = [m for m in load_migrations() if m.version == 1]
    connection = sqlite3.connect(database.path)
    try:
        apply_migrations(connection, baseline, clock)
        connection.executescript(
            f"""
            INSERT INTO plugin (id, name, version, updated_at)
            VALUES ('source.115', '115 网盘', '1', '{NOW}'),
                   ('source.local', '本地存储', '1', '{NOW}');

            INSERT INTO resource_source
                (id, plugin_id, name, config, status, created_at, updated_at)
            VALUES (1, 'source.115', '115 网盘', '{{}}', 'unknown', '{NOW}', '{NOW}'),
                   (2, 'source.local', '本地存储', '{{}}', 'unknown', '{NOW}', '{NOW}');

            INSERT INTO resource_root (id, source_id, locator, display_path, created_at)
            VALUES (1, 1, '{cid}', '/影视/电影', '{NOW}'),
                   (2, 2, 'movies', '/movies', '{NOW}'),
                   (3, 2, 'a/b', '/a/b', '{NOW}');

            INSERT INTO library (id, name, kind, sort_order, created_at, updated_at)
            VALUES (1, '电影', 'movie', 1, '{NOW}', '{NOW}');

            INSERT INTO library_folder
                (id, library_id, cid, cid_path, path, created_at, resource_root_id)
            VALUES (1, 1, '{cid}', '{cid_path}', '/影视/电影', '{NOW}', 1),
                   (2, 1, 'resource:2', '/source/2/movies/', '/movies', '{NOW}', 2),
                   (3, 1, 'resource:3', '/source/2/a/b/', '/a/b', '{NOW}', 3);
            """
        )
        connection.commit()
    finally:
        connection.close()

    database.migrate(clock)
    raw = sqlite3.connect(database.path)
    try:
        migrated = {
            int(row[0]): str(row[1])
            for row in raw.execute("SELECT id, scope_key FROM library_folder")
        }
    finally:
        raw.close()

    local_listings = {
        2: ResourceListing("movies", "/movies", (), (("movies", "movies"),)),
        3: ResourceListing("a/b", "/a/b", (), (("a", "a"), ("a/b", "b"))),
    }
    for folder_id, listing in local_listings.items():
        assert migrated[folder_id] == FolderRef.of(2, listing).scope_key

    cloud_listing = ResourceListing("12", "/影视/电影", (), (("12", "电影"),))
    assert migrated[1] == FolderRef.of(1, cloud_listing).scope_key


def test_source_busy_migration_renames_wait_reasons(tmp_path: Path, clock: FakeClock) -> None:
    database = Database(tmp_path / "waiting.db")
    connection = sqlite3.connect(database.path)
    try:
        apply_migrations(connection, [m for m in load_migrations() if m.version < 3], clock)
        connection.executescript(
            f"""
            INSERT INTO sync_job
                (id, trigger, scope, status, phase, stats, waiting_reason, started_at)
            VALUES (1, 'manual', 'all', 'succeeded', 'done', '{{}}', 'export_busy', '{NOW}'),
                   (2, 'manual', 'all', 'succeeded', 'done', '{{}}', 'export_wait', '{NOW}'),
                   (3, 'manual', 'all', 'succeeded', 'done', '{{}}', 'rate_limited', '{NOW}'),
                   (4, 'manual', 'all', 'succeeded', 'done', '{{}}', NULL, '{NOW}');
            """
        )
        connection.commit()
    finally:
        connection.close()

    database.migrate(clock)

    raw = sqlite3.connect(database.path)
    try:
        reasons = [row[0] for row in raw.execute("SELECT waiting_reason FROM sync_job ORDER BY id")]
    finally:
        raw.close()
    assert reasons == ["source_busy", "source_busy", "rate_limited", None]


def test_admin_library_access_migration_keeps_existing_admins_seeing_everything(
    tmp_path: Path, clock: FakeClock
) -> None:
    database = Database(tmp_path / "admins.db")
    connection = sqlite3.connect(database.path)
    try:
        apply_migrations(connection, [m for m in load_migrations() if m.version < 6], clock)
        connection.execute(
            "INSERT INTO user (username, password_hash, role, created_at, updated_at) "
            "VALUES ('root', 'x', 'admin', ?, ?), ('bob', 'x', 'user', ?, ?)",
            (NOW, NOW, NOW, NOW),
        )
        connection.commit()
    finally:
        connection.close()

    database.migrate(clock)

    raw = sqlite3.connect(database.path)
    try:
        flags = dict(raw.execute("SELECT username, all_libraries FROM user"))
    finally:
        raw.close()
    assert flags == {"root": 1, "bob": 0}
