from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.ai.suggestions import Suggestion
from app.api.admin.items import DEFAULT_PAGE_SIZE, AdminItemDetailOut, Limit, Offset, item_detail
from app.api.admin.pending_batch import (
    assign_files_batch,
    assign_works_batch,
    ignore_files_batch,
    rematch_works_batch,
    restore_files_batch,
)
from app.api.admin.pending_batch import router as batch_router
from app.api.admin.pending_delete import delete_pending
from app.api.admin.pending_delete import router as delete_router
from app.api.admin.pending_models import (
    AssignFileBatchRequest,
    AssignFileRequest,
    AssignWorkBatchRequest,
    AssignWorkRequest,
    CandidateOut,
    FileOut,
    FilePage,
    ItemRefOut,
    MediaFileIdsRequest,
    MetadataResultOut,
    PendingDeleteRequest,
    PendingSummaryOut,
    RematchWorkBatchRequest,
    SuggestionOut,
    SuggestionStepOut,
    WorkOut,
    WorkPage,
)
from app.deps import AdminUser, AppCuration, AppDatabase, MetadataPlugins, ReadDb, Suggestions
from app.library.metadata_providers import DEFAULT_PROVIDER, BoundProvider, MetadataProviders
from app.library.recognize import LibraryKind
from app.library.review import FileStatus, ReviewQueries, WorkState
from app.priority import Priority

# 重试批量任务的接口（admin_tasks）按这些名字取用
__all__ = [
    "AssignFileBatchRequest",
    "AssignWorkBatchRequest",
    "MediaFileIdsRequest",
    "PendingDeleteRequest",
    "RematchWorkBatchRequest",
    "assign_files_batch",
    "assign_works_batch",
    "delete_pending",
    "ignore_files_batch",
    "rematch_works_batch",
    "restore_files_batch",
    "router",
]

router = APIRouter(prefix="/admin", tags=["admin"])
router.include_router(batch_router)
router.include_router(delete_router)


@router.get("/pending")
def pending_summary(
    _: AdminUser, db: ReadDb, providers: MetadataPlugins, library_id: int | None = None
) -> PendingSummaryOut:
    summary = ReviewQueries(db).summary(library_id)
    problem = providers.problem(library_id)
    return PendingSummaryOut(
        metadata_problem=problem.message if problem else None,
        metadata_action=problem.action_path if problem else None,
        waiting=summary.waiting,
        unmatched=summary.unmatched,
        unplaced=summary.unplaced,
        ignored=summary.ignored,
    )


@router.get("/works")
def list_works(
    _: AdminUser,
    db: ReadDb,
    providers: MetadataPlugins,
    suggestions: Suggestions,
    state: WorkState = "unmatched",
    library_id: int | None = None,
    offset: Offset = 0,
    limit: Limit = DEFAULT_PAGE_SIZE,
) -> WorkPage:
    page = ReviewQueries(db).works(state, library_id, offset, limit)
    url = providers.url_lookup()
    suggested = suggestions.for_works(db, [w.id for w in page.items])
    return WorkPage(
        items=[
            WorkOut(
                id=w.id,
                library_id=w.library_id,
                library_name=w.library_name,
                kind=w.kind,
                path=w.path,
                title=w.title,
                year=w.year,
                state=w.state,
                manual=w.manual,
                reason=w.reason,
                candidates=[
                    CandidateOut(
                        provider_id=c.provider_id,
                        remote_id=c.remote_id,
                        title=c.title,
                        original_title=c.original_title,
                        year=c.year,
                        score=c.score,
                        reasons=list(c.reasons),
                        url=url(c.provider_id, w.kind, c.remote_id),
                    )
                    for c in w.candidates
                ],
                file_count=w.file_count,
                item=ItemRefOut.model_validate(w.item) if w.item else None,
                suggestion=_suggestion_out(suggested.get(w.id), w.kind, url),
            )
            for w in page.items
        ],
        total=page.total,
    )


def _suggestion_out(
    suggestion: Suggestion | None,
    kind: LibraryKind,
    url: Callable[[str, LibraryKind, str], str | None],
) -> SuggestionOut | None:
    if suggestion is None:
        return None
    return SuggestionOut(
        id=suggestion.id,
        provider_id=suggestion.provider_id,
        remote_id=suggestion.remote_id,
        title=suggestion.title,
        original_title=suggestion.original_title,
        year=suggestion.year,
        url=(
            url(suggestion.provider_id, kind, suggestion.remote_id)
            if suggestion.provider_id and suggestion.remote_id
            else None
        ),
        confidence=suggestion.confidence,
        reason=suggestion.reason,
        steps=[SuggestionStepOut(**vars(step)) for step in suggestion.steps],
        model=suggestion.model,
        status=suggestion.status,  # type: ignore[arg-type]
        created_at=suggestion.created_at,
    )


@router.post("/works/{work_id}/suggestion/adopt")
def adopt_suggestion(
    work_id: int,
    _: AdminUser,
    suggestions: Suggestions,
    database: AppDatabase,
    providers: MetadataPlugins,
) -> AdminItemDetailOut:
    """采用 AI 的匹配建议，等同于手动指定，之后同步不再改动。"""
    return item_detail(database, providers, suggestions.adopt(work_id))


@router.post("/works/{work_id}/suggestion/ignore", status_code=204)
def ignore_suggestion(work_id: int, _: AdminUser, suggestions: Suggestions) -> Response:
    suggestions.ignore(work_id)
    return Response(status_code=204)


@router.post("/works/{work_id}/suggestion/reanalyze", status_code=204)
def reanalyze_suggestion(work_id: int, _: AdminUser, suggestions: Suggestions) -> Response:
    """丢弃现有建议，稍后由后台任务重新分析。"""
    suggestions.reanalyze(work_id)
    return Response(status_code=204)


@router.post("/works/{work_id}/suggestion/revert", status_code=204)
def revert_suggestion(work_id: int, _: AdminUser, suggestions: Suggestions) -> Response:
    """撤销 AI 自动匹配：作品回到待处理，且不再自动匹配同一个候选。"""
    suggestions.revert(work_id)
    return Response(status_code=204)


@router.post("/works/{work_id}/assign")
def assign_work(
    work_id: int,
    body: AssignWorkRequest,
    _: AdminUser,
    curation: AppCuration,
    database: AppDatabase,
    providers: MetadataPlugins,
) -> AdminItemDetailOut:
    """手动指定作品对应的元数据条目，之后同步不再改动。"""
    item_id = curation.assign_work(work_id, body.remote_id, body.provider_id)
    return item_detail(database, providers, item_id)


@router.get("/files")
def list_files(
    _: AdminUser,
    db: ReadDb,
    status: FileStatus = "unplaced",
    library_id: int | None = None,
    reason: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    offset: Offset = 0,
    limit: Limit = DEFAULT_PAGE_SIZE,
) -> FilePage:
    """reason 精确匹配原因文字，可选值见响应的 reasons。"""
    queries = ReviewQueries(db)
    page = queries.files(status, library_id, offset, limit, reason)
    return FilePage(
        items=[FileOut.model_validate(f) for f in page.items],
        total=page.total,
        reasons=queries.file_reasons(status, library_id),
    )


@router.post("/files/{media_file_id}/assign")
def assign_file(
    media_file_id: int,
    body: AssignFileRequest,
    _: AdminUser,
    curation: AppCuration,
    database: AppDatabase,
    providers: MetadataPlugins,
) -> AdminItemDetailOut:
    """把一个视频手动挂到电影或剧集的某一集。也用于改挂已有版本、恢复被误判为附加内容的视频。"""
    item_id = curation.assign_file(
        media_file_id, body.remote_id, body.season, body.episode, body.provider_id
    )
    return item_detail(database, providers, item_id)


@router.post("/files/{media_file_id}/restore", status_code=204)
def restore_file(media_file_id: int, _: AdminUser, curation: AppCuration) -> None:
    curation.restore_file(media_file_id)


@router.get("/metadata/search")
def search_metadata(
    _: AdminUser,
    providers: MetadataPlugins,
    kind: LibraryKind,
    query: Annotated[str, Query(min_length=1, max_length=100)],
    library_id: int | None = None,
    provider_id: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
) -> list[MetadataResultOut]:
    """用指定插件搜索；不指定时用资料库选择的插件，都不指定时用默认插件。"""
    provider = _search_provider(providers, library_id, provider_id)
    return [
        MetadataResultOut(
            provider_id=r.provider_id,
            remote_id=r.remote_id,
            title=r.title,
            original_title=r.original_title,
            year=r.year,
            overview=r.overview,
            url=provider.page_url(kind, r.remote_id),
        )
        for r in provider.search(kind, query.strip(), Priority.INTERACTIVE)
    ]


def _search_provider(
    providers: MetadataProviders, library_id: int | None, provider_id: str | None
) -> BoundProvider:
    if provider_id is not None:
        return providers.get(provider_id)
    if library_id is not None:
        return providers.for_library(library_id)
    return providers.get(DEFAULT_PROVIDER)
