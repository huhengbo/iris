from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.db import Database, Migration, apply_migrations, load_migrations
from app.main import create_app
from tests.conftest import FakeClock


def test_health_reports_schema_version(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["schema_version"] == max(m.version for m in load_migrations())


def test_unknown_api_path_returns_json_404_not_web_page(client: TestClient) -> None:
    response = client.get("/api/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_validation_error_does_not_echo_input(config: AppConfig, clock: FakeClock) -> None:
    app = create_app(config, clock)

    @app.post("/api/_echo")
    def _echo(payload: dict[str, int]) -> dict[str, int]:
        return payload

    # 兜底路由先注册，测试路由需要排到它前面
    app.router.routes.insert(0, app.router.routes.pop())
    with TestClient(app) as client:
        response = client.post("/api/_echo", json={"password": "secret-value"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "secret-value" not in response.text


def test_migrations_are_idempotent_across_restarts(config: AppConfig, clock: FakeClock) -> None:
    create_app(config, clock)
    create_app(config, clock)

    with Database(config.database_path).session() as connection:
        rows = connection.execute("SELECT version FROM schema_migration").fetchall()
    assert [r[0] for r in rows] == [m.version for m in load_migrations()]


def test_failed_migration_rolls_back_completely(tmp_path: Path, clock: FakeClock) -> None:
    database = Database(tmp_path / "db.sqlite3")
    broken = [
        Migration(1, "ok", "CREATE TABLE a (id INTEGER);"),
        Migration(2, "broken", "CREATE TABLE b (id INTEGER); INSERT INTO missing VALUES (1);"),
    ]

    with pytest.raises(sqlite3.OperationalError), database.session() as connection:
        apply_migrations(connection, broken, clock)

    with database.session() as connection:
        tables = {
            r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert tables.isdisjoint({"a", "b", "schema_migration"})


@pytest.mark.skipif(os.geteuid() == 0, reason="root 不受目录权限限制，无法构造只读目录")
def test_read_only_data_dir_fails_with_a_hint(tmp_path: Path, clock: FakeClock) -> None:
    # Docker 自动创建的挂载目录属于 root，容器用户写不进去
    data = tmp_path / "data"
    data.mkdir(mode=0o555)
    try:
        with pytest.raises(RuntimeError, match="chown"):
            Database(data / "db.sqlite3").migrate(clock)
    finally:
        data.chmod(0o755)


def test_foreign_keys_are_enforced(tmp_path: Path) -> None:
    with Database(tmp_path / "db.sqlite3").session() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_read_session_does_not_serialize_writer(tmp_path: Path) -> None:
    database = Database(tmp_path / "db.sqlite3")
    with database.session() as connection:
        connection.execute("CREATE TABLE value (id INTEGER PRIMARY KEY)")

    entered = threading.Event()
    release = threading.Event()
    writer_done = threading.Event()
    errors: list[BaseException] = []

    def read() -> None:
        try:
            with database.read_session() as connection:
                entered.set()
                assert release.wait(2)
                connection.execute("SELECT COUNT(*) FROM value").fetchone()
        except BaseException as error:
            errors.append(error)

    def write() -> None:
        try:
            assert entered.wait(2)
            with database.session() as connection:
                connection.execute("INSERT INTO value DEFAULT VALUES")
            writer_done.set()
        except BaseException as error:
            errors.append(error)

    reader = threading.Thread(target=read)
    writer = threading.Thread(target=write)
    reader.start()
    writer.start()
    assert writer_done.wait(1)
    release.set()
    reader.join(timeout=2)
    writer.join(timeout=2)
    assert not errors


def test_serves_web_client_with_spa_fallback(config: AppConfig, clock: FakeClock) -> None:
    config.static_dir.mkdir(parents=True)
    (config.static_dir / "index.html").write_text("<html>iris</html>", encoding="utf-8")
    (config.static_dir / "assets").mkdir()
    (config.static_dir / "assets" / "logo.txt").write_text("logo", encoding="utf-8")
    (config.static_dir.parent / "outside.txt").write_text("private", encoding="utf-8")

    with TestClient(create_app(config, clock)) as client:
        assert client.get("/").text == "<html>iris</html>"
        assert client.get("/library/3").text == "<html>iris</html>"
        assert client.get("/assets/logo.txt").text == "logo"
        assert "private" not in client.get("/..%2Foutside.txt").text
        assert client.get("/play/unknown").status_code == 404


def test_web_assets_are_revalidated_after_redeploy(config: AppConfig, clock: FakeClock) -> None:
    # 图标字体等资源文件名不带哈希，被缓存的旧版本会和新代码对不上
    config.static_dir.mkdir(parents=True)
    (config.static_dir / "index.html").write_text("<html>iris</html>", encoding="utf-8")
    fonts = config.static_dir / "assets" / "fonts"
    fonts.mkdir(parents=True)
    (fonts / "MaterialIcons-Regular.otf").write_bytes(b"v1")
    (fonts / "NotoSansCJKsc-Regular.v2.otf").write_bytes(b"font")

    with TestClient(create_app(config, clock)) as client:
        first = client.get("/assets/fonts/MaterialIcons-Regular.otf")
        assert first.headers["cache-control"] == "no-cache"
        font = client.get("/assets/fonts/NotoSansCJKsc-Regular.v2.otf")
        assert font.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert font.headers["content-type"] == "font/otf"
        etag = first.headers["etag"]
        assert (
            client.get(
                "/assets/fonts/MaterialIcons-Regular.otf", headers={"If-None-Match": etag}
            ).status_code
            == 304
        )
        (fonts / "MaterialIcons-Regular.otf").write_bytes(b"v2-new")
        refreshed = client.get(
            "/assets/fonts/MaterialIcons-Regular.otf", headers={"If-None-Match": etag}
        )
        assert refreshed.status_code == 200
        assert refreshed.content == b"v2-new"


def test_missing_web_build_still_serves_api(client: TestClient) -> None:
    assert client.get("/").status_code == 404
    assert client.get("/api/health").status_code == 200
