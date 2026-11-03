"""面向所有用户的 AI 接口：AI 找片（ADR 0030）。没有配置对话模型时返回 409，客户端据此隐藏入口。"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.ai.ask import AskFilters
from app.ai.types import AiError
from app.api.browse import ItemCardOut
from app.api.schemas import Out
from app.deps import Ask, Container, CurrentUser
from app.errors import AppError

router = APIRouter(prefix="/ai", tags=["ai"])

MAX_QUERY_LENGTH = 300
MAX_PAGE_SIZE = 60
DEFAULT_PAGE_SIZE = 30


class AskSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=MAX_QUERY_LENGTH)
    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)


class AskQueryRequest(BaseModel):
    """用（可能被用户改过的）筛选条件直接查询，不再请求模型。"""

    filters: AskFilters
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)


class AskResultOut(Out):
    items: list[ItemCardOut]
    total: int
    # 这次结果是否用了语义召回（按相近程度排序）；否则按评分排序
    semantic_used: bool


class AskSearchOut(AskResultOut):
    # AI 对问题的理解，客户端显示成可以修改的筛选条件
    understood: AskFilters


@router.post("/ask-search")
def ask_search(
    body: AskSearchRequest, user: CurrentUser, ask: Ask, container: Container
) -> AskSearchOut:
    """把一句描述转成筛选条件并返回第一页结果。模型不可用时返回 502，客户端退回普通搜索。"""
    _require_available(container)
    try:
        filters = ask.understand(container, user, body.query.strip())
    except AiError as error:
        raise _unavailable(error) from error
    result = ask.run(user, filters, 0, body.limit)
    return AskSearchOut(
        understood=filters,
        items=[ItemCardOut.model_validate(i) for i in result.items],
        total=result.total,
        semantic_used=result.semantic_used,
    )


@router.post("/ask-search/query")
def ask_search_query(
    body: AskQueryRequest, user: CurrentUser, ask: Ask, container: Container
) -> AskResultOut:
    """修改筛选条件后重新查询，或翻页。不调用对话模型。"""
    _require_available(container)
    result = ask.run(user, body.filters, body.offset, body.limit)
    return AskResultOut(
        items=[ItemCardOut.model_validate(i) for i in result.items],
        total=result.total,
        semantic_used=result.semantic_used,
    )


def _require_available(container: Container) -> None:
    if not container.ai.availability().ask_search:
        raise AppError(409, "ai_not_configured", "管理员还没有配置 AI 助手")


def _unavailable(error: AiError) -> AppError:
    return AppError(502, "ai_unavailable", f"AI 暂不可用：{error.message}")
