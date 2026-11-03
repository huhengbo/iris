"""匹配建议：后台任务模型为未匹配的作品找出首选候选；达到条件时可自动写入匹配（ADR 0030）。

模型只给建议。自动写入要同时满足置信度阈值和硬校验（年份、季集数、片名），任何一项
不满足都只留作建议，由管理员在待处理页采用。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from iris_plugin_sdk import MetadataNotFound, MetadataUnavailable

from app.ai import match_tools
from app.ai.actions import PendingActions
from app.ai.loop import ToolEnd, ToolStart, run_tool_loop
from app.ai.match_tools import MatchScope, SeenCandidate
from app.ai.service import AiService
from app.ai.settings import AiSettings
from app.ai.tools import SYSTEM_USER, ToolContext, ToolRegistry
from app.ai.types import AiError, Message
from app.clock import Clock, to_iso
from app.db import Database
from app.errors import AppError, NotFound
from app.library.catalog import CatalogRepository
from app.library.curation import Curation
from app.library.matcher import comparable
from app.library.metadata_providers import MetadataProviders
from app.library.recognize import LibraryKind, recognize
from app.ops.alerts import AlertRepository
from app.priority import Priority

if TYPE_CHECKING:
    from app.container import AppContainer

logger = logging.getLogger(__name__)

TICK_SECONDS = 30.0
MAX_TOOL_CALLS = 6
MAX_FILES_IN_PROMPT = 10
# 每轮最多分析的作品数；其余留到下一轮，避免一次占用模型和元数据来源太久
BATCH_SIZE = 5
# 模型调用失败后的等待：Key 失效不会自己好，等久一点
RETRY_AFTER = timedelta(minutes=5)
RETRY_AFTER_AUTH = timedelta(minutes=30)
ALERT_AFTER_FAILURES = 3
ALERT_FINGERPRINT = "ai:task-model"

SYSTEM_PROMPT = """你是影视库整理助手。网盘里的电影、剧集目录和文件名大多没有统一规范，\
规则匹配没能认出其中一部分。你的任务：找出它对应的作品在元数据来源里的条目。

做法：
1. 先从目录名和文件名里提炼真正的片名。文件名常带字幕组、分辨率、编码、来源、语言标记，\
还有中英文混排、拼音缩写、季集写法不一。
2. 用 metadata_search 搜索；搜不到就换关键词（原名、英文名、去掉季集标记和修饰词）。
3. 用年份、导演、主演、季数和集数核对候选，必要时用 metadata_details、metadata_season。
4. 最后必须调用 submit_match 提交结果。remote_id 只能来自搜索或详情结果，不能编造；\
没有把握就留空、confidence 填 0。confidence 表示你有多大把握这就是文件对应的作品，\
有任何一个核对项对不上就要降低它。

工具调用最多 6 次（含 submit_match），请留出提交的次数。reason 用一句中文说明依据。"""

NO_CONCLUSION = "AI 没有给出结论"


@dataclass(frozen=True)
class Step:
    tool: str
    label: str
    summary: str
    ok: bool


@dataclass(frozen=True)
class Suggestion:
    id: int
    work_id: int
    provider_id: str | None
    remote_id: str | None
    title: str | None
    original_title: str | None
    year: int | None
    confidence: float
    reason: str
    steps: tuple[Step, ...]
    model: str
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class WorkFacts:
    id: int
    library_id: int
    kind: LibraryKind
    library_name: str
    path: str
    title: str | None
    alt_title: str | None
    year: int | None
    reason: str | None
    rule_candidates: tuple[SeenCandidate, ...]
    # 前几个视频的相对路径，以及视频总数
    files: tuple[str, ...]
    file_count: int
    folder_id: int
    root_name: str


class SuggestionService:
    def __init__(
        self,
        database: Database,
        clock: Clock,
        ai: AiService,
        registry: ToolRegistry,
        actions: PendingActions,
        settings: AiSettings,
        providers: MetadataProviders,
        curation: Curation,
    ) -> None:
        self._database = database
        self._clock = clock
        self._ai = ai
        self._registry = registry
        self._actions = actions
        self._settings = settings
        self._providers = providers
        self._curation = curation
        match_tools.register(registry)
        self._failures = 0
        self._retry_at = clock.now()

    # 查询 -----------------------------------------------------------------

    def for_works(
        self, connection: sqlite3.Connection, work_ids: Sequence[int]
    ) -> dict[int, Suggestion]:
        """每个作品最新的一条建议。"""
        if not work_ids:
            return {}
        marks = ", ".join("?" for _ in work_ids)
        rows = connection.execute(
            f"SELECT * FROM work_suggestion WHERE id IN ("
            f"SELECT MAX(id) FROM work_suggestion WHERE status != 'retired' "
            f"AND work_id IN ({marks}) GROUP BY work_id)",
            tuple(work_ids),
        ).fetchall()
        return {r["work_id"]: _suggestion(r) for r in rows}

    def get(self, work_id: int) -> Suggestion:
        with self._database.read_session() as connection:
            found = self.for_works(connection, [work_id]).get(work_id)
        if found is None:
            raise NotFound("这个作品没有匹配建议")
        return found

    # 管理员操作 -----------------------------------------------------------

    def adopt(self, work_id: int) -> int:
        """采用建议：等同于手动指定。返回条目 ID。"""
        suggestion = self.get(work_id)
        if suggestion.status != "pending":
            raise AppError(409, "suggestion_not_pending", "这条建议已经处理过了")
        if suggestion.remote_id is None or suggestion.provider_id is None:
            raise AppError(409, "suggestion_empty", "AI 没有给出候选，无法采用")
        item_id = self._curation.assign_work(work_id, suggestion.remote_id, suggestion.provider_id)
        self._set_status(suggestion.id, "adopted")
        return item_id

    def ignore(self, work_id: int) -> None:
        suggestion = self.get(work_id)
        if suggestion.status != "pending":
            raise AppError(409, "suggestion_not_pending", "这条建议已经处理过了")
        self._set_status(suggestion.id, "ignored")

    def reanalyze(self, work_id: int) -> None:
        """丢弃现有建议（保留撤销记录），下一轮后台任务重新分析。"""
        with self._database.session() as connection:
            row = connection.execute(
                "SELECT state, manual FROM work WHERE id = ?", (work_id,)
            ).fetchone()
            if row is None:
                raise NotFound("作品不存在")
            if row["state"] != "unmatched" or row["manual"]:
                raise AppError(409, "work_not_unmatched", "只能重新分析未匹配的作品")
            connection.execute(
                "DELETE FROM work_suggestion WHERE work_id = ? "
                "AND status NOT IN ('reverted', 'retired')",
                (work_id,),
            )
            connection.execute(
                "UPDATE work_suggestion SET status = 'retired' WHERE work_id = ? "
                "AND status = 'reverted'",
                (work_id,),
            )

    def revert(self, work_id: int) -> None:
        """撤销 AI 自动匹配：作品回到待处理，且不再自动匹配同一个候选。"""
        suggestion = self.get(work_id)
        if suggestion.status != "auto_matched":
            raise AppError(409, "work_not_auto_matched", "这个作品不是 AI 自动匹配的")
        self._curation.unassign_auto_match(work_id)
        self._set_status(suggestion.id, "reverted")

    # 后台任务 -------------------------------------------------------------

    def enabled(self) -> bool:
        return self._settings.get().suggest_matches and self._ai.roles()["task"] is not None

    def run_pending(self, container: AppContainer, limit: int = BATCH_SIZE) -> int:
        """分析一批还没有建议的未匹配作品，返回分析的数量。模型调用失败时停止本轮并退避。"""
        if not self.enabled() or self._clock.now() < self._retry_at:
            return 0
        done = 0
        for work_id in self._queue(limit):
            try:
                if self.analyze(container, work_id) is None:
                    break  # 元数据来源暂时不可用，等下一轮
            except AiError as error:
                self._failed(error)
                break
            self._succeeded()
            done += 1
        return done

    def analyze(self, container: AppContainer, work_id: int) -> Suggestion | None:
        """分析一个作品并保存建议；元数据来源暂时不可用时不保存，返回 None。

        模型调用失败（AiError）向上抛出。
        """
        facts = self._facts(work_id)
        if facts is None:
            return None
        settings = self._settings.get()
        scope = MatchScope(
            facts.library_id,
            facts.kind,
            seen={c.remote_id: c for c in facts.rule_candidates},
            banned=self._banned(work_id),
        )
        provider = self._providers.for_library(facts.library_id)
        if provider.unavailable_reason() is not None:
            return None
        messages = [Message("system", SYSTEM_PROMPT), Message("user", _describe(facts))]
        steps: list[Step] = []
        labels: dict[str, str] = {}
        for event in run_tool_loop(
            ai=self._ai,
            registry=self._registry,
            actions=self._actions,
            context=ToolContext(SYSTEM_USER, container, scope),
            role="task",
            messages=messages,
            tool_names=set(match_tools.MATCH_TOOLS),
            max_tool_calls=MAX_TOOL_CALLS,
            stop_after=frozenset({match_tools.SUBMIT}),
            max_tokens=1024,
        ):
            if isinstance(event, ToolStart):
                labels[event.call_id] = event.label
            elif isinstance(event, ToolEnd) and event.name != match_tools.SUBMIT:
                steps.append(
                    Step(event.name, labels.get(event.call_id, ""), event.summary, event.ok)
                )
        if scope.outage is not None:
            logger.info("匹配建议暂停：%s", scope.outage)
            return None
        model = self._ai.roles()["task"]
        suggestion_id = self._save(work_id, scope, steps, model.model if model else "")
        suggestion = self.get(work_id)
        assert suggestion.id == suggestion_id
        if settings.auto_match:
            suggestion = self._maybe_auto_match(
                facts, suggestion, scope, settings.auto_match_threshold
            )
        return suggestion

    # 内部 -----------------------------------------------------------------

    def _queue(self, limit: int) -> list[int]:
        with self._database.read_session() as connection:
            rows = connection.execute(
                "SELECT w.id FROM work w WHERE w.state = 'unmatched' AND w.manual = 0 "
                "AND EXISTS (SELECT 1 FROM media_file c WHERE c.work_id = w.id "
                "AND c.kind = 'video' AND c.state = 'active') "
                "AND NOT EXISTS (SELECT 1 FROM work_suggestion s WHERE s.work_id = w.id "
                "AND s.status != 'retired') "
                "ORDER BY w.id LIMIT ?",
                (limit,),
            ).fetchall()
        return [r["id"] for r in rows]

    def _facts(self, work_id: int) -> WorkFacts | None:
        with self._database.read_session() as connection:
            row = connection.execute(
                "SELECT w.id, w.path, w.title, w.alt_title, w.year, w.reason, w.candidates, "
                "w.folder_id, f.library_id, f.path AS folder_path, l.kind, l.name AS library_name "
                "FROM work w JOIN library_folder f ON f.id = w.folder_id "
                "JOIN library l ON l.id = f.library_id "
                "WHERE w.id = ? AND w.state = 'unmatched' AND w.manual = 0",
                (work_id,),
            ).fetchone()
            if row is None:
                return None
            files = [
                r["rel_path"]
                for r in connection.execute(
                    "SELECT rel_path FROM media_file WHERE work_id = ? AND kind = 'video' "
                    "AND state = 'active' ORDER BY rel_path LIMIT ?",
                    (work_id, MAX_FILES_IN_PROMPT),
                )
            ]
            count = connection.execute(
                "SELECT COUNT(*) FROM media_file WHERE work_id = ? AND kind = 'video' "
                "AND state = 'active'",
                (work_id,),
            ).fetchone()[0]
            root_name = (
                CatalogRepository(connection, self._clock).folder(row["folder_id"]).root_name
            )
        return WorkFacts(
            id=row["id"],
            library_id=row["library_id"],
            kind=row["kind"],
            library_name=row["library_name"],
            path=row["path"],
            title=row["title"],
            alt_title=row["alt_title"],
            year=row["year"],
            reason=row["reason"],
            rule_candidates=_rule_candidates(row["candidates"]),
            files=tuple(files),
            file_count=count,
            folder_id=row["folder_id"],
            root_name=root_name,
        )

    def _banned(self, work_id: int) -> frozenset[str]:
        with self._database.read_session() as connection:
            rows = connection.execute(
                "SELECT remote_id FROM work_suggestion WHERE work_id = ? "
                "AND status IN ('reverted', 'retired') "
                "AND remote_id IS NOT NULL",
                (work_id,),
            ).fetchall()
        return frozenset(r["remote_id"] for r in rows)

    def _save(self, work_id: int, scope: MatchScope, steps: list[Step], model: str) -> int:
        submitted = scope.submitted
        chosen = scope.seen.get(submitted.remote_id) if submitted and submitted.remote_id else None
        confidence = submitted.confidence if submitted and chosen else 0.0
        reason = submitted.reason if submitted else NO_CONCLUSION
        now = to_iso(self._clock.now())
        with self._database.session() as connection:
            cursor = connection.execute(
                "INSERT INTO work_suggestion (work_id, provider_id, remote_id, title, "
                "original_title, year, confidence, reason, steps, model, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    work_id,
                    chosen.provider_id if chosen else None,
                    chosen.remote_id if chosen else None,
                    chosen.title if chosen else None,
                    chosen.original_title if chosen else None,
                    chosen.year if chosen else None,
                    confidence,
                    reason,
                    json.dumps([s.__dict__ for s in steps], ensure_ascii=False),
                    model,
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid or 0)

    def _set_status(self, suggestion_id: int, status: str) -> None:
        with self._database.session() as connection:
            connection.execute(
                "UPDATE work_suggestion SET status = ?, updated_at = ? WHERE id = ?",
                (status, to_iso(self._clock.now()), suggestion_id),
            )

    def _maybe_auto_match(
        self, facts: WorkFacts, suggestion: Suggestion, scope: MatchScope, threshold: float
    ) -> Suggestion:
        chosen = scope.seen.get(suggestion.remote_id or "")
        if chosen is None or suggestion.status != "pending":
            return suggestion
        problem = self._check(facts, suggestion.confidence, chosen, scope.banned, threshold)
        if problem is not None:
            logger.info("作品 %s 的建议不自动匹配：%s", facts.id, problem)
            return suggestion
        try:
            item_id = self._curation.auto_assign_work(
                facts.id, chosen.remote_id, chosen.provider_id, Priority.BACKGROUND
            )
        except (MetadataUnavailable, AppError) as exc:
            logger.info("作品 %s 自动匹配失败：%s", facts.id, exc)
            return suggestion
        if item_id is None:
            return suggestion
        self._set_status(suggestion.id, "auto_matched")
        return self.get(facts.id)

    def _check(
        self,
        facts: WorkFacts,
        confidence: float,
        chosen: SeenCandidate,
        banned: frozenset[str],
        threshold: float,
    ) -> str | None:
        """硬校验：返回不能自动匹配的原因，通过时为 None。"""
        if chosen.remote_id in banned:
            return "管理员撤销过这个候选"
        if confidence < threshold:
            return f"置信度 {confidence:.2f} 低于阈值 {threshold:.2f}"
        if facts.year is None or chosen.year is None:
            return "缺少年份，无法核对"
        if abs(facts.year - chosen.year) > 1:
            return f"年份相差超过 1（文件 {facts.year}，候选 {chosen.year}）"
        names = {comparable(n) for n in (facts.title, facts.alt_title) if n}
        titles = {
            comparable(t) for t in (chosen.title, chosen.original_title, chosen.en_title) if t
        }
        if not names & titles:
            return "片名与候选的标题、原名都不相符"
        if facts.kind == "series":
            return self._check_episodes(facts, chosen)
        return None

    def _check_episodes(self, facts: WorkFacts, chosen: SeenCandidate) -> str | None:
        """文件里识别出的季、集必须真实存在于候选里。"""
        wanted: dict[int, set[int]] = {}
        with self._database.read_session() as connection:
            paths = [
                r["rel_path"]
                for r in connection.execute(
                    "SELECT rel_path FROM media_file WHERE work_id = ? AND kind = 'video' "
                    "AND state = 'active'",
                    (facts.id,),
                )
            ]
        for path in paths:
            recognized = recognize("series", path, facts.root_name)
            if recognized.season is not None and recognized.episode is not None:
                wanted.setdefault(recognized.season, set()).add(recognized.episode)
        provider = self._providers.for_library(facts.library_id)
        for season, episodes in sorted(wanted.items()):
            try:
                info = provider.season(chosen.remote_id, season, Priority.BACKGROUND)
            except MetadataNotFound:
                return f"候选没有第 {season} 季"
            except (MetadataUnavailable, AppError):
                return "暂时无法核对季集数"
            have = {e.number for e in info.episodes}
            if have and not episodes <= have:
                return f"第 {season} 季没有第 {sorted(episodes - have)[0]} 集"
        return None

    def _failed(self, error: AiError) -> None:
        self._failures += 1
        wait = RETRY_AFTER_AUTH if error.kind in ("auth", "not_configured") else RETRY_AFTER
        self._retry_at = self._clock.now() + wait
        logger.warning("匹配建议的模型调用失败（%s）：%s", error.kind, error.message)
        if self._failures >= ALERT_AFTER_FAILURES or error.kind == "auth":
            self._raise_alert(error)

    def _succeeded(self) -> None:
        if self._failures == 0:
            return
        self._failures = 0
        with self._database.session() as connection:
            connection.execute(
                "UPDATE alert SET resolved_at = ? WHERE fingerprint = ? AND resolved_at IS NULL",
                (to_iso(self._clock.now()), ALERT_FINGERPRINT),
            )

    def _raise_alert(self, error: AiError) -> None:
        message = f"AI 匹配建议的模型调用连续失败：{error.message}"
        # 同样的警报还开着时 create 会报冲突，忽略即可
        with suppress(AppError), self._database.session() as connection:
            AlertRepository(connection, self._clock).create(
                "ai", "warning", message, ALERT_FINGERPRINT
            )


def run_suggestions(container: AppContainer, stop: threading.Event) -> None:
    """后台线程：定期给新出现的未匹配作品生成建议，和同步互不等待。"""
    while not stop.wait(TICK_SECONDS):
        try:
            container.suggestions.run_pending(container)
        except Exception:
            logger.exception("匹配建议任务出错")


def _rule_candidates(raw: str) -> tuple[SeenCandidate, ...]:
    try:
        data: list[dict[str, Any]] = json.loads(raw)
    except ValueError:
        return ()
    return tuple(
        SeenCandidate(
            c["provider_id"], c["remote_id"], c["title"], c.get("original_title"), c.get("year")
        )
        for c in data
        if c.get("provider_id") and c.get("remote_id") and c.get("title")
    )


def _describe(facts: WorkFacts) -> str:
    kind = "电影" if facts.kind == "movie" else "剧集"
    lines = [
        f"资料库：{facts.library_name}（{kind}）",
        f"作品目录：{facts.path or '（资料库根目录）'}",
        f"规则解析出的片名：{facts.title or '（无）'}"
        + (f"；别名：{facts.alt_title}" if facts.alt_title else ""),
        f"规则解析出的年份：{facts.year or '（无）'}",
    ]
    if facts.reason:
        lines.append(f"规则匹配没成功的原因：{facts.reason}")
    lines.append(f"视频文件（共 {facts.file_count} 个，列出前 {len(facts.files)} 个）：")
    lines.extend(f"- {path}" for path in facts.files)
    if facts.rule_candidates:
        lines.append("规则匹配留下的候选（remote_id | 标题 | 原名 | 年份）：")
        lines.extend(
            f"- {c.remote_id} | {c.title} | {c.original_title or ''} | {c.year or ''}"
            for c in facts.rule_candidates
        )
    return "\n".join(lines)


def _suggestion(row: sqlite3.Row) -> Suggestion:
    return Suggestion(
        id=row["id"],
        work_id=row["work_id"],
        provider_id=row["provider_id"],
        remote_id=row["remote_id"],
        title=row["title"],
        original_title=row["original_title"],
        year=row["year"],
        confidence=row["confidence"],
        reason=row["reason"],
        steps=tuple(Step(**s) for s in json.loads(row["steps"])),
        model=row["model"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
