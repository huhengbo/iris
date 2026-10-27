from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.auth.users import User, UserRepository, verify_password
from app.clock import Clock, to_iso
from app.db import Database

_TREE_CACHE_TTL = 3.0
_TREE_CACHE_LIMIT = 128


@dataclass(frozen=True)
class WebDavInfo:
    enabled: bool
    username: str


class WebDavService:
    """每个用户的 WebDAV 开关，默认开启；认证直接复用登录账号和密码。"""

    def __init__(
        self, database: Database, clock: Clock, *, monotonic: Callable[[], float] = time.monotonic
    ) -> None:
        self._database = database
        self._clock = clock
        self._monotonic = monotonic
        # 缓存跟着服务实例走而不是模块全局，每个应用（和每个测试）各用各的
        self._trees: dict[tuple[str, int, int], tuple[float, Any]] = {}
        self._trees_lock = threading.Lock()

    def info(self, user: User) -> WebDavInfo:
        with self._database.read_session() as connection:
            return self._info(connection, user)

    def set_enabled(self, user: User, enabled: bool) -> WebDavInfo:
        now = to_iso(self._clock.now())
        with self._database.session() as connection:
            connection.execute(
                """
                INSERT INTO webdav_setting (user_id, enabled, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (user.id, int(enabled), now),
            )
            return self._info(connection, user)

    def authenticate(self, username: str, password: str) -> User | None:
        if not username or not password:
            return None
        # 每个 WebDAV 请求都要认证：查库用只读连接，耗时的密码校验放在连接之外
        with self._database.read_session() as connection:
            credentials = UserRepository(connection, self._clock).find_credentials(username)
            if credentials is None:
                return None
            user, password_hash = credentials
            if user.disabled or not _enabled(connection, user):
                return None
        return user if verify_password(password_hash, password) else None

    def tree[T](self, user: User, build: Callable[[sqlite3.Connection], T]) -> T:
        """短时间复用目录树，避免播放器的重复 GET/HEAD 每次扫描整库。"""
        key = (str(Path(self._database.path).resolve()), user.id, user.token_version)
        now = self._monotonic()
        with self._trees_lock:
            cached = self._trees.get(key)
            if cached is not None and now - cached[0] < _TREE_CACHE_TTL:
                result: T = cached[1]
                return result
        with self._database.read_session() as connection:
            tree = build(connection)
        with self._trees_lock:
            cutoff = now - _TREE_CACHE_TTL
            for cache_key, (created_at, _) in list(self._trees.items()):
                if created_at < cutoff:
                    self._trees.pop(cache_key, None)
            if len(self._trees) >= _TREE_CACHE_LIMIT:
                self._trees.pop(next(iter(self._trees)))
            self._trees[key] = (now, tree)
        return tree

    def invalidate(self) -> None:
        """字幕等节点变化后立刻丢掉目录树缓存，不用等 TTL 过期。"""
        with self._trees_lock:
            self._trees.clear()

    @staticmethod
    def _info(connection: sqlite3.Connection, user: User) -> WebDavInfo:
        return WebDavInfo(enabled=_enabled(connection, user), username=user.username)


def _enabled(connection: sqlite3.Connection, user: User) -> bool:
    # 没有记录说明用户从没动过开关，按默认开启处理
    row = connection.execute(
        "SELECT enabled FROM webdav_setting WHERE user_id = ?", (user.id,)
    ).fetchone()
    return row is None or bool(row["enabled"])
