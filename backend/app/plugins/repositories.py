"""插件仓库：默认仓库对齐、索引缓存和目录。

网络请求都经过 :class:`~app.plugins.fetch.SafeFetcher`；拉取失败时保留上一次成功解析的
缓存，只更新状态和错误，目录继续展示缓存内容。schema 不支持的仓库保留缓存但不展示条目。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.clock import Clock, to_iso
from app.db import Database
from app.errors import AppError
from app.plugins.fetch import FetchError, FetchTooLarge, SafeFetcher, check_url
from app.plugins.index_schema import (
    BUILTIN_PREFIX,
    MAX_INDEX_BYTES,
    IndexEntry,
    IndexUnsupported,
    ParsedIndex,
    best_compatible,
    best_entry,
    compare_versions,
    dump_index,
    load_dumped_index,
    parse_index,
)
from app.plugins.index_schema import (
    IndexError as IndexParseError,
)
from app.plugins.registry import plugin_category

logger = logging.getLogger(__name__)

REFRESH_TIMEOUT_SECONDS = 15.0
CHECK_PLUGIN_UPDATES_KEY = "check_plugin_updates"

_SELECT = (
    "SELECT id, url, name, default_slot, enabled, status, last_error, index_schema, "
    "index_json, etag, last_checked_at, last_success_at, created_at, updated_at "
    "FROM plugin_repository"
)


class _FetchFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class PluginRepositories:
    def __init__(
        self,
        database: Database,
        clock: Clock,
        *,
        fetcher: SafeFetcher | None = None,
        proxy: Callable[[], str | None] = lambda: None,
        system_plugin_ids: Callable[[], set[str]] = lambda: set(),
    ) -> None:
        self._database = database
        self._clock = clock
        self._fetcher = fetcher or SafeFetcher()
        self._proxy = proxy
        self._system_plugin_ids = system_plugin_ids

    # ---- 默认仓库 ----

    def ensure_defaults(self, urls: tuple[str, ...]) -> None:
        """按 APP_DEFAULT_PLUGIN_REPOS 的顺序对齐 default_slot。

        已有槽位只改地址，id 不变，已安装插件的来源仍然有效；地址已经是用户仓库时把那一行
        提升为默认；槽位超出新列表时降为用户仓库。匹配按 URL 优先、槽位行次之：URL 仍在
        默认列表里的行保持原 id，只有真正被替换掉的槽位才复用旧行 id。先清空槽位再分配，
        交换顺序时不会撞上 default_slot 唯一约束。
        """
        for url in urls:
            try:
                check_url(url)
            except FetchError as exc:
                raise RuntimeError(str(exc)) from exc
        with self._database.session() as connection:
            rows = connection.execute(_SELECT).fetchall()
            original_slots = {
                int(row["default_slot"]): row for row in rows if row["default_slot"] is not None
            }
            by_url = {str(row["url"]): row for row in rows}
            url_set = set(urls)
            used: set[int] = set()
            assignments: list[tuple[str, int, sqlite3.Row | None]] = []
            for slot, url in enumerate(urls):
                existing = by_url.get(url)
                if existing is not None:
                    assignments.append((url, slot, existing))
                    used.add(int(existing["id"]))
                    continue
                candidate = original_slots.get(slot)
                if (
                    candidate is not None
                    and str(candidate["url"]) not in url_set
                    and int(candidate["id"]) not in used
                ):
                    assignments.append((url, slot, candidate))
                    used.add(int(candidate["id"]))
                else:
                    assignments.append((url, slot, None))
            now = self._now()
            connection.execute(
                "UPDATE plugin_repository SET default_slot = NULL, updated_at = ? "
                "WHERE default_slot IS NOT NULL",
                (now,),
            )
            for url, slot, row in assignments:
                if row is None:
                    connection.execute(
                        "INSERT INTO plugin_repository "
                        "(url, name, default_slot, enabled, status, created_at, updated_at) "
                        "VALUES (?, '', ?, 1, 'pending', ?, ?)",
                        (url, slot, now, now),
                    )
                elif str(row["url"]) == url:
                    connection.execute(
                        "UPDATE plugin_repository SET default_slot = ?, updated_at = ? "
                        "WHERE id = ?",
                        (slot, now, row["id"]),
                    )
                else:
                    # 换地址时清掉旧缓存，避免在新地址下展示旧仓库的条目
                    connection.execute(
                        "UPDATE plugin_repository SET url = ?, default_slot = ?, "
                        "etag = NULL, index_json = NULL, index_schema = NULL, "
                        "status = 'pending', last_error = NULL, last_checked_at = NULL, "
                        "last_success_at = NULL, updated_at = ? WHERE id = ?",
                        (url, slot, now, row["id"]),
                    )

    # ---- 查询与增删改 ----

    def list(self) -> list[dict[str, Any]]:
        with self._database.read_session() as connection:
            rows = connection.execute(f"{_SELECT} ORDER BY id").fetchall()
        return [self._output(row) for row in rows]

    def get(self, repository_id: int) -> dict[str, Any] | None:
        with self._database.read_session() as connection:
            row = connection.execute(f"{_SELECT} WHERE id = ?", (repository_id,)).fetchone()
        return None if row is None else self._output(row)

    def require(self, repository_id: int) -> dict[str, Any]:
        item = self.get(repository_id)
        if item is None:
            raise AppError(404, "not_found", "插件仓库不存在")
        return item

    def add(self, url: str, accept_risk: bool) -> dict[str, Any]:
        if not accept_risk:
            raise AppError(400, "risk_not_accepted", "添加非官方插件仓库前需要确认风险")
        try:
            check_url(url)
        except FetchError as exc:
            raise AppError(400, "invalid_repository_url", str(exc)) from exc
        with self._database.read_session() as connection:
            exists = connection.execute(
                "SELECT 1 FROM plugin_repository WHERE url = ?", (url,)
            ).fetchone()
        if exists is not None:
            raise AppError(409, "repository_exists", "这个插件仓库已经添加")
        try:
            index, etag = self._fetch_index(url, etag=None, timeout=None)
        except _FetchFailure as exc:
            if exc.code == "repository_schema_unsupported":
                raise AppError(422, "repository_schema_unsupported", exc.message) from exc
            if exc.code == "repository_invalid":
                raise AppError(422, "repository_invalid", exc.message) from exc
            raise AppError(422, "repository_unreachable", exc.message) from exc
        if index is None:
            raise AppError(422, "repository_invalid", "仓库对首次拉取返回了 304")
        now = self._now()
        try:
            with self._database.session() as connection:
                cursor = connection.execute(
                    "INSERT INTO plugin_repository "
                    "(url, name, default_slot, enabled, status, last_error, index_schema, "
                    " index_json, etag, last_checked_at, last_success_at, created_at, updated_at) "
                    "VALUES (?, ?, NULL, 1, 'ok', NULL, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        url,
                        index.name,
                        index.schema,
                        dump_index(index),
                        etag,
                        now,
                        now,
                        now,
                        now,
                    ),
                )
                assert cursor.lastrowid is not None
                row = connection.execute(f"{_SELECT} WHERE id = ?", (cursor.lastrowid,)).fetchone()
        except sqlite3.IntegrityError as exc:
            raise AppError(409, "repository_exists", "这个插件仓库已经添加") from exc
        assert row is not None
        return self._output(row)

    def set_enabled(self, repository_id: int, enabled: bool) -> dict[str, Any]:
        with self._database.session() as connection:
            cursor = connection.execute(
                "UPDATE plugin_repository SET enabled = ?, updated_at = ? WHERE id = ?",
                (int(enabled), self._now(), repository_id),
            )
            if cursor.rowcount == 0:
                raise AppError(404, "not_found", "插件仓库不存在")
            row = connection.execute(f"{_SELECT} WHERE id = ?", (repository_id,)).fetchone()
        assert row is not None
        return self._output(row)

    def delete(self, repository_id: int) -> None:
        with self._database.session() as connection:
            row = connection.execute(
                "SELECT default_slot FROM plugin_repository WHERE id = ?", (repository_id,)
            ).fetchone()
            if row is None:
                raise AppError(404, "not_found", "插件仓库不存在")
            if row["default_slot"] is not None:
                raise AppError(409, "repository_official", "官方仓库不能删除，可以停用")
            used = connection.execute(
                "SELECT 1 FROM plugin_install WHERE repository_id = ? LIMIT 1", (repository_id,)
            ).fetchone()
            if used is not None:
                raise AppError(409, "repository_in_use", "还有插件安装自这个仓库，不能删除")
            connection.execute("DELETE FROM plugin_repository WHERE id = ?", (repository_id,))

    # ---- 刷新 ----

    def refresh(self, repository_id: int, *, timeout: float | None = None) -> dict[str, Any]:
        with self._database.read_session() as connection:
            row = connection.execute(f"{_SELECT} WHERE id = ?", (repository_id,)).fetchone()
        if row is None:
            raise AppError(404, "not_found", "插件仓库不存在")
        self._refresh_row(row, timeout=timeout)
        return self.require(repository_id)

    def refresh_all(self) -> dict[str, int]:
        with self._database.read_session() as connection:
            ids = [
                int(row["id"])
                for row in connection.execute(
                    "SELECT id FROM plugin_repository WHERE enabled = 1 ORDER BY id"
                ).fetchall()
            ]
        failed = 0
        if ids:
            with ThreadPoolExecutor(max_workers=min(8, len(ids))) as pool:
                futures = {
                    repository_id: pool.submit(
                        self.refresh, repository_id, timeout=REFRESH_TIMEOUT_SECONDS
                    )
                    for repository_id in ids
                }
                for repository_id, future in futures.items():
                    try:
                        future.result()
                    except Exception:
                        failed += 1
                        logger.exception("刷新插件仓库 %s 失败", repository_id)
            with self._database.read_session() as connection:
                failed += sum(
                    1
                    for row in connection.execute(
                        "SELECT status FROM plugin_repository "
                        "WHERE enabled = 1 AND status IN ('error', 'unsupported')"
                    )
                )
        return {
            "repositories": len(ids),
            "failed": failed,
            "updates": self.update_count(),
        }

    def refresh_pending(self) -> None:
        """宿主启动后刷新还没拉取过的已启用仓库（新装的官方仓库）。

        在后台线程里调用；失败只记录到仓库状态和日志，不抛出。
        """
        with self._database.read_session() as connection:
            ids = [
                int(row["id"])
                for row in connection.execute(
                    "SELECT id FROM plugin_repository "
                    "WHERE enabled = 1 AND status = 'pending' ORDER BY id"
                ).fetchall()
            ]
        for repository_id in ids:
            try:
                self.refresh(repository_id, timeout=REFRESH_TIMEOUT_SECONDS)
            except Exception:
                logger.exception("启动后刷新插件仓库 %s 失败", repository_id)

    def _refresh_row(self, row: sqlite3.Row, *, timeout: float | None) -> None:
        repository_id = int(row["id"])
        url = str(row["url"])
        etag = row["etag"] if isinstance(row["etag"], str) else None
        checked_at = self._now()
        try:
            index, new_etag = self._fetch_index(url, etag=etag, timeout=timeout)
        except _FetchFailure as exc:
            status = "unsupported" if exc.code == "repository_schema_unsupported" else "error"
            self._store_failure(repository_id, status, exc.message)
            return
        with self._database.session() as connection:
            if index is None:
                connection.execute(
                    "UPDATE plugin_repository SET status = 'ok', last_error = NULL, "
                    "last_checked_at = ?, last_success_at = ?, updated_at = ? WHERE id = ?",
                    (checked_at, checked_at, checked_at, repository_id),
                )
                return
            connection.execute(
                "UPDATE plugin_repository SET name = ?, status = 'ok', last_error = NULL, "
                "index_schema = ?, index_json = ?, etag = ?, last_checked_at = ?, "
                "last_success_at = ?, updated_at = ? WHERE id = ?",
                (
                    index.name or str(row["name"]),
                    index.schema,
                    dump_index(index),
                    new_etag,
                    checked_at,
                    checked_at,
                    checked_at,
                    repository_id,
                ),
            )

    def _store_failure(self, repository_id: int, status: str, message: str) -> None:
        with self._database.session() as connection:
            connection.execute(
                "UPDATE plugin_repository SET status = ?, last_error = ?, last_checked_at = ?, "
                "updated_at = ? WHERE id = ?",
                (status, message, self._now(), self._now(), repository_id),
            )

    def _fetch_index(
        self, url: str, *, etag: str | None, timeout: float | None
    ) -> tuple[ParsedIndex | None, str | None]:
        try:
            result = self._fetcher.fetch(
                url,
                proxy=self._proxy(),
                etag=etag,
                max_bytes=MAX_INDEX_BYTES,
                timeout=timeout,
            )
        except FetchTooLarge as exc:
            raise _FetchFailure("repository_invalid", f"索引超过 2 MiB：{exc}") from exc
        except FetchError as exc:
            raise _FetchFailure("repository_unreachable", str(exc)) from exc
        if result.not_modified:
            return None, result.etag
        try:
            data = json.loads(result.content or b"")
        except (TypeError, ValueError) as exc:
            raise _FetchFailure("repository_invalid", "index.json 不是合法的 JSON") from exc
        try:
            return parse_index(data, result.url), result.etag
        except IndexUnsupported as exc:
            raise _FetchFailure("repository_schema_unsupported", str(exc)) from exc
        except IndexParseError as exc:
            raise _FetchFailure("repository_invalid", str(exc)) from exc

    # ---- 目录 ----

    def catalog(self) -> dict[str, Any]:
        with self._database.read_session() as connection:
            repositories = connection.execute(f"{_SELECT} WHERE enabled = 1 ORDER BY id").fetchall()
            install_rows = connection.execute(
                "SELECT plugin_id, source, repository_id, version FROM plugin_install"
            ).fetchall()
        installs = {str(row["plugin_id"]): row for row in install_rows}
        system_ids = self._system_plugin_ids()
        items: list[dict[str, Any]] = []
        outputs = [self._output(row) for row in repositories]
        for row, output in zip(repositories, outputs, strict=True):
            if output["status"] == "unsupported":
                continue
            index = load_dumped_index(row["index_json"])
            if index is None:
                continue
            grouped: dict[str, list[IndexEntry]] = defaultdict(list)
            for entry in index.entries:
                grouped[entry.id].append(entry)
            for plugin_id in sorted(grouped):
                entries = tuple(grouped[plugin_id])
                chosen = best_entry(entries)
                if chosen is None:
                    continue
                installed = installs.get(plugin_id)
                installed_version = str(installed["version"]) if installed is not None else None
                compatible = best_compatible(entries)
                from_this_repo = (
                    installed is not None
                    and str(installed["source"]) == "repository"
                    and installed["repository_id"] == row["id"]
                )
                status, reason = self._catalog_status(
                    plugin_id=plugin_id,
                    entries=entries,
                    compatible=compatible,
                    installed=installed,
                    installed_version=installed_version,
                    from_this_repo=from_this_repo,
                    system_ids=system_ids,
                )
                items.append(
                    {
                        "id": plugin_id,
                        "name": chosen.name,
                        "description": chosen.description,
                        "version": chosen.version,
                        "repository_id": int(row["id"]),
                        "repository_name": output["name"],
                        "category": plugin_category(chosen.provides),
                        "sdk": chosen.sdk,
                        "provides": list(chosen.provides),
                        "capabilities": list(chosen.capabilities),
                        "homepage": chosen.homepage,
                        "size": chosen.size,
                        "status": status,
                        "installed_version": installed_version,
                        "reason": reason,
                    }
                )
        return {
            "sdk_version": _sdk_version(),
            "repositories": outputs,
            "plugins": items,
        }

    def _catalog_status(
        self,
        *,
        plugin_id: str,
        entries: tuple[IndexEntry, ...],
        compatible: IndexEntry | None,
        installed: sqlite3.Row | None,
        installed_version: str | None,
        from_this_repo: bool,
        system_ids: set[str],
    ) -> tuple[str, str | None]:
        if plugin_id.startswith(BUILTIN_PREFIX) or plugin_id in system_ids:
            return "conflict", "与系统插件 id 冲突"
        if installed is not None and not from_this_repo:
            return "conflict", "这个 id 已从其他来源安装"
        if compatible is None:
            return "incompatible", "插件的 SDK 范围与宿主不兼容"
        if from_this_repo and installed_version is not None:
            try:
                if compare_versions(compatible.version, installed_version) > 0:
                    return "update_available", None
            except ValueError:
                pass
            return "installed", None
        return "available", None

    def find_entry(self, repository_id: int, plugin_id: str, version: str) -> IndexEntry | None:
        index = self._cached_index(repository_id)
        if index is None:
            return None
        for entry in index.entries:
            if entry.id == plugin_id and entry.version == version:
                return entry
        return None

    def entries(self, repository_id: int, plugin_id: str) -> tuple[IndexEntry, ...]:
        index = self._cached_index(repository_id)
        if index is None:
            return ()
        return tuple(entry for entry in index.entries if entry.id == plugin_id)

    def available_version(
        self, plugin_id: str, installed_version: str, repository_id: int | None
    ) -> str | None:
        if repository_id is None:
            return None
        with self._database.read_session() as connection:
            row = connection.execute(
                "SELECT enabled, status FROM plugin_repository WHERE id = ?", (repository_id,)
            ).fetchone()
        if row is None or not row["enabled"] or row["status"] == "unsupported":
            return None
        compatible = best_compatible(self.entries(repository_id, plugin_id))
        if compatible is None:
            return None
        try:
            if compare_versions(compatible.version, installed_version) > 0:
                return compatible.version
        except ValueError:
            return None
        return None

    def update_count(self) -> int:
        with self._database.read_session() as connection:
            rows = connection.execute(
                "SELECT plugin_id, version, repository_id FROM plugin_install "
                "WHERE source = 'repository'"
            ).fetchall()
        return sum(
            1
            for row in rows
            if self.available_version(
                str(row["plugin_id"]), str(row["version"]), row["repository_id"]
            )
            is not None
        )

    def _cached_index(self, repository_id: int) -> ParsedIndex | None:
        with self._database.read_session() as connection:
            row = connection.execute(
                "SELECT index_json FROM plugin_repository WHERE id = ?", (repository_id,)
            ).fetchone()
        if row is None:
            return None
        return load_dumped_index(row["index_json"])

    # ---- 输出 ----

    def _output(self, row: sqlite3.Row) -> dict[str, Any]:
        index = load_dumped_index(row["index_json"])
        return {
            "id": int(row["id"]),
            "name": str(row["name"]) or str(row["url"]),
            "url": str(row["url"]),
            "official": row["default_slot"] is not None,
            "enabled": bool(row["enabled"]),
            "status": str(row["status"]),
            "error": row["last_error"],
            "schema": row["index_schema"],
            "plugin_count": len(index.entries) if index is not None else 0,
            "invalid_count": index.invalid_count if index is not None else 0,
            "last_checked_at": row["last_checked_at"],
            "last_success_at": row["last_success_at"],
            "created_at": row["created_at"],
        }

    def _now(self) -> str:
        return to_iso(self._clock.now())


def _sdk_version() -> str:
    from iris_plugin_sdk import SDK_VERSION

    return SDK_VERSION
