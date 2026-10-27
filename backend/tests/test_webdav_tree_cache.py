from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.auth.users import User
from app.auth.webdav import WebDavService
from app.db import Database
from tests.conftest import FakeClock


class Ticker:
    """手动推进的单调时钟，代替 time.monotonic。"""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_user(user_id: int, token_version: int = 0) -> User:
    return User(
        id=user_id,
        username=f"user{user_id}",
        role="user",
        disabled=False,
        token_version=token_version,
        created_at="2026-01-01T00:00:00Z",
    )


@pytest.fixture
def ticker() -> Ticker:
    return Ticker()


@pytest.fixture
def service(tmp_path: Path, clock: FakeClock, ticker: Ticker) -> WebDavService:
    database = Database(tmp_path / "tree.sqlite")
    database.migrate(clock)
    return WebDavService(database, clock, monotonic=ticker)


class Builder:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, connection: sqlite3.Connection) -> int:
        self.calls += 1
        return self.calls


def test_tree_hits_cache_within_ttl_and_expires_after(
    service: WebDavService, ticker: Ticker
) -> None:
    user = make_user(1)
    build = Builder()

    assert service.tree(user, build) == 1
    ticker.now += 2.9
    assert service.tree(user, build) == 1
    assert build.calls == 1

    ticker.now += 0.1
    assert service.tree(user, build) == 2
    assert build.calls == 2


def test_tree_is_isolated_per_user_and_token_version(service: WebDavService) -> None:
    build = Builder()

    assert service.tree(make_user(1), build) == 1
    assert service.tree(make_user(2), build) == 2
    # 令牌版本变了（改密码、被禁用等）就不能再用旧目录树
    assert service.tree(make_user(1, token_version=1), build) == 3
    assert service.tree(make_user(1), build) == 1
    assert build.calls == 3


def test_tree_cache_belongs_to_service_instance(
    tmp_path: Path, clock: FakeClock, ticker: Ticker
) -> None:
    database = Database(tmp_path / "tree.sqlite")
    database.migrate(clock)
    first = WebDavService(database, clock, monotonic=ticker)
    second = WebDavService(database, clock, monotonic=ticker)
    build = Builder()

    first.tree(make_user(1), build)
    second.tree(make_user(1), build)

    assert build.calls == 2


def test_tree_cache_evicts_oldest_entry_when_full(service: WebDavService) -> None:
    build = Builder()
    for user_id in range(1, 130):
        service.tree(make_user(user_id), build)
    assert build.calls == 129

    # 超过 128 条时最早的一条被挤掉，最新的仍命中
    service.tree(make_user(129), build)
    assert build.calls == 129
    service.tree(make_user(1), build)
    assert build.calls == 130
