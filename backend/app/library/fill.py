"""元数据补缺和选图（ADR 0025）：按资料库的下载器列表，用后面的插件补前面缺的字段和图片。

补缺只按外部 ID（TMDB、IMDb）找条目，绝不按片名搜索；找不到就不填，不会把别的作品的数据
填进来。网络请求都在事务外完成，拿到结果后开一个短事务写入。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import cast

from iris_plugin_sdk import (
    ImageLookupRequest,
    ImageType,
    MetadataKind,
    MetadataNotFound,
    MetadataRecord,
    MetadataUnavailable,
)

from app.clock import Clock, to_iso
from app.db import Database
from app.errors import AppError, NotFound
from app.library import merge
from app.library.metadata_fields import METADATA_FIELDS
from app.library.metadata_providers import BoundProvider, MetadataProviders
from app.priority import Priority

logger = logging.getLogger(__name__)

# 资料库的元数据语言，只用来挑图；TMDB 的文字仍然固定 zh-CN
METADATA_LANGUAGE_KEY = "metadata_language"
DEFAULT_METADATA_LANGUAGE = "zh-CN"
# 补缺插件找不到条目后，这么多天内不再按同样的外部 ID 重试
NEGATIVE_CACHE_DAYS = 7
FIND_SCHEMES: tuple[str, ...] = ("tmdb", "imdb")


@dataclass(frozen=True)
class CachedSource:
    """media_item_source 里已记录的一个来源。"""

    remote_id: str | None
    checked_at: str


@dataclass(frozen=True)
class FillItem:
    """补缺要用到的条目快照，在事务外读取。"""

    id: int
    library_id: int
    kind: MetadataKind
    provider_id: str
    remote_id: str
    external_ids: dict[str, str]
    fallback_fields: frozenset[str]
    locked: frozenset[str]
    metadata_language: str
    sources: Mapping[str, CachedSource]


@dataclass(frozen=True)
class FillImage:
    provider_id: str
    source: str


@dataclass(frozen=True)
class FillRecord:
    provider_id: str
    record: MetadataRecord


@dataclass
class FillPlan:
    item_id: int
    external_ids: dict[str, str]
    fallback_fields: frozenset[str] = frozenset()
    records: list[FillRecord] = field(default_factory=list)
    # 图片获取器为每种类型挑中的图
    fetched_images: dict[ImageType, FillImage] = field(default_factory=dict)
    # 没有获取器提供、且条目当前无图时，回退到补缺记录里的图
    fallback_images: dict[ImageType, FillImage] = field(default_factory=dict)
    # 按外部 ID 找不到的补缺插件，写负缓存
    negative: list[str] = field(default_factory=list)
    # 不阻塞补缺的插件问题，调用方记日志或展示
    problems: list[str] = field(default_factory=list)
    # 有插件请求时暂时不可用：结果照写，但不记 filled_at，下次同步再补
    incomplete: bool = False


def metadata_language(connection: sqlite3.Connection, library_id: int) -> str:
    row = connection.execute(
        "SELECT value FROM library_setting WHERE library_id = ? AND key = ?",
        (library_id, METADATA_LANGUAGE_KEY),
    ).fetchone()
    value = str(row[0]).strip() if row is not None else ""
    return value or DEFAULT_METADATA_LANGUAGE


def load_item(connection: sqlite3.Connection, item_id: int) -> FillItem:
    """读取补缺要用的条目快照：外部 ID、fallback_fields、锁定字段和来源缓存。"""
    row = connection.execute(
        "SELECT id, library_id, kind, provider_id, remote_id, fallback_fields "
        "FROM media_item WHERE id = ?",
        (item_id,),
    ).fetchone()
    if row is None:
        raise NotFound("条目不存在")
    external_ids = {
        str(external[0]).lower(): str(external[1])
        for external in connection.execute(
            "SELECT scheme, value FROM media_item_external_id WHERE media_item_id = ?",
            (item_id,),
        ).fetchall()
    }
    if "imdb" not in external_ids:
        # NFO 里的 IMDb ID 只在还没有记录时补上
        imdb = connection.execute(
            "SELECT MIN(nfo_imdb_id) FROM work WHERE media_item_id = ? "
            "AND nfo_imdb_id IS NOT NULL AND TRIM(nfo_imdb_id) <> ''",
            (item_id,),
        ).fetchone()
        if imdb is not None and imdb[0]:
            external_ids["imdb"] = str(imdb[0]).strip()
    fallback = frozenset(json.loads(row["fallback_fields"] or "[]"))
    locked = frozenset(
        str(field_row[0])
        for field_row in connection.execute(
            "SELECT field FROM metadata_field WHERE entity_kind = 'item' "
            "AND entity_id = ? AND locked = 1",
            (item_id,),
        ).fetchall()
    )
    sources = {
        str(source[0]): CachedSource(source[1], str(source[2]))
        for source in connection.execute(
            "SELECT provider_id, remote_id, checked_at FROM media_item_source "
            "WHERE media_item_id = ?",
            (item_id,),
        ).fetchall()
    }
    return FillItem(
        id=int(row["id"]),
        library_id=int(row["library_id"]),
        kind=cast(MetadataKind, row["kind"]),
        provider_id=str(row["provider_id"]),
        remote_id=str(row["remote_id"]),
        external_ids=external_ids,
        fallback_fields=fallback,
        locked=locked,
        metadata_language=metadata_language(connection, int(row["library_id"])),
        sources=sources,
    )


class Filler:
    def __init__(self, database: Database, clock: Clock, providers: MetadataProviders) -> None:
        self._database = database
        self._clock = clock
        self._providers = providers

    def fill_item(
        self,
        item_id: int,
        priority: Priority,
        primary_record: MetadataRecord | None = None,
        blocked: set[str] | None = None,
    ) -> FillPlan:
        """补缺一个条目：读快照、在事务外请求插件、再开一个短写事务。"""
        with self._database.read_session() as connection:
            item = load_item(connection, item_id)
        plan = self.collect(item, primary_record, priority, blocked)
        with self._database.session() as connection:
            self.apply(connection, item_id, plan)
        return plan

    # ---- 收集 ----

    def collect(
        self,
        item: FillItem,
        primary_record: MetadataRecord | None,
        priority: Priority,
        blocked: set[str] | None = None,
    ) -> FillPlan:
        """只做网络请求，不写数据库。

        blocked 是这一轮里已经报过暂时不可用的插件：同步时跨条目共用，后面的条目不再请求它们。
        """
        if blocked is None:
            blocked = set()
        external_ids = dict(item.external_ids)
        fallback_fields = item.fallback_fields
        if primary_record is not None:
            for scheme, value in primary_record.external_ids.items():
                if value:
                    external_ids.setdefault(str(scheme).lower(), str(value))
            # 刷新时用这次拉到的 fallback_fields，不必等写入之后再读一次
            fallback_fields = primary_record.fallback_fields
        plan = FillPlan(
            item_id=item.id,
            external_ids=external_ids,
            fallback_fields=fallback_fields,
        )
        if not external_ids:
            # 没有任何外部 ID，补缺插件和图片获取器都无从下手，一个请求也不发
            return plan

        now = self._clock.now()
        for plugin_id in self._providers.fill_provider_ids(item.library_id):
            if plugin_id in blocked:
                plan.incomplete = True
                continue
            try:
                provider = self._providers.get(plugin_id)
            except AppError as exc:
                # 资料库勾选了但插件停用或启动失败，记下来但不影响匹配和其他来源
                plan.problems.append(exc.message)
                continue
            reason = provider.unavailable_reason()
            if reason is not None:
                plan.problems.append(f"{provider.name}：{reason}")
                continue
            remote_id = self._find_remote(
                provider, item, external_ids, plan, now, priority, blocked
            )
            if remote_id is None:
                continue
            try:
                record = provider.details(item.kind, remote_id, priority)
            except MetadataNotFound:
                plan.negative.append(provider.id)
                continue
            except MetadataUnavailable as exc:
                plan.problems.append(f"{provider.name}：{exc.message}")
                plan.incomplete = True
                blocked.add(provider.id)
                continue
            except Exception:
                logger.exception("补缺插件 %s 拉取条目 %s 时出错", provider.id, remote_id)
                plan.problems.append(f"元数据插件「{provider.name}」出错，请查看日志")
                plan.incomplete = True
                blocked.add(provider.id)
                continue
            if not _ids_agree(external_ids, record.external_ids):
                # 插件返回的条目和我们手上的外部 ID 对不上，丢弃，避免填进别的作品
                logger.info(
                    "补缺插件 %s 返回的条目 %s 与外部 ID 不一致，已丢弃", provider.id, remote_id
                )
                plan.negative.append(provider.id)
                continue
            for scheme, value in record.external_ids.items():
                if value:
                    external_ids.setdefault(str(scheme).lower(), str(value))
            plan.records.append(FillRecord(provider.id, record))

        self._collect_images(item, plan, external_ids, priority, blocked)
        return plan

    def _find_remote(
        self,
        provider: BoundProvider,
        item: FillItem,
        external_ids: Mapping[str, str],
        plan: FillPlan,
        now: datetime,
        priority: Priority,
        blocked: set[str],
    ) -> str | None:
        """按缓存的 remote_id 或外部 ID 找条目，找不到时记负缓存。"""
        cached = item.sources.get(provider.id)
        if cached is not None:
            if cached.remote_id is not None:
                return cached.remote_id
            if _recent(cached.checked_at, now):
                # 最近刚确认找不到，先不再请求
                return None
        attempted = False
        for scheme in FIND_SCHEMES:
            value = external_ids.get(scheme)
            if not value:
                continue
            attempted = True
            try:
                found = provider.find(item.kind, scheme, value, priority)
            except MetadataUnavailable as exc:
                plan.problems.append(f"{provider.name}：{exc.message}")
                plan.incomplete = True
                blocked.add(provider.id)
                return None
            except Exception:
                logger.exception("补缺插件 %s 按 %s 查找时出错", provider.id, scheme)
                plan.problems.append(f"元数据插件「{provider.name}」出错，请查看日志")
                plan.incomplete = True
                blocked.add(provider.id)
                return None
            if found:
                return found
        if attempted:
            plan.negative.append(provider.id)
        return None

    def _collect_images(
        self,
        item: FillItem,
        plan: FillPlan,
        external_ids: Mapping[str, str],
        priority: Priority,
        blocked: set[str],
    ) -> None:
        for fetcher in self._providers.image_fetchers(item.library_id):
            needed = [
                image_type
                for image_type in merge.IMAGE_TYPES
                if image_type not in plan.fetched_images and f"{image_type}_path" not in item.locked
            ]
            if not needed:
                break
            if fetcher.id in blocked:
                plan.incomplete = True
                continue
            reason = fetcher.unavailable_reason()
            if reason is not None:
                plan.problems.append(f"{fetcher.name}：{reason}")
                continue
            try:
                images = fetcher.images(
                    ImageLookupRequest(
                        kind=item.kind,
                        external_ids=dict(external_ids),
                        language=item.metadata_language,
                    ),
                    priority,
                )
            except MetadataNotFound:
                continue
            except MetadataUnavailable as exc:
                plan.problems.append(f"{fetcher.name}：{exc.message}")
                plan.incomplete = True
                blocked.add(fetcher.id)
                continue
            except Exception:
                logger.exception("图片插件 %s 取图时出错", fetcher.id)
                plan.problems.append(f"图片插件「{fetcher.name}」出错，请查看日志")
                plan.incomplete = True
                blocked.add(fetcher.id)
                continue
            for image_type in needed:
                picked = merge.pick_image(images, image_type, item.metadata_language)
                if picked is not None:
                    plan.fetched_images[image_type] = FillImage(fetcher.id, picked.source)
        for record in plan.records:
            for image_type in merge.IMAGE_TYPES:
                if image_type in plan.fallback_images:
                    continue
                source = getattr(record.record, image_type)
                if source:
                    plan.fallback_images[image_type] = FillImage(record.provider_id, source)

    # ---- 写入 ----

    def apply(self, connection: sqlite3.Connection, item_id: int, plan: FillPlan) -> None:
        """把补缺结果写进一个短事务；只填当前为空（或主插件 fallback）且未锁定的字段。"""
        row = connection.execute("SELECT * FROM media_item WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise NotFound("条目不存在")
        now = to_iso(self._clock.now())
        locked = frozenset(
            str(field_row[0])
            for field_row in connection.execute(
                "SELECT field FROM metadata_field WHERE entity_kind = 'item' "
                "AND entity_id = ? AND locked = 1",
                (item_id,),
            ).fetchall()
        )
        records = [entry.record for entry in plan.records]
        merged = merge.merge_text(dict(row), records, plan.fallback_fields, locked)

        updates: list[str] = []
        params: list[object] = []
        filled_fields: list[str] = []
        for name, value in merged.items():
            updates.append(f"{name} = ?")
            params.append(
                json.dumps(value, ensure_ascii=False) if name in merge.LIST_FIELDS else value
            )
            filled_fields.append(name)
            if name == "title" and "sort_title" not in locked:
                updates.append("sort_title = ?")
                params.append(value)
        rating = merge.fill_rating(row["rating"], records)
        if rating is not None:
            updates.append("rating = ?")
            params.append(rating)

        image_fields: list[str] = []
        for image_type in merge.IMAGE_TYPES:
            path_field = f"{image_type}_path"
            if path_field in locked:
                continue
            chosen = plan.fetched_images.get(image_type)
            if chosen is None and merge.is_empty(row[path_field]):
                # 没有获取器提供这类图时，回退到第一个有图的补缺记录
                chosen = plan.fallback_images.get(image_type)
            if chosen is None:
                continue
            changed = chosen.source != row[path_field]
            updates.append(f"{path_field} = ?")
            params.append(chosen.source)
            updates.append(f"{image_type}_provider = ?")
            params.append(chosen.provider_id)
            if changed:
                updates.append(f"{image_type}_image_id = NULL")
            image_fields.append(path_field)

        updates.append("filled_at = ?")
        params.append(None if plan.incomplete else now)
        params.append(item_id)
        connection.execute(f"UPDATE media_item SET {', '.join(updates)} WHERE id = ?", params)
        _write_provenance(
            connection,
            item_id,
            [
                *filled_fields,
                *image_fields,
            ],
            now,
        )

        for scheme, value in plan.external_ids.items():
            if not scheme or not value:
                continue
            connection.execute(
                "INSERT INTO media_item_external_id (media_item_id, scheme, value) "
                "VALUES (?, ?, ?) ON CONFLICT (media_item_id, scheme) DO NOTHING",
                (item_id, scheme, value),
            )

        sources: list[tuple[str, str | None, float | None]] = [
            # 主插件自己的评分；补进 media_item.rating 的别家评分不算它的
            (str(row["provider_id"]), str(row["remote_id"]), row["rating"])
        ]
        sources.extend(
            (entry.provider_id, entry.record.remote_id, entry.record.rating)
            for entry in plan.records
        )
        sources.extend((provider_id, None, None) for provider_id in plan.negative)
        for provider_id, remote_id, source_rating in sources:
            connection.execute(
                "INSERT INTO media_item_source "
                "(media_item_id, provider_id, remote_id, rating, checked_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT (media_item_id, provider_id) DO UPDATE SET "
                "remote_id = excluded.remote_id, rating = excluded.rating, "
                "checked_at = excluded.checked_at",
                (item_id, provider_id, remote_id, source_rating, now),
            )

    # ---- 供同步和整理复用的工具 ----

    @staticmethod
    def blocked_image_providers(
        providers: MetadataProviders, plugin_ids: Sequence[str]
    ) -> dict[str, str]:
        """不能用图片能力的插件和原因；给同步判断哪些图片任务先跳过。"""
        blocked: dict[str, str] = {}
        for plugin_id in sorted(set(plugin_ids)):
            try:
                source = providers.image_source(plugin_id)
            except AppError as exc:
                blocked[plugin_id] = exc.message
                continue
            if source is None:
                blocked[plugin_id] = f"图片插件 {plugin_id} 没有安装"
                continue
            reason = source.unavailable_reason()
            if reason is not None:
                blocked[plugin_id] = reason
        return blocked


def _ids_agree(known: Mapping[str, str], returned: Mapping[str, str]) -> bool:
    """插件返回的同 scheme 外部 ID 必须和已知值一致。"""
    for scheme, value in returned.items():
        expected = known.get(str(scheme).lower())
        if expected is not None and value and str(value) != expected:
            return False
    return True


def _recent(checked_at: str, now: datetime) -> bool:
    try:
        checked = datetime.fromisoformat(checked_at)
    except ValueError:
        return False
    return now - checked < timedelta(days=NEGATIVE_CACHE_DAYS)


def _write_provenance(
    connection: sqlite3.Connection, item_id: int, fields: Sequence[str], now: str
) -> None:
    for field_name in dict.fromkeys(fields):
        if field_name not in METADATA_FIELDS:
            continue
        connection.execute(
            """
            INSERT INTO metadata_field (entity_kind, entity_id, field, source, updated_at)
            VALUES ('item', ?, ?, 'provider', ?)
            ON CONFLICT (entity_kind, entity_id, field) DO UPDATE SET
                source = excluded.source, updated_at = excluded.updated_at
            """,
            (item_id, field_name, now),
        )


__all__ = [
    "FillImage",
    "FillItem",
    "FillPlan",
    "FillRecord",
    "Filler",
    "load_item",
    "metadata_language",
]
