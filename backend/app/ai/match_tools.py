"""匹配建议用的工具：通过资料库选用的元数据插件搜索、核对条目，并提交结果（ADR 0030）。

只读，且只给后台任务用，不对话开放。每次分析的范围（哪个资料库、哪个作品、见过哪些条目）
放在 MatchScope 里，通过 ToolContext.scope 传入，模型不能越出这个范围。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from iris_plugin_sdk import MetadataUnavailable
from pydantic import BaseModel, Field

from app.ai.tools import ADMIN_ONLY, Tool, ToolContext, ToolError, ToolRegistry, ToolResult
from app.library.metadata_providers import BoundProvider
from app.priority import Priority

SEARCH = "metadata_search"
DETAILS = "metadata_details"
SEASON = "metadata_season"
SUBMIT = "submit_match"
MATCH_TOOLS = frozenset({SEARCH, DETAILS, SEASON, SUBMIT})

MAX_SEARCH_RESULTS = 8
MAX_OVERVIEW = 80


@dataclass(frozen=True)
class SeenCandidate:
    provider_id: str
    remote_id: str
    title: str
    original_title: str | None
    year: int | None
    en_title: str | None = None


@dataclass
class MatchScope:
    library_id: int
    kind: Literal["movie", "series"]
    # 本次分析里出现过的候选（规则候选、搜索结果、详情），提交结果时只能从中选择
    seen: dict[str, SeenCandidate] = field(default_factory=dict)
    # 管理员撤销过的候选，不能再次提交
    banned: frozenset[str] = frozenset()
    # 提交的结果
    submitted: SubmitMatchParams | None = None
    # 元数据插件暂时不可用（未就绪、网络错误）：这次分析不算数，稍后重试
    outage: str | None = None


class SearchParams(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=100,
        description="搜索关键词，可以是中文名、原名或英文名",
    )


class DetailsParams(BaseModel):
    remote_id: str = Field(min_length=1, max_length=64, description="搜索结果里的 remote_id")


class SeasonParams(BaseModel):
    remote_id: str = Field(min_length=1, max_length=64, description="剧集的 remote_id")
    season: int = Field(ge=0, le=100, description="季号，0 是特别篇")


class SubmitMatchParams(BaseModel):
    remote_id: str | None = Field(
        default=None,
        max_length=64,
        description="选定条目的 remote_id；没有把握时留空",
    )
    confidence: float = Field(ge=0, le=1, description="把握程度，0 到 1；没选定条目时填 0")
    reason: str = Field(min_length=1, max_length=300, description="一句话说明依据")


def register(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            SEARCH,
            "在元数据来源里按关键词搜索电影或剧集，返回候选条目（remote_id、标题、原名、年份、简介）。",
            SearchParams,
            "read",
            ADMIN_ONLY,
            _search,
            running=lambda a: f"正在搜索「{a.query}」…",
        )
    )
    registry.register(
        Tool(
            DETAILS,
            "查看一个候选条目的详细信息：标题、原名、上映日期、类型、主演、导演。用来核对年份和人物。",
            DetailsParams,
            "read",
            ADMIN_ONLY,
            _details,
            running=lambda a: "正在查看条目详情…",
        )
    )
    registry.register(
        Tool(
            SEASON,
            "查看剧集某一季的集数和首播日期，用来核对文件里的季号、集号是否存在。",
            SeasonParams,
            "read",
            ADMIN_ONLY,
            _season,
            running=lambda a: f"正在核对第 {a.season} 季…",
        )
    )
    registry.register(
        Tool(
            SUBMIT,
            (
                "提交最终结果并结束分析。remote_id 必须来自搜索或详情的结果；"
                "没有把握就留空、confidence 填 0。"
            ),
            SubmitMatchParams,
            "read",
            ADMIN_ONLY,
            _submit,
            running=lambda a: "正在记录结果…",
        )
    )


def _scope(ctx: ToolContext) -> MatchScope:
    scope = ctx.scope
    if not isinstance(scope, MatchScope):
        raise ToolError("这个工具只能在匹配分析里使用")
    return scope


def _provider(ctx: ToolContext, scope: MatchScope) -> BoundProvider:
    return ctx.container.metadata.providers.for_library(scope.library_id)


def _guard[T](scope: MatchScope, call: Callable[[], T]) -> T:
    """元数据插件暂时不可用时记下来并向模型报告，不让模型把它当成「没有结果」。"""
    try:
        return call()
    except MetadataUnavailable as exc:
        scope.outage = exc.message
        raise


def _search(ctx: ToolContext, args: SearchParams) -> ToolResult:
    scope = _scope(ctx)
    provider = _provider(ctx, scope)
    results = _guard(
        scope, lambda: provider.search(scope.kind, args.query.strip(), Priority.BACKGROUND)
    )[:MAX_SEARCH_RESULTS]
    items = []
    for r in results:
        scope.seen[r.remote_id] = SeenCandidate(
            r.provider_id, r.remote_id, r.title, r.original_title, r.year
        )
        items.append(
            {
                "remote_id": r.remote_id,
                "title": r.title,
                "original_title": r.original_title,
                "year": r.year,
                "overview": (r.overview or "")[:MAX_OVERVIEW],
            }
        )
    return ToolResult({"results": items}, f"搜索「{args.query}」：{len(items)} 个结果")


def _details(ctx: ToolContext, args: DetailsParams) -> ToolResult:
    scope = _scope(ctx)
    provider = _provider(ctx, scope)
    record = _guard(
        scope, lambda: provider.details(scope.kind, args.remote_id, Priority.BACKGROUND)
    )
    scope.seen[record.remote_id] = SeenCandidate(
        record.provider_id,
        record.remote_id,
        record.title,
        record.original_title,
        record.year,
        record.en_title,
    )
    content = {
        "remote_id": record.remote_id,
        "title": record.title,
        "original_title": record.original_title,
        "year": record.year,
        "release_date": record.release_date,
        "genres": list(record.genres),
        "directors": [c.name for c in record.directors[:3]],
        "cast": [c.name for c in record.cast[:5]],
        "overview": (record.overview or "")[:200],
    }
    return ToolResult(content, f"查看「{record.title}」的详情")


def _season(ctx: ToolContext, args: SeasonParams) -> ToolResult:
    scope = _scope(ctx)
    if scope.kind != "series":
        raise ToolError("这是电影资料库，没有季")
    provider = _provider(ctx, scope)
    season = _guard(
        scope, lambda: provider.season(args.remote_id, args.season, Priority.BACKGROUND)
    )
    numbers = [e.number for e in season.episodes]
    content = {
        "season": season.number,
        "name": season.name,
        "air_date": season.air_date,
        "episode_count": len(numbers),
        "last_episode": max(numbers) if numbers else 0,
    }
    return ToolResult(content, f"第 {args.season} 季共 {len(numbers)} 集")


def _submit(ctx: ToolContext, args: SubmitMatchParams) -> ToolResult:
    scope = _scope(ctx)
    if args.remote_id is not None:
        if args.remote_id in scope.banned:
            raise ToolError("这个条目已被管理员撤销过，不能再推荐")
        if args.remote_id not in scope.seen:
            raise ToolError("这个 remote_id 没有在搜索或详情结果中出现过，请先搜索")
    scope.submitted = args
    return ToolResult({"status": "recorded"}, "已记录结果")
