"""季和单集的元数据、刷新与删除管理接口。"""

from __future__ import annotations

from contextlib import suppress
from typing import Any, Literal

from fastapi import APIRouter, Response
from iris_plugin_sdk import ResourceNotFound
from pydantic import BaseModel, Field

from app.api.schemas import Out
from app.deps import (
    AdminUser,
    AppClock,
    AppDatabase,
    AppMetadata,
    Db,
    DeleteExecutor,
    Resources,
)
from app.errors import AppError
from app.library.seasons import SeasonRepository
from app.ops.tasks import TaskRepository, tracked_task
from app.priority import Priority
from app.resources.deletable import require_deletable

router = APIRouter(prefix="/admin", tags=["admin"])


class MetadataPatch(BaseModel):
    values: dict[str, str | None] = Field(default_factory=dict)
    locked: dict[str, bool] = Field(default_factory=dict)


class MetadataPatchOut(Out):
    id: int
    kind: Literal["season", "episode"]


class RefreshOut(Out):
    task_id: int


@router.patch("/seasons/{season_id}/metadata")
def patch_season_metadata(
    season_id: int, body: MetadataPatch, _: AdminUser, db: Db, clock: AppClock
) -> MetadataPatchOut:
    SeasonRepository(db, clock).patch_metadata("season", season_id, body.values, body.locked)
    return MetadataPatchOut(id=season_id, kind="season")


@router.patch("/episodes/{episode_id}/metadata")
def patch_episode_metadata(
    episode_id: int, body: MetadataPatch, _: AdminUser, db: Db, clock: AppClock
) -> MetadataPatchOut:
    SeasonRepository(db, clock).patch_metadata("episode", episode_id, body.values, body.locked)
    return MetadataPatchOut(id=episode_id, kind="episode")


@router.post("/seasons/{season_id}/refresh", status_code=202)
def refresh_season(
    season_id: int,
    admin_user: AdminUser,
    database: AppDatabase,
    clock: AppClock,
    metadata: AppMetadata,
) -> RefreshOut:
    with database.session() as connection:
        target = SeasonRepository(connection, clock).season_target(season_id)
    with tracked_task(database, clock, "refresh_season", created_by=admin_user.id) as task:
        metadata.scraper.scrape_season(
            season_id,
            target.provider_id,
            target.remote_id,
            target.number,
            Priority.INTERACTIVE,
        )
        task.result["season_id"] = season_id
    return RefreshOut(task_id=task.id)


@router.post("/episodes/{episode_id}/refresh", status_code=202)
def refresh_episode(
    episode_id: int,
    admin_user: AdminUser,
    database: AppDatabase,
    clock: AppClock,
    metadata: AppMetadata,
) -> RefreshOut:
    with database.session() as connection:
        target = SeasonRepository(connection, clock).episode_target(episode_id)
    with tracked_task(database, clock, "refresh_episode", created_by=admin_user.id) as task:
        metadata.scraper.scrape_season(
            target.season_id,
            target.provider_id,
            target.remote_id,
            target.number,
            Priority.INTERACTIVE,
        )
        task.result["episode_id"] = episode_id
    return RefreshOut(task_id=task.id)


def _queue_child_delete(
    database: AppDatabase,
    clock: AppClock,
    resources: Resources,
    executor: DeleteExecutor,
    task_id: int,
    identities: list[str],
    cleanup: Any,
) -> None:
    def run() -> None:
        completed = 0
        try:
            for identity in identities:
                # 来源自己解释定位符并处理删除语义，核心只传已经索引的来源文件标识。
                with database.read_session() as connection, suppress(ResourceNotFound):
                    resources.delete_file(connection, identity)
                completed += 1
                with database.session() as connection:
                    TaskRepository(connection, clock).progress(
                        task_id, "deleting_cloud", completed, len(identities)
                    )
            with database.session() as connection:
                cleanup(connection)
                TaskRepository(connection, clock).finish(
                    task_id, result={"requested": len(identities), "succeeded": completed}
                )
        except Exception as error:
            with database.session() as connection:
                TaskRepository(connection, clock).finish(
                    task_id,
                    error=str(error),
                    result={"requested": len(identities), "succeeded": completed},
                )

    executor(run)


@router.delete("/seasons/{season_id}", status_code=202)
def delete_season(
    season_id: int,
    admin_user: AdminUser,
    database: AppDatabase,
    clock: AppClock,
    resources: Resources,
    executor: DeleteExecutor,
    confirm: bool = False,
) -> Response:
    if not confirm:
        raise AppError(409, "delete_confirmation_required", "删除前需要明确确认")
    with database.session() as db:
        identities = SeasonRepository(db, clock).season_identities(season_id)
        require_deletable(db, set(), identities, resources)
        task = TaskRepository(db, clock).create(
            "delete_season", total=len(identities), created_by=admin_user.id
        )

    _queue_child_delete(
        database,
        clock,
        resources,
        executor,
        task.id,
        identities,
        lambda connection: SeasonRepository(connection, clock).delete_season(season_id),
    )
    return Response(status_code=202, headers={"X-Task-Id": str(task.id)})


@router.delete("/episodes/{episode_id}", status_code=202)
def delete_episode(
    episode_id: int,
    admin_user: AdminUser,
    database: AppDatabase,
    clock: AppClock,
    resources: Resources,
    executor: DeleteExecutor,
    confirm: bool = False,
) -> Response:
    if not confirm:
        raise AppError(409, "delete_confirmation_required", "删除前需要明确确认")
    with database.session() as db:
        identities = SeasonRepository(db, clock).episode_identities(episode_id)
        require_deletable(db, set(), identities, resources)
        task = TaskRepository(db, clock).create(
            "delete_episode", total=len(identities), created_by=admin_user.id
        )

    _queue_child_delete(
        database,
        clock,
        resources,
        executor,
        task.id,
        identities,
        lambda connection: SeasonRepository(connection, clock).delete_episode(episode_id),
    )
    return Response(status_code=202, headers={"X-Task-Id": str(task.id)})
