from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.schemas import Out
from app.deps import (
    AdminUser,
    AppClock,
    AppCuration,
    AppDatabase,
    DeleteExecutor,
    ReadDb,
    Resources,
)
from app.errors import AppError
from app.ops.tasks import BackgroundTask, TaskRepository, TaskStatus

router = APIRouter(prefix="/admin/tasks", tags=["admin"])


class TaskOut(Out):
    id: int
    kind: str
    phase: str
    status: TaskStatus
    progress_done: int
    progress_total: int
    retry_count: int
    cancel_requested: bool
    error: str | None
    result: dict[str, object]
    created_by: int | None
    idempotency_key: str | None
    created_at: str
    started_at: str
    finished_at: str | None

    @classmethod
    def of(cls, task: BackgroundTask) -> TaskOut:
        return cls.model_validate(task)


@router.get("")
def list_tasks(
    _: AdminUser,
    db: ReadDb,
    clock: AppClock,
    kind: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[TaskOut]:
    return [TaskOut.of(task) for task in TaskRepository(db, clock).list(kind=kind, limit=limit)]


@router.get("/{task_id}")
def get_task(task_id: int, _: AdminUser, db: ReadDb, clock: AppClock) -> TaskOut:
    return TaskOut.of(TaskRepository(db, clock).require(task_id))


@router.post("/{task_id}/cancel")
def cancel_task(
    task_id: int,
    _: AdminUser,
    database: AppDatabase,
    clock: AppClock,
) -> TaskOut:
    with database.session() as connection:
        task = TaskRepository(connection, clock).request_cancel(task_id)
    return TaskOut.of(task)


@router.post("/{task_id}/retry")
def retry_task(
    task_id: int,
    admin: AdminUser,
    database: AppDatabase,
    clock: AppClock,
    curation: AppCuration,
    resources: Resources,
    executor: DeleteExecutor,
) -> TaskOut:
    """重试保存了原始输入的批量任务，生成新的任务记录。"""
    with database.session() as connection:
        task = TaskRepository(connection, clock).require(task_id)
    if task.status not in ("failed", "cancelled"):
        raise AppError(409, "task_not_retryable", "只有失败或已取消的任务可以重试")
    raw_input = task.result.get("input")
    if not isinstance(raw_input, dict):
        raise AppError(409, "task_not_retryable", "这个任务没有保存可重试的输入")

    # 延迟导入避免 admin_pending 与本路由模块互相初始化。
    from app.api.admin.items import delete_item
    from app.api.admin.pending import (
        AssignFileBatchRequest,
        AssignWorkBatchRequest,
        MediaFileIdsRequest,
        PendingDeleteRequest,
        RematchWorkBatchRequest,
        assign_files_batch,
        assign_works_batch,
        delete_pending,
        ignore_files_batch,
        rematch_works_batch,
        restore_files_batch,
    )

    if task.kind in ("delete_media", "delete_pending"):
        if task.kind == "delete_media":
            item_id = raw_input.get("item_id")
            if not isinstance(item_id, int) or item_id < 1:
                raise AppError(409, "task_not_retryable", "这个删除任务没有保存可重试的条目")
            response = delete_item(
                item_id, admin, database, clock, resources, executor, confirm=True
            )
        else:
            response = delete_pending(
                PendingDeleteRequest.model_validate(raw_input),
                admin,
                database,
                clock,
                resources,
                executor,
                confirm=True,
            )
        task_header = response.headers.get("X-Task-Id")
        if task_header is None:
            raise AppError(500, "task_retry_failed", "重试删除任务未返回任务编号")
        with database.session() as connection:
            return TaskOut.of(TaskRepository(connection, clock).require(int(task_header)))

    if task.kind == "batch_assign_work":
        report = assign_works_batch(
            AssignWorkBatchRequest.model_validate(raw_input),
            admin,
            curation,
            database,
            clock,
        )
    elif task.kind == "batch_rematch_work":
        report = rematch_works_batch(
            RematchWorkBatchRequest.model_validate(raw_input),
            admin,
            curation,
            database,
            clock,
        )
    elif task.kind == "batch_assign_file":
        report = assign_files_batch(
            AssignFileBatchRequest.model_validate(raw_input),
            admin,
            curation,
            database,
            clock,
        )
    elif task.kind == "batch_ignore_file":
        report = ignore_files_batch(
            MediaFileIdsRequest.model_validate(raw_input),
            admin,
            curation,
            database,
            clock,
        )
    elif task.kind == "batch_restore_file":
        report = restore_files_batch(
            MediaFileIdsRequest.model_validate(raw_input),
            admin,
            curation,
            database,
            clock,
        )
    else:
        raise AppError(409, "task_not_retryable", "这个任务类型暂不支持重试")
    with database.session() as connection:
        return TaskOut.of(TaskRepository(connection, clock).require(report.task_id))
