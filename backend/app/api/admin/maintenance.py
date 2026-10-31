"""计划任务、资料库统计与元数据刷新、回收站接口。"""

from __future__ import annotations

import logging
import threading

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.schemas import Out
from app.deps import (
    AdminUser,
    AppClock,
    AppDatabase,
    AppMetadata,
    Backups,
    Db,
    Images,
    PluginRepositoriesDep,
    ReadDb,
    SubtitleAuto,
)
from app.errors import AppError
from app.library.recycle_bin import RecycleBin
from app.library.repository import LibraryRepository
from app.library.stats import LibraryStatsQueries
from app.ops.maintenance import (
    SUBTITLE_DOWNLOAD_KEY,
    check_plugin_updates,
    start_subtitle_download,
)
from app.ops.schedule import ScheduledTask, ScheduledTaskRepository, ScheduledTaskRunner
from app.ops.tasks import TaskRepository, tracked_task
from app.plugins.repositories import CHECK_PLUGIN_UPDATES_KEY
from app.priority import Priority

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


class ScheduledTaskOut(Out):
    key: str
    # 一直以 0/1 返回，客户端按这个格式解析
    enabled: int
    interval: int
    last_run_at: str | None
    last_task_id: int | None

    @classmethod
    def of(cls, task: ScheduledTask) -> ScheduledTaskOut:
        return cls(
            key=task.key,
            enabled=int(task.enabled),
            interval=task.interval,
            last_run_at=task.last_run_at,
            last_task_id=task.last_task_id,
        )


class TaskStartedOut(Out):
    task_id: int


class LibraryStatsOut(Out):
    items: int
    # 资料库没有条目时为空，不是 0
    movies: int | None
    series: int | None
    episodes: int
    bytes: int
    unmatched: int
    missing: int


class RefreshStartedOut(TaskStartedOut):
    total: int


class RecycledFileOut(Out):
    media_file_id: int
    rel_path: str
    size: int | None
    missing_at: str | None
    library_id: int
    library_name: str
    media_item_id: int | None
    title: str | None


class RecycleBinEmptiedOut(TaskStartedOut):
    deleted: int


@router.get("/scheduled-tasks")
def scheduled_tasks(_: AdminUser, db: ReadDb, clock: AppClock) -> list[ScheduledTaskOut]:
    return [ScheduledTaskOut.of(task) for task in ScheduledTaskRepository(db, clock).all()]


class ScheduledPatch(BaseModel):
    enabled: bool | None = None
    interval: int | None = Field(default=None, ge=60, le=31_536_000)


@router.patch("/scheduled-tasks/{key}")
def patch_scheduled(
    key: str, body: ScheduledPatch, _: AdminUser, db: Db, clock: AppClock
) -> ScheduledTaskOut:
    task = ScheduledTaskRepository(db, clock).patch(
        key, enabled=body.enabled, interval=body.interval
    )
    return ScheduledTaskOut.of(task)


@router.post("/scheduled-tasks/{key}/run", status_code=202)
def run_scheduled(
    key: str,
    admin_user: AdminUser,
    database: AppDatabase,
    clock: AppClock,
    backups: Backups,
    images: Images,
    subtitle_auto: SubtitleAuto,
    repositories: PluginRepositoriesDep,
) -> TaskStartedOut:
    if key == SUBTITLE_DOWNLOAD_KEY:
        # 自动下载字幕要在独立线程里跑，不能占着请求的写事务
        task_id = start_subtitle_download(
            database, clock, subtitle_auto, created_by=admin_user.id, automatic=False
        )
        if task_id is None:
            raise AppError(409, "task_already_running", "下载字幕正在运行，请稍后再试")
        return TaskStartedOut(task_id=task_id)
    if key == CHECK_PLUGIN_UPDATES_KEY:
        # 拉取索引会联网，必须在任务事务之外执行
        task_id = check_plugin_updates(
            database, clock, repositories, created_by=admin_user.id, automatic=False
        )
        return TaskStartedOut(task_id=task_id)
    with database.session() as connection:
        task_id = ScheduledTaskRunner(connection, clock, backups, images).run_now(
            key, created_by=admin_user.id
        )
    return TaskStartedOut(task_id=task_id)


class ImageCleanupOut(Out):
    deleted_images: int
    task_id: int


@router.post("/images/cleanup")
def cleanup_images(
    admin_user: AdminUser, database: AppDatabase, clock: AppClock, images: Images
) -> ImageCleanupOut:
    """立即清理没有引用的图片和无出演的人物，结果记在后台任务里。"""
    with tracked_task(database, clock, "cleanup_images", created_by=admin_user.id) as task:
        with database.session() as connection:
            deleted = images.cleanup(connection)
        task.result["deleted_images"] = deleted
    return ImageCleanupOut(deleted_images=deleted, task_id=task.id)


@router.get("/libraries/{library_id}/stats")
def library_stats(library_id: int, _: AdminUser, db: ReadDb, clock: AppClock) -> LibraryStatsOut:
    LibraryRepository(db, clock).require(library_id)
    return LibraryStatsOut.model_validate(LibraryStatsQueries(db).summary(library_id))


@router.post("/libraries/{library_id}/refresh-metadata", status_code=202)
def refresh_library_metadata(
    library_id: int,
    admin_user: AdminUser,
    database: AppDatabase,
    clock: AppClock,
    metadata: AppMetadata,
) -> RefreshStartedOut:
    with database.session() as db:
        libraries = LibraryRepository(db, clock)
        libraries.require(library_id)
        ids = libraries.item_ids(library_id)
        task = TaskRepository(db, clock).create(
            "refresh_library_metadata",
            total=len(ids),
            created_by=admin_user.id,
            result={"library_id": library_id},
        )

    def worker() -> None:
        done = 0
        try:
            for item_id in ids:
                metadata.scraper.refresh_item(item_id, Priority.BACKGROUND)
                done += 1
                with database.session() as db:
                    TaskRepository(db, clock).progress(task.id, "scraping", done, len(ids))
            with database.session() as db:
                TaskRepository(db, clock).finish(
                    task.id, result={"library_id": library_id, "total": len(ids), "done": done}
                )
        except Exception as exc:
            logger.exception("资料库 %s 元数据刷新失败", library_id)
            with database.session() as db:
                TaskRepository(db, clock).finish(task.id, error=str(exc), result={"done": done})

    threading.Thread(target=worker, name="library-metadata-refresh", daemon=True).start()
    return RefreshStartedOut(task_id=task.id, total=len(ids))


@router.get("/recycle-bin")
def recycle_bin(
    _: AdminUser, db: ReadDb, clock: AppClock, library_id: int | None = None
) -> list[RecycledFileOut]:
    return [
        RecycledFileOut.model_validate(file) for file in RecycleBin(db, clock).files(library_id)
    ]


@router.post("/recycle-bin/empty", status_code=202)
def empty_recycle_bin(
    admin_user: AdminUser,
    database: AppDatabase,
    clock: AppClock,
    library_id: int | None = None,
) -> RecycleBinEmptiedOut:
    with database.session() as db:
        deleted = RecycleBin(db, clock).empty(library_id)
        task = TaskRepository(db, clock).create(
            "empty_recycle_bin", created_by=admin_user.id, result={"deleted": deleted}
        )
        TaskRepository(db, clock).finish(task.id, result={"deleted": deleted})
    return RecycleBinEmptiedOut(task_id=task.id, deleted=deleted)
