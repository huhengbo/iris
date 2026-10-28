"""Emby 兼容路由。全部注册在 /emby/ 小写路径下，根路径由中间件改写（ADR 0024）。"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict
from starlette.responses import FileResponse, PlainTextResponse, RedirectResponse

from app.auth.sessions import SessionService, Unauthorized
from app.auth.users import User
from app.deps import (
    AccessSigner,
    AppClock,
    AppDatabase,
    Db,
    LoginThrottleDep,
    MetadataPlugins,
    Playback,
    ReadDb,
    Settings,
)
from app.emby.auth import (
    EmbySession,
    EmbyUser,
    login_client,
    playback_grants,
    require_user,
    session_for_token,
    token_from,
)
from app.emby.dto import emby_date, item_dto, media_source_dto, user_data
from app.emby.ids import (
    EPISODE,
    ITEM,
    LIBRARY,
    PERSON,
    SEASON,
    VERSION,
    decode,
    encode,
)
from app.errors import AppError, NotFound
from app.library.browse import BrowseQueries
from app.library.covers import library_cover
from app.library.emby import EmbyEntry, EmbyQueries, find_item_image
from app.library.favorites import FavoriteKind, Favorites
from app.library.images import ImageKind, image_sizes
from app.library.people import ensure_person_detail
from app.playback.emby import EmbyPlayback
from app.playback.events import PlayEvents
from app.playback.service import InlineContent, PlayDenied
from app.playback.state import WATCHED_RATIO, PlaybackStates

router = APIRouter(include_in_schema=False)

EMBY_VERSION = "4.8.0.0"
TICKS = 10_000_000


# ---- 请求体 ----


class AuthenticateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    Username: str = ""
    # 新客户端用 Pw，旧客户端可能用 Password
    Pw: str | None = None
    Password: str | None = None


class PlaybackReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ItemId: str | int | None = None
    MediaSourceId: str | int | None = None
    PositionTicks: int | None = None
    Client: str | None = None
    DeviceId: str | None = None


# ---- 服务器和用户 ----


def _server(settings: Settings) -> tuple[str, str]:
    server = settings.server()
    return server.id, server.name


def _user_dto(user: User, server_id: str) -> dict[str, Any]:
    # 管理员通过 Emby 登录也没有管理能力（ADR 0024）
    return {
        "Name": user.username,
        "ServerId": server_id,
        "Id": str(user.id),
        "HasPassword": True,
        "HasConfiguredPassword": True,
        "LastActivityDate": emby_date(user.last_seen_at),
        "Policy": {
            "IsAdministrator": False,
            "IsHidden": False,
            "IsDisabled": user.disabled,
            "EnableAllFolders": True,
            "EnabledFolders": [],
            "EnableRemoteControlOfOtherUsers": False,
            "EnableSharedDeviceControl": False,
            "EnableContentDownloading": False,
            "EnableSyncTranscoding": False,
            "EnableAllChannels": False,
            "EnableAllDevices": False,
        },
        "Configuration": {
            "DisplayMissingEpisodes": False,
            "EnableThemeSongs": False,
            "EnableThemeVideos": False,
            "SubtitleLanguagePreference": "",
            "PlayDefaultAudioTrack": True,
        },
    }


def _session_dto(session: EmbySession, server_id: str, at: str) -> dict[str, Any]:
    return {
        "Id": str(session.session_id),
        "UserId": str(session.user.id),
        "UserName": session.user.username,
        "Client": session.client_name,
        "DeviceName": session.device_name,
        "DeviceId": session.device_id,
        "ApplicationVersion": session.client_version,
        "LastActivityDate": emby_date(at),
        "ServerId": server_id,
        "IsActive": True,
    }


def _local_address(request: Request, settings: Settings) -> str:
    return settings.public_url() or str(request.base_url).rstrip("/")


# ---- 公开接口 ----


@router.get("/emby/system/info/public")
def system_info_public(request: Request, settings: Settings) -> dict[str, Any]:
    server_id, name = _server(settings)
    return {
        "ServerName": name,
        "Version": EMBY_VERSION,
        "Id": server_id,
        "LocalAddress": _local_address(request, settings),
        "OperatingSystem": "Linux",
        "OperatingSystemDisplayName": "Linux",
        "ProductName": "Iris",
        "StartupWizardCompleted": True,
        "HasPendingRestart": False,
        "IsShuttingDown": False,
        "SupportsLibraryMonitor": False,
        "WebSocketPortNumber": 8096,
    }


@router.api_route("/emby/system/ping", methods=["GET", "POST"])
def system_ping() -> PlainTextResponse:
    return PlainTextResponse("Emby Server")


@router.get("/emby/users/public")
def users_public() -> list[Any]:
    # 不公开用户列表
    return []


@router.get("/emby/branding/configuration")
def branding_configuration() -> dict[str, Any]:
    return {}


@router.post("/emby/users/authenticatebyname")
def authenticate_by_name(
    body: AuthenticateRequest,
    request: Request,
    database: AppDatabase,
    clock: AppClock,
    throttle: LoginThrottleDep,
    signer: AccessSigner,
    settings: Settings,
) -> dict[str, Any]:
    client_ip = request.client.host if request.client else "unknown"
    username = body.Username.strip()
    password = body.Pw if body.Pw is not None else body.Password
    if not username or password is None:
        raise AppError(401, "invalid_credentials", "用户名或密码错误")
    wait = throttle.retry_after_seconds(username, client_ip)
    if wait:
        raise AppError(
            429, "too_many_attempts", f"登录失败次数过多，请 {wait // 60 + 1} 分钟后再试"
        )
    # 密码哈希在只读连接上做，登录成功后才开写事务
    with database.read_session() as connection:
        user = SessionService(connection, clock, signer).check_credentials(username, password)
    if user is None:
        throttle.record_failure(username, client_ip)
        raise AppError(401, "invalid_credentials", "用户名或密码错误")
    info = login_client(request)
    with database.session() as connection:
        device = SessionService(connection, clock, signer).start_device_session_for(
            user,
            client_name=info.client_name,
            device_name=info.device_name,
            device_id=info.device_id,
            client_version=info.client_version,
            ip=client_ip,
        )
    throttle.record_success(username)
    server_id, _ = _server(settings)
    at = clock.now().isoformat()
    return {
        "User": _user_dto(user, server_id),
        "AccessToken": device.token,
        "ServerId": server_id,
        "SessionInfo": _session_dto(
            EmbySession(
                user=user,
                session_id=device.session_id,
                client_name=info.client_name,
                device_name=info.device_name,
                device_id=info.device_id,
                client_version=info.client_version,
            ),
            server_id,
            at,
        ),
    }


# ---- 登录后 ----


@router.get("/emby/system/info")
def system_info(_: EmbyUser, settings: Settings) -> dict[str, Any]:
    server_id, name = _server(settings)
    return {
        "ServerName": name,
        "Version": EMBY_VERSION,
        "Id": server_id,
        "OperatingSystem": "Linux",
        "SystemUpdateLevel": "Release",
        "HasPendingRestart": False,
        "IsShuttingDown": False,
        "SupportsLibraryMonitor": False,
    }


@router.get("/emby/users/me")
def users_me(session: EmbyUser, settings: Settings) -> dict[str, Any]:
    server_id, _ = _server(settings)
    return _user_dto(session.user, server_id)


@router.get("/emby/users/{uid}")
def user_detail(uid: str, session: EmbyUser, settings: Settings) -> dict[str, Any]:
    require_user(session, uid)
    server_id, _ = _server(settings)
    return _user_dto(session.user, server_id)


@router.post("/emby/sessions/logout", status_code=204)
def sessions_logout(session: EmbyUser, db: Db, clock: AppClock, signer: AccessSigner) -> Response:
    SessionService(db, clock, signer).end_device_session(session.user.id, session.session_id)
    return Response(status_code=204)


@router.post("/emby/sessions/capabilities", status_code=204)
@router.post("/emby/sessions/capabilities/full", status_code=204)
def sessions_capabilities() -> Response:
    return Response(status_code=204)


@router.get("/emby/users/{uid}/views")
def user_views(uid: str, session: EmbyUser, db: ReadDb, settings: Settings) -> dict[str, Any]:
    require_user(session, uid)
    return _views(db, session.user, settings)


@router.get("/emby/library/mediafolders")
def media_folders(session: EmbyUser, db: ReadDb, settings: Settings) -> dict[str, Any]:
    return _views(db, session.user, settings)


def _views(db: ReadDb, user: User, settings: Settings) -> dict[str, Any]:
    server_id, _ = _server(settings)
    items = [_library_dto(library, server_id) for library in BrowseQueries(db, user).libraries()]
    return {"Items": items, "TotalRecordCount": len(items)}


def _library_dto(library: Any, server_id: str) -> dict[str, Any]:
    return {
        "Name": library.name,
        "ServerId": server_id,
        "Id": encode(library.id, LIBRARY),
        "Type": "CollectionFolder",
        "CollectionType": "movies" if library.kind == "movie" else "tvshows",
        "IsFolder": True,
        "ChildCount": library.item_count,
        # 封面是横版图，Primary、Thumb 和 Backdrop 都给同一张
        "ImageTags": {"Primary": library.cover_hash, "Thumb": library.cover_hash}
        if library.cover_hash
        else {},
        "BackdropImageTags": [library.cover_hash] if library.cover_hash else [],
        "PrimaryImageAspectRatio": 16 / 9,
        "UserData": {
            "PlaybackPositionTicks": 0,
            "PlayCount": 0,
            "IsFavorite": False,
            "Played": False,
        },
    }


# ---- 条目 ----


def _entry_for(db: ReadDb, user: User, internal_id: int, type_bit: int) -> EmbyEntry:
    queries = EmbyQueries(db, user)
    if type_bit == ITEM:
        entry = queries.get("Movie", internal_id) or queries.get("Series", internal_id)
    elif type_bit == SEASON:
        entry = queries.get("Season", internal_id)
    elif type_bit == EPISODE:
        entry = queries.get("Episode", internal_id)
    elif type_bit == PERSON:
        entry = queries.get("Person", internal_id)
    else:
        entry = None
    if entry is None:
        raise NotFound("条目不存在")
    return entry


def _entry_by_value(db: ReadDb, user: User, value: str | int) -> EmbyEntry:
    internal_id, type_bit = decode(value)
    return _entry_for(db, user, internal_id, type_bit)


def _parent(queries: EmbyQueries, value: str | None) -> tuple[str | None, int | None]:
    if not value:
        return None, None
    internal_id, type_bit = decode(value)
    if type_bit == LIBRARY:
        return "Library", internal_id
    if type_bit == SEASON:
        return "Season", internal_id
    if type_bit == ITEM:
        kind = queries.item_kind(internal_id)
        if kind == "series":
            return "Series", internal_id
        if kind == "movie":
            return "Movie", internal_id
    raise NotFound("ParentId 不存在")


def _list(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _bool(value: str | None) -> bool | None:
    if value is None or value == "":
        return None
    return value.strip().lower() in ("1", "true", "yes")


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


def _filters(request: Request) -> tuple[bool | None, bool | None, bool]:
    selected = set(_list(request.query_params.get("Filters")))
    favorite = _bool(request.query_params.get("IsFavorite"))
    if favorite is None and "IsFavorite" in selected:
        favorite = True
    played = _bool(request.query_params.get("IsPlayed"))
    if played is None:
        if "IsPlayed" in selected:
            played = True
        elif "IsUnplayed" in selected:
            played = False
    return favorite, played, "IsResumable" in selected


def _filter_id(queries: EmbyQueries, value: str) -> tuple[int | None, str]:
    """Ids 参数里的对外 ID 转成 (内部 ID, Emby 类型)。"""
    try:
        internal_id, type_bit = decode(value)
    except NotFound:
        return None, ""
    if type_bit == ITEM:
        kind = queries.item_kind(internal_id)
        if kind is None:
            return None, ""
        return internal_id, "Movie" if kind == "movie" else "Series"
    names = {SEASON: "Season", EPISODE: "Episode", PERSON: "Person"}
    name = names.get(type_bit)
    return (internal_id, name) if name is not None else (None, "")


_SCOPING_PARAMS = (
    "ParentId",
    "Ids",
    "SearchTerm",
    "PersonIds",
    "IncludeItemTypes",
    "Filters",
    "IsFavorite",
    "IsPlayed",
    "Genres",
    "Years",
)


def _is_root_listing(request: Request) -> bool:
    """没有 ParentId 也不递归时，Emby 返回用户根目录下的媒体库，不是全部条目。"""
    params = request.query_params
    if _bool(params.get("Recursive")):
        return False
    return not any(params.get(name) for name in _SCOPING_PARAMS)


def _wants_media_sources(request: Request) -> bool:
    fields = _list(request.query_params.get("Fields"))
    return "mediasources" in {field.lower() for field in fields}


def _list_dtos(
    request: Request,
    db: ReadDb,
    playback: Playback,
    session: EmbySession,
    entries: list[EmbyEntry],
    server_id: str,
) -> list[dict[str, Any]]:
    """列表里的条目。Fields 带 MediaSources 时附上播放源。

    有的客户端（网易爆米花）直接拿列表里的播放源播放，不再请求 PlaybackInfo。
    """
    if not _wants_media_sources(request):
        return [item_dto(entry, server_id=server_id) for entry in entries]
    api_key = token_from(request) or ""
    return [
        item_dto(
            entry,
            server_id=server_id,
            include_media_sources=True,
            media_sources=_media_sources(db, playback, session.user, entry, api_key)
            if entry.type in ("Movie", "Episode")
            else [],
        )
        for entry in entries
    ]


def _items(
    request: Request,
    session: EmbySession,
    db: ReadDb,
    playback: Playback,
    settings: Settings,
) -> dict[str, Any]:
    if _is_root_listing(request):
        return _views(db, session.user, settings)
    queries = EmbyQueries(db, session.user)
    parent_type, parent_id = _parent(queries, request.query_params.get("ParentId"))
    include = _list(request.query_params.get("IncludeItemTypes"))
    if not include:
        include = {
            "Library": ["Movie", "Series"],
            "Series": ["Season"],
            "Season": ["Episode"],
            None: ["Movie", "Series"],
        }.get(parent_type, ["Movie", "Series"])
    favorite, played, resumable = _filters(request)
    ids: list[tuple[str, int]] = []
    for value in _list(request.query_params.get("Ids")):
        internal_id, type_bit = _filter_id(queries, value)
        if internal_id is not None:
            ids.append((type_bit, internal_id))
    person_ids = []
    for value in _list(request.query_params.get("PersonIds")):
        try:
            person_internal_id, _person_bit = decode(value, expected=PERSON)
            person_ids.append(person_internal_id)
        except NotFound:
            continue
    years = [_int(value, 0) for value in _list(request.query_params.get("Years"))]
    start = max(_int(request.query_params.get("StartIndex"), 0), 0)
    limit = min(max(_int(request.query_params.get("Limit"), 100), 1), 10000)
    sort_by = _list(request.query_params.get("SortBy")) or ["SortName"]
    descending = (request.query_params.get("SortOrder") or "").lower().startswith("desc")
    page = queries.browse(
        include_types=include,
        parent_type=parent_type,
        parent_id=parent_id,
        favorite=favorite,
        played=played,
        resumable=resumable,
        search=request.query_params.get("SearchTerm"),
        ids=ids,
        genres=_list(request.query_params.get("Genres")),
        years=years,
        person_ids=person_ids,
        sort_by=sort_by,
        descending=descending,
        offset=start,
        limit=limit,
    )
    server_id, _ = _server(settings)
    return {
        "Items": _list_dtos(request, db, playback, session, page.items, server_id),
        "TotalRecordCount": page.total,
        "StartIndex": start,
    }


@router.get("/emby/users/{uid}/items")
def user_items(
    uid: str,
    request: Request,
    session: EmbyUser,
    db: ReadDb,
    playback: Playback,
    settings: Settings,
) -> dict[str, Any]:
    require_user(session, uid)
    return _items(request, session, db, playback, settings)


@router.get("/emby/items")
def items(
    request: Request, session: EmbyUser, db: ReadDb, playback: Playback, settings: Settings
) -> dict[str, Any]:
    return _items(request, session, db, playback, settings)


@router.get("/emby/users/{uid}/items/resume")
def user_resume(
    uid: str,
    request: Request,
    session: EmbyUser,
    db: ReadDb,
    playback: Playback,
    settings: Settings,
) -> dict[str, Any]:
    require_user(session, uid)
    limit = min(max(_int(request.query_params.get("Limit"), 100), 1), 1000)
    entries = EmbyQueries(db, session.user).resume(limit)
    server_id, _ = _server(settings)
    return {
        "Items": _list_dtos(request, db, playback, session, entries, server_id),
        "TotalRecordCount": len(entries),
        "StartIndex": 0,
    }


@router.get("/emby/users/{uid}/items/latest")
def user_latest(
    uid: str,
    request: Request,
    session: EmbyUser,
    db: ReadDb,
    playback: Playback,
    settings: Settings,
) -> list[dict[str, Any]]:
    require_user(session, uid)
    queries = EmbyQueries(db, session.user)
    parent_type, parent_id = _parent(queries, request.query_params.get("ParentId"))
    limit = min(max(_int(request.query_params.get("Limit"), 20), 1), 200)
    entries = queries.latest(parent_type, parent_id, limit)
    server_id, _ = _server(settings)
    return _list_dtos(request, db, playback, session, entries, server_id)


@router.get("/emby/users/{uid}/items/{item_id}")
def user_item(
    uid: str,
    item_id: str,
    request: Request,
    session: EmbyUser,
    db: ReadDb,
    database: AppDatabase,
    clock: AppClock,
    providers: MetadataPlugins,
    playback: Playback,
    settings: Settings,
) -> dict[str, Any]:
    require_user(session, uid)
    return _item_detail(
        item_id, request, session, db, database, clock, providers, playback, settings
    )


@router.get("/emby/items/{item_id}")
def item_detail(
    item_id: str,
    request: Request,
    session: EmbyUser,
    db: ReadDb,
    database: AppDatabase,
    clock: AppClock,
    providers: MetadataPlugins,
    playback: Playback,
    settings: Settings,
) -> dict[str, Any]:
    return _item_detail(
        item_id, request, session, db, database, clock, providers, playback, settings
    )


def _item_detail(
    item_id: str,
    request: Request,
    session: EmbySession,
    db: ReadDb,
    database: AppDatabase,
    clock: AppClock,
    providers: MetadataPlugins,
    playback: Playback,
    settings: Settings,
) -> dict[str, Any]:
    internal_id, type_bit = decode(item_id)
    if type_bit == PERSON:
        # 人物简介按需拉取一次，和 /api/people/{id} 的行为一致
        ensure_person_detail(database, clock, providers, internal_id)
    entry = _entry_for(db, session.user, internal_id, type_bit)
    server_id, _ = _server(settings)
    sources = (
        _media_sources(db, playback, session.user, entry, token_from(request) or "")
        if entry.type in ("Movie", "Episode")
        else []
    )
    return item_dto(
        entry,
        server_id=server_id,
        include_media_sources=True,
        media_sources=sources,
    )


# ---- 剧集 ----


@router.get("/emby/shows/nextup")
def shows_next_up(
    request: Request, session: EmbyUser, db: ReadDb, playback: Playback, settings: Settings
) -> dict[str, Any]:
    limit = min(max(_int(request.query_params.get("Limit"), 20), 1), 100)
    entries = EmbyQueries(db, session.user).next_up(limit)
    server_id, _ = _server(settings)
    return {
        "Items": _list_dtos(request, db, playback, session, entries, server_id),
        "TotalRecordCount": len(entries),
        "StartIndex": 0,
    }


@router.get("/emby/shows/{series_id}/seasons")
def show_seasons(
    series_id: str, session: EmbyUser, db: ReadDb, settings: Settings
) -> dict[str, Any]:
    internal_id, type_bit = decode(series_id, expected=ITEM)
    entry = _entry_for(db, session.user, internal_id, type_bit)
    if entry.type != "Series":
        raise NotFound("剧集不存在")
    entries = EmbyQueries(db, session.user).seasons_of(entry.id)
    server_id, _ = _server(settings)
    return {
        "Items": [item_dto(item, server_id=server_id) for item in entries],
        "TotalRecordCount": len(entries),
        "StartIndex": 0,
    }


@router.get("/emby/shows/{series_id}/episodes")
def show_episodes(
    series_id: str,
    request: Request,
    session: EmbyUser,
    db: ReadDb,
    playback: Playback,
    settings: Settings,
) -> dict[str, Any]:
    internal_id, type_bit = decode(series_id, expected=ITEM)
    entry = _entry_for(db, session.user, internal_id, type_bit)
    if entry.type != "Series":
        raise NotFound("剧集不存在")
    season_id = request.query_params.get("SeasonId")
    season_internal = None
    if season_id:
        season_internal, _ = decode(season_id, expected=SEASON)
    entries = EmbyQueries(db, session.user).episodes_of(entry.id, season_internal)
    server_id, _ = _server(settings)
    return {
        "Items": _list_dtos(request, db, playback, session, entries, server_id),
        "TotalRecordCount": len(entries),
        "StartIndex": 0,
    }


# ---- 空结果和其他 ----


@router.get("/emby/users/{uid}/items/{item_id}/intros")
def item_intros(uid: str, item_id: str, session: EmbyUser, db: ReadDb) -> list[Any]:
    require_user(session, uid)
    _entry_by_value(db, session.user, item_id)
    return []


@router.get("/emby/items/{item_id}/intros")
def item_intros_root(item_id: str, session: EmbyUser, db: ReadDb) -> list[Any]:
    _entry_by_value(db, session.user, item_id)
    return []


@router.get("/emby/users/{uid}/items/{item_id}/specialfeatures")
def item_special_features(uid: str, item_id: str, session: EmbyUser, db: ReadDb) -> list[Any]:
    require_user(session, uid)
    _entry_by_value(db, session.user, item_id)
    return []


@router.get("/emby/items/{item_id}/specialfeatures")
def item_special_features_root(item_id: str, session: EmbyUser, db: ReadDb) -> list[Any]:
    _entry_by_value(db, session.user, item_id)
    return []


@router.get("/emby/users/{uid}/items/{item_id}/similar")
def user_item_similar(uid: str, item_id: str, session: EmbyUser, db: ReadDb) -> dict[str, Any]:
    require_user(session, uid)
    _entry_by_value(db, session.user, item_id)
    return {"Items": [], "TotalRecordCount": 0, "StartIndex": 0}


@router.get("/emby/items/{item_id}/similar")
def item_similar(item_id: str, session: EmbyUser, db: ReadDb) -> dict[str, Any]:
    _entry_by_value(db, session.user, item_id)
    return {"Items": [], "TotalRecordCount": 0, "StartIndex": 0}


@router.get("/emby/users/{uid}/items/{item_id}/thememedia")
def user_item_theme_media(uid: str, item_id: str, session: EmbyUser, db: ReadDb) -> dict[str, Any]:
    require_user(session, uid)
    _entry_by_value(db, session.user, item_id)
    return _empty_theme_media()


@router.get("/emby/items/{item_id}/thememedia")
def item_theme_media(item_id: str, session: EmbyUser, db: ReadDb) -> dict[str, Any]:
    _entry_by_value(db, session.user, item_id)
    return _empty_theme_media()


def _empty_theme_media() -> dict[str, Any]:
    empty = {"Items": [], "TotalRecordCount": 0}
    return {
        "ThemeVideosResult": empty,
        "ThemeSongsResult": empty,
        "SoundtrackSongsResult": empty,
    }


@router.get("/emby/plugins")
def plugins(_: EmbyUser) -> list[Any]:
    # Infuse 登录后会查已装插件（InfuseSync）；不返回 JSON 数组会报「意外服务器响应」
    return []


@router.get("/emby/genres")
def genres(_: EmbyUser) -> dict[str, Any]:
    return {"Items": [], "TotalRecordCount": 0}


@router.get("/emby/persons")
def persons(_: EmbyUser) -> dict[str, Any]:
    return {"Items": [], "TotalRecordCount": 0}


@router.get("/emby/displaypreferences/{preference_id}")
def display_preferences(preference_id: str, _: EmbyUser) -> dict[str, Any]:
    return {
        "Id": preference_id,
        "SortBy": "SortName",
        "SortOrder": "Ascending",
        "CustomPrefs": {},
    }


# ---- 图片 ----


@router.get("/emby/items/{item_id}/images/{image_type}")
@router.get("/emby/items/{item_id}/images/{image_type}/{index}")
def item_image(
    item_id: str,
    image_type: str,
    request: Request,
    db: ReadDb,
    index: str = "0",
) -> RedirectResponse:
    # 和 Emby 一样不要求令牌：客户端拉图片时不带 api_key。
    internal_id, type_bit = decode(item_id)
    item_type = {ITEM: "Movie", SEASON: "Season", EPISODE: "Episode", PERSON: "Person"}.get(
        type_bit
    )
    if type_bit == LIBRARY:
        cover = (
            library_cover(db, internal_id)
            if image_type.lower() in ("primary", "thumb", "backdrop")
            else None
        )
        image: tuple[str, str] | None = None if cover is None else (cover.hash, cover.kind)
    else:
        image = (
            None if item_type is None else find_item_image(db, item_type, internal_id, image_type)
        )
    if image is None:
        raise NotFound("图片不存在")
    digest, kind = image
    sizes = image_sizes(digest, cast(ImageKind, kind))
    if not sizes:
        raise NotFound("图片不存在")
    target = _int(request.query_params.get("maxWidth"), 0)
    if target <= 0:
        target = _int(request.query_params.get("fillWidth"), 0)
    chosen = min(sizes, key=lambda size: abs(size.width - target)) if target > 0 else sizes[-1]
    return RedirectResponse(chosen.url, status_code=302, headers={"Cache-Control": "no-store"})


# ---- 收藏和已看 ----


def _favorite_kind(entry: EmbyEntry) -> FavoriteKind:
    if entry.type == "Movie":
        return "movie"
    if entry.type == "Series":
        return "series"
    if entry.type == "Episode":
        return "episode"
    if entry.type == "Person":
        return "person"
    raise AppError(400, "not_favoriteable", "这一类型不能收藏")


def _refetched(db: ReadDb, user: User, entry: EmbyEntry) -> EmbyEntry:
    result = EmbyQueries(db, user).get(entry.type, entry.id)
    if result is None:
        raise NotFound("条目不存在")
    return result


@router.post("/emby/users/{uid}/favoriteitems/{item_id}")
def add_favorite(
    uid: str, item_id: str, session: EmbyUser, db: Db, clock: AppClock
) -> dict[str, Any]:
    require_user(session, uid)
    entry = _entry_by_value(db, session.user, item_id)
    kind = _favorite_kind(entry)
    Favorites(db, session.user, clock).add(kind, entry.id)
    return user_data(_refetched(db, session.user, entry))


@router.delete("/emby/users/{uid}/favoriteitems/{item_id}")
def remove_favorite(
    uid: str, item_id: str, session: EmbyUser, db: Db, clock: AppClock
) -> dict[str, Any]:
    require_user(session, uid)
    entry = _entry_by_value(db, session.user, item_id)
    kind = _favorite_kind(entry)
    Favorites(db, session.user, clock).remove(kind, entry.id)
    return user_data(_refetched(db, session.user, entry))


@router.post("/emby/users/{uid}/playeditems/{item_id}")
def mark_played(
    uid: str, item_id: str, session: EmbyUser, db: Db, clock: AppClock
) -> dict[str, Any]:
    require_user(session, uid)
    entry = _entry_by_value(db, session.user, item_id)
    states = PlaybackStates(db, clock, session.user)
    if entry.type in ("Movie", "Series"):
        states.mark_item(entry.id, True)
    elif entry.type == "Season":
        states.mark_season(entry.id, True)
    elif entry.type == "Episode":
        states.mark_episode(entry.id, True)
    else:
        raise AppError(400, "not_playable", "这一类型不能标记已看")
    return user_data(_refetched(db, session.user, entry))


@router.delete("/emby/users/{uid}/playeditems/{item_id}")
def unmark_played(
    uid: str, item_id: str, session: EmbyUser, db: Db, clock: AppClock
) -> dict[str, Any]:
    require_user(session, uid)
    entry = _entry_by_value(db, session.user, item_id)
    states = PlaybackStates(db, clock, session.user)
    if entry.type in ("Movie", "Series"):
        states.mark_item(entry.id, False)
    elif entry.type == "Season":
        states.mark_season(entry.id, False)
    elif entry.type == "Episode":
        states.mark_episode(entry.id, False)
    else:
        raise AppError(400, "not_playable", "这一类型不能标记已看")
    return user_data(_refetched(db, session.user, entry))


# ---- 播放信息 ----


def _media_sources(
    db: ReadDb,
    playback: Playback,
    user: User,
    entry: EmbyEntry,
    api_key: str,
) -> list[dict[str, Any]]:
    if entry.playable_id is None:
        return []
    emby = EmbyPlayback(db, user)
    versions = emby.versions(entry.playable_id)
    default_id = emby.default_version(playback, entry.playable_id)
    ordered = sorted(versions, key=lambda version: version.version_id != default_id)
    return [
        media_source_dto(
            version,
            entry,
            api_key=api_key,
            is_default=version.version_id == default_id,
        )
        for version in ordered
    ]


@router.api_route("/emby/items/{item_id}/playbackinfo", methods=["GET", "POST"])
def playback_info(
    item_id: str,
    request: Request,
    session: EmbyUser,
    db: ReadDb,
    playback: Playback,
) -> dict[str, Any]:
    entry = _entry_by_value(db, session.user, item_id)
    if entry.type not in ("Movie", "Episode") or entry.playable_id is None:
        raise NotFound("这个条目不能播放")
    token = token_from(request) or ""
    sources = _media_sources(db, playback, session.user, entry, token)
    grants = playback_grants(request)
    for source in sources:
        grants.remember(decode(source["Id"], expected=VERSION)[0], token)
    return {"MediaSources": sources, "PlaySessionId": uuid.uuid4().hex}


# ---- 播放跳转 ----


def _denied(exc: PlayDenied) -> PlainTextResponse:
    headers = {"Cache-Control": "no-store"}
    if exc.retry_after is not None:
        headers["Retry-After"] = str(exc.retry_after)
    return PlainTextResponse(exc.message, status_code=exc.status, headers=headers)


def _stream_response(target: Any) -> Response:
    if isinstance(target, InlineContent):
        return Response(
            target.content,
            media_type=target.media_type,
            headers={"Cache-Control": "no-store"},
        )
    if target.path is not None:
        return FileResponse(
            target.path,
            filename=Path(target.path).name,
            headers={"Cache-Control": "no-store"},
        )
    assert target.url is not None
    return RedirectResponse(target.url, status_code=302, headers={"Cache-Control": "no-store"})


def _version_for(
    db: ReadDb,
    playback: Playback,
    user: User,
    entry: EmbyEntry,
    media_source_id: str | None,
) -> int:
    if entry.playable_id is None:
        raise NotFound("这个条目不能播放")
    emby = EmbyPlayback(db, user)
    versions = emby.versions(entry.playable_id)
    if media_source_id:
        version_id, _ = decode(media_source_id, expected=VERSION)
        if version_id not in {version.version_id for version in versions}:
            raise NotFound("版本不存在")
        return version_id
    return emby.default_version(playback, entry.playable_id)


def _stream(
    item_id: str,
    request: Request,
    session: EmbySession,
    db: ReadDb,
    playback: Playback,
) -> Response:
    entry = _entry_by_value(db, session.user, item_id)
    if entry.type not in ("Movie", "Episode"):
        raise NotFound("这个条目不能播放")
    version_id = _version_for(
        db, playback, session.user, entry, request.query_params.get("MediaSourceId")
    )
    playback_grants(request).remember(version_id, token_from(request) or "")
    user_agent = request.headers.get("user-agent", "")
    try:
        target = playback.redirect_version(
            session.user, version_id, user_agent, f"emby:{session.session_id}"
        )
    except PlayDenied as exc:
        return _denied(exc)
    return _stream_response(target)


@router.api_route("/emby/videos/{item_id}/stream", methods=["GET", "HEAD"])
@router.api_route("/emby/videos/{item_id}/stream.{extension}", methods=["GET", "HEAD"])
@router.api_route("/emby/videos/{item_id}/original.{extension}", methods=["GET", "HEAD"])
def video_stream(
    item_id: str,
    request: Request,
    session: EmbyUser,
    db: ReadDb,
    playback: Playback,
    extension: str = "",
) -> Response:
    return _stream(item_id, request, session, db, playback)


@router.api_route(
    "/emby/videos/{item_id}/{media_source_id}/subtitles/{index}/stream.{extension}",
    methods=["GET", "HEAD"],
)
def subtitle_stream(
    item_id: str,
    media_source_id: str,
    index: int,
    request: Request,
    db: ReadDb,
    playback: Playback,
    extension: str = "",
) -> Response:
    token = token_from(request)
    if not token:
        # 不带令牌的字幕请求只放行最近请求过播放的版本（见 PlaybackGrants）
        try:
            grant_version, _ = decode(media_source_id, expected=VERSION)
        except NotFound:
            grant_version = None
        token = playback_grants(request).token_for(grant_version) if grant_version else None
    if not token:
        raise Unauthorized("请先登录")
    session = session_for_token(request, token)
    entry = _entry_by_value(db, session.user, item_id)
    if entry.type not in ("Movie", "Episode"):
        raise NotFound("这个条目不能播放")
    version_id = _version_for(db, playback, session.user, entry, media_source_id)
    if index >= 1000:
        index -= 1000
    subtitle = EmbyPlayback(db, session.user).subtitle_at(version_id, index)
    try:
        target = playback.redirect_version(
            session.user,
            version_id,
            request.headers.get("user-agent", ""),
            f"emby:{session.session_id}",
            subtitle_id=subtitle.id,
        )
    except PlayDenied as exc:
        return _denied(exc)
    return _stream_response(target)


# ---- 进度 ----


def _report_item(
    db: ReadDb, playback: Playback, user: User, body: PlaybackReport
) -> tuple[EmbyEntry, int, int]:
    if body.ItemId is None:
        raise NotFound("缺少 ItemId")
    entry = _entry_by_value(db, user, body.ItemId)
    if entry.type not in ("Movie", "Episode") or entry.playable_id is None:
        raise NotFound("这个条目不能播放")
    version_id = _version_for(
        db, playback, user, entry, str(body.MediaSourceId) if body.MediaSourceId else None
    )
    return entry, entry.playable_id, version_id


def _seconds(value: int | None) -> int | None:
    return None if value is None else max(int(value // TICKS), 0)


def _record_progress(
    body: PlaybackReport,
    session: EmbySession,
    db: Db,
    clock: AppClock,
    playback: Playback,
    *,
    stopped: bool,
) -> Response:
    _, playable_id, version_id = _report_item(db, playback, session.user, body)
    position = _seconds(body.PositionTicks)
    duration = EmbyQueries(db, session.user).playable_duration(playable_id)
    events = PlayEvents(db, clock)
    if position is not None and duration is not None:
        PlaybackStates(db, clock, session.user).save_progress(playable_id, position, duration)
    event = events.open_event(session.user.id, playable_id)
    if event is None:
        PlaybackStates(db, clock, session.user).record_play(
            playable_id,
            version_id,
            session.client_name or body.Client or "Emby",
            mode="external",
            device_id=session.device_id or body.DeviceId,
        )
        event = events.open_event(session.user.id, playable_id)
    if event is not None and position is not None:
        finished = stopped or (duration is not None and position >= duration * WATCHED_RATIO)
        if stopped:
            events.finish_event(event.id, position)
        else:
            events.record_progress(event.id, position, finished=finished)
    return Response(status_code=204)


@router.post("/emby/sessions/playing", status_code=204)
def playing(
    body: PlaybackReport,
    session: EmbyUser,
    db: Db,
    clock: AppClock,
    playback: Playback,
) -> Response:
    _, playable_id, version_id = _report_item(db, playback, session.user, body)
    PlaybackStates(db, clock, session.user).record_play(
        playable_id,
        version_id,
        session.client_name or body.Client or "Emby",
        mode="external",
        device_id=session.device_id or body.DeviceId,
    )
    return Response(status_code=204)


@router.post("/emby/sessions/playing/progress", status_code=204)
def playing_progress(
    body: PlaybackReport,
    session: EmbyUser,
    db: Db,
    clock: AppClock,
    playback: Playback,
) -> Response:
    return _record_progress(body, session, db, clock, playback, stopped=False)


@router.post("/emby/sessions/playing/stopped", status_code=204)
def playing_stopped(
    body: PlaybackReport,
    session: EmbyUser,
    db: Db,
    clock: AppClock,
    playback: Playback,
) -> Response:
    return _record_progress(body, session, db, clock, playback, stopped=True)
