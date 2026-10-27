"""在线字幕的用户接口和管理员接口（ADR 0025）。"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from app.api.browse import SubtitleOut
from app.api.schemas import Out
from app.deps import (
    AdminUser,
    CurrentUser,
    OnlineSubtitlesDep,
    Playback,
    require_subtitle_download,
)
from app.subtitles.online import DownloadRequest, SubtitleSearch, VersionSubtitles

router = APIRouter(tags=["subtitles"])

Language = Literal["zh-Hans", "zh-Hant", "zh", "en", "ja", "ko"]
ProviderStatus = Literal["ok", "failed", "unavailable"]


class SubtitleProviderOut(Out):
    id: str
    name: str
    ready: bool
    # 只有管理员能看到不可用的原因
    reason: str | None


class SubtitleProvidersOut(Out):
    # 当前用户是否有权搜索和下载在线字幕；管理员始终为 true
    allowed: bool
    providers: list[SubtitleProviderOut]


class SubtitleSearchProviderOut(Out):
    id: str
    name: str
    status: ProviderStatus
    message: str | None


class SubtitleCandidateOut(Out):
    provider_id: str
    provider_name: str
    remote_id: str
    label: str
    release: str | None
    language: str | None
    format: str
    forced: bool
    hearing_impaired: bool
    score: float | None
    downloads: int | None
    uploaded_at: str | None
    # 这个版本里已经保存过同一条字幕
    downloaded: bool


class SubtitleSearchOut(Out):
    version_id: int
    language: str | None
    providers: list[SubtitleSearchProviderOut]
    results: list[SubtitleCandidateOut]


class SubtitleDownloadIn(BaseModel):
    provider_id: str = Field(min_length=1, max_length=64)
    remote_id: str = Field(min_length=1, max_length=1024)
    language: Language | None = None
    label: str = Field(min_length=1, max_length=200)
    release: str | None = Field(default=None, max_length=300)
    format: str | None = Field(default=None, max_length=8)
    forced: bool = False
    hearing_impaired: bool = False


class SubtitlePlayOut(Out):
    path: str
    # 没配置对外访问地址时为 null，客户端用自己连的服务器地址拼 path
    url: str | None


class OnlineSubtitleCreateOut(Out):
    subtitle: SubtitleOut
    created: bool
    play: SubtitlePlayOut


class AdminSubtitleVersionOut(Out):
    id: int
    label: str
    filename: str


class AdminExternalSubtitleOut(Out):
    rel_path: str
    deletable: bool
    delete_blocked_reason: str | None


class AdminCreatedByOut(Out):
    id: int
    username: str


class AdminOnlineSubtitleOut(Out):
    provider_id: str
    provider_name: str
    release: str | None
    size: int
    created_at: str
    created_by: AdminCreatedByOut | None


class AdminSubtitleOut(Out):
    id: int
    source: Literal["external", "online"]
    label: str
    language: str | None
    forced: bool
    hearing_impaired: bool
    default: bool
    format: str | None
    external: AdminExternalSubtitleOut | None
    online: AdminOnlineSubtitleOut | None


class AdminEmbeddedSubtitleOut(Out):
    language: str | None
    title: str | None
    codec: str | None
    forced: bool
    default: bool


class AdminVersionSubtitlesOut(Out):
    version: AdminSubtitleVersionOut
    subtitles: list[AdminSubtitleOut]
    # 没人用内置播放器播过时为 null
    embedded: list[AdminEmbeddedSubtitleOut] | None


@router.get("/subtitles/providers")
def list_subtitle_providers(
    user: CurrentUser,
    subtitles: OnlineSubtitlesDep,
    version_id: Annotated[int | None, Query(ge=1)] = None,
) -> SubtitleProvidersOut:
    allowed = user.is_admin or user.allow_subtitle_download
    library_id = None if version_id is None else subtitles.library_id_of(version_id)
    return SubtitleProvidersOut(
        allowed=allowed,
        providers=[
            SubtitleProviderOut(
                id=info.id,
                name=info.name,
                ready=info.ready,
                # 普通用户不需要知道插件为什么不可用，管理员才看原因
                reason=info.reason if user.is_admin else None,
            )
            for info in subtitles.providers(library_id)
        ],
    )


@router.get("/versions/{version_id}/subtitles/search")
def search_subtitles(
    version_id: int,
    user: CurrentUser,
    subtitles: OnlineSubtitlesDep,
    language: Annotated[Language | None, Query()] = None,
    provider_id: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
) -> SubtitleSearchOut:
    # 先按可见范围判断版本是否存在，再检查下载权限
    subtitles.ensure_visible(user, version_id)
    require_subtitle_download(user)
    result = subtitles.search(user, version_id, language=language, provider_id=provider_id)
    return _search_out(result)


@router.post(
    "/versions/{version_id}/subtitles",
    responses={
        201: {
            "description": "新建在线字幕",
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/OnlineSubtitleCreateOut"}
                }
            },
        }
    },
)
def download_subtitle(
    version_id: int,
    body: SubtitleDownloadIn,
    user: CurrentUser,
    subtitles: OnlineSubtitlesDep,
    playback: Playback,
    response: Response,
) -> OnlineSubtitleCreateOut:
    subtitles.ensure_visible(user, version_id)
    require_subtitle_download(user)
    outcome = subtitles.download(
        user,
        version_id,
        DownloadRequest(
            provider_id=body.provider_id,
            remote_id=body.remote_id,
            language=body.language,
            label=body.label,
            release=body.release,
            format=body.format,
            forced=body.forced,
            hearing_impaired=body.hearing_impaired,
        ),
    )
    path, url = playback.subtitle_link(user, version_id, outcome.subtitle.id)
    response.status_code = 201 if outcome.created else 200
    return OnlineSubtitleCreateOut(
        subtitle=SubtitleOut(
            id=outcome.subtitle.id,
            label=outcome.subtitle.label,
            language=outcome.subtitle.language,
            forced=outcome.subtitle.forced,
            source=outcome.subtitle.source,
        ),
        created=outcome.created,
        play=SubtitlePlayOut(path=path, url=url),
    )


@router.get("/admin/versions/{version_id}/subtitles")
def admin_version_subtitles(
    version_id: int, _: AdminUser, subtitles: OnlineSubtitlesDep
) -> AdminVersionSubtitlesOut:
    return _admin_out(subtitles.admin_list(version_id))


@router.delete("/admin/subtitles/{subtitle_id}", status_code=204)
def delete_subtitle(
    subtitle_id: int,
    _: AdminUser,
    subtitles: OnlineSubtitlesDep,
    confirm: Annotated[bool, Query()] = False,
) -> Response:
    """在线字幕直接删除；外挂字幕要 confirm=true，并删除来源上的文件。"""
    subtitles.delete(subtitle_id, confirm=confirm)
    return Response(status_code=204)


def _search_out(result: SubtitleSearch) -> SubtitleSearchOut:
    return SubtitleSearchOut(
        version_id=result.version_id,
        language=result.language,
        providers=[SubtitleSearchProviderOut.model_validate(item) for item in result.providers],
        results=[SubtitleCandidateOut.model_validate(item) for item in result.results],
    )


def _admin_out(result: VersionSubtitles) -> AdminVersionSubtitlesOut:
    return AdminVersionSubtitlesOut(
        version=AdminSubtitleVersionOut(
            id=result.version_id, label=result.label, filename=result.filename
        ),
        subtitles=[AdminSubtitleOut.model_validate(item) for item in result.subtitles],
        embedded=(
            None
            if result.embedded is None
            else [AdminEmbeddedSubtitleOut.model_validate(item) for item in result.embedded]
        ),
    )
