from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime
from itertools import groupby

from iris_plugin_sdk import (
    MetadataUnavailable,
    ResourceEntry,
    ResourceListing,
    ResourceNotFound,
    ResourceUnavailable,
)
from iris_plugin_sdk.protocol import WaitReason

from app.clock import Clock, to_iso
from app.db import Database
from app.errors import AppError
from app.library.catalog import CatalogRepository, NfoTask, folder_name
from app.library.collections import PLACEHOLDER_PREFIX, fill_from_tmdb
from app.library.fill import Filler
from app.library.images import ImageError
from app.library.metadata import Metadata
from app.library.nfo import MAX_NFO_BYTES, parse_nfo
from app.library.repository import LibraryFolder, LibraryKind, LibraryRepository
from app.priority import Priority
from app.resources.repository import ResourceRepository
from app.resources.service import FolderSource, ResourceSources, file_key, locator_of
from app.settings import SettingsService
from app.sync.files import FileEntry, MediaFileRepository, PendingFile
from app.sync.jobs import (
    Phase,
    SyncCancelled,
    SyncJob,
    SyncJobRepository,
    SyncStats,
)
from app.sync.purge import MissingFilePurger
from app.sync.targets import DirTarget, LibraryDir, SourceDir

logger = logging.getLogger(__name__)

Sleeper = Callable[[float], None]


def _relative_dir(root: str | None, listing: ResourceListing) -> str | None:
    """资料库目录的根在变动目录里的相对位置；变动目录不在它里面时返回 None。"""
    names = [name for _, name in listing.ancestors]
    if root is None:
        # 资料库目录就是来源根目录：变动目录的整条路径都是相对路径
        return "/".join(names)
    for index, (locator, _) in enumerate(listing.ancestors):
        if locator == root:
            return "/".join(names[index + 1 :])
    return None


class SyncRun:
    """一次同步任务的执行过程和统计。"""

    def __init__(
        self,
        database: Database,
        clock: Clock,
        metadata: Metadata,
        settings: SettingsService,
        resources: ResourceSources,
        sleeper: Sleeper,
        job: SyncJob,
    ) -> None:
        self._database = database
        self._clock = clock
        self._metadata = metadata
        self._settings = settings
        self._resources = resources
        self._sleeper = sleeper
        self._job = job
        self.stats = SyncStats()
        self._labels: dict[int, str] = {}
        self._kinds: dict[int, LibraryKind] = {}
        self._sources: dict[int, FolderSource] = {}
        # 来源扫描到的条目：资料库目录 ID → 相对路径 → 条目，补全时使用
        self._entries: dict[int, dict[str, ResourceEntry]] = {}
        # 这次同步各来源的结果：None 表示正常，否则是第一个错误
        self._source_errors: dict[int, str | None] = {}
        # 这次扫描或补全有失败的资料库：看到的来源不完整，不做同步删除
        self._incomplete: set[int] = set()

    def execute(self) -> None:
        folders = self._active(self._load_folders(self._job.library_id))

        for index, folder in enumerate(folders):
            self._progress("exporting", index, len(folders))
            self._sync_folder(folder)
        self._progress("exporting", len(folders), len(folders))
        self._record_sources()
        self._enrich_all(folders)

        # 扫描时刷新过目录路径，重新读取，识别时要用资料库目录的最新名字
        with self._database.read_session() as connection:
            folders = LibraryRepository(connection, self._clock).folders(self._job.library_id)
        library_ids = sorted({folder.library_id for folder in folders})
        self._purge(library_ids)
        self._recognize(folders)
        self._match(folders, library_ids)
        self._fill_items(library_ids)
        self._scrape_seasons(library_ids)
        self._sync_automatic_collections(library_ids)
        self._download_images(library_ids)
        self._metadata.artwork.cleanup()

    def execute_targeted(self, targets: list[DirTarget]) -> None:
        folders = self._active(self._load_folders(None))
        touched: dict[int, LibraryFolder] = {}
        for index, target in enumerate(targets):
            self._progress("listing", index, len(targets))
            folder = self._sync_target(target, folders)
            if folder is not None:
                touched[folder.id] = folder
        self._progress("listing", len(targets), len(targets))
        self._record_sources()
        if not touched:
            return
        involved = list(touched.values())
        self._enrich_all(involved)
        library_ids = sorted({folder.library_id for folder in involved})
        self._recognize(involved)
        self._match(involved, library_ids)
        self._fill_items(library_ids)
        self._scrape_seasons(library_ids)
        self._sync_automatic_collections(library_ids)
        self._download_images(library_ids)
        self._metadata.artwork.cleanup()

    def _load_folders(self, library_id: int | None) -> list[LibraryFolder]:
        with self._database.read_session() as connection:
            libraries = LibraryRepository(connection, self._clock)
            folders = libraries.folders(library_id)
            all_libraries = libraries.all()
        names = {library.id: library.name for library in all_libraries}
        self._kinds = {library.id: library.kind for library in all_libraries}
        self._labels = {f.id: f"「{names[f.library_id]}」的目录「{f.path}」" for f in folders}
        with self._database.read_session() as connection:
            self._sources = ResourceSources.folders(connection, [f.id for f in folders])
        return folders

    def _active(self, folders: list[LibraryFolder]) -> list[LibraryFolder]:
        """去掉来源已停用的目录。它们看不到来源的现状，所在资料库这次不做同步删除。"""
        active = []
        for folder in folders:
            source = self._sources[folder.id]
            if source.enabled:
                active.append(folder)
                continue
            self.stats.problem(f"{self._labels[folder.id]}所在的{source.disabled_reason()}，已跳过")
            self._incomplete.add(folder.library_id)
        return active

    def _source_result(self, folder: LibraryFolder, error: str | None) -> None:
        source_id = self._sources[folder.id].source_id
        if self._source_errors.get(source_id) is None:
            self._source_errors[source_id] = error

    def _record_sources(self) -> None:
        with self._database.session() as connection:
            repository = ResourceRepository(connection, self._clock)
            for source_id, error in self._source_errors.items():
                repository.record_check(source_id, error)

    # ---- 定向同步 ----

    def _sync_target(self, target: DirTarget, folders: list[LibraryFolder]) -> LibraryFolder | None:
        """列出目标目录并比对，返回它所在的资料库目录；不在任何资料库里或失败时返回 None。"""
        if isinstance(target, LibraryDir):
            return self._sync_library_dir(target, folders)
        return self._sync_source_dir(target, folders)

    def _sync_library_dir(
        self, target: LibraryDir, folders: list[LibraryFolder]
    ) -> LibraryFolder | None:
        """资料库目录下的相对路径；路径已不存在时逐级退到还在的上级目录。"""
        folder = next((f for f in folders if f.id == target.folder_id), None)
        if folder is None:
            return None
        source = self._sources[folder.id]
        rel_dir = target.rel_dir.strip("/")
        while True:
            if not rel_dir:
                locator = source.locator
            else:
                try:
                    locator = self._resources.resolve_path(source, rel_dir, self._wait_source)
                except ResourceUnavailable as exc:
                    if exc.fatal:
                        raise AppError(exc.status_code, exc.code, exc.message) from exc
                    self._resource_failed(
                        folder,
                        f"{self._labels[folder.id]}列目录失败：{exc.message}",
                        exc.message,
                    )
                    return None
                if locator is None:
                    rel_dir = rel_dir.rpartition("/")[0]
                    continue
            try:
                listing = self._resources.open(source, self._wait_source).browse(locator)
            except (ResourceUnavailable, ResourceNotFound) as exc:
                if isinstance(exc, ResourceUnavailable) and exc.fatal:
                    raise AppError(exc.status_code, exc.code, exc.message) from exc
                self._resource_failed(
                    folder,
                    f"{self._labels[folder.id]}列目录失败：{exc.message}",
                    exc.message,
                )
                return None
            self._sync_resource_listing(folder, source, rel_dir, listing, target.deep)
            return folder

    def _sync_source_dir(
        self, target: SourceDir, folders: list[LibraryFolder]
    ) -> LibraryFolder | None:
        """来源里发生变动的目录：找出它所在的资料库目录，只比对这一个目录。"""
        for folder in folders:
            source = self._sources[folder.id]
            if source.source_id != target.source_id:
                continue
            try:
                listing = self._resources.open(source, self._wait_source).browse(target.locator)
            except ResourceNotFound:
                continue
            except ResourceUnavailable as exc:
                if exc.fatal:
                    raise AppError(exc.status_code, exc.code, exc.message) from exc
                self.stats.problem(f"{self._labels[folder.id]}列目录失败：{exc.message}", count=0)
                self._incomplete.add(folder.library_id)
                self._source_result(folder, exc.message)
                return None
            rel_dir = _relative_dir(source.locator, listing)
            if rel_dir is None:
                continue
            self._sync_resource_listing(folder, source, rel_dir, listing)
            return folder
        return None

    # ---- 扫描与比对 ----

    def _sync_folder(self, folder: LibraryFolder) -> None:
        self._sync_resource_folder(folder)

    def _sync_resource_listing(
        self,
        folder: LibraryFolder,
        source: FolderSource,
        rel_dir: str,
        listing: ResourceListing,
        deep: bool = False,
    ) -> bool:
        """用 browse 递归列出定向目标，未变化的已知子目录保持在作用域外。"""
        entries = listing.entries
        prefix = f"{rel_dir}/" if rel_dir else ""
        with self._database.read_session() as connection:
            repository = MediaFileRepository(connection, self._clock)
            known = set() if deep else repository.known_dirs(folder.id, prefix)
        kept: set[str] = set()
        selected: list[ResourceEntry] = []

        def walk(
            current_rel: str,
            current_entries: Sequence[ResourceEntry],
        ) -> None:
            for entry in current_entries:
                relative = (
                    f"{current_rel}/{entry.relative_path}" if current_rel else entry.relative_path
                )
                if entry.is_dir:
                    if current_rel == rel_dir and entry.relative_path in known:
                        kept.add(entry.relative_path)
                        continue
                    try:
                        child = self._resources.open(source, self._wait_source).browse(
                            entry.locator
                        )
                    except (ResourceNotFound, ResourceUnavailable) as exc:
                        if isinstance(exc, ResourceUnavailable) and exc.fatal:
                            raise AppError(exc.status_code, exc.code, exc.message) from exc
                        raise ResourceUnavailable(exc.message) from exc
                    walk(relative, child.entries)
                else:
                    selected.append(
                        ResourceEntry(
                            entry.locator,
                            relative,
                            entry.kind,
                            entry.size,
                            entry.sha1,
                            False,
                            entry.playback_locator,
                            entry.key,
                            entry.identity,
                            entry.root_path,
                        )
                    )

        try:
            walk(rel_dir, entries)
        except ResourceUnavailable as exc:
            self._resource_failed(
                folder, f"{self._labels[folder.id]}扫描失败：{exc.message}", exc.message
            )
            return False

        def in_scope(path: str) -> bool:
            if not path.startswith(prefix):
                return False
            first, nested, _ = path[len(prefix) :].partition("/")
            return not nested or first not in kept

        with self._database.session() as connection:
            diff = MediaFileRepository(connection, self._clock).apply_snapshot(
                folder.id,
                [entry.relative_path for entry in selected],
                in_scope,
            )
        self._entries[folder.id] = {entry.relative_path: entry for entry in selected}
        self.stats.removed += diff.vanished
        self._source_result(folder, None)
        return True

    def _sync_resource_folder(
        self, folder: LibraryFolder, rel_dir: str | None = None, deep: bool = True
    ) -> LibraryFolder | None:
        """扫描来源目录并比对，返回所在目录；失败时返回 None。

        扫描失败或来源看起来没有挂载（一个文件也没扫到而库里还有文件）时跳过这个目录：
        不能据此把文件标为失效，所在资料库这次也不做同步删除。
        """
        source = self._sources[folder.id]
        label = self._labels[folder.id]
        try:
            entries = self._scan(folder, source)
            path = next((entry.root_path for entry in entries if entry.root_path), None)
            if path is not None and path != source.display_path:
                with self._database.session() as connection:
                    LibraryRepository(connection, self._clock).refresh_folder_path(folder.id, path)
                source = replace(source, display_path=path)
                self._sources[folder.id] = source
        except ResourceUnavailable as exc:
            if exc.fatal:
                raise AppError(exc.status_code, exc.code, exc.message) from exc
            self._resource_failed(folder, f"{label}扫描失败：{exc.message}", exc.message)
            return None
        except ResourceNotFound as exc:
            message = f"{label}{exc.message}，已跳过"
            self._resource_failed(folder, message, exc.message)
            return None
        except Exception:
            logger.exception("scanning folder %s of source %s failed", folder.id, source.name)
            self._resource_failed(folder, f"{label}扫描失败，详见服务日志", "扫描出错")
            return None
        scope: Callable[[str], bool] | None
        if rel_dir is not None:
            prefix = f"{rel_dir.strip('/')}/" if rel_dir.strip("/") else ""
            with self._database.session() as connection:
                known = MediaFileRepository(connection, self._clock).known_dirs(folder.id, prefix)
            kept = set() if deep else known
            entries = [
                entry
                for entry in entries
                if entry.relative_path.startswith(prefix)
                and (
                    deep
                    or not (rest := entry.relative_path[len(prefix) :]).partition("/")[1]
                    or rest.partition("/")[0] not in kept
                )
            ]

            def scope(path: str) -> bool:
                rest = path[len(prefix) :]
                first, nested, _ = rest.partition("/")
                return path.startswith(prefix) and (deep or not nested or first not in kept)
        else:
            scope = None
        with self._database.session() as connection:
            repository = MediaFileRepository(connection, self._clock)
            if not entries and repository.has_present(folder.id):
                message = "一个文件也没扫到，可能没有挂载"
                self._resource_failed(folder, f"{label}{message}，已跳过", message)
                return None
            diff = repository.apply_snapshot(
                folder.id,
                [entry.relative_path for entry in entries],
                scope,
            )
        self._entries[folder.id] = {entry.relative_path: entry for entry in entries}
        self.stats.removed += diff.vanished
        self._source_result(folder, None)
        return folder

    def _scan(self, folder: LibraryFolder, source: FolderSource) -> list[ResourceEntry]:
        """扫描来源目录。来源声明 known_scan 时带上库里已有的记录，让它少访问云端；
        没有声明 known_scan 的来源照旧完整扫描。"""
        provider = self._resources.provider(source.plugin_id)
        source_reader = self._resources.open(source, self._wait_source)
        if provider is None or "known_scan" not in provider.capabilities:
            return source_reader.scan(source.locator)
        with self._database.read_session() as connection:
            rows = MediaFileRepository(connection, self._clock).known_entries(folder.id)
        known = {
            path: ResourceEntry(
                locator=row["playback_locator"] or row["identity"] or "",
                relative_path=path,
                kind=row["kind"],
                size=row["size"],
                sha1=row["sha1"],
                playback_locator=row["playback_locator"],
                key=row["identity"],
                identity=row["identity"],
            )
            for path, row in rows.items()
        }
        return source_reader.scan(source.locator, known=known)

    def _resource_failed(self, folder: LibraryFolder, problem: str, error: str) -> None:
        self.stats.problem(problem)
        self._incomplete.add(folder.library_id)
        self._source_result(folder, error)

    # ---- 补全 ----

    def _enrich_all(self, folders: list[LibraryFolder]) -> None:
        by_id = {folder.id: folder for folder in folders}
        with self._database.read_session() as connection:
            pending = MediaFileRepository(connection, self._clock).pending(list(by_id))

        # 按所在目录分组，每个目录列一次。字符串排序会把「a/b.mkv」「a/b/c.mkv」「a/c.mkv」
        # 中同目录的文件隔开，所以按分组键重新排序
        def directory(file: PendingFile) -> tuple[int, str]:
            return file.folder_id, file.rel_path.rpartition("/")[0]

        groups = [
            (key, list(files)) for key, files in groupby(sorted(pending, key=directory), directory)
        ]
        for index, ((folder_id, rel_dir), files) in enumerate(groups):
            self._progress("enriching", index, len(groups))
            self._enrich_group(by_id[folder_id], rel_dir, files)
        self._progress("enriching", len(groups), len(groups))

    def _enrich_group(self, folder: LibraryFolder, rel_dir: str, files: list[PendingFile]) -> None:
        source = self._sources[folder.id]
        entries = self._entries.get(folder.id)
        if entries is None:
            # 这次没扫描这个目录（扫描失败），留到下次同步补全
            return
        with self._database.session() as connection:
            repository = MediaFileRepository(connection, self._clock)
            for file in files:
                found = entries.get(file.rel_path)
                if found is None:
                    if repository.drop(file) and file.kind != "nfo":
                        self.stats.removed += 1
                    continue
                entry = FileEntry(
                    identity=found.key
                    or found.identity
                    or file_key(source.source_id, found.locator),
                    name=file.rel_path.rpartition("/")[2],
                    size=found.size or 0,
                    playback_locator=found.playback_locator or found.locator,
                    sha1=found.sha1 or "",
                )
                self._count(repository.enrich(file, entry, self._job.started_at), file)

    def _count(self, outcome: str, file: PendingFile) -> None:
        if file.kind == "nfo":
            # NFO 只是匹配的辅助信息，不计入文件统计
            return
        if outcome == "renamed":
            self.stats.renamed += 1
            self.stats.removed -= 1
        elif outcome == "moved":
            self.stats.renamed += 1
        elif outcome == "restored":
            self.stats.restored += 1
        else:
            self.stats.added += 1

    # ---- 同步删除 ----

    def _purge(self, library_ids: list[int]) -> None:
        """删除失效超过保留期的文件。成批到期的留给管理员确认，见 MissingFilePurger。"""
        for library_id in library_ids:
            if library_id in self._incomplete:
                continue
            grace_days = self._library_setting(
                library_id, "missing_grace_days", self._settings.missing_grace_days()
            )
            with self._database.session() as connection:
                purger = MissingFilePurger(connection, self._clock)
                plan = purger.plan(library_id, grace_days)
                if plan is None or plan.due == 0:
                    continue
                if plan.held:
                    self.stats.problem(
                        f"「{plan.library_name}」有 {plan.due} 个视频在资源来源上消失超过 "
                        f"{grace_days} 天，数量较多，没有自动删除，请在同步页确认",
                        count=0,
                    )
                    continue
                self.stats.purged += purger.purge(library_id, grace_days)

    # ---- 识别与刮削 ----

    def _recognize(self, folders: list[LibraryFolder]) -> None:
        """识别视频、过滤附加内容、按作品分组，再读取等待匹配的作品的 NFO。"""
        tasks: list[tuple[LibraryFolder, NfoTask]] = []
        for index, folder in enumerate(folders):
            self._progress("recognizing", index, len(folders))
            extras_min_mb = self._library_setting(
                folder.library_id, "extras_min_mb", self._settings.extras_min_mb()
            )
            with self._database.session() as connection:
                result = CatalogRepository(connection, self._clock).assign_works(
                    folder.id,
                    self._kinds[folder.library_id],
                    folder_name(folder.path),
                    extras_min_mb * 1024 * 1024,
                )
            self.stats.ignored += result.ignored
            tasks.extend((folder, task) for task in result.nfo_tasks)
        failed = 0
        for index, (folder, task) in enumerate(tasks):
            self._progress("recognizing", index, len(tasks))
            source = self._sources.get(folder.id)
            if source is not None and not source.enabled:
                continue
            try:
                if source is None:
                    raise ResourceNotFound("文件来源不存在")
                locator = locator_of(task.identity)
                data = self._resources.open(source).read(locator, MAX_NFO_BYTES)
            except (ResourceUnavailable, ResourceNotFound):
                # 读不到 NFO 就按片名搜索，不影响匹配
                failed += 1
                continue
            ids = parse_nfo(data, self._kinds[folder.library_id])
            with self._database.session() as connection:
                CatalogRepository(connection, self._clock).set_nfo(
                    task.work_id, task.sha1, ids.tmdb_id, ids.imdb_id
                )
        if failed:
            self.stats.problem(f"{failed} 个 NFO 读取失败，这些作品按片名搜索匹配", count=0)
        self._progress("recognizing", len(tasks), len(tasks))

    def _match(self, folders: list[LibraryFolder], library_ids: list[int]) -> None:
        matcher = self._metadata.matcher
        works = matcher.pending_works(library_ids)
        providers = self._metadata.providers
        provider_of = {
            library_id: providers.library_provider(library_id)
            for library_id in {work.library_id for work in works}
        }
        # 元数据插件不能用时，它负责的资料库的作品留到下次同步，其他资料库照常匹配
        blocked = self._blocked_providers(set(provider_of.values()))
        for provider_id, reason in blocked.items():
            waiting = sum(1 for w in works if provider_of[w.library_id] == provider_id)
            self.stats.problem(f"{reason}，{waiting} 部作品等待匹配，处理后再同步", count=0)
        works = [work for work in works if provider_of[work.library_id] not in blocked]
        for index, work in enumerate(works):
            self._progress("matching", index, len(works))
            provider_id = provider_of[work.library_id]
            if provider_id in blocked:
                continue
            try:
                outcome = matcher.match(work, Priority.BACKGROUND)
            except MetadataUnavailable as exc:
                blocked[provider_id] = exc.message
                left = sum(1 for w in works[index:] if provider_of[w.library_id] == provider_id)
                self.stats.problem(f"匹配中止：{exc.message}，{left} 部作品下次同步再匹配", count=0)
                continue
            if outcome == "matched":
                self.stats.matched += 1
            else:
                self.stats.unmatched += 1
        self._progress("matching", len(works), len(works))
        for folder in folders:
            with self._database.session() as connection:
                CatalogRepository(
                    connection, self._clock, self._resources.local_subtitles()
                ).place_folder(folder.id, self._kinds[folder.library_id], folder_name(folder.path))
        self.stats.waiting = len(matcher.pending_works(library_ids))

    def _fill_items(self, library_ids: list[int]) -> None:
        """新匹配、还没补过缺的条目：用后面的元数据下载器补字段，再按图片列表选图。"""
        if not library_ids:
            return
        marks = ", ".join("?" for _ in library_ids)
        with self._database.read_session() as connection:
            item_ids = [
                int(row[0])
                for row in connection.execute(
                    f"SELECT id FROM media_item WHERE filled_at IS NULL "
                    f"AND library_id IN ({marks}) ORDER BY id",
                    tuple(library_ids),
                ).fetchall()
            ]
        if not item_ids:
            return
        filler = self._metadata.filler
        reported: set[str] = set()
        # 报过暂时不可用的插件这次同步不再请求；涉及的条目不记 filled_at，下次同步再补
        blocked: set[str] = set()
        for index, item_id in enumerate(item_ids):
            self._progress("filling", index, len(item_ids))
            try:
                plan = filler.fill_item(item_id, Priority.BACKGROUND, blocked=blocked)
            except Exception:
                logger.exception("补缺条目 %s 失败", item_id)
                self.stats.problem("补缺元数据时出错，详见服务日志", count=0)
                continue
            for problem in plan.problems:
                if problem in reported:
                    continue
                reported.add(problem)
                self.stats.problem(problem, count=0)
        self._progress("filling", len(item_ids), len(item_ids))

    def _scrape_seasons(self, library_ids: list[int]) -> None:
        scraper = self._metadata.scraper
        seasons = scraper.stale_seasons(library_ids)
        # 插件不能用时它的季静默留到下次：匹配阶段已经报告过原因
        blocked = self._blocked_providers({season[1] for season in seasons})
        seasons = [season for season in seasons if season[1] not in blocked]
        for index, (season_id, provider_id, remote_id, number) in enumerate(seasons):
            self._progress("scraping", index, len(seasons))
            if provider_id in blocked:
                continue
            try:
                scraper.scrape_season(
                    season_id, provider_id, remote_id, number, Priority.BACKGROUND
                )
            except MetadataUnavailable as exc:
                blocked[provider_id] = exc.message
                left = sum(1 for season in seasons[index:] if season[1] == provider_id)
                message = f"拉取季信息中止：{exc.message}，{left} 季下次同步再拉取"
                self.stats.problem(message, count=0)
        if seasons:
            self._progress("scraping", len(seasons), len(seasons))

    def _download_images(self, library_ids: list[int]) -> None:
        artwork = self._metadata.artwork
        tasks = artwork.pending(library_ids)
        # 图片可能来自 Fanart 这类只提供 image.fetch 的插件，不能用元数据插件的 get 判断
        blocked = Filler.blocked_image_providers(
            self._metadata.providers, [task.provider_id for task in tasks]
        )
        tasks = [task for task in tasks if task.provider_id not in blocked]
        broken = 0
        for index, task in enumerate(tasks):
            self._progress("images", index, len(tasks))
            if task.provider_id in blocked:
                continue
            try:
                artwork.download(task, Priority.BACKGROUND)
            except ImageError:
                broken += 1
            except MetadataUnavailable as exc:
                blocked[task.provider_id] = exc.message
                left = sum(1 for t in tasks[index:] if t.provider_id == task.provider_id)
                self.stats.problem(f"下载图片中止：{exc.message}，{left} 张下次同步再下载", count=0)
        if broken:
            self.stats.problem(f"{broken} 张图片无法识别，已跳过", count=0)
        if tasks:
            self._progress("images", len(tasks), len(tasks))

    def _blocked_providers(self, provider_ids: set[str]) -> dict[str, str]:
        """不能请求的元数据插件和原因（停用、启动失败、没配置等）。"""
        blocked: dict[str, str] = {}
        for provider_id in sorted(provider_ids):
            try:
                reason = self._metadata.providers.get(provider_id).unavailable_reason()
            except AppError as exc:
                reason = exc.message
            if reason is not None:
                blocked[provider_id] = reason
        return blocked

    def _library_setting(self, library_id: int, key: str, fallback: int) -> int:
        with self._database.read_session() as connection:
            row = connection.execute(
                "SELECT value FROM library_setting WHERE library_id = ? AND key = ?",
                (library_id, key),
            ).fetchone()
        try:
            return int(row["value"]) if row is not None else fallback
        except (TypeError, ValueError):
            return fallback

    def _sync_automatic_collections(self, library_ids: list[int]) -> None:
        for library_id in library_ids:
            if self._library_setting(library_id, "auto_collections", 0) != 1:
                continue
            with self._database.session() as connection:
                groups = connection.execute(
                    "SELECT tmdb_collection_id, GROUP_CONCAT(id) AS ids "
                    "FROM media_item WHERE library_id=? AND tmdb_collection_id IS NOT NULL "
                    "GROUP BY tmdb_collection_id HAVING COUNT(*) >= 2",
                    (library_id,),
                ).fetchall()
                active_ids: set[int] = set()
                now = to_iso(self._clock.now())
                for group in groups:
                    tmdb_id = int(group["tmdb_collection_id"])
                    collection = connection.execute(
                        "SELECT id FROM collection WHERE library_id=? AND tmdb_collection_id=?",
                        (library_id, tmdb_id),
                    ).fetchone()
                    if collection is None:
                        cur = connection.execute(
                            "INSERT INTO collection(library_id,title,tmdb_collection_id,automatic,"
                            "created_at,updated_at) "
                            "VALUES (?,?,?,1,?,?)",
                            (library_id, f"{PLACEHOLDER_PREFIX}{tmdb_id}", tmdb_id, now, now),
                        )
                        assert cur.lastrowid is not None
                        collection_id = int(cur.lastrowid)
                    else:
                        collection_id = int(collection["id"])
                        connection.execute(
                            "UPDATE collection SET updated_at=? WHERE id=?", (now, collection_id)
                        )
                    fill_from_tmdb(connection, collection_id, tmdb_id)
                    active_ids.add(collection_id)
                    ids = [int(value) for value in str(group["ids"]).split(",") if value]
                    connection.executemany(
                        "INSERT INTO collection_item(collection_id,media_item_id,position,manual) "
                        "VALUES (?,?,?,0) ON CONFLICT(collection_id,media_item_id) DO UPDATE SET "
                        "position=excluded.position,manual=collection_item.manual",
                        [
                            (collection_id, item_id, position)
                            for position, item_id in enumerate(ids)
                        ],
                    )
                    marks = ",".join("?" * len(ids)) or "NULL"
                    connection.execute(
                        f"DELETE FROM collection_item WHERE collection_id=? AND manual=0 "
                        f"AND media_item_id NOT IN ({marks})",
                        (collection_id, *ids),
                    )
                if active_ids:
                    marks = ",".join("?" * len(active_ids))
                    connection.execute(
                        f"DELETE FROM collection WHERE library_id=? AND automatic=1 "
                        f"AND id NOT IN ({marks})",
                        (library_id, *active_ids),
                    )

    # ---- 同步等待 ----

    def _wait(self, until: datetime, reason: WaitReason) -> None:
        with self._database.session() as connection:
            SyncJobRepository(connection, self._clock).waiting(
                self._job.id, until, reason, self.stats
            )
        self._sleep((until - self._clock.now()).total_seconds())

    def _wait_source(self, until: datetime, reason: WaitReason) -> None:
        if reason == "rate_limited":
            self.stats.paused += 1
        self._wait(until, reason)
        with self._database.session() as connection:
            SyncJobRepository(connection, self._clock).waiting(self._job.id, None, None, self.stats)

    def _sleep(self, seconds: float) -> None:
        if seconds > 0:
            self._sleeper(seconds)

    def _progress(self, phase: Phase, done: int, total: int) -> None:
        with self._database.session() as connection:
            repository = SyncJobRepository(connection, self._clock)
            repository.progress(self._job.id, phase, done, total, self.stats)
            if repository.cancelled(self._job.id):
                raise SyncCancelled()
