"""更多类似接口：详情页底部的一行，按当前用户的可见范围过滤（ADR 0009、0026）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.browse import ItemCardOut
from app.deps import CurrentUser, ReadDb
from app.library.related import DEFAULT_RELATED_LIMIT, MAX_RELATED_LIMIT, RelatedQueries

router = APIRouter(tags=["browse"])


@router.get("/items/{item_id}/related")
def related_items(
    item_id: int,
    user: CurrentUser,
    db: ReadDb,
    limit: Annotated[int, Query(ge=1, le=MAX_RELATED_LIMIT)] = DEFAULT_RELATED_LIMIT,
) -> list[ItemCardOut]:
    """同类型的条目，类型重合多的在前；条目不可见或不存在时 404。"""
    return [
        ItemCardOut.model_validate(card)
        for card in RelatedQueries(db, user).related(item_id, limit)
    ]
