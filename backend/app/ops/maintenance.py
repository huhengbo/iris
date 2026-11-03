"""轻量级计划维护循环。

计划任务仍由数据库中的 ``scheduled_task`` 驱动，手动执行和自动执行共用同一套
任务语义。维护循环只负责到期判定，单次执行失败会留下后台任务错误，不影响同步线程。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from app.clock import Clock
from app.db import Database
from app.library.images import ImageStore
from app.library.metadata import Metadata
from app.ops.backup import BackupService
from app.ops.cleanup import Cleanup
from app.ops.schedule import ScheduledTaskRepository
from app.ops.tasks import TaskRepository, TrackedTask
from app.plugins.repositories import CHECK_PLUGIN_UPDATES_KEY, PluginRepositories
from app.priority import Priority
from app.subtitles.auto import SubtitleAutoDownloader

logger = logging.getLogger(__name__)
TICK_SECONDS = 30.0
SUBTITLE_DOWNLOAD_KEY = "download_subtitles"

# 「下载字幕」在自己的线程里跑，同一时间只允许一个；锁不放进 ScheduledTaskRunner，
# 因为自动执行和手动执行都要经过它
_subtitle_lock = threading.Lock()


def run_maintenance(
    database: Database,
    backups: BackupService,
    images: ImageStore,
    metadata: Metadata,
    subtitles: SubtitleAutoDownloader,
    repositories: PluginRepositories,
    clock: Clock,
    stop: threading.Event,
) -> None:
    """按 ``scheduled_task.interval`` 执行已启用的维护任务。"""

    while not stop.wait(TICK_SECONDS):
        try:
            run_due(database, backups, images, metadata, subtitles, repositories, clock)
        except Exception:
            logger.exception("maintenance schedule tick failed")


def run_due(
    database: Database,
    backups: BackupService,
    images: ImageStore,
    metadata: Metadata,
    subtitles: SubtitleAutoDownloader,
    repositories: PluginRepositories,
    clock: Clock,
) -> None:
    now = clock.now()
    with database.session() as connection:
        scheduled = [
            task for task in ScheduledTaskRepository(connection, clock).all() if task.enabled
        ]
    for task in scheduled:
        last = _parse(task.last_run_at)
        interval = max(60, task.interval or 86400)
        if last is not None and now < last + timedelta(seconds=interval):
            continue
        _run_one(database, backups, images, metadata, subtitles, repositories, clock, task.key)


def _run_one(
    database: Database,
    backups: BackupService,
    images: ImageStore,
    metadata: Metadata,
    subtitles: SubtitleAutoDownloader,
    repositories: PluginRepositories,
    clock: Clock,
    key: str,
) -> None:
    # 自动下载字幕在独立线程里跑，不占维护循环；已经有一个在跑就等下一次 tick
    if key == SUBTITLE_DOWNLOAD_KEY:
        start_subtitle_download(database, clock, subtitles, created_by=None, automatic=True)
        return
    if key == CHECK_PLUGIN_UPDATES_KEY:
        check_plugin_updates(database, clock, repositories, created_by=None, automatic=True)
        return
    with database.session() as connection:
        task = TaskRepository(connection, clock).create(f"scheduled:{key}")
    result: dict[str, object] = {"scheduled_key": key, "automatic": True}
    error: str | None = None
    try:
        if key == "backup":
            result["backup"] = backups.create().name
            result["deleted_backups"] = backups.prune()
        elif key == "optimize_database":
            with database.session() as connection:
                Cleanup(connection, clock).optimize_database()
        elif key == "vacuum":
            # VACUUM 需要事务外执行；WAL 下由 SQLite 的定期 checkpoint
            # 负责回收空间，计划项仍记录为已处理，避免阻塞维护循环。
            result["status"] = "deferred"
        elif key == "cleanup_images":
            with database.session() as connection:
                result["deleted_images"] = images.cleanup(connection)
        elif key == "cleanup_sessions":
            with database.session() as connection:
                result["deleted_sessions"] = Cleanup(connection, clock).expired_sessions()
        elif key == "cleanup_play_events":
            with database.session() as connection:
                result["deleted_events"] = Cleanup(connection, clock).old_play_events()
        elif key == "cleanup_ai_chats":
            with database.session() as connection:
                result["deleted_chats"] = Cleanup(connection, clock).old_ai_chats()
        elif key == "refresh_metadata":
            with database.session() as connection:
                stale = connection.execute(
                    "SELECT id FROM media_item WHERE scraped_at IS NULL "
                    "OR scraped_at < datetime('now', '-30 days') ORDER BY scraped_at LIMIT 10"
                ).fetchall()
            refreshed = 0
            for row in stale:
                metadata.scraper.refresh_item(int(row["id"]), Priority.BACKGROUND)
                refreshed += 1
            result["refreshed"] = refreshed
        elif key == "empty_recycle_bin":
            with database.session() as connection:
                result["deleted_files"] = Cleanup(connection, clock).missing_files()
        else:
            result["status"] = "unsupported"
    except Exception as exc:  # 任务必须落盘为失败，不能让循环中止
        error = str(exc)
        result["error"] = error
    with database.session() as connection:
        repository = TaskRepository(connection, clock)
        repository.finish(task.id, error=error, result=result)
        ScheduledTaskRepository(connection, clock).mark_run(key, task.id)


def check_plugin_updates(
    database: Database,
    clock: Clock,
    repositories: PluginRepositories,
    *,
    created_by: int | None,
    automatic: bool,
) -> int:
    """拉取所有已启用仓库的索引；网络请求在任务事务之外执行。"""
    with database.session() as connection:
        task = TaskRepository(connection, clock).create(
            f"scheduled:{CHECK_PLUGIN_UPDATES_KEY}",
            created_by=created_by,
            result={
                "scheduled_key": CHECK_PLUGIN_UPDATES_KEY,
                "automatic": automatic,
                "manual": not automatic,
            },
        )
    result: dict[str, object] = {
        "scheduled_key": CHECK_PLUGIN_UPDATES_KEY,
        "automatic": automatic,
        "manual": not automatic,
    }
    error: str | None = None
    try:
        result.update(repositories.refresh_all())
    except Exception as exc:
        logger.exception("检查插件更新失败")
        error = str(exc)
        result["error"] = error
    with database.session() as connection:
        TaskRepository(connection, clock).finish(task.id, error=error, result=result)
        ScheduledTaskRepository(connection, clock).mark_run(CHECK_PLUGIN_UPDATES_KEY, task.id)
    return task.id


def start_subtitle_download(
    database: Database,
    clock: Clock,
    downloader: SubtitleAutoDownloader,
    *,
    created_by: int | None,
    automatic: bool,
) -> int | None:
    """在独立线程执行「下载字幕」，返回后台任务 ID；已经有一个在跑时返回 None。

    任务行先在调用方线程里建好，客户端马上就能轮询到它。
    """
    if not _subtitle_lock.acquire(blocking=False):
        return None
    try:
        with database.session() as connection:
            ScheduledTaskRepository(connection, clock).ensure(SUBTITLE_DOWNLOAD_KEY, enabled=True)
            task = TaskRepository(connection, clock).create(
                f"scheduled:{SUBTITLE_DOWNLOAD_KEY}",
                created_by=created_by,
                result={
                    "scheduled_key": SUBTITLE_DOWNLOAD_KEY,
                    "automatic": automatic,
                    "manual": not automatic,
                },
            )
    except BaseException:
        _subtitle_lock.release()
        raise
    threading.Thread(
        target=_run_subtitle_download,
        args=(database, clock, downloader, task.id),
        name="subtitle-download",
        daemon=True,
    ).start()
    return task.id


def _run_subtitle_download(
    database: Database, clock: Clock, downloader: SubtitleAutoDownloader, task_id: int
) -> None:
    handle = TrackedTask(task_id, database, clock)
    error: str | None = None
    result: dict[str, int] = {}
    try:
        result = downloader.run(handle, lambda: _cancel_requested(database, clock, task_id))
    except Exception as exc:
        logger.exception("自动下载字幕失败")
        error = str(exc)
    finally:
        try:
            with database.session() as connection:
                tasks = TaskRepository(connection, clock)
                tasks.finish(task_id, error=error, result=result)
                ScheduledTaskRepository(connection, clock).mark_run(SUBTITLE_DOWNLOAD_KEY, task_id)
        finally:
            _subtitle_lock.release()


def _cancel_requested(database: Database, clock: Clock, task_id: int) -> bool:
    with database.read_session() as connection:
        return TaskRepository(connection, clock).cancel_requested(task_id)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
