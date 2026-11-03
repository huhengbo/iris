from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from app.clock import Clock, to_iso

SQLITE_BUSY_TIMEOUT_MS = 60_000
# 全进程唯一的写事务锁。FastAPI 的同步依赖会把「进入」和「退出」分到两个线程池调用里，
# 所以必须用任何线程都能释放的 Lock，不能用 RLock（RLock 只有持有者线程能释放，
# 释放失败后锁会永远留在一个空闲线程手里，之后所有写请求都挂死）。
# 代价是不可重入：写会话里不要再开写会话，等锁最多等 busy_timeout，超时报错而不是挂死。
_SESSION_LOCK = threading.Lock()
logger = logging.getLogger(__name__)
_MIGRATION_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        # sqlite-vec 能否加载：None 还没试过，False 加载失败（之后不再尝试）
        self._vector_loadable: bool | None = None

    def vector_available(self) -> bool:
        """sqlite-vec 扩展能否加载。语义索引和语义召回依赖它，不可用时其他功能不受影响。"""
        if self._vector_loadable is None:
            connection = sqlite3.connect(":memory:")
            try:
                self._load_vector(connection)
            finally:
                connection.close()
        return bool(self._vector_loadable)

    def _load_vector(self, connection: sqlite3.Connection) -> None:
        if self._vector_loadable is False:
            raise sqlite3.OperationalError("sqlite-vec 不可用")
        try:
            import sqlite_vec

            connection.enable_load_extension(True)
            sqlite_vec.load(connection)
            connection.enable_load_extension(False)
        except (ImportError, AttributeError, OSError, sqlite3.Error) as error:
            if self._vector_loadable is None:
                logger.warning("sqlite-vec 加载失败，语义召回不可用：%s", error)
            self._vector_loadable = False
            raise sqlite3.OperationalError("sqlite-vec 不可用") from error
        self._vector_loadable = True

    def connect(self) -> sqlite3.Connection:
        return self._connect(configure_wal=True)

    def _connect(self, *, configure_wal: bool) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 自动提交模式下 PRAGMA 才能生效；事务边界由 session() 显式控制
        connection = sqlite3.connect(self.path, check_same_thread=False, autocommit=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        if configure_wal:
            connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def _write_lock(self) -> Iterator[None]:
        """排队等写事务锁，最多等 busy_timeout；等不到说明有人久占着，或写会话里嵌套了写会话。"""
        if not _SESSION_LOCK.acquire(timeout=SQLITE_BUSY_TIMEOUT_MS / 1000):
            raise sqlite3.OperationalError("database is locked")
        try:
            yield
        finally:
            _SESSION_LOCK.release()

    @contextmanager
    def session(self, *, vector: bool = False) -> Iterator[sqlite3.Connection]:
        """一次工作单元：正常结束提交，异常回滚，最后关闭连接。

        vector 为 True 时加载 sqlite-vec，才能读写 item_vec；加载失败抛出 OperationalError。"""
        # SQLite 同一进程同时只能有一个写事务；同步线程和管理请求并发时，
        # 统一排队比让某个阶段在 busy_timeout 后失败更可靠。
        with self._write_lock():
            connection = self.connect()
            try:
                if vector:
                    self._load_vector(connection)
                connection.execute("BEGIN")
                yield connection
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()

    @contextmanager
    def read_session(self, *, vector: bool = False) -> Iterator[sqlite3.Connection]:
        """只读事务不占用写事务锁，适合浏览接口的多次一致读取。vector 同 session。"""
        connection = self._connect(configure_wal=False)
        try:
            if vector:
                self._load_vector(connection)
            # 读会话不该写：误用时立刻报错，而不是悄悄绕开写锁
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            yield connection
        except BaseException:
            with suppress(sqlite3.Error):
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def migrate(self, clock: Clock) -> int:
        self._check_writable()
        with self._write_lock():
            connection = self.connect()
            try:
                # 重建表（SQLite 改约束的唯一办法）时外键检查必须关闭，否则 DROP TABLE 会级联删除
                # 子表数据。这个 PRAGMA 在事务里不生效，要在 BEGIN 之前设置；提交前再整体校验外键。
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute("BEGIN")
                try:
                    version = apply_migrations(connection, load_migrations(), clock)
                    broken = connection.execute("PRAGMA foreign_key_check").fetchall()
                    if broken:
                        raise RuntimeError(f"迁移后外键校验失败：{[tuple(r) for r in broken[:5]]}")
                    connection.execute("COMMIT")
                except BaseException:
                    connection.execute("ROLLBACK")
                    raise
                return version
            finally:
                connection.close()

    def integrity_check(self) -> str:
        """返回 SQLite 完整性检查结果。只读，WAL 下不阻塞写入。"""
        with self.read_session() as connection:
            return integrity_check(connection)

    def backup_to(self, target: Path) -> None:
        """使用 SQLite 在线备份 API 复制当前数据库，不读取 WAL 文件。

        备份在一个读事务里一次拷完，是一致的快照；WAL 下读者不挡写入，所以不占写锁。
        """
        target.parent.mkdir(parents=True, exist_ok=True)
        source = self._connect(configure_wal=False)
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
            destination.commit()
        finally:
            destination.close()
            source.close()

    def restore_from(self, source_path: Path) -> None:
        """把备份的内容写回当前数据库；调用方应先完成备份文件校验。

        用在线备份 API 直接写进正在使用的数据库，不替换文件、不动 WAL：别的连接看到的要么是
        恢复前、要么是恢复后的完整内容，不需要等读者退出，也就不会和读请求互相等待。
        """
        with self._write_lock():
            source = sqlite3.connect(source_path)
            destination = self.connect()
            try:
                source.backup(destination)
            finally:
                destination.close()
                source.close()

    def _check_writable(self) -> None:
        """启动时检查数据目录可写，否则 SQLite 只会报「unable to open database file」。"""
        directory = self.path.parent
        directory.mkdir(parents=True, exist_ok=True)
        if not os.access(directory, os.W_OK):
            raise RuntimeError(
                f"数据目录 {directory} 不可写（当前 UID {os.getuid()}）。"
                "用 Docker 部署时，Docker 自动创建的挂载目录属于 root，"
                "要先把它交给容器用户，例如 chown 1000:1000 data"
            )


def load_migrations() -> list[Migration]:
    migrations = []
    for entry in resources.files("app.migrations").iterdir():
        match = _MIGRATION_NAME.match(entry.name)
        if match is None:
            continue
        migrations.append(
            Migration(int(match.group(1)), match.group(2), entry.read_text(encoding="utf-8"))
        )
    migrations.sort(key=lambda m: m.version)
    versions = [m.version for m in migrations]
    if len(set(versions)) != len(versions):
        raise RuntimeError(f"Duplicate migration versions: {versions}")
    return migrations


def apply_migrations(
    connection: sqlite3.Connection, migrations: list[Migration], clock: Clock
) -> int:
    """按版本顺序执行未应用的迁移，返回当前结构版本。每个迁移在同一事务内完成。"""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migration (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    applied = {row[0] for row in connection.execute("SELECT version FROM schema_migration")}
    for migration in migrations:
        if migration.version in applied:
            continue
        # 自动提交模式下 executescript 不会隐式提交，迁移仍处于调用方的事务内
        connection.executescript(migration.sql)
        connection.execute(
            "INSERT INTO schema_migration (version, name, applied_at) VALUES (?, ?, ?)",
            (migration.version, migration.name, to_iso(clock.now())),
        )
    return schema_version(connection)


def integrity_check(connection: sqlite3.Connection) -> str:
    """SQLite 完整性检查结果，正常时为 ok。"""
    return str(connection.execute("PRAGMA integrity_check").fetchone()[0])


def schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migration").fetchone()
    return int(row[0])
