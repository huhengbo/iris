"""资料库推荐行和高级设置接口。"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.admin.library_downloaders import (
    SUBTITLE_LANGUAGES_KEY,
    SUBTITLE_SKIP_IF_AUDIO_KEY,
)
from app.api.browse import HubRowOut
from app.api.schemas import Out
from app.clock import to_iso
from app.deps import (
    AdminUser,
    AppClock,
    CurrentUser,
    Db,
    MetadataPlugins,
    ReadDb,
)
from app.errors import AppError, NotFound
from app.library.browse import BrowseQueries, visible_library_ids
from app.library.metadata_providers import SETTING_KEY as METADATA_PROVIDER_KEY
from app.library.metadata_providers import save_library_provider
from app.library.repository import LibraryRepository
from app.library.settings import (
    HUB_KINDS,
    LibraryHubSpec,
    LibrarySettings,
    hub_title,
)

router = APIRouter(prefix="/admin", tags=["admin"])
public_router = APIRouter(tags=["library-settings"])

# 一个资料库最多保存多少个推荐行，防止误传超长列表
MAX_HUBS = 50


class SettingIn(BaseModel):
    value: str = Field(max_length=2000)


class LibraryHubIn(BaseModel):
    kind: str = Field(min_length=1, max_length=32)
    # 类型行的类型名；其余行为空
    arg: str | None = Field(default=None, max_length=50)
    show_home: bool = True
    show_library: bool = True


class LibraryHubOut(Out):
    # 管理员还没保存过时是默认布局，id 为 0
    id: int
    library_id: int
    kind: str
    arg: str | None
    position: int
    show_home: bool
    show_library: bool
    title: str


def _library_genres(connection: sqlite3.Connection, library_id: int) -> set[str]:
    rows = connection.execute(
        "SELECT DISTINCT g.value FROM media_item m, json_each(m.genres) g WHERE m.library_id = ?",
        (library_id,),
    ).fetchall()
    return {str(row[0]) for row in rows}


@router.get("/libraries/{library_id}/hubs")
def list_library_hubs(
    library_id: int, user: AdminUser, db: ReadDb, clock: AppClock
) -> list[LibraryHubOut]:
    """管理员看到实际生效的推荐行：保存过的是保存的顺序，没保存过的是默认布局。"""
    library = LibraryRepository(db, clock).require(library_id)
    queries = BrowseQueries(db, user)
    saved = {hub.id: hub for hub in LibrarySettings(db, clock).hubs(library_id)}
    result = []
    for spec in queries.effective_hubs(library_id):
        # 默认布局没有数据库行；保存过的行按位置找回 ID
        hub_id = next(
            (hub.id for hub in saved.values() if hub.kind == spec.kind and hub.arg == spec.arg),
            0,
        )
        result.append(
            LibraryHubOut(
                id=hub_id,
                library_id=library_id,
                kind=spec.kind,
                arg=spec.arg,
                position=spec.position,
                show_home=spec.show_home,
                show_library=spec.show_library,
                title=hub_title(library.name, spec.kind, spec.arg),
            )
        )
    return result


@router.put("/libraries/{library_id}/hubs")
def set_library_hubs(
    library_id: int, body: list[LibraryHubIn], user: AdminUser, db: Db, clock: AppClock
) -> list[LibraryHubOut]:
    """整体替换推荐行。传空列表等于恢复默认布局。"""
    LibraryRepository(db, clock).require(library_id)
    if len(body) > MAX_HUBS:
        raise AppError(400, "invalid_library_hub", f"推荐行最多 {MAX_HUBS} 条")
    genres = _library_genres(db, library_id)
    seen: set[tuple[str, str | None]] = set()
    specs: list[LibraryHubSpec] = []
    for position, row in enumerate(body):
        kind = row.kind.strip()
        if kind not in HUB_KINDS:
            raise AppError(400, "invalid_library_hub", f"不支持的推荐行：{row.kind}")
        arg = (row.arg or "").strip() or None
        if kind == "genre":
            if arg is None:
                raise AppError(400, "invalid_library_hub", "类型行缺少类型名")
            if arg not in genres:
                raise AppError(400, "invalid_library_hub", f"资料库里没有类型「{arg}」")
        elif arg is not None:
            raise AppError(400, "invalid_library_hub", "只有类型行可以带类型名")
        key = (kind, arg)
        if key in seen:
            raise AppError(400, "invalid_library_hub", "推荐行重复")
        seen.add(key)
        specs.append(
            LibraryHubSpec(
                kind=kind,
                arg=arg,
                position=position,
                show_home=row.show_home,
                show_library=row.show_library,
            )
        )
    settings = LibrarySettings(db, clock)
    settings.replace_hubs(library_id, specs)
    return list_library_hubs(library_id, user, db, clock)


@public_router.get("/libraries/{library_id}/hubs")
def public_library_hubs(library_id: int, user: CurrentUser, db: ReadDb) -> list[HubRowOut]:
    """资料库「推荐」标签用：只返回当前用户可见、配置为显示且不为空的行。"""
    if library_id not in visible_library_ids(db, user):
        raise NotFound("资料库不存在")
    return [HubRowOut.of(row) for row in BrowseQueries(db, user).library_hubs(library_id)]


@router.get("/libraries/{library_id}/settings")
def library_settings(library_id: int, _: AdminUser, db: ReadDb, clock: AppClock) -> dict[str, str]:
    LibraryRepository(db, clock).require(library_id)
    return LibrarySettings(db, clock).effective(library_id)


@router.put("/libraries/{library_id}/settings/{key}")
def set_library_setting(
    library_id: int,
    key: str,
    body: SettingIn,
    _: AdminUser,
    db: Db,
    clock: AppClock,
    providers: MetadataPlugins,
) -> dict[str, str]:
    LibraryRepository(db, clock).require(library_id)
    if key == METADATA_PROVIDER_KEY:
        providers.check_selectable(body.value)
        save_library_provider(db, library_id, body.value, to_iso(clock.now()))
    elif key in (SUBTITLE_LANGUAGES_KEY, SUBTITLE_SKIP_IF_AUDIO_KEY):
        raise AppError(
            400,
            "library_setting_reserved",
            f"{key} 由下载器接口维护，请用专用的下载器设置",
        )
    else:
        LibrarySettings(db, clock).put(library_id, key, body.value)
    return {key: body.value}
