"""AI 找片：把一句描述变成影视墙已有的筛选条件，再按需要用语义召回补充（ADR 0030）。

模型只负责理解问题（调用 set_filters 给出条件），查询由影视墙的同一套查询执行，
所以结果一定在用户的可见范围内，模型也看不到范围外的内容。
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from app.ai.actions import PendingActions
from app.ai.loop import run_tool_loop
from app.ai.semantic import SemanticIndex
from app.ai.service import AiService
from app.ai.tools import ALL_ROLES, Tool, ToolContext, ToolError, ToolRegistry, ToolResult
from app.ai.types import AiError, Message
from app.auth.users import User
from app.clock import Clock
from app.db import Database
from app.library.browse import BrowseQueries, visible_library_ids
from app.library.browse_models import DiscoverFilters, ItemCard
from app.library.review import Page

if TYPE_CHECKING:
    from app.container import AppContainer

logger = logging.getLogger(__name__)

SET_FILTERS = "set_filters"
MAX_STEPS = 3
# 语义召回最多取这么多条，翻页只在这些结果里进行
SEMANTIC_LIMIT = 100

SYSTEM_PROMPT = """你帮用户在自己的影视库里找片。\
把用户的描述转换成筛选条件，并调用 set_filters 提交。

规则：
- 只填用户明确表达的条件，没说的留空。
- 类型（genres）只能从下面给出的列表里选；用户说的类型不在列表里，就把它的特点写进 semantic_query。
- 难以用条件表达的偏好（心情、氛围、题材、剧情特点、「像某部片那样」）写进 semantic_query：\
一两句中文，写得像影视简介，不要只写片名。
- 「没看过」「还没看的」→ watched=false；「看过的」→ watched=true。
- 「高分」「口碑好」→ min_rating=7.5；「短一点」「不太长」→ max_runtime=100。
- 年代要换算成年份：「90 年代」→ year_from=1990、year_to=1999；「近五年」按当前年份往前推。
- 用户明确说电影或剧集时才填 kind。
- 只调用一次 set_filters。"""


class AskFilters(BaseModel):
    """影视墙的筛选条件，加上一句用于语义召回的描述。没填的条件不参与筛选。"""

    kind: Literal["movie", "series"] | None = Field(default=None, description="电影或剧集")
    genres: list[str] = Field(
        default_factory=list, max_length=5, description="类型，满足任意一个即可"
    )
    year_from: int | None = Field(default=None, ge=1880, le=2100, description="起始年份")
    year_to: int | None = Field(default=None, ge=1880, le=2100, description="截止年份")
    min_rating: float | None = Field(default=None, ge=0, le=10, description="最低评分")
    max_runtime: int | None = Field(default=None, ge=1, le=1000, description="最长时长（分钟）")
    watched: bool | None = Field(default=None, description="true 只要看过的，false 只要没看过的")
    library_id: int | None = Field(default=None, description="限定资料库，编号见提示")
    semantic_query: str | None = Field(
        default=None, max_length=300, description="难以用条件表达的偏好，写得像影视简介"
    )

    def to_discover(self) -> DiscoverFilters:
        return DiscoverFilters(
            kind=self.kind,
            genres=tuple(self.genres),
            year_from=self.year_from,
            year_to=self.year_to,
            min_rating=self.min_rating,
            max_runtime=self.max_runtime,
            watched=self.watched,
            library_id=self.library_id,
        )


@dataclass
class AskScope:
    genres: list[str]
    library_ids: list[int]
    filters: AskFilters | None = None


@dataclass(frozen=True)
class AskResult:
    items: list[ItemCard]
    total: int
    # 这次结果是否用了语义召回
    semantic_used: bool


class AskSearch:
    def __init__(
        self,
        database: Database,
        clock: Clock,
        ai: AiService,
        registry: ToolRegistry,
        actions: PendingActions,
        semantic: SemanticIndex,
    ) -> None:
        self._database = database
        self._clock = clock
        self._ai = ai
        self._registry = registry
        self._actions = actions
        self._semantic = semantic
        registry.register(
            Tool(
                SET_FILTERS,
                "提交筛选条件。",
                AskFilters,
                "read",
                ALL_ROLES,
                _set_filters,
                running=lambda a: "正在理解你的问题…",
            )
        )

    def understand(self, container: AppContainer, user: User, query: str) -> AskFilters:
        """让对话模型把问句转成筛选条件。模型调用失败抛出 AiError。"""
        with self._database.read_session() as connection:
            library_ids = visible_library_ids(connection, user)
            genres = visible_genres(connection, library_ids)
            libraries = _library_lines(connection, library_ids)
        scope = AskScope(genres, library_ids)
        prompt = (
            f"当前年份：{self._clock.now().year}\n"
            f"可选类型：{'、'.join(genres) or '（无）'}\n"
            f"资料库（编号：名称）：{libraries or '（无）'}\n\n用户的描述：{query}"
        )
        for _ in run_tool_loop(
            ai=self._ai,
            registry=self._registry,
            actions=self._actions,
            context=ToolContext(user, container, scope),
            role="chat",
            messages=[Message("system", SYSTEM_PROMPT), Message("user", prompt)],
            tool_names={SET_FILTERS},
            max_steps=MAX_STEPS,
            stop_after=frozenset({SET_FILTERS}),
            max_tokens=512,
        ):
            pass
        if scope.filters is None:
            raise AiError("bad_request", "AI 没能理解这个问题")
        return scope.filters

    def run(self, user: User, filters: AskFilters, offset: int, limit: int) -> AskResult:
        """执行筛选。有语义描述且语义召回可用时按相近程度排序，否则按评分排序。"""
        discover = filters.to_discover()
        if filters.semantic_query and self._semantic.usable():
            ranked = self._ranked(user, filters.semantic_query, discover)
            if ranked:
                with self._database.read_session() as connection:
                    page = ranked[offset : offset + limit]
                    return AskResult(BrowseQueries(connection, user).cards(page), len(ranked), True)
        with self._database.read_session() as connection:
            result: Page[ItemCard] = BrowseQueries(connection, user).discover(
                discover, offset, limit
            )
        return AskResult(result.items, result.total, False)

    def _ranked(self, user: User, query: str, discover: DiscoverFilters) -> list[int]:
        try:
            with self._database.read_session(vector=True) as connection:
                browse = BrowseQueries(connection, user)
                return self._semantic.search(
                    connection,
                    query,
                    lambda ids: browse.accept(discover, ids),
                    SEMANTIC_LIMIT,
                    user_id=user.id,
                )
        except (AiError, sqlite3.OperationalError) as error:
            # 语义召回只是锦上添花，失败时退回只用筛选条件
            logger.warning("语义召回失败，退回筛选条件：%s", error)
            return []


def unknown_genres_message(unknown: list[str], available: list[str]) -> str:
    """模型选了库里没有的类型时回给它的提示。"""
    names = "、".join(unknown)
    if not available:
        return f"库里还没有可用的类型，不能按类型筛选（{names}）。请把它的特点写进 semantic_query。"
    return (
        f"类型 {names} 不在库里；可选：{'、'.join(available)}。"
        "请改用库里有的类型，或把它的特点写进 semantic_query。"
    )


def _set_filters(ctx: ToolContext, args: AskFilters) -> ToolResult:
    scope = ctx.scope
    assert isinstance(scope, AskScope)
    allowed = {g.lower(): g for g in scope.genres}
    unknown = [g for g in args.genres if g.lower() not in allowed]
    if unknown:
        raise ToolError(unknown_genres_message(unknown, scope.genres))
    if args.library_id is not None and args.library_id not in scope.library_ids:
        raise ToolError("资料库编号不对，请只使用提示里给出的编号")
    if args.year_from is not None and args.year_to is not None and args.year_from > args.year_to:
        raise ToolError("year_from 不能大于 year_to")
    scope.filters = args.model_copy(update={"genres": [allowed[g.lower()] for g in args.genres]})
    return ToolResult({"status": "ok"}, "已理解")


def visible_genres(connection: sqlite3.Connection, library_ids: list[int]) -> list[str]:
    if not library_ids:
        return []
    marks = ", ".join("?" for _ in library_ids)
    rows = connection.execute(
        f"SELECT DISTINCT g.value AS genre FROM media_item m, json_each(m.genres) g "
        f"WHERE m.library_id IN ({marks}) ORDER BY g.value",
        library_ids,
    ).fetchall()
    return [r["genre"] for r in rows]


def _library_lines(connection: sqlite3.Connection, library_ids: list[int]) -> str:
    if not library_ids:
        return ""
    marks = ", ".join("?" for _ in library_ids)
    rows = connection.execute(
        f"SELECT id, name, kind FROM library WHERE id IN ({marks}) ORDER BY sort_order, id",
        library_ids,
    ).fetchall()
    return "；".join(
        f"{r['id']}：{r['name']}（{'电影' if r['kind'] == 'movie' else '剧集'}）" for r in rows
    )
