"""稍后观看接口：电影和整部剧集的加入、移出，以及「我的稍后观看」列表。"""

from __future__ import annotations

from fastapi import APIRouter, Response

from app.api.browse import ItemCardOut, Limit, Offset
from app.api.schemas import Out
from app.deps import AppClock, CurrentUser, Db, ReadDb
from app.library.browse import BrowseQueries
from app.library.watchlist import Watchlist

router = APIRouter(tags=["watchlist"])


class WatchlistPageOut(Out):
    items: list[ItemCardOut]
    total: int


@router.put("/items/{item_id}/watchlist", status_code=204)
def add_item_watchlist(item_id: int, user: CurrentUser, db: Db, clock: AppClock) -> Response:
    Watchlist(db, user, clock).add(item_id)
    return Response(status_code=204)


@router.delete("/items/{item_id}/watchlist", status_code=204)
def remove_item_watchlist(item_id: int, user: CurrentUser, db: Db, clock: AppClock) -> Response:
    Watchlist(db, user, clock).remove(item_id)
    return Response(status_code=204)


@router.get("/me/watchlist")
def my_watchlist(
    user: CurrentUser, db: ReadDb, offset: Offset = 0, limit: Limit = 60
) -> WatchlistPageOut:
    """按加入时间从新到旧；只返回当前仍然可见的条目。"""
    page = Watchlist(db, user).list_ids(offset, limit)
    cards = BrowseQueries(db, user).cards_by_ids(page.items)
    return WatchlistPageOut(
        items=[ItemCardOut.model_validate(card) for card in cards], total=page.total
    )
