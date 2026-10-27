from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter

from app.api.admin.pending_models import (
    AssignFileBatchRequest,
    AssignWorkBatchRequest,
    BatchAssignmentOut,
    BatchAssignmentResult,
    BatchItemError,
    MediaFileIdsRequest,
    RematchWorkBatchRequest,
)
from app.deps import AdminUser, AppClock, AppCuration, AppDatabase
from app.errors import AppError
from app.ops.tasks import BackgroundTask, TaskRepository, TaskStatus

router = APIRouter()

_ID_KEYS = ("work_ids", "media_file_ids")
_BATCH_KINDS = ("batch_", "delete_pending")
_ACTIVE_STATUSES: tuple[TaskStatus, ...] = ("running", "cancel_requested")


@router.post("/works/batch-assign")
def assign_works_batch(
    body: AssignWorkBatchRequest,
    admin: AdminUser,
    curation: AppCuration,
    database: AppDatabase,
    clock: AppClock,
) -> BatchAssignmentOut:
    task = _start_task(
        database,
        clock,
        "batch_assign_work",
        len(body.work_ids),
        admin.id,
        {
            "work_ids": body.work_ids,
            "provider_id": body.provider_id,
            "remote_id": body.remote_id,
        },
    )
    results: list[BatchAssignmentResult] = []
    try:
        for index, work_id in enumerate(body.work_ids, 1):
            if _task_cancelled(database, clock, task.id):
                break
            try:
                item_id = curation.assign_work(work_id, body.remote_id, body.provider_id)
            except AppError as error:
                results.append(
                    BatchAssignmentResult(
                        id=work_id,
                        error=BatchItemError(code=error.code, message=error.message),
                    )
                )
            else:
                results.append(BatchAssignmentResult(id=work_id, item_id=item_id))
            _progress_task(database, clock, task.id, index)
    except Exception as error:
        _fail_task(database, clock, task.id, str(error))
        raise
    return _finish_batch(
        database,
        clock,
        task,
        results,
        requested=len(body.work_ids),
        unprocessed=len(body.work_ids) - len(results),
    )


@router.post("/works/batch-rematch")
def rematch_works_batch(
    body: RematchWorkBatchRequest,
    admin: AdminUser,
    curation: AppCuration,
    database: AppDatabase,
    clock: AppClock,
) -> BatchAssignmentOut:
    task = _start_task(
        database,
        clock,
        "batch_rematch_work",
        len(body.work_ids),
        admin.id,
        {"work_ids": body.work_ids},
    )
    results: list[BatchAssignmentResult] = []
    try:
        for index, work_id in enumerate(body.work_ids, 1):
            if _task_cancelled(database, clock, task.id):
                break
            try:
                item_id = curation.rematch_work(work_id)
            except AppError as error:
                results.append(
                    BatchAssignmentResult(
                        id=work_id,
                        error=BatchItemError(code=error.code, message=error.message),
                    )
                )
            else:
                results.append(
                    BatchAssignmentResult(
                        id=work_id,
                        item_id=item_id,
                        error=(
                            None
                            if item_id is not None
                            else BatchItemError(
                                code="still_unmatched", message="仍未找到唯一的匹配条目"
                            )
                        ),
                    )
                )
            _progress_task(database, clock, task.id, index)
    except Exception as error:
        _fail_task(database, clock, task.id, str(error))
        raise
    return _finish_batch(
        database,
        clock,
        task,
        results,
        requested=len(body.work_ids),
        unprocessed=len(body.work_ids) - len(results),
    )


@router.post("/files/batch-assign")
def assign_files_batch(
    body: AssignFileBatchRequest,
    admin: AdminUser,
    curation: AppCuration,
    database: AppDatabase,
    clock: AppClock,
) -> BatchAssignmentOut:
    task = _start_task(
        database,
        clock,
        "batch_assign_file",
        len(body.media_file_ids),
        admin.id,
        {
            "media_file_ids": body.media_file_ids,
            "provider_id": body.provider_id,
            "remote_id": body.remote_id,
            "season": body.season,
            "episode": body.episode,
        },
    )
    results: list[BatchAssignmentResult] = []
    try:
        for index, media_file_id in enumerate(body.media_file_ids, 1):
            if _task_cancelled(database, clock, task.id):
                break
            try:
                item_id = curation.assign_file(
                    media_file_id,
                    body.remote_id,
                    body.season,
                    body.episode,
                    body.provider_id,
                )
            except AppError as error:
                results.append(
                    BatchAssignmentResult(
                        id=media_file_id,
                        error=BatchItemError(code=error.code, message=error.message),
                    )
                )
            else:
                results.append(BatchAssignmentResult(id=media_file_id, item_id=item_id))
            _progress_task(database, clock, task.id, index)
    except Exception as error:
        _fail_task(database, clock, task.id, str(error))
        raise
    return _finish_batch(
        database,
        clock,
        task,
        results,
        requested=len(body.media_file_ids),
        unprocessed=len(body.media_file_ids) - len(results),
    )


@router.post("/files/batch-ignore")
def ignore_files_batch(
    body: MediaFileIdsRequest,
    admin: AdminUser,
    curation: AppCuration,
    database: AppDatabase,
    clock: AppClock,
) -> BatchAssignmentOut:
    task = _start_task(
        database,
        clock,
        "batch_ignore_file",
        len(body.media_file_ids),
        admin.id,
        {"media_file_ids": body.media_file_ids},
    )
    return _file_state_batch(body.media_file_ids, curation.ignore_file, database, clock, task)


@router.post("/files/batch-restore")
def restore_files_batch(
    body: MediaFileIdsRequest,
    admin: AdminUser,
    curation: AppCuration,
    database: AppDatabase,
    clock: AppClock,
) -> BatchAssignmentOut:
    task = _start_task(
        database,
        clock,
        "batch_restore_file",
        len(body.media_file_ids),
        admin.id,
        {"media_file_ids": body.media_file_ids},
    )
    return _file_state_batch(body.media_file_ids, curation.restore_file, database, clock, task)


def _batch_result(
    results: list[BatchAssignmentResult],
    task_id: int,
    *,
    requested: int,
    unprocessed: int,
) -> BatchAssignmentOut:
    succeeded = sum(result.error is None for result in results)
    return BatchAssignmentOut(
        task_id=task_id,
        requested=requested,
        succeeded=succeeded,
        failed=len(results) - succeeded,
        unprocessed=unprocessed,
        results=results,
    )


def _file_state_batch(
    media_file_ids: list[int],
    action: Callable[[int], None],
    database: AppDatabase,
    clock: AppClock,
    task: BackgroundTask,
) -> BatchAssignmentOut:
    results: list[BatchAssignmentResult] = []
    try:
        for index, media_file_id in enumerate(media_file_ids, 1):
            if _task_cancelled(database, clock, task.id):
                break
            try:
                action(media_file_id)
            except AppError as error:
                results.append(
                    BatchAssignmentResult(
                        id=media_file_id,
                        error=BatchItemError(code=error.code, message=error.message),
                    )
                )
            else:
                results.append(BatchAssignmentResult(id=media_file_id))
            _progress_task(database, clock, task.id, index)
    except Exception as error:
        _fail_task(database, clock, task.id, str(error))
        raise
    return _finish_batch(
        database,
        clock,
        task,
        results,
        requested=len(media_file_ids),
        unprocessed=len(media_file_ids) - len(results),
    )


def _start_task(
    database: AppDatabase,
    clock: AppClock,
    kind: str,
    total: int,
    user_id: int,
    input_data: dict[str, object],
) -> BackgroundTask:
    with database.session() as connection:
        repository = TaskRepository(connection, clock)
        # 检查和创建在同一个写事务里，两个同时到达的请求不会都通过
        _reject_overlapping(repository, input_data)
        return repository.create(
            kind, total=total, created_by=user_id, result={"input": input_data}
        )


def _reject_overlapping(repository: TaskRepository, input_data: dict[str, object]) -> None:
    """同一批记录还有未结束的批量任务（或删除任务）时拒绝，避免并行处理两遍。"""
    for key in _ID_KEYS:
        ids = input_data.get(key)
        if not isinstance(ids, list):
            continue
        for running in repository.list(status=_ACTIVE_STATUSES, limit=200):
            if not running.kind.startswith(_BATCH_KINDS):
                continue
            other = running.result.get("input", {}).get(key)
            if isinstance(other, list) and set(ids) & set(other):
                raise AppError(
                    409,
                    "batch_in_progress",
                    f"其中部分记录正在被任务 #{running.id} 处理，请等它结束后再提交",
                )


def _progress_task(database: AppDatabase, clock: AppClock, task_id: int, done: int) -> None:
    with database.session() as connection:
        TaskRepository(connection, clock).progress(task_id, "processing", done)


def _fail_task(database: AppDatabase, clock: AppClock, task_id: int, error: str) -> None:
    with database.session() as connection:
        TaskRepository(connection, clock).finish(task_id, error=error)


def _finish_batch(
    database: AppDatabase,
    clock: AppClock,
    task: BackgroundTask,
    results: list[BatchAssignmentResult],
    *,
    requested: int,
    unprocessed: int,
) -> BatchAssignmentOut:
    report = _batch_result(
        results,
        task.id,
        requested=requested,
        unprocessed=unprocessed,
    )
    with database.session() as connection:
        repository = TaskRepository(connection, clock)
        existing = repository.require(task.id)
        repository.finish(
            task.id,
            error=(
                "批量操作已取消"
                if report.unprocessed
                else "批量操作存在失败项"
                if report.failed
                else None
            ),
            result={
                **existing.result,
                "requested": report.requested,
                "succeeded": report.succeeded,
                "failed": report.failed,
                "unprocessed": report.unprocessed,
            },
        )
    return report


def _task_cancelled(database: AppDatabase, clock: AppClock, task_id: int) -> bool:
    with database.read_session() as connection:
        return TaskRepository(connection, clock).cancel_requested(task_id)
