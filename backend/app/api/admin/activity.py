"""管理后台的播放动态：正在播放和最近播放，覆盖全部用户。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.schemas import Out
from app.deps import AdminUser, AppClock, ReadDb
from app.ops.history import PlayHistory

router = APIRouter(prefix="/admin/activity", tags=["admin"])


class PlayingOut(Out):
    username: str
    item_id: int
    title: str
    # 剧集的季号、集号和集名；电影为空
    season_number: int | None
    episode_number: int | None
    episode_name: str | None
    # 秒；内置播放器和投屏上报，开头不到 1 分钟或已看完时为空
    position: int | None
    duration: int | None
    at: str
    # 最近一次发起播放的客户端，例如 Infuse
    client: str | None


class PlayEventOut(Out):
    username: str
    item_id: int
    title: str
    season_number: int | None
    episode_number: int | None
    episode_name: str | None
    # 发起播放的客户端：web、android、ios
    client: str
    at: str


class ActivityOut(Out):
    now_playing: list[PlayingOut]
    recent: list[PlayEventOut]


@router.get("")
def activity(_: AdminUser, db: ReadDb, clock: AppClock) -> ActivityOut:
    history = PlayHistory(db, clock)
    return ActivityOut(
        now_playing=[PlayingOut.model_validate(item) for item in history.now_playing()],
        recent=[PlayEventOut.model_validate(item) for item in history.recent()],
    )
