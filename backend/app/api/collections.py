"""合集管理接口（管理员）和合集浏览接口（用户）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from app.api.browse import ItemCardOut
from app.api.schemas import ImageOut, Out
from app.deps import (
    AdminUser,
    AppClock,
    CurrentUser,
    Db,
    ReadDb,
)
from app.library.browse import BrowseQueries
from app.library.browse_models import CollectionCover
from app.library.collections import (
    Collection,
    CollectionDraft,
    CollectionMember,
    CollectionRepository,
)

router = APIRouter(prefix="/admin", tags=["admin"])
public_router = APIRouter(tags=["collections"])


class CollectionIn(BaseModel):
    library_id: int
    title: str = Field(min_length=1, max_length=200)
    sort_title: str | None = Field(default=None, max_length=200)
    overview: str | None = None
    poster: str | None = None
    sort_mode: str = Field(default="release", pattern="^(release|custom|name)$")
    smart_filter: dict[str, Any] | None = None
    shared: bool = False


class MembersIn(BaseModel):
    item_ids: list[int] = Field(max_length=1000)


class CollectionItemIn(BaseModel):
    item_id: int


class CoverIn(BaseModel):
    # 用哪个成员的海报作封面；为空时恢复默认
    item_id: int | None = None


class CollectionOut(Out):
    id: int
    library_id: int
    title: str
    sort_title: str | None
    overview: str | None
    poster: str | None
    tmdb_collection_id: int | None
    sort_mode: str
    # 智能筛选条件以 JSON 文本原样保存和返回，由客户端解释
    smart_filter: str | None
    # automatic 一直以 0/1 返回，客户端按这个格式解析
    automatic: int
    # 管理员指定作封面的成员，没有指定时为空
    cover_item_id: int | None
    created_at: str
    updated_at: str

    @classmethod
    def of(cls, collection: Collection) -> CollectionOut:
        return cls(
            id=collection.id,
            library_id=collection.library_id,
            title=collection.title,
            sort_title=collection.sort_title,
            overview=collection.overview,
            poster=collection.poster,
            tmdb_collection_id=collection.tmdb_collection_id,
            sort_mode=collection.sort_mode,
            smart_filter=collection.smart_filter,
            automatic=int(collection.automatic),
            cover_item_id=collection.cover_item_id,
            created_at=collection.created_at,
            updated_at=collection.updated_at,
        )


class CollectionCardOut(CollectionOut):
    """用户浏览用：在合集上多带封面和当前用户看得到的成员数。"""

    # 取第一个有海报的成员的海报；没有时为空，客户端显示占位
    cover: list[ImageOut]
    # 详情页头图：合集自己的背景图，没有时用成员的
    backdrop: list[ImageOut]
    item_count: int

    @classmethod
    def with_cover(cls, collection: Collection, cover: CollectionCover) -> CollectionCardOut:
        base = CollectionOut.of(collection)
        return cls(
            **base.model_dump(),
            cover=[ImageOut.model_validate(size) for size in cover.poster],
            backdrop=[ImageOut.model_validate(size) for size in cover.backdrop],
            item_count=cover.item_count,
        )


class CollectionMemberOut(Out):
    collection_id: int
    media_item_id: int
    position: int
    # manual 一直以 0/1 返回，客户端按这个格式解析
    manual: int

    @classmethod
    def of(cls, member: CollectionMember) -> CollectionMemberOut:
        return cls(
            collection_id=member.collection_id,
            media_item_id=member.media_item_id,
            position=member.position,
            manual=int(member.manual),
        )


class AutoSyncOut(Out):
    created: int
    updated: int
    groups: int


class CollectionDetailOut(Out):
    collection: CollectionCardOut
    # 和影视墙一样的条目卡片，只含有可用文件的成员
    items: list[ItemCardOut]


def _draft(body: CollectionIn) -> CollectionDraft:
    return CollectionDraft(
        library_id=body.library_id,
        title=body.title,
        sort_title=body.sort_title,
        overview=body.overview,
        poster=body.poster,
        sort_mode=body.sort_mode,
        smart_filter=body.smart_filter,
    )


@router.get("/collections")
def admin_collections(
    _: AdminUser, db: ReadDb, clock: AppClock, library_id: int | None = None
) -> list[CollectionOut]:
    return [CollectionOut.of(c) for c in CollectionRepository(db, clock).list_all(library_id)]


@router.post("/collections", status_code=201)
def create_collection(body: CollectionIn, _: AdminUser, db: Db, clock: AppClock) -> CollectionOut:
    return CollectionOut.of(CollectionRepository(db, clock).create(_draft(body)))


@router.post("/libraries/{library_id}/collections/auto-sync")
def sync_automatic_collections(
    library_id: int, _: AdminUser, db: Db, clock: AppClock
) -> AutoSyncOut:
    """按 TMDB collection id 重建自动合集；手动成员不会被覆盖。"""
    return AutoSyncOut.model_validate(CollectionRepository(db, clock).sync_automatic(library_id))


@router.patch("/collections/{collection_id}")
def update_collection(
    collection_id: int, body: CollectionIn, _: AdminUser, db: Db, clock: AppClock
) -> CollectionOut:
    return CollectionOut.of(CollectionRepository(db, clock).update(collection_id, _draft(body)))


@router.delete("/collections/{collection_id}", status_code=204)
def delete_collection(collection_id: int, _: AdminUser, db: Db, clock: AppClock) -> Response:
    CollectionRepository(db, clock).delete(collection_id)
    return Response(status_code=204)


@router.put("/collections/{collection_id}/cover")
def set_collection_cover(
    collection_id: int, body: CoverIn, _: AdminUser, db: Db, clock: AppClock
) -> CollectionOut:
    return CollectionOut.of(CollectionRepository(db, clock).set_cover(collection_id, body.item_id))


@router.put("/collections/{collection_id}/items")
def set_collection_items(
    collection_id: int, body: MembersIn, _: AdminUser, db: Db, clock: AppClock
) -> list[CollectionMemberOut]:
    members = CollectionRepository(db, clock).set_members(collection_id, body.item_ids)
    return [CollectionMemberOut.of(member) for member in members]


@router.post("/collections/{collection_id}/items", status_code=204)
def add_collection_item(
    collection_id: int, body: CollectionItemIn, _: AdminUser, db: Db, clock: AppClock
) -> Response:
    """追加一个手动成员；已经在合集里时什么也不做。"""
    CollectionRepository(db, clock).add_member(collection_id, body.item_id)
    return Response(status_code=204)


@router.delete("/collections/{collection_id}/items/{item_id}", status_code=204)
def remove_collection_item(
    collection_id: int, item_id: int, _: AdminUser, db: Db, clock: AppClock
) -> Response:
    CollectionRepository(db, clock).remove_member(collection_id, item_id)
    return Response(status_code=204)


@public_router.get("/libraries/{library_id}/collections")
def list_collections(
    library_id: int, user: CurrentUser, db: ReadDb, clock: AppClock
) -> list[CollectionCardOut]:
    collections = CollectionRepository(db, clock).list_visible(library_id, user)
    covers = BrowseQueries(db, user).collection_covers([c.id for c in collections])
    return [CollectionCardOut.with_cover(c, covers[c.id]) for c in collections]


@public_router.get("/collections/{collection_id}")
def get_collection(
    collection_id: int, user: CurrentUser, db: ReadDb, clock: AppClock
) -> CollectionDetailOut:
    collection = CollectionRepository(db, clock).require_visible(collection_id, user)
    queries = BrowseQueries(db, user)
    cards = queries.collection_cards(collection_id)
    cover = queries.collection_covers([collection_id])[collection_id]
    return CollectionDetailOut(
        collection=CollectionCardOut.with_cover(collection, cover),
        items=[ItemCardOut.model_validate(card) for card in cards],
    )
