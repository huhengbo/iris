"""AI 对话用的工具（ADR 0030）：全部只读，以提问用户的身份查询，结果限定在其可见范围内。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from app.ai.ask import AskFilters, unknown_genres_message, visible_genres
from app.ai.tools import ALL_ROLES, Tool, ToolContext, ToolError, ToolRegistry, ToolResult
from app.library.browse import BrowseQueries, visible_library_ids
from app.library.browse_models import ItemCard

SEARCH = "search_library"
DETAILS = "item_details"
PERSON = "person_works"
HISTORY = "my_history"
FOLLOW_UPS = "suggest_followups"
CHAT_TOOLS = frozenset({SEARCH, DETAILS, PERSON, HISTORY, FOLLOW_UPS})

SEARCH_LIMIT = 12
HISTORY_LIMIT = 15
PERSON_WORKS_LIMIT = 15
MAX_OVERVIEW = 300


@dataclass
class ChatScope:
    # suggest_followups 提交的追问
    suggestions: list[str] = field(default_factory=list)


class SearchParams(AskFilters):
    limit: int = Field(default=SEARCH_LIMIT, ge=1, le=20, description="最多返回几部")


class DetailsParams(BaseModel):
    item_id: int = Field(description="条目编号，来自 search_library 等工具的结果")


class PersonParams(BaseModel):
    name: str = Field(min_length=1, max_length=50, description="演员或导演的姓名")


class HistoryParams(BaseModel):
    kind: Literal["recent", "watched"] = Field(
        description="recent 最近播放过（含看到哪一集）；watched 已经看完的"
    )


class FollowUpParams(BaseModel):
    questions: list[str] = Field(
        min_length=1, max_length=3, description="2 到 3 个用户可能接着问的短问题，每个不超过 20 字"
    )


def register(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            SEARCH,
            "按条件在用户的影视库里找片。类型、年份、评分、时长、是否看过都是筛选条件；"
            "难以用条件表达的偏好写进 semantic_query。只会返回库里有的作品。",
            SearchParams,
            "read",
            ALL_ROLES,
            _search,
            running=lambda a: "正在搜索资料库…",
        )
    )
    registry.register(
        Tool(
            DETAILS,
            "查看一部作品的详细信息：简介、演职员、季数、是否看过。",
            DetailsParams,
            "read",
            ALL_ROLES,
            _details,
            running=lambda a: "正在查看详情…",
        )
    )
    registry.register(
        Tool(
            PERSON,
            "查一位演员或导演在这个影视库里的作品。",
            PersonParams,
            "read",
            ALL_ROLES,
            _person,
            running=lambda a: f"正在查找「{a.name}」的作品…",
        )
    )
    registry.register(
        Tool(
            HISTORY,
            "查看当前用户自己的观影记录：最近播放的，或已经看完的。",
            HistoryParams,
            "read",
            ALL_ROLES,
            _history,
            running=lambda a: "正在查看观影记录…",
        )
    )
    registry.register(
        Tool(
            FOLLOW_UPS,
            "回答结束后给出用户可能接着问的问题。",
            FollowUpParams,
            "read",
            ALL_ROLES,
            _follow_ups,
            running=lambda a: "",
        )
    )


def _search(ctx: ToolContext, args: SearchParams) -> ToolResult:
    container = ctx.container
    with container.database.read_session() as connection:
        library_ids = visible_library_ids(connection, ctx.user)
        genres = visible_genres(connection, library_ids)
    allowed = {g.lower(): g for g in genres}
    unknown = [g for g in args.genres if g.lower() not in allowed]
    if unknown:
        raise ToolError(unknown_genres_message(unknown, genres))
    if args.library_id is not None and args.library_id not in library_ids:
        raise ToolError("没有这个资料库")
    filters = args.model_copy(update={"genres": [allowed[g.lower()] for g in args.genres]})
    result = container.ask.run(ctx.user, filters, 0, args.limit)
    return ToolResult(
        {"total": result.total, "items": [_card(c) for c in result.items]},
        f"找到 {result.total} 部" if result.total else "没有找到符合条件的",
        tuple(c.id for c in result.items),
    )


def _details(ctx: ToolContext, args: DetailsParams) -> ToolResult:
    with ctx.container.database.read_session() as connection:
        # 范围外的条目抛出 NotFound，按不存在处理
        detail = BrowseQueries(connection, ctx.user).item(args.item_id)
    card = detail.card
    content = {
        **_card(card),
        "en_title": detail.en_title,
        "release_date": detail.release_date,
        "overview": (detail.overview or "")[:MAX_OVERVIEW],
        "directors": [c.name for c in detail.directors[:3]],
        "cast": [c.name for c in detail.cast[:8]],
        "seasons": len(detail.seasons),
        "next_episode": (
            f"第 {detail.next_episode.season_number} 季第 {detail.next_episode.number} 集"
            if detail.next_episode
            else None
        ),
    }
    return ToolResult(content, f"查看「{card.title}」", (card.id,))


def _person(ctx: ToolContext, args: PersonParams) -> ToolResult:
    with ctx.container.database.read_session() as connection:
        browse = BrowseQueries(connection, ctx.user)
        people = browse.search_all(args.name.strip(), 0, 1).people[:3]
        found = []
        item_ids: list[int] = []
        for person in people:
            detail = browse.person(person.id)
            works = detail.works[:PERSON_WORKS_LIMIT]
            found.append(
                {
                    "name": detail.name,
                    "works": [
                        {**_card(w.card), "roles": list(w.roles), "character": w.character}
                        for w in works
                    ],
                }
            )
            item_ids.extend(w.card.id for w in works)
    summary = f"找到 {len(item_ids)} 部作品" if item_ids else f"库里没有「{args.name}」的作品"
    return ToolResult({"people": found}, summary, tuple(dict.fromkeys(item_ids)))


def _history(ctx: ToolContext, args: HistoryParams) -> ToolResult:
    with ctx.container.database.read_session() as connection:
        browse = BrowseQueries(connection, ctx.user)
        if args.kind == "recent":
            recent = browse.recent_plays()[:HISTORY_LIMIT]
            items = [
                {
                    **_card(r.item),
                    "last_played_at": r.played_at,
                    "episode": (
                        f"第 {r.episode.season_number} 季第 {r.episode.number} 集"
                        if r.episode
                        else None
                    ),
                    "progress_percent": (
                        round(100 * r.progress.position / r.progress.duration)
                        if r.progress and r.progress.duration
                        else None
                    ),
                }
                for r in recent
            ]
            ids = tuple(r.item.id for r in recent)
        else:
            cards = browse.watched_items(HISTORY_LIMIT)
            items = [_card(c) for c in cards]
            ids = tuple(c.id for c in cards)
    label = "最近播放" if args.kind == "recent" else "已看完"
    return ToolResult({"items": items}, f"{label}的有 {len(items)} 部", ids)


def _follow_ups(ctx: ToolContext, args: FollowUpParams) -> ToolResult:
    scope = ctx.scope
    assert isinstance(scope, ChatScope)
    scope.suggestions = [q.strip() for q in args.questions if q.strip()][:3]
    return ToolResult({"status": "ok"}, "")


def _card(card: ItemCard) -> dict[str, object]:
    return {
        "id": card.id,
        "title": card.title,
        "original_title": card.original_title,
        "kind": "电影" if card.kind == "movie" else "剧集",
        "year": card.year,
        "rating": card.rating,
        "genres": card.genres,
        "runtime": card.runtime,
        "watched": card.watched,
    }
