"""资料库的下载器列表：元数据、图片、字幕三个有序列表和字幕下载语言（ADR 0025）。"""

from __future__ import annotations

import json
import re
import sqlite3

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.schemas import Out
from app.clock import to_iso
from app.deps import AdminUser, AppClock, Db, MetadataPlugins, Plugins, ReadDb
from app.errors import AppError
from app.library.metadata_providers import MetadataProviders
from app.library.repository import LibraryRepository
from app.plugins import library_plugins
from app.plugins.library_plugins import ROLE_CAPABILITY, Entry, Role
from app.plugins.registry import PluginRegistry
from app.subtitles.content import normalize_language_code

router = APIRouter(prefix="/admin/libraries", tags=["admin"])

SUBTITLE_LANGUAGES_KEY = "subtitle_download_languages"
SUBTITLE_SKIP_IF_AUDIO_KEY = "subtitle_skip_if_audio"
MAX_SUBTITLE_LANGUAGES = 10
# 规范码：en、ja、ko 这类两位或 zh-Hans/zh-Hant
_LANGUAGE_CODE = re.compile(r"[a-z]{2,3}(?:-[A-Za-z]{2,4})?")


class DownloaderOut(Out):
    id: str
    name: str
    # 本资料库勾选了它
    enabled: bool
    plugin_enabled: bool
    # 启用但不能请求的原因（例如没填 Key）；停用或可用时为空
    unavailable_reason: str | None
    # 能负责匹配（同时提供 metadata.search 和 metadata.details）
    can_match: bool


class LibraryDownloadersOut(Out):
    metadata: list[DownloaderOut]
    images: list[DownloaderOut]
    subtitles: list[DownloaderOut]
    subtitle_languages: list[str]
    subtitle_skip_if_audio: bool


class DownloaderIn(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    enabled: bool = True


class DownloadersIn(BaseModel):
    metadata: list[DownloaderIn] | None = None
    images: list[DownloaderIn] | None = None
    subtitles: list[DownloaderIn] | None = None
    subtitle_languages: list[str] | None = None
    subtitle_skip_if_audio: bool | None = None


def _candidates(plugins: PluginRegistry, role: Role) -> list[str]:
    capability = ROLE_CAPABILITY[role]
    return [manifest.id for manifest in plugins.manifests() if capability in manifest.provides]


def _reason(
    role: Role,
    plugin_id: str,
    plugins: PluginRegistry,
    providers: MetadataProviders,
) -> str | None:
    if not plugins.is_enabled(plugin_id):
        return None
    try:
        if role == "metadata":
            return providers.get(plugin_id).unavailable_reason()
        if role == "image":
            source = providers.image_source(plugin_id)
            return None if source is None else source.unavailable_reason()
        provider = plugins.provider(plugin_id, "subtitle.download")
        check = getattr(provider, "unavailable_reason", None)
        return check() if callable(check) else None
    except AppError as exc:
        return exc.message
    except Exception:
        return "检查插件状态时出错，请查看日志"


def _downloader(
    entry: Entry,
    role: Role,
    plugins: PluginRegistry,
    providers: MetadataProviders,
) -> DownloaderOut:
    manifest = plugins.manifest(entry.id)
    return DownloaderOut(
        id=entry.id,
        name=entry.id if manifest is None else manifest.name,
        enabled=entry.enabled,
        plugin_enabled=plugins.is_enabled(entry.id),
        unavailable_reason=_reason(role, entry.id, plugins, providers),
        can_match=providers.can_match(entry.id),
    )


def _setting(connection: sqlite3.Connection, library_id: int, key: str) -> str | None:
    row = connection.execute(
        "SELECT value FROM library_setting WHERE library_id = ? AND key = ?",
        (library_id, key),
    ).fetchone()
    return None if row is None else str(row[0])


def subtitle_languages(connection: sqlite3.Connection, library_id: int) -> list[str]:
    raw = _setting(connection, library_id, SUBTITLE_LANGUAGES_KEY)
    if not raw:
        return []
    try:
        values = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [str(value) for value in values] if isinstance(values, list) else []


def subtitle_skip_if_audio(connection: sqlite3.Connection, library_id: int) -> bool:
    return _setting(connection, library_id, SUBTITLE_SKIP_IF_AUDIO_KEY) == "true"


def library_downloaders(
    connection: sqlite3.Connection,
    library_id: int,
    plugins: PluginRegistry,
    providers: MetadataProviders,
) -> LibraryDownloadersOut:
    """资料库的三个下载器列表；没保存过的候选插件按默认值或未勾选补齐。"""
    return LibraryDownloadersOut(
        metadata=[
            _downloader(entry, "metadata", plugins, providers)
            for entry in library_plugins.entries(
                connection, library_id, "metadata", _candidates(plugins, "metadata")
            )
        ],
        images=[
            _downloader(entry, "image", plugins, providers)
            for entry in library_plugins.entries(
                connection, library_id, "image", _candidates(plugins, "image")
            )
        ],
        subtitles=[
            _downloader(entry, "subtitle", plugins, providers)
            for entry in library_plugins.entries(
                connection, library_id, "subtitle", _candidates(plugins, "subtitle")
            )
        ],
        subtitle_languages=subtitle_languages(connection, library_id),
        subtitle_skip_if_audio=subtitle_skip_if_audio(connection, library_id),
    )


def _normalize_languages(values: list[str]) -> list[str]:
    languages: list[str] = []
    for value in values:
        code = normalize_language_code(value)
        if code is None or not _LANGUAGE_CODE.fullmatch(code):
            raise AppError(400, "invalid_subtitle_language", f"不支持的下载语言：{value}")
        if code not in languages:
            languages.append(code)
    if len(languages) > MAX_SUBTITLE_LANGUAGES:
        raise AppError(
            400,
            "invalid_subtitle_language",
            f"下载语言最多 {MAX_SUBTITLE_LANGUAGES} 个",
        )
    return languages


def _entries(role: Role, items: list[DownloaderIn], plugins: PluginRegistry) -> list[Entry]:
    allowed = set(_candidates(plugins, role))
    seen: set[str] = set()
    entries: list[Entry] = []
    for item in items:
        if item.id in seen or item.id not in allowed:
            raise AppError(
                400,
                "invalid_downloader",
                f"下载器 {item.id} 无效、重复或能力不符",
            )
        seen.add(item.id)
        entries.append(Entry(item.id, item.enabled))
    if not entries:
        # 空列表表示一个也不勾选。WP-0 的 entries 把「没有任何行」当成默认值，
        # 所以显式保存全部候选为未勾选，才能真正关掉这一列表
        entries = [Entry(plugin_id, False) for plugin_id in sorted(allowed)]
    return entries


def _check_primary(
    entries: list[Entry],
    plugins: PluginRegistry,
    providers: MetadataProviders,
) -> None:
    """第一个勾选的元数据插件负责匹配，必须能按片名搜索。"""
    listed = {entry.id for entry in entries}
    combined = [
        *entries,
        *(
            Entry(plugin_id, False)
            for plugin_id in _candidates(plugins, "metadata")
            if plugin_id not in listed
        ),
    ]
    primary = next((entry.id for entry in combined if entry.enabled), None)
    if primary is None:
        raise AppError(400, "metadata_downloader_required", "至少要勾选一个元数据下载器")
    if not providers.can_match(primary):
        raise AppError(
            400,
            "primary_cannot_match",
            f"元数据插件「{providers.name(primary)}」只能补缺，不能放在第一位",
        )
    if not plugins.is_enabled(primary):
        raise AppError(
            409,
            "metadata_provider_disabled",
            f"元数据插件「{providers.name(primary)}」没有启用",
        )


@router.get("/{library_id}/downloaders")
def get_library_downloaders(
    library_id: int,
    _: AdminUser,
    db: ReadDb,
    clock: AppClock,
    plugins: Plugins,
    providers: MetadataPlugins,
) -> LibraryDownloadersOut:
    LibraryRepository(db, clock).require(library_id)
    return library_downloaders(db, library_id, plugins, providers)


@router.put("/{library_id}/downloaders")
def set_library_downloaders(
    library_id: int,
    body: DownloadersIn,
    _: AdminUser,
    db: Db,
    clock: AppClock,
    plugins: Plugins,
    providers: MetadataPlugins,
) -> LibraryDownloadersOut:
    LibraryRepository(db, clock).require(library_id)
    fields = body.model_fields_set

    planned: dict[Role, list[Entry]] = {}
    if "metadata" in fields and body.metadata is not None:
        planned["metadata"] = _entries("metadata", body.metadata, plugins)
        _check_primary(planned["metadata"], plugins, providers)
    if "images" in fields and body.images is not None:
        planned["image"] = _entries("image", body.images, plugins)
    if "subtitles" in fields and body.subtitles is not None:
        planned["subtitle"] = _entries("subtitle", body.subtitles, plugins)
    languages = (
        _normalize_languages(body.subtitle_languages)
        if "subtitle_languages" in fields and body.subtitle_languages is not None
        else None
    )

    now = to_iso(clock.now())
    for role, entries in planned.items():
        library_plugins.replace(db, library_id, role, entries, now)
    if "subtitle_languages" in fields and languages is not None:
        db.execute(
            "INSERT INTO library_setting(library_id,key,value,updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(library_id,key) DO UPDATE SET value=excluded.value,"
            "updated_at=excluded.updated_at",
            (library_id, SUBTITLE_LANGUAGES_KEY, json.dumps(languages), now),
        )
    if "subtitle_skip_if_audio" in fields and body.subtitle_skip_if_audio is not None:
        db.execute(
            "INSERT INTO library_setting(library_id,key,value,updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(library_id,key) DO UPDATE SET value=excluded.value,"
            "updated_at=excluded.updated_at",
            (
                library_id,
                SUBTITLE_SKIP_IF_AUDIO_KEY,
                "true" if body.subtitle_skip_if_audio else "false",
                now,
            ),
        )
    return library_downloaders(db, library_id, plugins, providers)


__all__ = [
    "DownloadersIn",
    "LibraryDownloadersOut",
    "library_downloaders",
    "subtitle_languages",
    "subtitle_skip_if_audio",
]
