from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Header, Query, Response

from app.api.admin.items import existing_delete_task, start_delete_task
from app.api.admin.pending_models import PendingDeleteRequest
from app.deps import AdminUser, AppClock, AppDatabase, DeleteExecutor, Resources
from app.errors import AppError
from app.library.pending import PendingDeletion

router = APIRouter()


@router.post("/pending/delete", status_code=202)
def delete_pending(
    body: PendingDeleteRequest,
    admin: AdminUser,
    database: AppDatabase,
    clock: AppClock,
    resources: Resources,
    executor: DeleteExecutor,
    confirm: Annotated[bool, Query()] = False,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=200)] = None,
) -> Response:
    """删除待处理的作品和文件，连同来源上的文件一起删除。

    没匹配上的作品有自己的目录时整个目录删除，否则逐个删除它的视频；文件只删它本身。
    已经归入条目的不在这里删，要从条目里删除。云端删除全部成功后才清理数据库。
    """
    if not confirm:
        raise AppError(409, "delete_confirmation_required", "删除前需要明确确认")
    idempotency_key = idempotency_key.strip() if idempotency_key else None
    task_input: dict[str, object] = body.model_dump()
    if existing := existing_delete_task(
        database, clock, idempotency_key, "delete_pending", task_input
    ):
        return existing
    with database.session() as connection:
        plan = PendingDeletion(connection).plan(body.work_ids, body.media_file_ids)
    with database.session() as connection:
        roots = PendingDeletion(connection).folder_roots({folder_id for folder_id, _ in plan.dirs})

    def cleanup(connection: sqlite3.Connection) -> None:
        PendingDeletion(connection).cleanup(plan, body.work_ids, body.media_file_ids)

    return start_delete_task(
        database,
        clock,
        resources,
        executor,
        "delete_pending",
        admin.id,
        idempotency_key,
        task_input,
        roots,
        plan.dirs,
        plan.identities,
        cleanup,
    )
