"""数据库并发约定：读请求和认证不排队等写锁，写请求仍然串行，恢复备份不会和读者互相等待。"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from anyio import to_thread
from fastapi.testclient import TestClient

from app.db import _SESSION_LOCK, Database
from app.main import REQUEST_THREADS
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME
from tests.fake_tmdb import FakeTmdb, image_bytes
from tests.test_browse import build_catalog, find
from tests.test_sync import fresh_admin

WAIT = 5.0
WEBHOOKS = "/api/admin/webhooks"
HOOK = {"url": "https://example.com/hook", "events": ["alert"]}


@contextmanager
def write_lock_held() -> Iterator[None]:
    """另一个线程占着全局写锁，模拟同步、刮削等后台任务正在写库。"""
    acquired = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with _SESSION_LOCK:
            acquired.set()
            release.wait(WAIT * 2)

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    assert acquired.wait(WAIT)
    try:
        yield
    finally:
        release.set()
        holder.join(WAIT)


def run_in_thread(call: Callable[[], Any]) -> tuple[threading.Thread, list[Any]]:
    result: list[Any] = []
    thread = threading.Thread(target=lambda: result.append(call()), daemon=True)
    thread.start()
    return thread, result


def database_of(client: TestClient) -> Database:
    database: Database = client.app.state.container.database  # type: ignore[attr-defined]
    return database


def test_reads_and_auth_do_not_wait_for_the_write_lock(
    client: TestClient, admin: dict[str, str]
) -> None:
    paths = [
        "/api/admin/alerts?active=true&limit=200",
        "/api/admin/libraries",
        "/api/admin/users",
        "/api/admin/tasks",
        "/api/auth/me",
        "/api/me/sessions",
        "/api/health",
        "/api/libraries",
    ]
    with write_lock_held():
        thread, result = run_in_thread(lambda: [client.get(p, headers=admin) for p in paths])
        thread.join(WAIT)
        assert not thread.is_alive(), "读请求被写锁挡住了"
    assert [r.status_code for r in result[0]] == [200] * len(paths)


def test_rejected_credentials_do_not_wait_for_the_write_lock(client: TestClient) -> None:
    with write_lock_held():
        thread, result = run_in_thread(
            lambda: (
                client.post(
                    "/api/auth/login",
                    json={"username": ADMIN_USERNAME, "password": "wrong-password"},
                ),
                client.get("/api/admin/alerts", headers={"Authorization": "Bearer nope"}),
            )
        )
        thread.join(WAIT)
        assert not thread.is_alive(), "校验失败的请求被写锁挡住了"
    bad_login, bad_token = result[0]
    assert bad_login.status_code == 401
    assert bad_token.status_code == 401


def test_writes_still_queue_behind_the_write_lock(
    client: TestClient, admin: dict[str, str]
) -> None:
    with write_lock_held():
        thread, result = run_in_thread(lambda: client.post(WEBHOOKS, headers=admin, json=HOOK))
        thread.join(0.5)
        assert thread.is_alive(), "写请求应该排队等写锁"
    thread.join(WAIT)
    assert result[0].status_code == 201


def test_login_still_issues_a_session(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    assert client.get("/api/auth/me", headers=headers).json()["username"] == ADMIN_USERNAME
    assert client.get("/api/me/sessions", headers=headers).json() != []


def test_read_sessions_reject_writes(client: TestClient) -> None:
    database = database_of(client)
    with (
        database.read_session() as connection,
        pytest.raises(sqlite3.OperationalError, match="readonly"),
    ):
        connection.execute("INSERT INTO setting(key, value, updated_at) VALUES ('a','b','c')")


def test_backup_does_not_take_the_write_lock(client: TestClient, tmp_path: Path) -> None:
    database = database_of(client)
    target = tmp_path / "copy.db"
    with write_lock_held():
        thread, _ = run_in_thread(lambda: database.backup_to(target))
        thread.join(WAIT)
        assert not thread.is_alive(), "备份被写锁挡住了"
    with sqlite3.connect(target) as copy:
        assert copy.execute("SELECT COUNT(*) FROM user").fetchone()[0] == 1


def test_restore_writes_back_in_place_while_readers_are_open(
    client: TestClient, tmp_path: Path
) -> None:
    """恢复备份不换文件、不等读者：已打开的读者继续看到自己的快照，新读者看到恢复后的内容。"""
    database = database_of(client)
    backup = tmp_path / "backup.db"
    database.backup_to(backup)
    with database.session() as connection:
        connection.execute("INSERT INTO setting(key, value, updated_at) VALUES ('x','1','t')")

    with database.read_session() as reader:
        assert reader.execute("SELECT COUNT(*) FROM setting WHERE key='x'").fetchone()[0] == 1
        thread, _ = run_in_thread(lambda: database.restore_from(backup))
        thread.join(WAIT)
        assert not thread.is_alive(), "恢复备份在等读者"
        # 读者的快照不变
        assert reader.execute("SELECT COUNT(*) FROM setting WHERE key='x'").fetchone()[0] == 1
    with database.read_session() as fresh:
        assert fresh.execute("SELECT COUNT(*) FROM setting WHERE key='x'").fetchone()[0] == 0


def test_write_lock_can_be_released_from_another_thread(client: TestClient) -> None:
    """FastAPI 把依赖的进入和退出分在两个线程执行：在一个线程拿锁、另一个线程释放必须可行。"""
    database = database_of(client)
    manager = database.session()
    thread, _ = run_in_thread(manager.__enter__)
    thread.join(WAIT)
    assert not thread.is_alive()
    thread, _ = run_in_thread(lambda: manager.__exit__(None, None, None))
    thread.join(WAIT)
    assert not thread.is_alive()
    with database.session() as connection:  # 锁已经释放
        connection.execute("SELECT 1")


def test_concurrent_requests_do_not_wedge_the_write_lock(
    client: TestClient, admin: dict[str, str]
) -> None:
    errors: list[tuple[str, int]] = []

    def worker(i: int) -> None:
        for j in range(8):
            created = client.post(
                WEBHOOKS, headers=admin, json={**HOOK, "url": f"https://example.com/{i}-{j}"}
            )
            listed = client.get(WEBHOOKS, headers=admin)
            for kind, response, expected in (("w", created, 201), ("r", listed, 200)):
                if response.status_code != expected:
                    errors.append((kind, response.status_code))

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    assert not any(thread.is_alive() for thread in threads), "并发请求把写锁卡死了"
    assert errors == []


# ---- 图片设置：网络和上传不在写事务里，结果和以前一致 ----


def multipart(data: bytes) -> tuple[bytes, dict[str, str]]:
    boundary = "irisboundary"
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.png"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode()
        + data
        + f"\r\n--{boundary}--\r\n".encode()
    )
    return body, {"content-type": f"multipart/form-data; boundary={boundary}"}


def test_artwork_provider_upload_and_errors(
    client: TestClient, admin: dict[str, str], cloud115: Any, tmdb: FakeTmdb
) -> None:
    build_catalog(client, admin, cloud115, tmdb)
    headers = fresh_admin(client)
    item_id = find(client, "流浪地球")["id"]
    url = f"/api/admin/items/{item_id}/artwork/poster"

    before = client.get(url, headers=headers)
    assert before.status_code == 200
    assert before.json()["kind"] == "poster"

    chosen = client.put(url, headers=headers, json={"source": "provider", "value": "/chosen.jpg"})
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["current"] == "/chosen.jpg"
    assert "/chosen.jpg" in chosen.json()["candidates"]
    assert client.get(url, headers=headers).json()["current"] == "/chosen.jpg"
    fields = client.get(f"/api/admin/items/{item_id}/metadata", headers=headers).json()
    assert any(f["field"] == "poster_path" and f["locked"] for f in fields)

    body, content = multipart(image_bytes(png=True))
    uploaded = client.put(url + "/upload", headers={**headers, **content}, content=body)
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["current"].startswith("upload:")
    assert client.get(url, headers=headers).json()["current"] == uploaded.json()["current"]

    broken, content = multipart(b"not an image")
    rejected = client.put(url + "/upload", headers={**headers, **content}, content=broken)
    assert (rejected.status_code, rejected.json()["error"]["code"]) == (400, "invalid_artwork")

    assert (
        client.put(
            f"/api/admin/items/{item_id}/artwork/nope",
            headers=headers,
            json={"source": "provider", "value": "/x.jpg"},
        ).json()["error"]["code"]
        == "invalid_artwork_kind"
    )
    assert (
        client.put(
            url, headers=headers, json={"source": "url", "value": "http://localhost/x.png"}
        ).json()["error"]["code"]
        == "invalid_artwork_url"
    )
    missing = client.put(
        "/api/admin/items/999999/artwork/poster",
        headers=headers,
        json={"source": "provider", "value": "/x.jpg"},
    )
    assert missing.status_code == 404


def test_artwork_set_does_not_hold_the_write_lock_while_downloading(
    client: TestClient, admin: dict[str, str], cloud115: Any, tmdb: FakeTmdb
) -> None:
    build_catalog(client, admin, cloud115, tmdb)
    headers = fresh_admin(client)
    item_id = find(client, "流浪地球")["id"]
    url = f"/api/admin/items/{item_id}/artwork/poster"
    tmdb.image_delay = 1.0
    thread, result = run_in_thread(
        lambda: client.put(url, headers=headers, json={"source": "provider", "value": "/s.jpg"})
    )
    try:
        # 图片还在下载，别的写请求不该被它挡住
        time.sleep(0.2)
        other, other_result = run_in_thread(
            lambda: client.post(WEBHOOKS, headers=headers, json=HOOK)
        )
        other.join(WAIT)
        assert not other.is_alive(), "下载图片期间占着写锁"
        assert other_result[0].status_code == 201
    finally:
        tmdb.image_delay = 0
    thread.join(WAIT)
    assert result[0].status_code == 200


def test_no_get_endpoint_fails_on_a_read_only_connection(
    client: TestClient, admin: dict[str, str], cloud115: Any, tmdb: FakeTmdb
) -> None:
    """GET 接口都应当只读。有数据的目录下逐个请求，误写会在只读连接上变成 500。"""
    build_catalog(client, admin, cloud115, tmdb)
    headers = fresh_admin(client)
    schema = client.get("/api/openapi.json").json()
    failures = []
    checked = 0
    for path, operations in schema["paths"].items():
        if "get" not in operations:
            continue
        url = re.sub(r"\{[^}]+\}", "1", path)
        response = client.get(url if url.startswith("/api/") else f"/api{url}", headers=headers)
        checked += 1
        if response.status_code >= 500:
            failures.append((path, response.status_code, response.text[:200]))
    assert checked > 50
    assert failures == []


def test_request_thread_pool_is_larger_than_the_default(client: TestClient) -> None:
    async def tokens() -> float:
        return float(to_thread.current_default_thread_limiter().total_tokens)

    assert REQUEST_THREADS > 40
    assert client.portal is not None
    assert client.portal.call(tokens) == REQUEST_THREADS
