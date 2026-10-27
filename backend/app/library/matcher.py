"""匹配器：用资料库选择的元数据插件，把等待匹配的作品对应到插件里的条目（ADR 0016、0022）。"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from difflib import SequenceMatcher
from typing import Literal

from iris_plugin_sdk import MetadataCandidate, MetadataNotFound

from app.clock import Clock, to_iso
from app.db import Database
from app.library.metadata_providers import BoundProvider, MetadataProviders
from app.library.recognize import LibraryKind, normalize
from app.library.scraper import Scraper
from app.priority import Priority

# 未匹配时保留的候选数，供管理员在待处理页里挑选
MAX_CANDIDATES = 8
MatchState = Literal["matched", "unmatched"]


@dataclass(frozen=True)
class Candidate:
    provider_id: str
    remote_id: str
    title: str
    original_title: str | None
    year: int | None
    # 匹配度（0–100）和依据，只用于排序和展示，不参与自动匹配的判定
    score: int = 0
    reasons: tuple[str, ...] = ()

    @classmethod
    def of(cls, result: MetadataCandidate) -> Candidate:
        return cls(
            result.provider_id, result.remote_id, result.title, result.original_title, result.year
        )


@dataclass(frozen=True)
class PendingWork:
    id: int
    folder_id: int
    library_id: int
    kind: LibraryKind
    path: str
    title: str | None
    alt_title: str | None
    year: int | None
    tmdb_hint: int | None
    nfo_tmdb_id: int | None
    nfo_imdb_id: str | None


class Matcher:
    def __init__(
        self, database: Database, clock: Clock, providers: MetadataProviders, scraper: Scraper
    ) -> None:
        self._database = database
        self._clock = clock
        self._providers = providers
        self._scraper = scraper

    def pending_works(self, library_ids: Sequence[int]) -> list[PendingWork]:
        if not library_ids:
            return []
        marks = ", ".join("?" for _ in library_ids)
        with self._database.read_session() as connection:
            rows = connection.execute(
                "SELECT w.id, w.folder_id, f.library_id, l.kind, w.path, w.title, w.alt_title, "
                "w.year, w.tmdb_hint, w.nfo_tmdb_id, w.nfo_imdb_id FROM work w "
                "JOIN library_folder f ON f.id = w.folder_id JOIN library l ON l.id = f.library_id "
                f"WHERE w.state = 'pending' AND f.library_id IN ({marks}) ORDER BY w.id",
                tuple(library_ids),
            ).fetchall()
        return [PendingWork(**dict(row)) for row in rows]

    def match(self, work: PendingWork, priority: Priority) -> MatchState:
        """按「路径里的 {tmdb-ID} → NFO 的 TMDB ID → NFO 的 IMDb ID → 搜索」的顺序匹配。

        TMDB、IMDb ID 交给插件换成它自己的条目 ID，插件不认识时跳过，直接搜索。
        """
        provider = self._providers.for_library(work.library_id)
        ids: list[str] = []
        for tmdb_id in dict.fromkeys(i for i in (work.tmdb_hint, work.nfo_tmdb_id) if i):
            found = provider.find(work.kind, "tmdb", str(tmdb_id), priority)
            if found is not None:
                ids.append(found)
        if work.nfo_imdb_id and not ids:
            found = provider.find(work.kind, "imdb", work.nfo_imdb_id, priority)
            if found is not None:
                ids.append(found)
        for remote_id in dict.fromkeys(ids):
            if self._try(work, provider, remote_id, priority):
                return "matched"
        if not work.title:
            self._set_unmatched(work.id, "无法从文件名识别片名", [])
            return "unmatched"
        found_any = False
        candidates: dict[str, Candidate] = {}
        names = {_comparable(n) for n in (work.title, work.alt_title) if n}
        for query in _queries(work.title, work.alt_title):
            results = provider.search(work.kind, query, priority)
            found_any = found_any or bool(results)
            for result in results:
                candidates.setdefault(result.remote_id, Candidate.of(result))
            choice = choose(results, names, work.year)
            if choice is not None and self._try(work, provider, choice, priority):
                return "matched"
        label = f"「{work.title}」" + (f"（{work.year}）" if work.year else "")
        reason = (
            f"有多个可能的结果，无法确定{label}" if found_any else f"{provider.name} 搜不到{label}"
        )
        ranked = [
            _scored(c, names, work.year, unique=len(candidates) == 1) for c in candidates.values()
        ]
        # 分数相同时保持插件的搜索顺序（sorted 是稳定排序）
        ranked.sort(key=lambda c: c.score, reverse=True)
        self._set_unmatched(work.id, reason, ranked[:MAX_CANDIDATES])
        return "unmatched"

    def assign(self, work_id: int, item_id: int) -> None:
        """管理员手动指定，之后同步不再改动。"""
        with self._database.session() as connection:
            connection.execute(
                "UPDATE work SET state = 'matched', media_item_id = ?, manual = 1, reason = NULL, "
                "candidates = '[]', updated_at = ? WHERE id = ?",
                (item_id, to_iso(self._clock.now()), work_id),
            )

    def reset(self, work_ids: Sequence[int]) -> None:
        """清掉匹配结果（包括手动指定）和读过的 NFO，作品回到等待匹配。"""
        if not work_ids:
            return
        marks = ", ".join("?" for _ in work_ids)
        with self._database.session() as connection:
            connection.execute(
                f"UPDATE work SET state = 'pending', manual = 0, media_item_id = NULL, "
                f"nfo_sha1 = NULL, updated_at = ? WHERE id IN ({marks})",
                (to_iso(self._clock.now()), *work_ids),
            )

    def _try(
        self, work: PendingWork, provider: BoundProvider, remote_id: str, priority: Priority
    ) -> bool:
        try:
            item_id = self._scraper.ensure_item(
                work.library_id, work.kind, provider, remote_id, priority
            )
        except MetadataNotFound:
            # ID 写错了或者条目被插件那边删了，换下一个线索
            return False
        self._set_auto_matched(work.id, item_id)
        return True

    # 自动匹配的结果只写入非手动的作品：同步匹配期间管理员可能已经手动指定了
    def _set_auto_matched(self, work_id: int, item_id: int) -> None:
        with self._database.session() as connection:
            connection.execute(
                "UPDATE work SET state = 'matched', media_item_id = ?, reason = NULL, "
                "candidates = '[]', updated_at = ? WHERE id = ? AND manual = 0",
                (item_id, to_iso(self._clock.now()), work_id),
            )

    def _set_unmatched(self, work_id: int, reason: str, candidates: list[Candidate]) -> None:
        data = json.dumps([asdict(c) for c in candidates], ensure_ascii=False)
        with self._database.session() as connection:
            connection.execute(
                "UPDATE work SET state = 'unmatched', media_item_id = NULL, reason = ?, "
                "candidates = ?, updated_at = ? WHERE id = ? AND manual = 0",
                (reason, data, to_iso(self._clock.now()), work_id),
            )


def choose(results: list[MetadataCandidate], names: set[str], year: int | None) -> str | None:
    """从搜索结果里挑出可以确定的那一个，确定不了时返回 None。

    有年份时：同年的结果只有一个就是它；没有同年的，放宽到前后一年。候选不止一个时，
    取片名（或原名）和文件名一致的结果中相关度最高的。没有年份时，只接受片名一致的唯一结果。
    """
    if not results:
        return None

    def same_name(result: MetadataCandidate) -> bool:
        return _comparable(result.title) in names or (
            result.original_title is not None and _comparable(result.original_title) in names
        )

    if year is not None:
        pool = [r for r in results if r.year == year]
        if not pool:
            pool = [r for r in results if r.year is not None and abs(r.year - year) <= 1]
        if len(pool) == 1:
            return pool[0].remote_id
        hits = [r for r in pool if same_name(r)]
        # 同名同年的多个结果几乎都是同一部作品的重复条目，取相关度最高的
        return hits[0].remote_id if hits else None
    hits = [r for r in results if same_name(r)]
    return hits[0].remote_id if len(hits) == 1 else None


def _scored(candidate: Candidate, names: set[str], year: int | None, *, unique: bool) -> Candidate:
    """给候选打分（满分 100）并写明依据：片名最多 60 分，年份最多 30 分，搜索结果唯一 10 分。"""
    score = 0
    reasons: list[str] = []
    titles = [t for t in (candidate.title, candidate.original_title) if t]
    comparable = [_comparable(t) for t in titles]
    if any(t in names for t in comparable):
        score += 60
        reasons.append("片名完全一致")
    else:
        ratio = max(
            (SequenceMatcher(None, t, n).ratio() for t in comparable for n in names), default=0.0
        )
        score += round(ratio * 40)
        reasons.append(f"片名相似度 {round(ratio * 100)}%")
    if year is None:
        reasons.append("文件名没有年份")
    elif candidate.year is None:
        reasons.append("候选没有年份")
    elif candidate.year == year:
        score += 30
        reasons.append("年份一致")
    elif abs(candidate.year - year) <= 1:
        score += 15
        reasons.append(f"年份相差 1 年（文件 {year}，候选 {candidate.year}）")
    else:
        reasons.append(f"年份不一致（文件 {year}，候选 {candidate.year}）")
    if unique:
        score += 10
        reasons.append("搜索结果唯一")
    return replace(candidate, score=score, reasons=tuple(reasons))


_PARENTHESES = re.compile(r"\s*[(（\[【][^)）\]】]*[)）\]】]\s*")
_NOT_WORD = re.compile(r"[^\w]+")


def _queries(title: str, alt_title: str | None) -> list[str]:
    queries = [title, alt_title or "", _PARENTHESES.sub(" ", title)]
    return [q for q in dict.fromkeys(normalize(q) for q in queries) if q]


def _comparable(text: str) -> str:
    """比较片名用：忽略大小写、空格和标点。"""
    return _NOT_WORD.sub("", text.lower())
