"""语义索引：把条目的标题、简介等变成向量，按语义召回「像某部片那样」的条目（ADR 0030）。

向量存在 sqlite-vec 的 item_vec 虚表里（rowid 就是条目 ID），维度由向量模型决定，在第一批向量
返回后创建。换了向量模型（提供方或模型名）就清空重建，不同模型的向量不会混用。sqlite-vec 加载
失败时整个模块不可用，只影响语义召回。
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Literal

import sqlite_vec

from app.ai.service import AiService
from app.ai.types import AiError
from app.clock import Clock, to_iso
from app.db import Database
from app.settings import delete_setting, get_setting, put_setting

if TYPE_CHECKING:
    from app.container import AppContainer

logger = logging.getLogger(__name__)

_SIGNATURE = "ai.semantic.index"
BATCH_SIZE = 32
TICK_SECONDS = 60.0
RETRY_AFTER = timedelta(minutes=5)
MAX_CAST = 5
# 召回时先取 k 个再按可见范围过滤，不够就把 k 翻倍重取，直到上限
INITIAL_K = 100
MAX_K = 1600

IndexState = Literal["off", "unavailable", "building", "ready", "error"]


@dataclass(frozen=True)
class _Stale:
    id: int
    stamp: str
    text: str
    text_hash: str


@dataclass(frozen=True)
class IndexStatus:
    # off 没有配置向量模型；unavailable sqlite-vec 加载失败；building 正在建索引；
    # ready 已是最新；error 向量模型调用失败
    state: IndexState
    model: str | None
    total: int
    indexed: int
    error: str | None


class SemanticIndex:
    def __init__(self, database: Database, clock: Clock, ai: AiService) -> None:
        self._database = database
        self._clock = clock
        self._ai = ai
        self._retry_at = clock.now()
        self._error: str | None = None
        ai.vector_search_available = self.usable

    # 状态 -----------------------------------------------------------------

    def usable(self) -> bool:
        """能否用于召回：扩展可用，并且索引已按当前向量模型建过（至少有一个条目）。"""
        if not self._database.vector_available():
            return False
        binding = self._ai.roles()["embedding"]
        if binding is None:
            return False
        with self._database.read_session() as connection:
            signature = _read_signature(connection)
            has_rows = connection.execute("SELECT 1 FROM item_embedding LIMIT 1").fetchone()
        return (
            signature is not None
            and signature.get("dim") is not None
            and (signature["provider_id"], signature["model"])
            == (binding.provider_id, binding.model)
            and has_rows is not None
        )

    def status(self) -> IndexStatus:
        binding = self._ai.roles()["embedding"]
        if binding is None:
            return IndexStatus("off", None, 0, 0, None)
        if not self._database.vector_available():
            return IndexStatus("unavailable", binding.model, 0, 0, None)
        with self._database.read_session() as connection:
            total = connection.execute("SELECT COUNT(*) FROM media_item").fetchone()[0]
            signature = _read_signature(connection)
            current = signature is not None and (
                signature["provider_id"],
                signature["model"],
            ) == (binding.provider_id, binding.model)
            indexed = (
                connection.execute(
                    "SELECT COUNT(*) FROM item_embedding e JOIN media_item m ON m.id = e.item_id "
                    "WHERE e.stamp = " + _STAMP
                ).fetchone()[0]
                if current
                else 0
            )
        if self._error is not None:
            state: IndexState = "error"
        else:
            state = "ready" if indexed >= total else "building"
        return IndexStatus(state, binding.model, total, indexed, self._error)

    # 建索引 ---------------------------------------------------------------

    def sync(self) -> int:
        """处理一批需要（重新）生成向量的条目，返回处理的数量；没有要做的事时为 0。"""
        binding = self._ai.roles()["embedding"]
        if binding is None or not self._database.vector_available():
            return 0
        if self._clock.now() < self._retry_at:
            return 0
        self._reset_if_model_changed(binding.provider_id, binding.model)
        self._drop_orphans()
        batch = self._stale(BATCH_SIZE)
        if not batch:
            return 0
        known = self._text_hashes([row.id for row in batch])
        changed = [row for row in batch if known.get(row.id) != row.text_hash]
        try:
            vectors = self._embed([row.text for row in changed]) if changed else []
        except AiError as error:
            self._error = error.message
            self._retry_at = self._clock.now() + RETRY_AFTER
            logger.warning("语义索引的向量模型调用失败（%s）：%s", error.kind, error.message)
            return 0
        self._error = None
        self._store(batch, {row.id: v for row, v in zip(changed, vectors, strict=True)})
        return len(batch)

    # 召回 -----------------------------------------------------------------

    def search(
        self,
        connection: sqlite3.Connection,
        text: str,
        accept: Callable[[Sequence[int]], list[int]] | None,
        limit: int,
        *,
        user_id: int | None = None,
    ) -> list[int]:
        """按语义相近程度返回条目 ID，最相近的在前。

        accept 从候选里挑出调用者可见、满足其他条件的（保持顺序），传 None 表示不过滤。
        候选不够时放大范围重取，所以只看得到少数条目的用户也能得到结果。
        connection 必须已加载 sqlite-vec。"""
        if not self.usable():
            return []
        vector = self._ai.embed([text], user_id=user_id).vectors[0]
        wanted = limit
        k = max(INITIAL_K, limit)
        while True:
            rows = connection.execute(
                "SELECT rowid, distance FROM item_vec WHERE embedding MATCH ? AND k = ?",
                (sqlite_vec.serialize_float32(vector), k),
            ).fetchall()
            candidates = [r["rowid"] for r in rows]
            found = accept(candidates) if accept else candidates
            if len(found) >= wanted or len(rows) < k or k >= MAX_K:
                return found[:limit]
            k = min(k * 2, MAX_K)

    # 内部 -----------------------------------------------------------------

    def _reset_if_model_changed(self, provider_id: int, model: str) -> None:
        # 先用只读会话看一眼：绝大多数时候没变，不必去抢写锁
        with self._database.read_session() as connection:
            signature = _read_signature(connection)
        if signature is not None and (signature["provider_id"], signature["model"]) == (
            provider_id,
            model,
        ):
            return
        with self._database.session(vector=True) as connection:
            signature = _read_signature(connection)
            if signature is not None and (signature["provider_id"], signature["model"]) == (
                provider_id,
                model,
            ):
                return
            connection.execute("DROP TABLE IF EXISTS item_vec")
            connection.execute("DELETE FROM item_embedding")
            put_setting(
                connection,
                _SIGNATURE,
                json.dumps({"provider_id": provider_id, "model": model, "dim": None}),
                to_iso(self._clock.now()),
            )
        logger.info("向量模型已更换，语义索引将重建")

    def _drop_orphans(self) -> None:
        """条目删除后 item_vec 里的向量没有外键级联，这里清掉。"""
        with self._database.read_session(vector=True) as connection:
            if not _has_vec_table(connection):
                return
            stale = [
                r[0]
                for r in connection.execute(
                    "SELECT rowid FROM item_vec "
                    "WHERE rowid NOT IN (SELECT item_id FROM item_embedding)"
                )
            ]
        if not stale:
            return
        with self._database.session(vector=True) as connection:
            for rowid in stale:
                connection.execute("DELETE FROM item_vec WHERE rowid = ?", (rowid,))

    def _stale(self, limit: int) -> list[_Stale]:
        with self._database.read_session() as connection:
            rows = connection.execute(
                f"SELECT m.id, {_STAMP} AS stamp FROM media_item m "
                "LEFT JOIN item_embedding e ON e.item_id = m.id "
                f"WHERE e.item_id IS NULL OR e.stamp != {_STAMP} ORDER BY m.id LIMIT ?",
                (limit,),
            ).fetchall()
            out: list[_Stale] = []
            for row in rows:
                text = _item_text(connection, row["id"])
                digest = hashlib.sha256(text.encode()).hexdigest()
                out.append(_Stale(row["id"], row["stamp"], text, digest))
        return out

    def _text_hashes(self, ids: list[int]) -> dict[int, str]:
        marks = ", ".join("?" for _ in ids)
        with self._database.read_session() as connection:
            rows = connection.execute(
                f"SELECT item_id, text_hash FROM item_embedding WHERE item_id IN ({marks})", ids
            ).fetchall()
        return {r["item_id"]: r["text_hash"] for r in rows}

    def _embed(self, texts: list[str]) -> list[list[float]]:
        return self._ai.embed(texts).vectors

    def _store(self, batch: list[_Stale], vectors: dict[int, list[float]]) -> None:
        now = to_iso(self._clock.now())
        with self._database.session(vector=True) as connection:
            signature = _read_signature(connection)
            assert signature is not None
            if vectors:
                dim = len(next(iter(vectors.values())))
                if signature.get("dim") not in (None, dim):
                    raise AiError("bad_request", "向量模型返回的维度和已有索引不一致")
                if signature.get("dim") is None:
                    connection.execute(
                        f"CREATE VIRTUAL TABLE IF NOT EXISTS item_vec USING vec0("
                        f"embedding float[{dim}] distance_metric=cosine)"
                    )
                    signature["dim"] = dim
                    put_setting(connection, _SIGNATURE, json.dumps(signature), now)
            for row in batch:
                item_id = row.id
                if item_id in vectors:
                    connection.execute("DELETE FROM item_vec WHERE rowid = ?", (item_id,))
                    connection.execute(
                        "INSERT INTO item_vec (rowid, embedding) VALUES (?, ?)",
                        (item_id, sqlite_vec.serialize_float32(vectors[item_id])),
                    )
                connection.execute(
                    "INSERT INTO item_embedding (item_id, stamp, text_hash, updated_at) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT (item_id) DO UPDATE SET "
                    "stamp = excluded.stamp, text_hash = excluded.text_hash, "
                    "updated_at = excluded.updated_at",
                    (item_id, row.stamp, row.text_hash, now),
                )

    def clear(self) -> None:
        """清空索引（测试和手动重建用）。"""
        with self._database.session(vector=True) as connection:
            connection.execute("DROP TABLE IF EXISTS item_vec")
            connection.execute("DELETE FROM item_embedding")
            delete_setting(connection, _SIGNATURE)


# 条目的刮削时间加上字段修改时间：元数据改过就变，用来发现需要重新检查的条目
_STAMP = (
    "(m.scraped_at || '|' || COALESCE((SELECT MAX(f.updated_at) FROM metadata_field f "
    "WHERE f.entity_kind = 'item' AND f.entity_id = m.id), ''))"
)


def _read_signature(connection: sqlite3.Connection) -> dict[str, int | str | None] | None:
    raw = get_setting(connection, _SIGNATURE)
    if raw is None:
        return None
    data: dict[str, int | str | None] = json.loads(raw)
    return data


def _has_vec_table(connection: sqlite3.Connection) -> bool:
    return (
        connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'item_vec'").fetchone()
        is not None
    )


def _item_text(connection: sqlite3.Connection, item_id: int) -> str:
    """送去生成向量的文本：标题、原名、类型、简介、制作信息、主演和导演。"""
    row = connection.execute(
        "SELECT kind, title, original_title, en_title, year, overview, tagline, genres, "
        "studios, countries FROM media_item WHERE id = ?",
        (item_id,),
    ).fetchone()
    people = connection.execute(
        "SELECT p.name, c.role FROM credit c JOIN person p ON p.id = c.person_id "
        "WHERE c.media_item_id = ? AND c.role IN ('cast', 'director') "
        "ORDER BY c.role, c.sort_order",
        (item_id,),
    ).fetchall()
    cast = [p["name"] for p in people if p["role"] == "cast"][:MAX_CAST]
    directors = [p["name"] for p in people if p["role"] == "director"]
    names = [row["title"], row["original_title"], row["en_title"]]
    lines = [
        "标题：" + " / ".join(dict.fromkeys(n for n in names if n)),
        "类型：" + ("电影" if row["kind"] == "movie" else "剧集"),
    ]
    if row["year"]:
        lines.append(f"年份：{row['year']}")
    for label, column in (("分类", "genres"), ("制作", "studios"), ("地区", "countries")):
        values = json.loads(row[column])
        if values:
            lines.append(f"{label}：" + "、".join(values))
    if directors:
        lines.append("导演：" + "、".join(directors))
    if cast:
        lines.append("主演：" + "、".join(cast))
    if row["tagline"]:
        lines.append(f"标语：{row['tagline']}")
    if row["overview"]:
        lines.append(f"简介：{row['overview']}")
    return "\n".join(lines)


def run_semantic(container: AppContainer, stop: threading.Event) -> None:
    """后台线程：一批一批补齐语义索引，有积压时不等待，没有时每分钟看一次。"""
    wait = 0.0
    while not stop.wait(wait):
        try:
            wait = 0.0 if container.semantic.sync() else TICK_SECONDS
        except Exception:
            logger.exception("语义索引任务出错")
            wait = TICK_SECONDS
