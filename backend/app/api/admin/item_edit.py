"""条目信息、元数据编辑、取消匹配、合并与批量操作接口。"""

from __future__ import annotations

import sqlite3
import threading
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.admin.seasons import MetadataPatch
from app.api.schemas import Out
from app.clock import Clock
from app.deps import (
    AdminUser,
    AppClock,
    AppCuration,
    AppDatabase,
    Db,
    ReadDb,
)
from app.errors import AppError
from app.library.item_batch import ItemBatchRepository
from app.library.item_edit import ItemEditRepository
from app.ops.tasks import TaskRepository

router = APIRouter(prefix="/admin", tags=["admin"])


class ItemRecordOut(Out):
    id: int
    library_id: int
    kind: str
    provider_id: str
    remote_id: str
    title: str
    original_title: str | None
    en_title: str | None
    year: int | None
    release_date: str | None
    overview: str | None
    rating: float | None
    genres: str
    runtime: int | None
    poster_path: str | None
    poster_image_id: int | None
    backdrop_path: str | None
    backdrop_image_id: int | None
    thumb_path: str | None
    thumb_image_id: int | None
    logo_path: str | None
    logo_image_id: int | None
    scraped_at: str
    added_at: str
    sort_title: str | None
    tagline: str | None
    studios: str
    countries: str
    tmdb_collection_id: int | None
    content_rating: str | None


class VersionInfoOut(Out):
    id: int
    label: str
    attributes: str
    rel_path: str
    size: int | None
    state: str
    identity: str | None
    duration: int | None
    width: int | None
    height: int | None
    video_codec: str | None
    hdr: str | None
    audio: str | None
    subtitles: str | None


class SubtitleInfoOut(Out):
    id: int
    label: str
    language: str | None
    # 客户端一直按 0/1 读取，保持整数
    forced: int
    rel_path: str


class FieldInfoOut(Out):
    field: str
    source: str
    # 同 forced：0/1 整数
    locked: int
    updated_at: str


class ItemInfoOut(Out):
    item: ItemRecordOut
    versions: list[VersionInfoOut]
    subtitles: list[SubtitleInfoOut]
    metadata: list[FieldInfoOut]


def _item_info(item_id: int, db: sqlite3.Connection, clock: Clock) -> ItemInfoOut:
    return ItemInfoOut.model_validate(ItemEditRepository(db, clock).info(item_id))


@router.get("/items/{item_id}/info")
def item_info(item_id: int, _: AdminUser, db: ReadDb, clock: AppClock) -> ItemInfoOut:
    return _item_info(item_id, db, clock)


@router.patch("/items/{item_id}/metadata")
def patch_item_metadata(
    item_id: int, body: MetadataPatch, _: AdminUser, db: Db, clock: AppClock
) -> ItemInfoOut:
    ItemEditRepository(db, clock).patch_metadata(item_id, body.values, body.locked)
    return _item_info(item_id, db, clock)


class UnmatchOut(Out):
    task_id: int
    item_id: int
    works: int


@router.post("/items/{item_id}/unmatch", status_code=202)
def unmatch_item(
    item_id: int, admin_user: AdminUser, database: AppDatabase, clock: AppClock
) -> UnmatchOut:
    with database.session() as db:
        edits = ItemEditRepository(db, clock)
        edits.require(item_id)
        task = TaskRepository(db, clock).create("unmatch_item", total=1, created_by=admin_user.id)
        works = edits.unmatch(item_id)
        TaskRepository(db, clock).finish(task.id, result={"item_id": item_id, "works": works})
    return UnmatchOut(task_id=task.id, item_id=item_id, works=works)


class BatchRequest(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=1000)
    filter: dict[str, Any] | None = None
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)


class MergeRequest(BaseModel):
    target_id: int
    source_ids: list[int] = Field(min_length=1, max_length=100)


class MergeOut(Out):
    task_id: int
    target_id: int
    source_ids: list[int]


@router.post("/items/merge", status_code=202)
def merge_items(
    body: MergeRequest, admin_user: AdminUser, database: AppDatabase, clock: AppClock
) -> MergeOut:
    with database.session() as db:
        edits = ItemEditRepository(db, clock)
        edits.check_merge(body.target_id, body.source_ids)
        task = TaskRepository(db, clock).create(
            "merge_items",
            total=len(body.source_ids),
            created_by=admin_user.id,
            result={"input": body.model_dump()},
        )
        edits.merge(body.target_id, body.source_ids)
        TaskRepository(db, clock).finish(
            task.id, result={"target_id": body.target_id, "source_ids": body.source_ids}
        )
    return MergeOut(task_id=task.id, target_id=body.target_id, source_ids=body.source_ids)


class BatchOut(Out):
    task_id: int
    success: list[int]
    failed: list[int]


@router.post("/items/batch", status_code=202)
def batch_items(
    body: BatchRequest,
    admin_user: AdminUser,
    database: AppDatabase,
    clock: AppClock,
    curation: AppCuration,
) -> BatchOut:
    if not body.ids and body.filter is None:
        raise AppError(400, "empty_selection", "至少选择一个条目")
    if body.action not in {
        "watched",
        "unwatched",
        "refresh",
        "labels_add",
        "labels_remove",
        "delete",
    }:
        raise AppError(400, "invalid_batch_action", "不支持的批量操作")
    with database.session() as db:
        batch = ItemBatchRepository(db)
        if body.filter is not None:
            known = batch.filtered(body.filter, admin_user.id)
        else:
            known = batch.existing(body.ids)
        task = TaskRepository(db, clock).create(
            "batch_items",
            total=len(known),
            created_by=admin_user.id,
            result={"input": body.model_dump()},
        )
        label = str(body.payload.get("label", "")).strip()
        if body.action == "labels_add" and label:
            batch.add_label(known, label)
        elif body.action == "labels_remove" and label:
            batch.remove_label(known, label)
        elif body.action in {"watched", "unwatched"}:
            batch.set_watched(admin_user.id, known, body.action == "watched")
        elif body.action == "refresh" and known:
            # 刮削和图片下载属于慢 I/O，提交后在后台逐项执行。
            task_id = task.id

            def refresh() -> None:
                done = 0
                try:
                    for item_id in known:
                        curation.refresh_item(item_id)
                        done += 1
                        with database.session() as connection:
                            TaskRepository(connection, clock).progress(
                                task_id, "scraping", done, len(known)
                            )
                    with database.session() as connection:
                        TaskRepository(connection, clock).finish(
                            task_id, result={"success": known, "failed": []}
                        )
                except Exception as exc:
                    with database.session() as connection:
                        TaskRepository(connection, clock).finish(
                            task_id,
                            error=str(exc),
                            result={"success": known[:done], "failed": known[done:]},
                        )

            threading.Thread(target=refresh, name="batch-metadata-refresh", daemon=True).start()
            return BatchOut(task_id=task.id, success=[], failed=[])
        failed = [i for i in body.ids if i not in known]
        TaskRepository(db, clock).finish(task.id, result={"success": known, "failed": failed})
    return BatchOut(task_id=task.id, success=known, failed=failed)
