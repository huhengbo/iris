"""播放活动历史、统计和停止内置播放会话接口。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.schemas import Out
from app.deps import (
    AdminUser,
    AppClock,
    Db,
    ReadDb,
)
from app.ops.history import HistoryFilter, PlayHistory
from app.playback.events import PlayEvents

router = APIRouter(prefix="/admin", tags=["admin"])


class PlayRecordOut(Out):
    id: int
    user_id: int
    playable_id: int
    version_id: int | None
    client: str
    created_at: str
    device_id: str | None
    mode: str
    ended_at: str | None
    watched_seconds: int | None
    username: str
    title: str
    library_id: int


class PlayRecordPageOut(Out):
    items: list[PlayRecordOut]
    total: int


class ClientPlaysOut(Out):
    mode: str
    count: int


class TopItemOut(Out):
    id: int
    title: str
    count: int


class PlayStatsOut(Out):
    plays: int
    watched_seconds: int
    by_client: list[ClientPlaysOut]
    top_items: list[TopItemOut]


class StopOut(Out):
    event_id: int
    stopped: bool


@router.get("/activity/history")
def activity_history(
    _: AdminUser,
    db: ReadDb,
    clock: AppClock,
    user_id: int | None = None,
    library_id: int | None = None,
    item_id: int | None = None,
    client: str | None = None,
    start: str | None = None,
    end: str | None = None,
    offset: int = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> PlayRecordPageOut:
    criteria = HistoryFilter(user_id, library_id, item_id, client, start, end)
    page = PlayHistory(db, clock).search(criteria, offset=offset, limit=limit)
    return PlayRecordPageOut.model_validate(page)


@router.get("/activity/stats")
def activity_stats(_: AdminUser, db: ReadDb, clock: AppClock) -> PlayStatsOut:
    return PlayStatsOut.model_validate(PlayHistory(db, clock).stats())


@router.post("/activity/sessions/{event_id}/stop")
def stop_activity(event_id: int, _: AdminUser, db: Db, clock: AppClock) -> StopOut:
    PlayEvents(db, clock).request_stop(event_id)
    return StopOut(event_id=event_id, stopped=True)
