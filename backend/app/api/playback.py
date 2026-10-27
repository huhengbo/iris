"""播放接口：签发播放链接、已看标记（ADR 0015），以及给本机播放器用的播放跳转（ADR 0003）。"""

from __future__ import annotations

import logging
import re
from typing import Annotated, Literal, Self
from urllib.parse import quote

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, Field, model_validator
from starlette.responses import FileResponse, PlainTextResponse, RedirectResponse, Response

from app.api.schemas import Out
from app.clock import to_iso
from app.deps import AppClock, AppDatabase, CurrentUser, Db, Playback
from app.library.browse_models import AudioTrack, EmbeddedSubtitle, HdrFormat, MediaInfo
from app.ops.webhooks import emit as emit_webhook
from app.playback.events import PlayEvents
from app.playback.media_info import save_media_info
from app.playback.service import InlineContent, PlayDenied
from app.playback.state import WATCHED_RATIO, PlaybackStates, PlayClient

logger = logging.getLogger(__name__)

router = APIRouter(tags=["playback"])
# 跳转不在 /api 下：播放器拿到的是短路径，也不带登录态
redirect_router = APIRouter(include_in_schema=False)


class PlayRequest(BaseModel):
    """三选一：指定版本；或者电影条目、单集，用默认版本（上次用过的，否则最大的）。"""

    version_id: int | None = None
    item_id: int | None = None
    episode_id: int | None = None
    subtitle_id: int | None = Field(default=None, ge=1)
    client: PlayClient = "web"
    # builtin 是 App 的内置播放器：直接连当前服务器，没配置对外访问地址也能播
    player: Literal["external", "builtin"] = "external"

    @model_validator(mode="after")
    def exactly_one_target(self) -> Self:
        targets = [self.version_id, self.item_id, self.episode_id]
        if sum(t is not None for t in targets) != 1:
            raise ValueError("version_id、item_id、episode_id 只能且必须给一个")
        return self


class PlaySubtitleOut(Out):
    id: int
    label: str
    language: str | None
    forced: bool
    url: str | None
    path: str
    source: str


class LaunchLinksOut(Out):
    infuse: str
    vlc: str
    nplayer: str
    senplayer: str
    android: str


class PlayOut(Out):
    playable_id: int
    version_id: int
    title: str
    filename: str
    # 播放跳转地址，24 小时内有效，复制给其他播放器也能用。
    # 只有内置播放器在没配置对外访问地址时签发，url 和 launch 才为 null
    url: str | None
    # 跳转地址的路径部分（/play/...），内置播放器拼在自己连的服务器地址后面
    path: str
    expires_at: str
    # 第一条是默认字幕，调起链接里带的就是它
    subtitles: list[PlaySubtitleOut]
    selected_subtitle_id: int | None
    launch: LaunchLinksOut | None
    # 续播位置（秒），没有记录时为 null
    position: int | None


class ProgressIn(BaseModel):
    """内置播放器上报的进度，单位秒。"""

    position: int = Field(ge=0)
    duration: int = Field(gt=0)
    event: Literal["progress", "paused", "ended"] = "progress"


# 流信息里的文本都来自片源，长度和条数设上限，防止把乱七八糟的东西塞进库里
_Language = Annotated[str | None, Field(max_length=16)]
_TrackTitle = Annotated[str | None, Field(max_length=200)]
_Codec = Annotated[str | None, Field(max_length=32)]


class AudioTrackIn(BaseModel):
    language: _Language = None
    title: _TrackTitle = None
    codec: _Codec = None
    channels: int | None = Field(default=None, ge=1, le=64)
    default: bool = False


class EmbeddedSubtitleIn(BaseModel):
    language: _Language = None
    title: _TrackTitle = None
    codec: _Codec = None
    forced: bool = False
    default: bool = False


class MediaInfoIn(BaseModel):
    """内置播放器打开文件后读到的流信息。外挂字幕不算在 subtitles 里。"""

    # 秒
    duration: int | None = Field(default=None, gt=0)
    width: int | None = Field(default=None, gt=0, le=16384)
    height: int | None = Field(default=None, gt=0, le=16384)
    video_codec: _Codec = None
    hdr: HdrFormat | None = None
    audio: list[AudioTrackIn] = Field(default_factory=list, max_length=64)
    subtitles: list[EmbeddedSubtitleIn] = Field(default_factory=list, max_length=128)

    def to_media_info(self) -> MediaInfo:
        return MediaInfo(
            duration=self.duration,
            width=self.width,
            height=self.height,
            video_codec=self.video_codec,
            hdr=self.hdr,
            audio=[AudioTrack(**t.model_dump()) for t in self.audio],
            subtitles=[EmbeddedSubtitle(**t.model_dump()) for t in self.subtitles],
        )


@router.post("/play")
def issue_play(
    body: PlayRequest,
    user: CurrentUser,
    db: Db,
    database: AppDatabase,
    clock: AppClock,
    playback: Playback,
) -> PlayOut:
    ticket = playback.issue(
        db,
        user,
        version_id=body.version_id,
        item_id=body.item_id,
        episode_id=body.episode_id,
        subtitle_id=body.subtitle_id,
        client=body.client,
        builtin=body.player == "builtin",
    )
    emit_webhook(
        db,
        database,
        clock,
        "playback_started",
        {
            "user_id": user.id,
            "playable_id": ticket.playable_id,
            "version_id": ticket.version_id,
            "client": body.client,
            "mode": "builtin" if body.player == "builtin" else "external",
            "title": ticket.title,
        },
    )
    return PlayOut(
        playable_id=ticket.playable_id,
        version_id=ticket.version_id,
        title=ticket.title,
        filename=ticket.filename,
        url=ticket.url,
        path=ticket.path,
        expires_at=to_iso(ticket.expires_at),
        subtitles=[PlaySubtitleOut.model_validate(s) for s in ticket.subtitles],
        selected_subtitle_id=ticket.selected_subtitle_id,
        launch=LaunchLinksOut.model_validate(ticket.launch) if ticket.launch else None,
        position=ticket.position,
    )


@router.put("/playables/{playable_id}/progress", status_code=204)
def save_progress(
    playable_id: int,
    body: ProgressIn,
    user: CurrentUser,
    db: Db,
    database: AppDatabase,
    clock: AppClock,
) -> Response:
    """播放中定时、暂停和退出时上报。看到 90% 自动标记已看，开头 1 分钟内不记续播位置。"""
    PlaybackStates(db, clock, user).save_progress(playable_id, body.position, body.duration)
    finished = body.event == "ended" or body.position >= body.duration * WATCHED_RATIO
    events = PlayEvents(db, clock)
    event = events.open_event(user.id, playable_id)
    if event is not None:
        events.record_progress(event.id, body.position, finished=finished)
        if finished or body.event == "paused":
            emit_webhook(
                db,
                database,
                clock,
                "playback_ended" if finished else "playback_paused",
                {
                    "event_id": event.id,
                    "user_id": user.id,
                    "playable_id": playable_id,
                    "version_id": event.version_id,
                    "client": event.client,
                    "mode": event.mode,
                    "watched_seconds": body.position,
                },
            )
    stop_message = events.take_stop_request(user.id, playable_id)
    headers: dict[str, str] = {}
    if stop_message is not None:
        headers = {"X-Iris-Stop": "1", "X-Iris-Stop-Message": quote(stop_message)}
    return Response(status_code=204, headers=headers)


@router.put("/versions/{version_id}/media-info", status_code=204)
def report_media_info(
    version_id: int, body: MediaInfoIn, user: CurrentUser, db: Db, clock: AppClock
) -> Response:
    """内置播放器每次打开文件后上报；详情页的版本带上它（ADR 0019）。"""
    save_media_info(db, clock, user, version_id, body.to_media_info())
    return Response(status_code=204)


@router.put("/items/{item_id}/watched", status_code=204)
def mark_item_watched(item_id: int, user: CurrentUser, db: Db, clock: AppClock) -> Response:
    """电影标记它自己，剧集标记全部集。"""
    PlaybackStates(db, clock, user).mark_item(item_id, True)
    return Response(status_code=204)


@router.delete("/items/{item_id}/watched", status_code=204)
def unmark_item_watched(item_id: int, user: CurrentUser, db: Db, clock: AppClock) -> Response:
    PlaybackStates(db, clock, user).mark_item(item_id, False)
    return Response(status_code=204)


@router.put("/seasons/{season_id}/watched", status_code=204)
def mark_season_watched(season_id: int, user: CurrentUser, db: Db, clock: AppClock) -> Response:
    PlaybackStates(db, clock, user).mark_season(season_id, True)
    return Response(status_code=204)


@router.delete("/seasons/{season_id}/watched", status_code=204)
def unmark_season_watched(season_id: int, user: CurrentUser, db: Db, clock: AppClock) -> Response:
    PlaybackStates(db, clock, user).mark_season(season_id, False)
    return Response(status_code=204)


@router.put("/episodes/{episode_id}/watched", status_code=204)
def mark_episode_watched(episode_id: int, user: CurrentUser, db: Db, clock: AppClock) -> Response:
    PlaybackStates(db, clock, user).mark_episode(episode_id, True)
    return Response(status_code=204)


@router.delete("/episodes/{episode_id}/watched", status_code=204)
def unmark_episode_watched(episode_id: int, user: CurrentUser, db: Db, clock: AppClock) -> Response:
    PlaybackStates(db, clock, user).mark_episode(episode_id, False)
    return Response(status_code=204)


_PLAY_TOKEN_IN_PATH = re.compile(r"^/play/[^/?]+")


class RedactPlayTokens(logging.Filter):
    """把 uvicorn 访问日志里跳转地址的令牌换成 ***：令牌 24 小时内都能直接播放，不能落进日志。"""

    def filter(self, record: logging.LogRecord) -> bool:
        # uvicorn 访问日志的参数是 (客户端地址, 方法, 路径和查询串, HTTP 版本, 状态码)
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            record.args = (*args[:2], _PLAY_TOKEN_IN_PATH.sub("/play/***", args[2]), *args[3:])
        return True


@redirect_router.api_route("/play/{token}/{filename}", methods=["GET", "HEAD"])
def play_redirect(
    token: Annotated[str, Path(max_length=1024)],
    filename: str,
    request: Request,
    playback: Playback,
) -> Response:
    """文件名只是给播放器识别扩展名的，不参与校验。失败时返回纯文本，播放器只认状态码。"""
    user_agent = request.headers.get("user-agent", "")
    try:
        target = playback.resolve(token, user_agent)
    except PlayDenied as exc:
        logger.info("play %s denied %s, user-agent %r", request.method, exc.status, user_agent)
        headers = {"Cache-Control": "no-store"}
        if exc.retry_after is not None:
            headers["Retry-After"] = str(exc.retry_after)
        return PlainTextResponse(exc.message, status_code=exc.status, headers=headers)
    if isinstance(target, InlineContent):
        headers = {
            "Cache-Control": "no-store",
            "Content-Type": target.media_type,
            "Content-Length": str(len(target.content)),
        }
        if request.method == "HEAD":
            return Response(status_code=200, headers=headers)
        return Response(target.content, headers=headers)
    if target.path is not None:
        # 本地来源的文件由服务直接提供，FileResponse 支持播放器拖动用的 Range 请求
        return FileResponse(target.path, filename=filename, headers={"Cache-Control": "no-store"})
    assert target.url is not None
    # 直链绑定 UA：播放器跳转后换 UA 会被 115 拒绝，排查时要看它用的是什么 UA（不记直链和令牌）
    logger.info("play %s redirected, user-agent %r", request.method, user_agent)
    return RedirectResponse(target.url, status_code=302, headers={"Cache-Control": "no-store"})
