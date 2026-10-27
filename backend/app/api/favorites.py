"""收藏接口：电影、剧集、单集和人物的收藏与取消，以及「我的收藏」列表。"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response

from app.api.browse import ItemCardOut, Limit, Offset, RecentPlayOut, SearchPersonOut
from app.api.schemas import Out
from app.deps import AppClock, CurrentUser, Db, ReadDb
from app.errors import NotFound
from app.library.browse import BrowseQueries
from app.library.favorites import FavoriteKind, Favorites

router = APIRouter(tags=["favorites"])

FavoriteItemOut = ItemCardOut | RecentPlayOut | SearchPersonOut


class FavoritePageOut(Out):
    items: list[FavoriteItemOut]
    total: int


def _favorite(
    db: Db,
    user: CurrentUser,
    clock: AppClock,
    kind: FavoriteKind,
    target_id: int,
    value: bool,
) -> Response:
    favorites = Favorites(db, user, clock)
    if value:
        favorites.add(kind, target_id)
    else:
        favorites.remove(kind, target_id)
    return Response(status_code=204)


def _item_favorite(
    item_id: int, user: CurrentUser, db: Db, clock: AppClock, *, value: bool
) -> Response:
    """电影和剧集共用 /items/{id}/favorite；按条目的实际类型判断，季没有这个接口。"""
    favorites = Favorites(db, user, clock)
    item_kind = favorites.item_kind(item_id)
    if item_kind is None:
        raise NotFound("条目不存在")
    kind: FavoriteKind = "series" if item_kind == "series" else "movie"
    return _favorite(db, user, clock, kind, item_id, value)


@router.put("/items/{item_id}/favorite", status_code=204)
def add_item_favorite(item_id: int, user: CurrentUser, db: Db, clock: AppClock) -> Response:
    return _item_favorite(item_id, user, db, clock, value=True)


@router.delete("/items/{item_id}/favorite", status_code=204)
def remove_item_favorite(item_id: int, user: CurrentUser, db: Db, clock: AppClock) -> Response:
    return _item_favorite(item_id, user, db, clock, value=False)


@router.put("/episodes/{episode_id}/favorite", status_code=204)
def add_episode_favorite(episode_id: int, user: CurrentUser, db: Db, clock: AppClock) -> Response:
    return _favorite(db, user, clock, "episode", episode_id, True)


@router.delete("/episodes/{episode_id}/favorite", status_code=204)
def remove_episode_favorite(
    episode_id: int, user: CurrentUser, db: Db, clock: AppClock
) -> Response:
    return _favorite(db, user, clock, "episode", episode_id, False)


@router.put("/people/{person_id}/favorite", status_code=204)
def add_person_favorite(person_id: int, user: CurrentUser, db: Db, clock: AppClock) -> Response:
    return _favorite(db, user, clock, "person", person_id, True)


@router.delete("/people/{person_id}/favorite", status_code=204)
def remove_person_favorite(person_id: int, user: CurrentUser, db: Db, clock: AppClock) -> Response:
    return _favorite(db, user, clock, "person", person_id, False)


ListKind = Annotated[Literal["movie", "series", "episode", "person"], Query(description="收藏类型")]


@router.get("/me/favorites")
def my_favorites(
    user: CurrentUser, db: ReadDb, kind: ListKind, offset: Offset = 0, limit: Limit = 60
) -> FavoritePageOut:
    """按收藏时间从新到旧；只返回可见范围内的目标。"""
    favorites = Favorites(db, user)
    page = favorites.list_ids(kind, offset, limit)
    browse = BrowseQueries(db, user)
    items: list[FavoriteItemOut]
    if kind in ("movie", "series"):
        items = [ItemCardOut.model_validate(card) for card in browse.cards_by_ids(page.items)]
    elif kind == "episode":
        # 和首页「继续观看」共用 RecentPlayOut：所属剧集、单集、剧照和进度一次给全
        cards = browse.episode_play_cards(
            page.items, favorites.created_at_for("episode", page.items)
        )
        items = [RecentPlayOut.model_validate(card) for card in cards]
    else:
        items = [
            SearchPersonOut.model_validate(person) for person in browse.people_by_ids(page.items)
        ]
    return FavoritePageOut(items=items, total=page.total)
