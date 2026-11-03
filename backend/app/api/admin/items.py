from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import suppress
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, Query, Response
from iris_plugin_sdk import ResourceNotFound
from pydantic import BaseModel, Field

from app.api.admin.sync import JobOut
from app.api.schemas import ImageOut, Out
from app.deps import (
    AdminUser,
    AppClock,
    AppCuration,
    AppDatabase,
    DeleteExecutor,
    MetadataPlugins,
    ReadDb,
    Reenrich,
    Resources,
    Sync,
)
from app.errors import AppError
from app.library.deletion import ItemDeletionRepository
from app.library.item_edit import ItemEditRepository
from app.library.metadata_fields import MetadataField
from app.library.metadata_providers import MetadataProviders
from app.library.recognize import LibraryKind
from app.library.review import ItemDetail, ReviewQueries
from app.ops.tasks import TaskRepository, tracked_task
from app.resources.deletable import require_deletable
from app.resources.service import FolderSource
from app.sync.files import MediaFileRepository
from app.sync.targets import LibraryDir

router = APIRouter(prefix="/admin", tags=["admin"])

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50


Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]


class ItemOut(Out):
    id: int
    library_id: int
    library_name: str
    kind: LibraryKind
    # 负责这个条目元数据的插件和插件里的条目 ID
    provider_id: str
    remote_id: str
    title: str
    original_title: str | None
    year: int | None
    poster: list[ImageOut]
    version_count: int
    added_at: str


class ItemPage(BaseModel):
    items: list[ItemOut]
    total: int


class ItemWorkOut(Out):
    id: int
    path: str
    manual: bool
    # AI 自动匹配的结果；可以在条目操作里撤销（POST /admin/works/{id}/suggestion/revert）
    ai_matched: bool = False


class ItemVersionOut(Out):
    id: int
    media_file_id: int
    path: str
    size: int | None
    state: str
    label: str
    manual: bool
    season: int | None
    episode: int | None


class AdminItemDetailOut(ItemOut):
    provider_name: str
    # 条目在插件网站上的页面，插件不提供时为空
    external_url: str | None
    overview: str | None
    release_date: str | None
    rating: float | None
    genres: list[str]
    runtime: int | None
    backdrop: list[ImageOut]
    thumb: list[ImageOut]
    logo: list[ImageOut]
    scraped_at: str
    works: list[ItemWorkOut]
    versions: list[ItemVersionOut]

    @classmethod
    def of(cls, detail: ItemDetail, providers: MetadataProviders) -> AdminItemDetailOut:
        item = ItemOut.model_validate(detail.item)
        return cls(
            **item.model_dump(),
            provider_name=providers.name(item.provider_id),
            external_url=providers.external_url(item.provider_id, item.kind, item.remote_id),
            overview=detail.overview,
            release_date=detail.release_date,
            rating=detail.rating,
            genres=detail.genres,
            runtime=detail.runtime,
            backdrop=[ImageOut.model_validate(i) for i in detail.backdrop],
            thumb=[ImageOut.model_validate(i) for i in detail.thumb],
            logo=[ImageOut.model_validate(i) for i in detail.logo],
            scraped_at=detail.scraped_at,
            works=[ItemWorkOut.model_validate(w) for w in detail.works],
            versions=[ItemVersionOut.model_validate(v) for v in detail.versions],
        )


class RematchRequest(BaseModel):
    # 都不传时清掉匹配结果重新自动匹配；只传 remote_id 时用资料库选择的插件
    provider_id: str | None = Field(default=None, min_length=1, max_length=100)
    remote_id: str | None = Field(default=None, min_length=1, max_length=200)


class RematchOut(BaseModel):
    # 自动匹配没有结果时为 null，作品进入待处理列表
    item_id: int | None


class ReenrichOut(BaseModel):
    updated: int
    missing: int
    task_id: int


class MetadataFieldOut(BaseModel):
    field: MetadataField
    value: Any
    # provider：条目所属的元数据插件
    source: Literal["provider", "nfo", "manual"]
    locked: bool
    updated_at: str


class MetadataUpdateRequest(BaseModel):
    value: str | None = None
    locked: bool = True


@router.get("/items")
def list_items(
    _: AdminUser,
    db: ReadDb,
    library_id: int | None = None,
    query: Annotated[str | None, Query(max_length=100)] = None,
    offset: Offset = 0,
    limit: Limit = DEFAULT_PAGE_SIZE,
) -> ItemPage:
    page = ReviewQueries(db).items(library_id, (query or "").strip() or None, offset, limit)
    return ItemPage(items=[ItemOut.model_validate(i) for i in page.items], total=page.total)


@router.get("/items/{item_id}")
def get_item(
    item_id: int, _: AdminUser, db: ReadDb, providers: MetadataPlugins
) -> AdminItemDetailOut:
    return AdminItemDetailOut.of(ReviewQueries(db).item(item_id), providers)


@router.post("/items/{item_id}/refresh")
def refresh_item(
    item_id: int,
    admin: AdminUser,
    curation: AppCuration,
    database: AppDatabase,
    clock: AppClock,
    response: Response,
    providers: MetadataPlugins,
) -> AdminItemDetailOut:
    """用条目所属的元数据插件重新拉取详情、演职员和各季信息。"""
    with tracked_task(database, clock, "refresh_metadata", created_by=admin.id, total=1) as task:
        curation.refresh_item(item_id)
        task.progress("scraping", 1, 1)
        task.result["item_id"] = item_id
    response.headers["X-Task-Id"] = str(task.id)
    return item_detail(database, providers, item_id)


@router.get("/items/{item_id}/metadata", response_model=list[MetadataFieldOut])
def item_metadata(item_id: int, _: AdminUser, curation: AppCuration) -> list[MetadataFieldOut]:
    return [MetadataFieldOut.model_validate(row) for row in curation.metadata(item_id)]


@router.patch("/items/{item_id}/metadata/{field}", status_code=204)
def update_item_metadata(
    item_id: int,
    field: MetadataField,
    body: MetadataUpdateRequest,
    _: AdminUser,
    curation: AppCuration,
) -> Response:
    curation.update_metadata(item_id, field, body.value, body.locked)
    return Response(status_code=204)


@router.delete("/items/{item_id}/metadata/{field}", status_code=204)
def unlock_item_metadata(
    item_id: int, field: MetadataField, _: AdminUser, curation: AppCuration
) -> Response:
    """恢复到自动来源：解除锁定，下一次刷新或重新匹配会用插件数据覆盖这一项。"""
    curation.unlock_metadata(item_id, field)
    return Response(status_code=204)


@router.post("/items/{item_id}/rematch")
def rematch_item(
    item_id: int, body: RematchRequest, _: AdminUser, curation: AppCuration
) -> RematchOut:
    return RematchOut(item_id=curation.rematch_item(item_id, body.remote_id, body.provider_id))


@router.post("/items/{item_id}/reenrich")
def reenrich_item(
    item_id: int,
    admin: AdminUser,
    reenricher: Reenrich,
    database: AppDatabase,
    clock: AppClock,
) -> ReenrichOut:
    """到来源重新读取条目下文件的体积、sha1 等信息，找不到的标为失效。"""
    with tracked_task(database, clock, "reenrich_item", created_by=admin.id) as task:
        result = reenricher.item(item_id)
        task.progress("placing", result.updated + result.missing)
        task.result.update(updated=result.updated, missing=result.missing)
    return ReenrichOut(updated=result.updated, missing=result.missing, task_id=task.id)


@router.post("/items/{item_id}/sync", status_code=202)
def sync_item(item_id: int, _: AdminUser, db: ReadDb, clock: AppClock, sync: Sync) -> JobOut:
    """定向同步条目所在的目录：找出新加的版本、剧集和字幕，以及删掉的文件。"""
    title = ItemEditRepository(db, clock).title(item_id)
    dirs = MediaFileRepository(db, clock).item_dirs(item_id)
    if not dirs:
        raise AppError(400, "item_has_no_dir", "条目没有关联的目录，无法同步")
    targets = [LibraryDir(folder_id, path, deep=own) for folder_id, path, own in dirs]
    return JobOut.of(sync.start_targeted("manual", targets, f"「{title}」"))


@router.delete("/items/{item_id}", status_code=202)
def delete_item(
    item_id: int,
    admin: AdminUser,
    database: AppDatabase,
    clock: AppClock,
    resources: Resources,
    executor: DeleteExecutor,
    confirm: Annotated[bool, Query()] = False,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=200)] = None,
) -> Response:
    """删除电影或整部剧，并删除当前条目所属的来源目录或文件。

    这是不可逆操作，必须显式带 confirm=true。作品目录会整体删除，散放文件才逐个删除；
    云端删除全部成功后才提交数据库删除。
    """
    if not confirm:
        raise AppError(409, "delete_confirmation_required", "删除电影或剧集前需要明确确认")
    idempotency_key = idempotency_key.strip() if idempotency_key else None
    task_input: dict[str, object] = {"item_id": item_id}
    if existing := existing_delete_task(
        database, clock, idempotency_key, "delete_media", task_input
    ):
        return existing
    with database.session() as connection:
        plan = ItemDeletionRepository(connection, clock).plan(item_id)

    def cleanup(connection: sqlite3.Connection) -> None:
        ItemDeletionRepository(connection, clock).delete_records(item_id)

    return start_delete_task(
        database,
        clock,
        resources,
        executor,
        "delete_media",
        admin.id,
        idempotency_key,
        task_input,
        plan.roots,
        plan.own_dirs,
        plan.identities,
        cleanup,
    )


def existing_delete_task(
    database: AppDatabase,
    clock: AppClock,
    idempotency_key: str | None,
    kind: str,
    task_input: dict[str, object],
) -> Response | None:
    """同一个幂等键重复提交时返回已有任务，不重复删除。"""
    if not idempotency_key:
        return None
    with database.read_session() as connection:
        existing = TaskRepository(connection, clock).by_idempotency_key(idempotency_key)
    if existing is None:
        return None
    if existing.kind != kind:
        raise AppError(409, "idempotency_key_conflict", "幂等键已用于其他任务")
    if existing.result.get("input") != task_input:
        raise AppError(409, "idempotency_key_conflict", "幂等键已用于其他删除内容")
    if existing.status in ("failed", "cancelled"):
        raise AppError(409, "task_already_failed", "这个删除任务已失败，请从任务详情重试")
    return Response(status_code=202, headers={"X-Task-Id": str(existing.id)})


def start_delete_task(
    database: AppDatabase,
    clock: AppClock,
    resources: Resources,
    executor: DeleteExecutor,
    kind: str,
    user_id: int,
    idempotency_key: str | None,
    task_input: dict[str, object],
    roots: dict[int, str | None],
    own_dirs: list[tuple[int, str]],
    identities: list[str],
    cleanup: Callable[[sqlite3.Connection], None],
) -> Response:
    """建删除任务并在后台执行，返回 202 和任务编号。"""
    with database.session() as connection:
        require_deletable(connection, set(roots), identities, resources)
        task = TaskRepository(connection, clock).create(
            kind,
            created_by=user_id,
            idempotency_key=idempotency_key,
            result={"input": task_input},
        )

    executor(
        lambda: _run_delete_task(
            database,
            clock,
            task.id,
            roots,
            own_dirs,
            identities,
            cleanup,
            resources,
        )
    )
    return Response(status_code=202, headers={"X-Task-Id": str(task.id)})


def _run_delete_task(
    database: AppDatabase,
    clock: AppClock,
    task_id: int,
    roots: dict[int, str | None],
    own_dirs: list[tuple[int, str]],
    identities: list[str],
    cleanup: Callable[[sqlite3.Connection], None],
    resources: Resources,
) -> None:
    """后台删除云端目标，全部成功后再清理本地记录。"""
    folder_targets: dict[str, FolderSource] = {}
    completed_targets: list[str] = []
    current_target: str | None = None
    target_ids: list[str] = []
    try:
        with database.read_session() as connection:
            for folder_id, path in own_dirs:
                source = resources.source(connection, folder_id)
                target = resources.resolve_path(source, path)
                if target is not None:
                    folder_targets[target] = source
        target_ids = list(dict.fromkeys([*folder_targets, *identities]))
        with database.session() as connection:
            TaskRepository(connection, clock).progress(task_id, "planning", 0, len(target_ids))
        for index, target_id in enumerate(target_ids, 1):
            current_target = target_id
            with database.session() as connection:
                if TaskRepository(connection, clock).cancel_requested(task_id):
                    raise AppError(
                        409, "task_cancelled", "删除任务已取消，云端和数据库均未继续处理"
                    )

            if target_id in folder_targets:
                with suppress(ResourceNotFound):
                    resources.open(folder_targets[target_id]).delete(target_id)
            else:
                with database.read_session() as connection, suppress(ResourceNotFound):
                    resources.delete_file(connection, target_id)
                # 重试任务时目标可能已经被手动删除，保持删除操作幂等。
            with database.session() as connection:
                TaskRepository(connection, clock).progress(task_id, "deleting_cloud", index)
            completed_targets.append(target_id)
            current_target = None
    except Exception as error:
        with database.session() as connection:
            TaskRepository(connection, clock).finish(
                task_id,
                error=str(error),
                result={
                    "requested": len(target_ids),
                    "succeeded": len(completed_targets),
                    "failed": 1 if current_target is not None else 0,
                    "failed_targets": [current_target] if current_target is not None else [],
                    "unprocessed": max(0, len(target_ids) - len(completed_targets) - 1),
                },
            )
        return
    try:
        with database.session() as connection:
            cleanup(connection)
            TaskRepository(connection, clock).finish(
                task_id,
                result={"requested": len(target_ids), "succeeded": len(target_ids)},
            )
    except Exception as error:
        with database.session() as connection:
            TaskRepository(connection, clock).finish(
                task_id,
                error=str(error),
                result={
                    "requested": len(target_ids),
                    "succeeded": len(completed_targets),
                    "failed": 0,
                    "failed_targets": [],
                    "unprocessed": 0,
                },
            )
        return


@router.post("/versions/{version_id}/detach", status_code=204)
def detach_version(version_id: int, _: AdminUser, curation: AppCuration) -> Response:
    """把版本从条目上拆下来，文件进入待处理列表等手动指定。"""
    curation.detach_version(version_id)
    return Response(status_code=204)


def item_detail(
    database: AppDatabase, providers: MetadataProviders, item_id: int
) -> AdminItemDetailOut:
    with database.read_session() as connection:
        detail = ReviewQueries(connection).item(item_id)
    return AdminItemDetailOut.of(detail, providers)
