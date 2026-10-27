"""在线字幕：搜索、下载、入库、管理员查看和删除（ADR 0025）。

下载接口不依赖请求级写事务：先校验版本可见性，再在锁外调用字幕插件和校验内容，
最后用一个很短的写事务去重并入库。这样字幕站慢或超时不会一直占着全局写锁。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, cast

from iris_plugin_sdk import (
    MediaContext,
    MetadataKind,
    OnlineSubtitleProvider,
    ResourceNotFound,
    ResourceRateLimited,
    ResourceUnavailable,
    SubtitleCandidate,
    SubtitleSearchRequest,
    SubtitleUnavailable,
)

from app.auth.users import User
from app.clock import Clock, to_iso
from app.db import Database
from app.errors import AppError, NotFound
from app.library.browse import visible_library_ids
from app.plugins.registry import PluginRegistry
from app.resources.deletable import require_deletable
from app.resources.service import FolderSource, ResourceSources
from app.subtitles.content import (
    MAX_ONLINE_PER_VERSION,
    PreparedSubtitle,
    compress,
    prepare_subtitle,
)

logger = logging.getLogger(__name__)

# 普通用户搜索 10 次/分钟、下载 20 次/10 分钟；全局同时只处理 2 个下载
SEARCH_LIMIT = 10
SEARCH_WINDOW = timedelta(minutes=1)
DOWNLOAD_LIMIT = 20
DOWNLOAD_WINDOW = timedelta(minutes=10)
DOWNLOAD_CONCURRENCY = 2

ProviderStatus = Literal["ok", "failed", "unavailable"]


@dataclass(frozen=True)
class SubtitleProviderInfo:
    id: str
    name: str
    ready: bool
    reason: str | None


@dataclass(frozen=True)
class ProviderSearch:
    id: str
    name: str
    status: ProviderStatus
    message: str | None


@dataclass(frozen=True)
class SearchResult:
    provider_id: str
    provider_name: str
    remote_id: str
    label: str
    release: str | None
    language: str | None
    format: str
    forced: bool
    hearing_impaired: bool
    score: float | None
    downloads: int | None
    uploaded_at: str | None
    downloaded: bool


@dataclass(frozen=True)
class SubtitleSearch:
    version_id: int
    language: str | None
    providers: list[ProviderSearch]
    results: list[SearchResult]


@dataclass(frozen=True)
class OnlineSubtitleRecord:
    id: int
    label: str
    language: str | None
    forced: bool
    hearing_impaired: bool
    source: str
    format: str


@dataclass(frozen=True)
class DownloadOutcome:
    subtitle: OnlineSubtitleRecord
    created: bool


@dataclass(frozen=True)
class DownloadRequest:
    provider_id: str
    remote_id: str
    language: str | None = None
    label: str = ""
    release: str | None = None
    format: str | None = None
    forced: bool = False
    hearing_impaired: bool = False


@dataclass(frozen=True)
class VersionSubtitles:
    version_id: int
    label: str
    filename: str
    subtitles: list[dict[str, object]]
    embedded: list[dict[str, object]] | None


@dataclass(frozen=True)
class SubtitleVersionContext:
    version_id: int
    playable_id: int
    media_item_id: int
    library_id: int
    kind: MetadataKind
    title: str
    original_title: str | None
    year: int | None
    season: int | None
    episode: int | None
    rel_path: str
    video_size: int | None
    video_sha1: str | None
    imdb_id: str | None
    tmdb_id: int | None

    @property
    def filename(self) -> str:
        return self.rel_path.rpartition("/")[2]

    @property
    def media(self) -> MediaContext:
        title = self.title
        if self.kind == "series" and self.season is not None and self.episode is not None:
            title = f"{title} S{self.season:02d}E{self.episode:02d}"
        return MediaContext(
            title=title,
            original_title=self.original_title,
            year=self.year,
            season=self.season,
            episode=self.episode,
            filename=self.filename,
            kind=self.kind,
            imdb_id=self.imdb_id,
            tmdb_id=self.tmdb_id,
        )


class SubtitleThrottle:
    """内存滑动窗口；每个用户分别限制搜索和下载，状态不持久化。"""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._searches: dict[int, deque[datetime]] = {}
        self._downloads: dict[int, deque[datetime]] = {}
        self._last_prune = clock.now()

    def search(self, user_id: int) -> None:
        self._hit(self._searches, user_id, SEARCH_LIMIT, SEARCH_WINDOW, "搜索")

    def download(self, user_id: int) -> None:
        self._hit(self._downloads, user_id, DOWNLOAD_LIMIT, DOWNLOAD_WINDOW, "下载")

    def _hit(
        self,
        windows: dict[int, deque[datetime]],
        user_id: int,
        limit: int,
        span: timedelta,
        action: str,
    ) -> None:
        now = self._clock.now()
        with self._lock:
            self._prune(now)
            hits = windows.setdefault(user_id, deque())
            while hits and now - hits[0] >= span:
                hits.popleft()
            if len(hits) >= limit:
                raise AppError(
                    429,
                    "subtitle_rate_limited",
                    f"在线字幕{action}太频繁，请稍后再试",
                )
            hits.append(now)

    def _prune(self, now: datetime) -> None:
        if now - self._last_prune < timedelta(hours=1):
            return
        self._last_prune = now
        for windows, span in (
            (self._searches, SEARCH_WINDOW),
            (self._downloads, DOWNLOAD_WINDOW),
        ):
            for key in [key for key, hits in windows.items() if not hits or now - hits[-1] >= span]:
                del windows[key]


class OnlineSubtitles:
    def __init__(
        self,
        database: Database,
        clock: Clock,
        plugins: PluginRegistry,
        resources: ResourceSources,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self._database = database
        self._clock = clock
        self._plugins = plugins
        self._resources = resources
        self._on_change = on_change or (lambda: None)
        self._throttle = SubtitleThrottle(clock)
        self._slots = threading.BoundedSemaphore(DOWNLOAD_CONCURRENCY)

    # ---- 插件与版本 ----

    def providers(self, library_id: int | None = None) -> list[SubtitleProviderInfo]:
        return [
            SubtitleProviderInfo(
                id=plugin_id,
                name=name,
                ready=(reason := _unavailable_reason(provider)) is None,
                reason=reason,
            )
            for plugin_id, name, provider in self._plugins.subtitle_downloaders(library_id)
        ]

    def library_id_of(self, version_id: int) -> int:
        """版本所在的资料库；不做用户可见范围过滤，给按版本过滤字幕下载器用。"""
        with self._database.read_session() as connection:
            row = connection.execute(
                """
                SELECT m.library_id
                FROM version v
                JOIN playable p ON p.id = v.playable_id
                JOIN media_item m ON m.id = p.media_item_id
                WHERE v.id = ?
                """,
                (version_id,),
            ).fetchone()
        if row is None:
            raise NotFound("版本不存在")
        return int(row["library_id"])

    def _context(self, user: User, version_id: int) -> SubtitleVersionContext:
        with self._database.read_session() as connection:
            context = _version_context(connection, user, version_id)
        if context is None:
            raise NotFound("版本不存在或已失效")
        return context

    def ensure_visible(self, user: User, version_id: int) -> None:
        """只校验版本在用户可见范围内且文件可用，不做权限和限流判断。"""
        self._context(user, version_id)

    def _selected_providers(
        self, library_id: int, provider_id: str | None
    ) -> list[tuple[str, str, OnlineSubtitleProvider]]:
        providers = self._plugins.subtitle_downloaders(library_id)
        if provider_id is not None:
            providers = [item for item in providers if item[0] == provider_id]
            if not providers:
                raise AppError(
                    409, "subtitle_provider_unavailable", "这个字幕下载器不存在或没有启用"
                )
            return providers
        if not providers:
            raise AppError(
                409,
                "subtitle_provider_unavailable",
                "这个资料库没有勾选可用的在线字幕下载器，请管理员在资料库设置里选择",
            )
        return providers

    # ---- 搜索 ----

    def search(
        self,
        user: User,
        version_id: int,
        *,
        language: str | None = None,
        provider_id: str | None = None,
    ) -> SubtitleSearch:
        context = self._context(user, version_id)
        providers = self._selected_providers(context.library_id, provider_id)
        self._throttle.search(user.id)
        request = SubtitleSearchRequest(media=context.media, language=language)
        statuses: list[ProviderSearch] = []
        results: list[SearchResult] = []
        for plugin_id, name, provider in providers:
            reason = _unavailable_reason(provider)
            if reason is not None:
                statuses.append(ProviderSearch(plugin_id, name, "unavailable", reason))
                continue
            try:
                candidates = provider.search(request)
            except SubtitleUnavailable as exc:
                statuses.append(
                    ProviderSearch(plugin_id, name, "unavailable", _safe_message(exc.message))
                )
                continue
            except Exception as exc:
                # 只记异常类型：插件异常消息里可能带凭据，不能进日志
                logger.warning("在线字幕插件 %s 搜索失败：%s", plugin_id, type(exc).__name__)
                statuses.append(
                    ProviderSearch(plugin_id, name, "failed", "字幕站请求失败，请稍后再试")
                )
                continue
            statuses.append(ProviderSearch(plugin_id, name, "ok", None))
            results.extend(
                _candidate_result(plugin_id, name, candidate) for candidate in candidates
            )
        downloaded = self._downloaded_keys(context.version_id)
        return SubtitleSearch(
            version_id=context.version_id,
            language=language,
            providers=statuses,
            results=[
                _with_downloaded(result, downloaded)
                for result in results
                if _language_matches(result.language, language)
            ],
        )

    def _downloaded_keys(self, version_id: int) -> set[tuple[str, str]]:
        with self._database.read_session() as connection:
            rows = connection.execute(
                """
                SELECT o.provider_id, o.remote_id
                FROM online_subtitle o JOIN subtitle s ON s.id = o.subtitle_id
                WHERE s.version_id = ?
                """,
                (version_id,),
            ).fetchall()
        return {(str(row[0]), str(row[1])) for row in rows}

    # ---- 下载 ----

    def download(
        self,
        user: User,
        version_id: int,
        request: DownloadRequest,
        *,
        origin: Literal["manual", "auto"] = "manual",
    ) -> DownloadOutcome:
        """用户下载：占用户限流，下载槽满了直接报错，不排队。"""
        context = self._context(user, version_id)
        self._throttle.download(user.id)
        return self._fetch_and_save(
            context, request, created_by=user.id, origin=origin, wait_slot=False
        )

    def auto_download(
        self, context: SubtitleVersionContext, request: DownloadRequest
    ) -> DownloadOutcome:
        """自动下载：不占用户限流，阻塞等待下载槽，created_by 为空。"""
        return self._fetch_and_save(
            context, request, created_by=None, origin="auto", wait_slot=True
        )

    def _fetch_and_save(
        self,
        context: SubtitleVersionContext,
        request: DownloadRequest,
        *,
        created_by: int | None,
        origin: Literal["manual", "auto"],
        wait_slot: bool,
    ) -> DownloadOutcome:
        provider = next(
            (
                item
                for item in self._selected_providers(context.library_id, request.provider_id)
                if item[0] == request.provider_id
            ),
            None,
        )
        if provider is None:
            raise AppError(409, "subtitle_provider_unavailable", "这个字幕下载器不存在或没有启用")
        plugin_id, _, downloader = provider
        reason = _unavailable_reason(downloader)
        if reason is not None:
            raise AppError(409, "subtitle_provider_unavailable", reason)
        with self._download_slot(wait=wait_slot):
            candidate = SubtitleCandidate(
                provider_id=plugin_id,
                remote_id=request.remote_id,
                label=request.label or request.remote_id,
                language=request.language or "",
                format=request.format or "",
                forced=request.forced,
                hearing_impaired=request.hearing_impaired,
            )
            try:
                download = downloader.download(candidate)
            except SubtitleUnavailable as exc:
                if exc.retry_after is not None:
                    raise AppError(
                        429, "subtitle_rate_limited", _safe_message(exc.message)
                    ) from None
                raise AppError(
                    409, "subtitle_provider_unavailable", _safe_message(exc.message)
                ) from None
            except Exception as exc:
                logger.warning("在线字幕插件 %s 下载失败：%s", plugin_id, type(exc).__name__)
                raise AppError(
                    502, "subtitle_provider_failed", "字幕站请求失败，请稍后再试"
                ) from None
            prepared = prepare_subtitle(
                download.content,
                language=download.language or request.language,
                format_hint=download.format or request.format,
                filename=request.remote_id,
            )
            return self._save(
                context,
                request,
                prepared,
                created_by=created_by,
                origin=origin,
            )

    @contextmanager
    def _download_slot(self, *, wait: bool) -> Iterator[None]:
        if wait:
            self._slots.acquire()
        elif not self._slots.acquire(blocking=False):
            raise AppError(429, "subtitle_rate_limited", "正在下载的字幕太多，请稍后再试")
        try:
            yield
        finally:
            self._slots.release()

    def _save(
        self,
        context: SubtitleVersionContext,
        request: DownloadRequest,
        prepared: PreparedSubtitle,
        *,
        created_by: int | None,
        origin: Literal["manual", "auto"],
    ) -> DownloadOutcome:
        now = to_iso(self._clock.now())
        with self._database.session() as connection:
            existing = connection.execute(
                """
                SELECT s.id, s.label, s.language, s.forced, s.hearing_impaired, o.format
                FROM subtitle s JOIN online_subtitle o ON o.subtitle_id = s.id
                WHERE s.version_id = ? AND o.sha256 = ?
                """,
                (context.version_id, prepared.sha256),
            ).fetchone()
            if existing is not None:
                return DownloadOutcome(_record(existing, "online"), False)
            count = connection.execute(
                "SELECT COUNT(*) FROM subtitle WHERE version_id = ? AND source = 'online'",
                (context.version_id,),
            ).fetchone()[0]
            if int(count) >= MAX_ONLINE_PER_VERSION:
                raise AppError(
                    409,
                    "subtitle_limit_reached",
                    f"这个版本最多保存 {MAX_ONLINE_PER_VERSION} 条在线字幕，请先删除一些",
                )
            cursor = connection.execute(
                """
                INSERT INTO subtitle
                    (version_id, media_file_id, source, language, label, forced, is_default,
                     hearing_impaired)
                VALUES (?, NULL, 'online', ?, ?, ?, 0, ?)
                """,
                (
                    context.version_id,
                    prepared.language,
                    request.label or request.remote_id,
                    int(request.forced),
                    int(request.hearing_impaired),
                ),
            )
            assert cursor.lastrowid is not None
            subtitle_id = cursor.lastrowid
            connection.execute(
                """
                INSERT INTO online_subtitle
                    (subtitle_id, provider_id, remote_id, release, format, sha256, size,
                     content, original_encoding, video_size, video_sha1, origin, created_by,
                     created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    subtitle_id,
                    request.provider_id,
                    request.remote_id,
                    request.release,
                    prepared.format,
                    prepared.sha256,
                    prepared.size,
                    compress(prepared.content),
                    prepared.original_encoding,
                    context.video_size,
                    context.video_sha1,
                    origin,
                    created_by,
                    now,
                ),
            )
        self._on_change()
        return DownloadOutcome(
            OnlineSubtitleRecord(
                id=subtitle_id,
                label=request.label or request.remote_id,
                language=prepared.language,
                forced=request.forced,
                hearing_impaired=request.hearing_impaired,
                source="online",
                format=prepared.format,
            ),
            True,
        )

    # ---- 管理员 ----

    def admin_list(self, version_id: int) -> VersionSubtitles:
        with self._database.read_session() as connection:
            version = connection.execute(
                """
                SELECT v.id, v.label, c.rel_path
                FROM version v JOIN media_file c ON c.id = v.media_file_id
                WHERE v.id = ?
                """,
                (version_id,),
            ).fetchone()
            if version is None:
                raise NotFound("版本不存在")
            rows = connection.execute(
                """
                SELECT s.id, s.source, s.label, s.language, s.forced, s.hearing_impaired,
                    s.is_default, c.rel_path, c.identity, c.folder_id,
                    o.provider_id, o.release, o.size, o.format, o.created_at, o.created_by,
                    u.username
                FROM subtitle s
                LEFT JOIN media_file c ON c.id = s.media_file_id
                LEFT JOIN online_subtitle o ON o.subtitle_id = s.id
                LEFT JOIN user u ON u.id = o.created_by
                WHERE s.version_id = ?
                ORDER BY s.is_default DESC, s.id
                """,
                (version_id,),
            ).fetchall()
            sources = self._sources_for(connection, rows)
            embedded = connection.execute(
                """
                SELECT mi.subtitles
                FROM version v JOIN media_file c ON c.id = v.media_file_id
                LEFT JOIN media_info mi ON mi.media_file_id = c.id
                    AND mi.size IS c.size AND mi.sha1 IS c.sha1
                WHERE v.id = ?
                """,
                (version_id,),
            ).fetchone()
        names = {manifest.id: manifest.name for manifest in self._plugins.manifests()}
        subtitles = [
            _admin_subtitle(row, sources.get(row["folder_id"]), names, self._resources)
            for row in rows
        ]
        return VersionSubtitles(
            version_id=int(version["id"]),
            label=str(version["label"]),
            filename=_basename(str(version["rel_path"])),
            subtitles=subtitles,
            embedded=_embedded(embedded["subtitles"] if embedded is not None else None),
        )

    def _sources_for(
        self, connection: sqlite3.Connection, rows: list[sqlite3.Row]
    ) -> dict[int, FolderSource]:
        folder_ids = {
            int(row["folder_id"])
            for row in rows
            if row["folder_id"] is not None and row["source"] == "external"
        }
        return ResourceSources.folders(connection, sorted(folder_ids))

    def delete(self, subtitle_id: int, *, confirm: bool) -> None:
        with self._database.read_session() as connection:
            row = connection.execute(
                """
                SELECT s.id, s.source, c.id AS media_file_id, c.identity, c.folder_id, c.rel_path
                FROM subtitle s LEFT JOIN media_file c ON c.id = s.media_file_id
                WHERE s.id = ?
                """,
                (subtitle_id,),
            ).fetchone()
        if row is None:
            raise NotFound("字幕不存在")
        if row["source"] == "online":
            with self._database.session() as connection:
                connection.execute("DELETE FROM subtitle WHERE id = ?", (subtitle_id,))
            self._on_change()
            return
        if not confirm:
            raise AppError(409, "delete_confirmation_required", "删除外挂字幕需要确认")
        if row["media_file_id"] is None or row["identity"] is None:
            raise AppError(409, "resource_delete_unsupported", "这个字幕没有可删除的来源文件")
        identity = str(row["identity"])
        folder_ids = {int(row["folder_id"])} if row["folder_id"] is not None else set()
        with self._database.read_session() as connection:
            require_deletable(connection, folder_ids, [identity], self._resources)
            sources = ResourceSources.folders(connection, sorted(folder_ids))
            source_name = next(iter(sources.values())).name if sources else "资源来源"
            try:
                self._resources.delete_file(connection, identity)
            except ResourceNotFound:
                # 来源上已经没有这个文件了，删掉本地记录即可
                pass
            except ResourceRateLimited:
                raise AppError(
                    429, "resource_rate_limited", f"资源来源「{source_name}」暂时受限，请稍后再试"
                ) from None
            except ResourceUnavailable as exc:
                raise AppError(503, "resource_unavailable", exc.message) from None
        with self._database.session() as connection:
            connection.execute("DELETE FROM media_file WHERE id = ?", (row["media_file_id"],))
        self._on_change()


_CONTEXT_SELECT = """
        SELECT v.id, v.playable_id, p.media_item_id, m.library_id, m.kind, m.title,
            m.original_title, m.year, c.rel_path, c.size, c.sha1,
            e.number AS episode_number, se.number AS season_number,
            w.nfo_imdb_id, m.provider_id, m.remote_id
        FROM version v
        JOIN playable p ON p.id = v.playable_id
        JOIN media_item m ON m.id = p.media_item_id
        JOIN media_file c ON c.id = v.media_file_id
        LEFT JOIN episode e ON e.id = p.episode_id
        LEFT JOIN season se ON se.id = e.season_id
        LEFT JOIN work w ON w.id = c.work_id
        WHERE v.id = ? AND c.state = 'active' AND c.playback_locator IS NOT NULL
"""


def _version_context(
    connection: sqlite3.Connection, user: User, version_id: int
) -> SubtitleVersionContext | None:
    library_ids = visible_library_ids(connection, user)
    scope = ", ".join("?" for _ in library_ids) or "NULL"
    row = connection.execute(
        f"{_CONTEXT_SELECT} AND m.library_id IN ({scope})",
        (version_id, *library_ids),
    ).fetchone()
    return None if row is None else _context_from_row(connection, row)


def _system_version_context(
    connection: sqlite3.Connection, version_id: int
) -> SubtitleVersionContext | None:
    """自动下载用的版本上下文：不做用户可见范围过滤。"""
    row = connection.execute(_CONTEXT_SELECT, (version_id,)).fetchone()
    return None if row is None else _context_from_row(connection, row)


def _context_from_row(connection: sqlite3.Connection, row: sqlite3.Row) -> SubtitleVersionContext:
    imdb_id, tmdb_id = _external_ids(
        connection,
        int(row["media_item_id"]),
        str(row["provider_id"]),
        str(row["remote_id"]),
        row["nfo_imdb_id"],
    )
    return SubtitleVersionContext(
        version_id=int(row["id"]),
        playable_id=int(row["playable_id"]),
        media_item_id=int(row["media_item_id"]),
        library_id=int(row["library_id"]),
        kind=cast(MetadataKind, row["kind"]),
        title=str(row["title"]),
        original_title=row["original_title"],
        year=row["year"],
        season=row["season_number"],
        episode=row["episode_number"],
        rel_path=str(row["rel_path"]),
        video_size=row["size"],
        video_sha1=row["sha1"],
        imdb_id=imdb_id,
        tmdb_id=tmdb_id,
    )


def _external_ids(
    connection: sqlite3.Connection,
    media_item_id: int,
    provider_id: str,
    remote_id: str,
    nfo_imdb_id: object,
) -> tuple[str | None, int | None]:
    """条目的 IMDb 和 TMDB ID，字幕搜索用。

    优先取条目记录的外部 ID；没有时按主插件和 NFO 推算。
    """
    imdb_id = str(nfo_imdb_id) if nfo_imdb_id else None
    tmdb_id = int(remote_id) if provider_id == "builtin.tmdb" and remote_id.isdigit() else None
    values = {
        str(row[0]).lower(): str(row[1])
        for row in connection.execute(
            "SELECT scheme, value FROM media_item_external_id WHERE media_item_id = ?",
            (media_item_id,),
        ).fetchall()
    }
    imdb_id = values.get("imdb") or imdb_id
    tmdb = values.get("tmdb")
    if tmdb is not None and tmdb.isdigit():
        tmdb_id = int(tmdb)
    return imdb_id, tmdb_id


def _unavailable_reason(provider: OnlineSubtitleProvider) -> str | None:
    check = getattr(provider, "unavailable_reason", None)
    if check is None:
        return None
    try:
        return cast(str | None, check())
    except Exception as exc:
        logger.warning(
            "字幕插件 %s 检查可用性失败：%s",
            getattr(provider, "id", "?"),
            type(exc).__name__,
        )
        return "字幕插件暂时不可用"


def _candidate_result(plugin_id: str, name: str, candidate: SubtitleCandidate) -> SearchResult:
    return SearchResult(
        provider_id=plugin_id,
        provider_name=name,
        remote_id=candidate.remote_id,
        label=candidate.label,
        release=candidate.release,
        language=candidate.language or None,
        format=candidate.format,
        forced=candidate.forced,
        hearing_impaired=candidate.hearing_impaired,
        score=candidate.score,
        downloads=candidate.downloads,
        uploaded_at=candidate.uploaded_at,
        downloaded=False,
    )


def _with_downloaded(result: SearchResult, downloaded: set[tuple[str, str]]) -> SearchResult:
    if (result.provider_id, result.remote_id) not in downloaded:
        return result
    return SearchResult(
        provider_id=result.provider_id,
        provider_name=result.provider_name,
        remote_id=result.remote_id,
        label=result.label,
        release=result.release,
        language=result.language,
        format=result.format,
        forced=result.forced,
        hearing_impaired=result.hearing_impaired,
        score=result.score,
        downloads=result.downloads,
        uploaded_at=result.uploaded_at,
        downloaded=True,
    )


def _language_matches(candidate: str | None, requested: str | None) -> bool:
    if requested is None:
        return True
    if candidate is None:
        return False
    if candidate == requested:
        return True
    # zh 是笼统的中文，兼容简繁两种细分
    return candidate == "zh" or (requested == "zh" and candidate.startswith("zh"))


def _safe_message(message: str) -> str:
    # 插件给用户的错误里不出现 Token；插件自己也遵守这条，这里再兜一层明显的关键词
    lowered = message.lower()
    if "token" in lowered or "authorization" in lowered or "bearer" in lowered:
        return "字幕站凭据不可用，请到插件设置里检查"
    return message


def _record(row: sqlite3.Row, source: str) -> OnlineSubtitleRecord:
    return OnlineSubtitleRecord(
        id=int(row["id"]),
        label=str(row["label"]),
        language=row["language"],
        forced=bool(row["forced"]),
        hearing_impaired=bool(row["hearing_impaired"]),
        source=source,
        format=str(row["format"]),
    )


def _admin_subtitle(
    row: sqlite3.Row,
    source: FolderSource | None,
    names: dict[str, str],
    resources: ResourceSources,
) -> dict[str, object]:
    online = (
        {
            "provider_id": row["provider_id"],
            "provider_name": names.get(str(row["provider_id"]), row["provider_id"]),
            "release": row["release"],
            "size": row["size"],
            "created_at": row["created_at"],
            "created_by": (
                {"id": row["created_by"], "username": row["username"]}
                if row["created_by"] is not None
                else None
            ),
        }
        if row["source"] == "online"
        else None
    )
    external = None
    if row["source"] == "external":
        deletable, reason = _deletable(source, resources)
        external = {
            "rel_path": row["rel_path"],
            "deletable": deletable,
            "delete_blocked_reason": reason,
        }
    rel_path = str(row["rel_path"] or "")
    return {
        "id": row["id"],
        "source": row["source"],
        "label": row["label"],
        "language": row["language"],
        "forced": bool(row["forced"]),
        "hearing_impaired": bool(row["hearing_impaired"]),
        "default": bool(row["is_default"]),
        "format": row["format"] if row["source"] == "online" else _extension(rel_path),
        "external": external,
        "online": online,
    }


def _deletable(source: FolderSource | None, resources: ResourceSources) -> tuple[bool, str | None]:
    if source is None:
        return False, "文件来源不存在"
    if not source.enabled:
        return False, source.disabled_reason()
    provider = resources.provider(source.plugin_id)
    if provider is None or "delete" not in provider.capabilities:
        return False, f"资源来源「{source.name}」不支持删除"
    return True, None


def _embedded(raw: object) -> list[dict[str, object]] | None:
    if raw is None:
        return None
    try:
        value = json.loads(str(raw))
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, dict)]


def _extension(rel_path: str) -> str | None:
    name = rel_path.rpartition("/")[2]
    suffix = name.rpartition(".")[2]
    return suffix.lower() if suffix and suffix != name else None


def _basename(rel_path: str) -> str:
    return rel_path.rpartition("/")[2]
