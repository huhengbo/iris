"""计划任务「下载字幕」：按资料库配置的语言自动下载在线字幕（ADR 0025）。

每个有效版本先汇总已有的字幕语言（外挂、在线、上报过的内封字幕，可选音轨），只补缺的语言。
每种语言按资料库的字幕下载器顺序问一遍，第一个有结果的下载器里取得分最高的一条；没有结果和
失败按结果退避一段时间，避免每次运行都重试同一个空结果。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from iris_plugin_sdk import (
    OnlineSubtitleProvider,
    SubtitleCandidate,
    SubtitleSearchRequest,
    SubtitleUnavailable,
)

from app.clock import Clock, to_iso
from app.db import Database
from app.errors import AppError
from app.ops.tasks import TrackedTask
from app.plugins.registry import PluginRegistry
from app.subtitles.content import normalize_language_code
from app.subtitles.languages import covers, track_language
from app.subtitles.online import (
    DownloadRequest,
    OnlineSubtitles,
    SubtitleVersionContext,
    _system_version_context,
    _unavailable_reason,
)

logger = logging.getLogger(__name__)

# 每次运行最多问字幕站这么多次，避免计划任务长时间占着字幕站
MAX_SEARCHES = 300
# 没有结果 7 天内不再搜，失败 1 天内不再搜
NONE_BACKOFF = timedelta(days=7)
FAILED_BACKOFF = timedelta(days=1)

LANGUAGES_KEY = "subtitle_download_languages"
SKIP_IF_AUDIO_KEY = "subtitle_skip_if_audio"

Downloaders = list[tuple[str, str, OnlineSubtitleProvider]]
Outcome = Literal["downloaded", "none", "failed"]


class _Stopped(Exception):
    """这次运行到此为止：任务被取消或搜索预算用完。"""


@dataclass(frozen=True)
class _LibraryPlan:
    library_id: int
    languages: list[str]
    skip_if_audio: bool
    downloaders: Downloaders
    versions: list[int]


class SubtitleAutoDownloader:
    def __init__(
        self,
        database: Database,
        clock: Clock,
        plugins: PluginRegistry,
        subtitles: OnlineSubtitles,
    ) -> None:
        self._database = database
        self._clock = clock
        self._plugins = plugins
        self._subtitles = subtitles

    def run(self, handle: TrackedTask, cancel: Callable[[], bool]) -> dict[str, int]:
        """跑一轮自动下载；进度写进后台任务，取消或预算用完时提前结束。"""
        plans = self._plans()
        total = sum(len(plan.versions) for plan in plans)
        handle.progress("subtitles", 0, total)
        counters = {
            "libraries": len(plans),
            "versions": 0,
            "languages": 0,
            "searches": 0,
            "downloaded": 0,
            "skipped": 0,
            "failed": 0,
        }
        # 本次运行里被字幕站要求退避的下载器，后面的版本和语言都不再问它
        disabled: set[str] = set()
        done = 0
        try:
            for plan in plans:
                for version_id in plan.versions:
                    if cancel():
                        raise _Stopped
                    self._process_version(plan, version_id, disabled, cancel, counters)
                    done += 1
                    handle.progress("subtitles", done, total)
        except _Stopped:
            logger.info("自动下载字幕提前结束：已搜索 %s 次", counters["searches"])
        return counters

    def _process_version(
        self,
        plan: _LibraryPlan,
        version_id: int,
        disabled: set[str],
        cancel: Callable[[], bool],
        counters: dict[str, int],
    ) -> None:
        with self._database.read_session() as connection:
            context = _system_version_context(connection, version_id)
            if context is None:
                # 版本在这期间被删了，不再处理
                return
            existing = _existing_languages(connection, context, plan.skip_if_audio)
        counters["versions"] += 1
        missing = [
            language
            for language in plan.languages
            if not any(covers(have, language) for have in existing)
        ]
        for language in missing:
            if cancel():
                raise _Stopped
            if counters["searches"] >= MAX_SEARCHES:
                raise _Stopped
            if self._in_backoff(version_id, language):
                counters["skipped"] += 1
                continue
            counters["languages"] += 1
            outcome = self._download_language(
                context, language, plan.downloaders, disabled, cancel, counters
            )
            self._record(version_id, language, outcome)
            if outcome == "downloaded":
                counters["downloaded"] += 1
            elif outcome == "failed":
                counters["failed"] += 1

    def _plans(self) -> list[_LibraryPlan]:
        """需要处理的语言、下载器和版本；下载语言为空或没有下载器的资料库直接跳过。"""
        plans: list[_LibraryPlan] = []
        with self._database.read_session() as connection:
            library_ids = [
                int(row[0])
                for row in connection.execute(
                    "SELECT id FROM library ORDER BY sort_order, id"
                ).fetchall()
            ]
        for library_id in library_ids:
            with self._database.read_session() as connection:
                languages = _wanted_languages(connection, library_id)
                if not languages:
                    continue
                skip_if_audio = _skip_if_audio(connection, library_id)
                versions = _version_ids(connection, library_id)
            if not versions:
                continue
            downloaders = self._plugins.subtitle_downloaders(library_id)
            if not downloaders:
                continue
            plans.append(_LibraryPlan(library_id, languages, skip_if_audio, downloaders, versions))
        return plans

    def _download_language(
        self,
        context: SubtitleVersionContext,
        language: str,
        downloaders: Downloaders,
        disabled: set[str],
        cancel: Callable[[], bool],
        counters: dict[str, int],
    ) -> Outcome:
        """按顺序问下载器；第一个有结果的下载器里取最高分的一条并下载。"""
        failed = False
        for plugin_id, name, provider in downloaders:
            if plugin_id in disabled:
                continue
            reason = _unavailable_reason(provider)
            if reason is not None:
                logger.info("自动下载字幕：跳过 %s（%s）", plugin_id, reason)
                failed = True
                continue
            if cancel() or counters["searches"] >= MAX_SEARCHES:
                raise _Stopped
            counters["searches"] += 1
            try:
                candidates = provider.search(
                    SubtitleSearchRequest(media=context.media, language=language)
                )
            except SubtitleUnavailable as exc:
                logger.info(
                    "自动下载字幕：%s 暂时不可用（%s），本次运行不再使用",
                    plugin_id,
                    type(exc).__name__,
                )
                disabled.add(plugin_id)
                failed = True
                continue
            except Exception as exc:
                # 插件异常消息里可能带凭据，只记类型
                logger.warning("自动下载字幕：%s 搜索失败（%s）", plugin_id, type(exc).__name__)
                failed = True
                continue
            matched = [
                candidate
                for candidate in candidates
                if _matches_language(candidate.language, language)
            ]
            if not matched:
                continue
            best = max(matched, key=_rank)
            request = DownloadRequest(
                provider_id=plugin_id,
                remote_id=best.remote_id,
                language=best.language or language,
                label=best.label,
                release=best.release,
                format=best.format,
                forced=best.forced,
                hearing_impaired=best.hearing_impaired,
            )
            try:
                self._subtitles.auto_download(context, request)
            except AppError as exc:
                if exc.code in ("subtitle_rate_limited", "subtitle_provider_unavailable"):
                    # 字幕站要求退避或插件不可用：换个下载器继续问这一种语言
                    disabled.add(plugin_id)
                    failed = True
                    continue
                logger.warning(
                    "自动下载字幕：%s 下载 %s 失败（%s）", plugin_id, best.remote_id, exc.code
                )
                return "failed"
            except sqlite3.IntegrityError:
                # 下载期间版本被删了，外键拒绝写入
                logger.info("自动下载字幕：版本 %s 在下载期间被删除", context.version_id)
                return "failed"
            logger.info("自动下载字幕：%s 从 %s 下载了 %s", context.title, name, best.remote_id)
            return "downloaded"
        return "failed" if failed else "none"

    def _in_backoff(self, version_id: int, language: str) -> bool:
        with self._database.read_session() as connection:
            row = connection.execute(
                "SELECT outcome, searched_at FROM subtitle_auto_search "
                "WHERE version_id = ? AND language = ?",
                (version_id, language),
            ).fetchone()
        if row is None:
            return False
        span = {"none": NONE_BACKOFF, "failed": FAILED_BACKOFF}.get(str(row["outcome"]))
        searched_at = _parse_time(row["searched_at"])
        if span is None or searched_at is None:
            return False
        return self._clock.now() - searched_at < span

    def _record(self, version_id: int, language: str, outcome: Outcome) -> None:
        with self._database.session() as connection:
            connection.execute(
                "INSERT INTO subtitle_auto_search (version_id, language, outcome, searched_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (version_id, language) DO UPDATE SET "
                "outcome = excluded.outcome, searched_at = excluded.searched_at",
                (version_id, language, outcome, to_iso(self._clock.now())),
            )


def _wanted_languages(connection: sqlite3.Connection, library_id: int) -> list[str]:
    raw = _setting(connection, library_id, LANGUAGES_KEY)
    if not raw:
        return []
    try:
        value: Any = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("资料库 %s 的字幕语言设置不是合法 JSON，按未设置处理", library_id)
        return []
    if not isinstance(value, list):
        return []
    languages: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        code = normalize_language_code(item)
        if code and code not in languages:
            languages.append(code)
    return languages


def _skip_if_audio(connection: sqlite3.Connection, library_id: int) -> bool:
    return _setting(connection, library_id, SKIP_IF_AUDIO_KEY) == "true"


def _setting(connection: sqlite3.Connection, library_id: int, key: str) -> str | None:
    row = connection.execute(
        "SELECT value FROM library_setting WHERE library_id = ? AND key = ?", (library_id, key)
    ).fetchone()
    return None if row is None else str(row[0])


def _version_ids(connection: sqlite3.Connection, library_id: int) -> list[int]:
    """资料库里可播放的云端文件对应的版本，按版本 id 排序。"""
    rows = connection.execute(
        """
        SELECT v.id
        FROM version v
        JOIN playable p ON p.id = v.playable_id
        JOIN media_item m ON m.id = p.media_item_id
        JOIN media_file c ON c.id = v.media_file_id
        WHERE m.library_id = ? AND c.state = 'active' AND c.playback_locator IS NOT NULL
        ORDER BY v.id
        """,
        (library_id,),
    ).fetchall()
    return [int(row[0]) for row in rows]


def _existing_languages(
    connection: sqlite3.Connection, context: SubtitleVersionContext, skip_if_audio: bool
) -> set[str]:
    """版本已有字幕的语言：外挂和在线字幕，加与当前文件一致的流信息里的内封字幕（可选音轨）。"""
    languages: set[str] = set()
    for row in connection.execute(
        "SELECT language FROM subtitle WHERE version_id = ?", (context.version_id,)
    ).fetchall():
        code = track_language(row["language"], None)
        if code is not None:
            languages.add(code)
    media = connection.execute(
        """
        SELECT mi.subtitles, mi.audio
        FROM version v JOIN media_file c ON c.id = v.media_file_id
        LEFT JOIN media_info mi ON mi.media_file_id = c.id
            AND mi.size IS c.size AND mi.sha1 IS c.sha1
        WHERE v.id = ?
        """,
        (context.version_id,),
    ).fetchone()
    if media is None:
        return languages
    for track in _tracks(media["subtitles"]):
        code = track_language(_text(track.get("language")), _text(track.get("title")))
        if code is not None:
            languages.add(code)
    if skip_if_audio:
        for track in _tracks(media["audio"]):
            code = track_language(_text(track.get("language")), _text(track.get("title")))
            if code is not None:
                languages.add(code)
    return languages


def _tracks(raw: object) -> list[dict[str, Any]]:
    if raw is None:
        return []
    try:
        value: Any = json.loads(str(raw))
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _matches_language(candidate: str | None, wanted: str) -> bool:
    """下载器给的候选语言是否符合要求的语言；笼统的 zh 两种中文都算。"""
    value = track_language(candidate, None) or (candidate or "").strip()
    return bool(value) and covers(value, wanted)


def _rank(candidate: SubtitleCandidate) -> tuple[bool, float, int]:
    """得分最高的优先；没有得分的排最后，同分时下载量大的优先。"""
    return (candidate.score is not None, candidate.score or 0.0, candidate.downloads or 0)


def _parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
