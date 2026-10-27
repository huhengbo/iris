from __future__ import annotations

import sqlite3
from collections.abc import Callable

from fastapi import APIRouter, Response
from iris_plugin_sdk import ResourceListing, ResourceNotFound, ResourceUnavailable
from pydantic import BaseModel, Field

from app.api.schemas import ImageOut
from app.clock import Clock, to_iso
from app.deps import (
    AdminUser,
    AppClock,
    AppDatabase,
    AppMetadata,
    Db,
    MetadataPlugins,
    ReadDb,
    Resources,
    Settings,
)
from app.errors import AppError
from app.library.covers import LibraryCover, library_cover
from app.library.metadata_providers import library_provider_id, save_library_provider
from app.library.repository import (
    MAX_NAME_LENGTH,
    FolderRef,
    Library,
    LibraryFolder,
    LibraryKind,
    LibraryRepository,
)
from app.resources.repository import ResourceRepository, ResourceSource
from app.resources.service import FolderSource, ResourceSources
from app.sync.jobs import SyncJobRepository

router = APIRouter(prefix="/admin/libraries", tags=["admin"])

MAX_FOLDERS = 50


class FolderOut(BaseModel):
    id: int
    path: str
    # 目录所在的来源；locator 是目录在来源里的定位符，核心不解释它
    source_id: int
    source_name: str
    plugin_id: str
    # 目录在来源里的定位符；null 表示来源根目录
    locator: str | None


class AdminLibraryOut(BaseModel):
    id: int
    name: str
    kind: LibraryKind
    sort_order: int
    folders: list[FolderOut]
    video_count: int
    missing_count: int
    created_at: str
    subtitle_language: str | None
    # 新匹配的条目用哪个元数据插件刮削
    metadata_provider: str
    # 横版封面；cover_custom 为 False 时是自动取的最近入库条目背景图
    cover: list[ImageOut]
    cover_custom: bool

    @classmethod
    def of(
        cls,
        library: Library,
        subtitle_language: str | None,
        sources: dict[int, FolderSource],
        metadata_provider: str,
        cover: LibraryCover | None,
    ) -> AdminLibraryOut:
        return cls(
            id=library.id,
            name=library.name,
            kind=library.kind,
            sort_order=library.sort_order,
            folders=[_folder_out(f.id, f.path, sources[f.id]) for f in library.folders],
            video_count=library.video_count,
            missing_count=library.missing_count,
            created_at=library.created_at,
            subtitle_language=subtitle_language,
            metadata_provider=metadata_provider,
            cover=[ImageOut(width=size.width, url=size.url) for size in cover.sizes]
            if cover is not None
            else [],
            cover_custom=cover is not None and cover.custom,
        )


class MetadataProviderOut(BaseModel):
    id: str
    name: str
    built_in: bool
    enabled: bool
    # 启用但还不能请求的原因，例如没填 Key
    unavailable_reason: str | None


def _folder_out(id: int, path: str, source: FolderSource) -> FolderOut:
    return FolderOut(
        id=id,
        path=path,
        source_id=source.source_id,
        source_name=source.name,
        plugin_id=source.plugin_id,
        locator=source.locator,
    )


class RootRef(BaseModel):
    """来源里的一个目录。"""

    source_id: int
    # null 表示来源根目录
    locator: str | None = Field(default=None, max_length=4096)


class CreateLibraryRequest(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    kind: LibraryKind
    roots: list[RootRef] = Field(min_length=1, max_length=MAX_FOLDERS)
    # 不传时用内置 TMDB
    metadata_provider: str | None = Field(default=None, min_length=1, max_length=100)


class UpdateLibraryRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME_LENGTH)
    # 给出 roots 时它就是资料库目录的完整列表
    roots: list[RootRef] | None = Field(default=None, max_length=MAX_FOLDERS)
    subtitle_language: str | None = Field(default=None, max_length=32)
    # 只影响之后新匹配的条目，已有条目仍由原来的插件刷新
    metadata_provider: str | None = Field(default=None, min_length=1, max_length=100)


class ReorderRequest(BaseModel):
    ids: list[int]


@router.get("")
def list_libraries(
    _: AdminUser, db: ReadDb, clock: AppClock, settings: Settings
) -> list[AdminLibraryOut]:
    libraries = LibraryRepository(db, clock).all()
    sources = ResourceSources.folders(db)
    return [
        AdminLibraryOut.of(
            library,
            settings.library_subtitle_language(library.id),
            sources,
            library_provider_id(db, library.id),
            library_cover(db, library.id),
        )
        for library in libraries
    ]


@router.get("/metadata-providers")
def list_metadata_providers(_: AdminUser, providers: MetadataPlugins) -> list[MetadataProviderOut]:
    """资料库可以选用的元数据插件，内置的排在前面。"""
    return [
        MetadataProviderOut(
            id=info.id,
            name=info.name,
            built_in=info.built_in,
            enabled=info.enabled,
            unavailable_reason=info.reason,
        )
        for info in providers.installed()
    ]


@router.post("", status_code=201)
def create_library(
    body: CreateLibraryRequest,
    _: AdminUser,
    db: ReadDb,
    database: AppDatabase,
    clock: AppClock,
    resources: Resources,
    providers: MetadataPlugins,
) -> AdminLibraryOut:
    if body.metadata_provider is not None:
        providers.check_selectable(body.metadata_provider)
    requested = _requested(body.roots)
    # 到来源上确认目录要走网络，先在事务外做完，再开一个短写事务落库
    plans = _plan_folders(db, clock, resources, requested, [])
    with database.session() as write:
        folders = [plan(ResourceRepository(write, clock)) for plan in plans]
        library = LibraryRepository(write, clock).create(body.name, body.kind, folders)
        if body.metadata_provider is not None:
            save_library_provider(write, library.id, body.metadata_provider, to_iso(clock.now()))
        return AdminLibraryOut.of(
            library,
            None,
            ResourceSources.folders(write),
            library_provider_id(write, library.id),
            library_cover(write, library.id),
        )


@router.put("/order")
def reorder_libraries(
    body: ReorderRequest, _: AdminUser, db: Db, clock: AppClock
) -> list[AdminLibraryOut]:
    libraries = LibraryRepository(db, clock).reorder(body.ids)
    sources = ResourceSources.folders(db)
    return [
        AdminLibraryOut.of(
            library,
            None,
            sources,
            library_provider_id(db, library.id),
            library_cover(db, library.id),
        )
        for library in libraries
    ]


@router.patch("/{library_id}")
def update_library(
    library_id: int,
    body: UpdateLibraryRequest,
    _: AdminUser,
    db: ReadDb,
    database: AppDatabase,
    clock: AppClock,
    resources: Resources,
    settings: Settings,
    providers: MetadataPlugins,
) -> AdminLibraryOut:
    library = LibraryRepository(db, clock).require(library_id)
    if body.metadata_provider is not None:
        providers.check_selectable(body.metadata_provider)
    plans: list[FolderPlan] | None = None
    if body.roots is not None:
        _ensure_not_syncing(db, clock)
        requested = _requested(body.roots)
        # 到来源上确认目录要走网络，先在事务外做完
        plans = _plan_folders(db, clock, resources, requested, library.folders)
    with database.session() as write:
        libraries = LibraryRepository(write, clock)
        if plans is not None:
            # 规划期间同步可能已经开始，写入前再确认一次
            _ensure_not_syncing(write, clock)
            repository = ResourceRepository(write, clock)
            library = libraries.set_folders(library.id, [plan(repository) for plan in plans])
            repository.prune_roots()
        if body.name is not None:
            library = libraries.rename(library.id, body.name)
        if body.metadata_provider is not None:
            save_library_provider(write, library.id, body.metadata_provider, to_iso(clock.now()))
        sources = ResourceSources.folders(write)
        provider_id = library_provider_id(write, library.id)
        cover = library_cover(write, library.id)
    # 设置服务自己开写事务，不能嵌在上面的写事务里
    if "subtitle_language" in body.model_fields_set:
        settings.save_library_subtitle_language(library.id, body.subtitle_language or None)
    return AdminLibraryOut.of(
        library, settings.library_subtitle_language(library.id), sources, provider_id, cover
    )


@router.delete("/{library_id}", status_code=204)
def delete_library(
    library_id: int,
    _: AdminUser,
    database: AppDatabase,
    clock: AppClock,
    metadata: AppMetadata,
) -> Response:
    with database.session() as connection:
        _ensure_not_syncing(connection, clock)
        LibraryRepository(connection, clock).delete(library_id)
        ResourceRepository(connection, clock).prune_roots()
    # 删除提交之后才能清理：资料库的条目没了，它们的图片不再被引用
    metadata.artwork.cleanup()
    return Response(status_code=204)


def _requested(roots: list[RootRef]) -> list[tuple[int, str | None]]:
    """请求里的目录，按 (来源 ID, 定位符) 去重；locator 为 None 表示来源根目录。"""
    return list(dict.fromkeys((root.source_id, root.locator) for root in roots))


FolderPlan = Callable[[ResourceRepository], FolderRef]


def _plan_folders(
    db: sqlite3.Connection,
    clock: Clock,
    resources: ResourceSources,
    requested: list[tuple[int, str | None]],
    current: list[LibraryFolder],
) -> list[FolderPlan]:
    """确认每个目录在来源上存在，得到重叠校验用的路径键。

    只读，可以走网络；登记来源目录要写库，留给调用方在写事务里执行返回的计划。
    资料库已有的目录不再访问来源，它们的路径在每次同步时刷新。
    """
    repository = ResourceRepository(db, clock)
    # 已登记的目录按来源目录相认，不用再访问来源
    known = {folder.resource_root_id: folder for folder in current}
    plans: list[FolderPlan] = []
    for source_id, locator in requested:
        source = repository.source(source_id)
        existing = repository.find_root(source.id, locator)
        kept = known.get(existing) if existing is not None else None
        if kept is not None:
            plans.append(_fixed(FolderRef(kept.scope_key, kept.path, kept.resource_root_id)))
            continue
        if not source.enabled:
            raise _disabled(source.name)
        provider = resources.provider(source.plugin_id)
        if provider is None:
            raise AppError(409, "resource_plugin_disabled", f"来源「{source.name}」的插件没有启用")
        try:
            listing = provider.open(source.id, source.config).browse(locator)
        except ResourceNotFound as exc:
            raise AppError(400, "invalid_folder", exc.message) from None
        except ResourceUnavailable as exc:
            raise AppError(503, "resource_unavailable", exc.message) from None
        if not listing.selectable:
            raise AppError(400, "library_folder_not_selectable", "不能把这个目录作为资料库目录")
        plans.append(_source_plan(source, listing))
    return plans


def _fixed(ref: FolderRef) -> FolderPlan:
    return lambda _repository: ref


def _source_plan(source: ResourceSource, listing: ResourceListing) -> FolderPlan:
    def plan(repository: ResourceRepository) -> FolderRef:
        root_id = repository.ensure_root(source.id, listing.locator, listing.path)
        return FolderRef.of(source.id, listing, root_id)

    return plan


def _disabled(name: str) -> AppError:
    return AppError(409, "resource_source_disabled", f"资源来源「{name}」已停用，先启用再添加目录")


def _ensure_not_syncing(db: sqlite3.Connection, clock: Clock) -> None:
    if SyncJobRepository(db, clock).running() is not None:
        raise AppError(409, "sync_running", "同步进行中，等同步结束后再修改资料库目录或删除资料库")
