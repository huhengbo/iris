"""刮削器：从元数据插件拉取条目详情、演职员和季信息写入数据库（ADR 0008、0016、0022）。

网络请求都在数据库事务之外进行，拿到结果后再开短事务写入，避免长时间占住 SQLite 的写锁。
插件不可用（MetadataUnavailable）时向上抛出，由调用方决定中止还是提示。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence

from iris_plugin_sdk import MetadataCredit, MetadataNotFound, MetadataRecord

from app.clock import Clock, to_iso
from app.db import Database
from app.errors import NotFound
from app.library.fill import Filler, load_item
from app.library.metadata_fields import IMAGE_METADATA_FIELDS, METADATA_FIELDS
from app.library.metadata_providers import TMDB_PROVIDER, BoundProvider, MetadataProviders
from app.library.recognize import LibraryKind
from app.priority import Priority

# 需要拉取的季：（季 ID，剧集所属的插件，剧集在插件里的 ID，季号）
StaleSeason = tuple[int, str, str, int]


class Scraper:
    def __init__(
        self, database: Database, clock: Clock, providers: MetadataProviders, filler: Filler
    ) -> None:
        self._database = database
        self._clock = clock
        self._providers = providers
        self._filler = filler

    def ensure_item(
        self,
        library_id: int,
        kind: LibraryKind,
        provider: BoundProvider,
        remote_id: str,
        priority: Priority,
    ) -> int:
        """返回资料库里对应插件条目的条目 ID，没有时拉取详情新建。"""
        with self._database.session() as connection:
            existing = _item_id(connection, library_id, provider.id, remote_id)
        if existing is not None:
            return existing
        record = provider.details(kind, remote_id, priority)
        with self._database.session() as connection:
            existing = _item_id(connection, library_id, provider.id, record.remote_id)
            if existing is not None:
                return existing
            return self._write_item(connection, library_id, record, None)

    def refresh_item(self, item_id: int, priority: Priority) -> int:
        """用条目所属的插件重新拉取详情、演职员、补缺和全部季的信息。返回条目所在的资料库 ID。"""
        with self._database.read_session() as connection:
            item = require_item(connection, item_id)
            fill_item = load_item(connection, item_id)
        kind: LibraryKind = item["kind"]
        provider = self._providers.get(item["provider_id"])
        record = provider.details(kind, item["remote_id"], priority)
        # 详情和补缺的请求都在事务外
        plan = self._filler.collect(fill_item, record, priority)
        with self._database.session() as connection:
            self._write_item(connection, item["library_id"], record, item_id)
            self._filler.apply(connection, item_id, plan)
            connection.execute(
                "UPDATE season SET scraped_at = NULL WHERE media_item_id = ?", (item_id,)
            )
            seasons = connection.execute(
                "SELECT id, number FROM season WHERE media_item_id = ?", (item_id,)
            ).fetchall()
        for season in seasons:
            self.scrape_season(
                season["id"], provider.id, item["remote_id"], season["number"], priority
            )
        return int(item["library_id"])

    # ---- 季 ----

    def stale_seasons(self, library_ids: Sequence[int]) -> list[StaleSeason]:
        if not library_ids:
            return []
        marks = ", ".join("?" for _ in library_ids)
        with self._database.read_session() as connection:
            rows = connection.execute(
                "SELECT s.id, m.provider_id, m.remote_id, s.number FROM season s "
                "JOIN media_item m ON m.id = s.media_item_id "
                f"WHERE s.scraped_at IS NULL AND m.library_id IN ({marks}) ORDER BY s.id",
                tuple(library_ids),
            ).fetchall()
        return [(row[0], row[1], row[2], row[3]) for row in rows]

    def scrape_season(
        self, season_id: int, provider_id: str, remote_id: str, number: int, priority: Priority
    ) -> None:
        now = to_iso(self._clock.now())
        try:
            season = self._providers.get(provider_id).season(remote_id, number, priority)
        except MetadataNotFound:
            # 插件里没有这一季（文件的季号不对），记为已拉取，免得每次同步都重试
            with self._database.session() as connection:
                connection.execute(
                    "UPDATE season SET scraped_at = ? WHERE id = ?", (now, season_id)
                )
            return
        episodes = {e.number: e for e in season.episodes}
        with self._database.session() as connection:
            connection.execute(
                "UPDATE season SET name = ?, overview = ?, air_date = ?, "
                "poster_image_id = CASE WHEN poster_path IS ? THEN poster_image_id END, "
                "poster_path = ?, scraped_at = ? WHERE id = ?",
                (
                    season.name,
                    season.overview,
                    season.air_date,
                    season.poster,
                    season.poster,
                    now,
                    season_id,
                ),
            )
            for row in connection.execute(
                "SELECT id, number FROM episode WHERE season_id = ?", (season_id,)
            ).fetchall():
                episode = episodes.get(row["number"])
                if episode is None:
                    continue
                connection.execute(
                    "UPDATE episode SET name = ?, overview = ?, air_date = ?, runtime = ?, "
                    "rating = ?, still_image_id = CASE WHEN still_path IS ? "
                    "THEN still_image_id END, still_path = ? WHERE id = ?",
                    (
                        episode.name,
                        episode.overview,
                        episode.air_date,
                        episode.runtime,
                        episode.rating,
                        episode.still,
                        episode.still,
                        row["id"],
                    ),
                )

    # ---- 写入 ----

    def _write_item(
        self,
        connection: sqlite3.Connection,
        library_id: int,
        details: MetadataRecord,
        item_id: int | None,
    ) -> int:
        now = to_iso(self._clock.now())
        fields = (
            details.title,
            details.original_title,
            details.en_title,
            details.year,
            details.release_date,
            details.overview,
            details.rating,
            json.dumps(list(details.genres), ensure_ascii=False),
            details.runtime,
            details.tagline,
            json.dumps(list(details.studios), ensure_ascii=False),
            json.dumps(list(details.countries), ensure_ascii=False),
            _tmdb_collection_id(details),
            details.content_rating,
        )
        images = (
            details.poster,
            details.backdrop,
            details.thumb,
            details.logo,
        )
        fallback_fields = json.dumps(sorted(details.fallback_fields), ensure_ascii=False)
        if item_id is None:
            cursor = connection.execute(
                "INSERT INTO media_item (library_id, kind, provider_id, remote_id, title, "
                "original_title, "
                "en_title, year, release_date, overview, rating, genres, runtime, tagline, "
                "studios, "
                "countries, tmdb_collection_id, content_rating, poster_path, backdrop_path, "
                "thumb_path, "
                "logo_path, fallback_fields, scraped_at, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?)",
                (
                    library_id,
                    details.kind,
                    details.provider_id,
                    details.remote_id,
                    *fields,
                    *images,
                    fallback_fields,
                    now,
                    now,
                ),
            )
            assert cursor.lastrowid is not None
            item_id = cursor.lastrowid
            connection.execute("UPDATE media_item SET sort_title = title WHERE id = ?", (item_id,))
            _write_provenance(connection, item_id, METADATA_FIELDS, now, source="provider")
        else:
            locked = {
                row["field"]
                for row in connection.execute(
                    "SELECT field FROM metadata_field WHERE entity_kind = 'item' "
                    "AND entity_id = ? AND locked = 1",
                    (item_id,),
                )
            }
            scalar_values = dict(
                zip(
                    (
                        "title",
                        "original_title",
                        "en_title",
                        "year",
                        "release_date",
                        "overview",
                        "rating",
                        "genres",
                        "runtime",
                        "tagline",
                        "studios",
                        "countries",
                        "tmdb_collection_id",
                        # 只读展示字段，不能手动编辑，也就不会被锁定
                        "content_rating",
                    ),
                    fields,
                    strict=True,
                )
            )
            updates: list[str] = []
            params: list[object] = []
            for field, value in scalar_values.items():
                if field not in locked:
                    updates.append(f"{field} = ?")
                    params.append(value)
            if "sort_title" not in locked:
                updates.append("sort_title = ?")
                params.append(details.title)
            for field, value in zip(IMAGE_METADATA_FIELDS, images, strict=True):
                if field in locked:
                    continue
                image_id = field.removesuffix("_path") + "_image_id"
                updates.append(f"{image_id} = CASE WHEN {field} IS ? THEN {image_id} END")
                params.append(value)
                updates.append(f"{field} = ?")
                params.append(value)
                # 主插件重写图片后，图片提供方回到条目自己的插件；补缺会再写它选的来源
                updates.append(f"{field.removesuffix('_path')}_provider = NULL")
            updates.append("fallback_fields = ?")
            params.append(fallback_fields)
            updates.append("scraped_at = ?")
            params.extend((now, item_id))
            connection.execute(f"UPDATE media_item SET {', '.join(updates)} WHERE id = ?", params)
            _write_provenance(
                connection,
                item_id,
                tuple(field for field in METADATA_FIELDS if field not in locked),
                now,
                source="provider",
            )
            connection.execute("DELETE FROM credit WHERE media_item_id = ?", (item_id,))
        _write_tmdb_collection(connection, details)
        order = 0
        for role, people in (
            ("director", details.directors),
            ("writer", details.writers),
            ("cast", details.cast),
        ):
            for person in people:
                person_id = _upsert_person(connection, details.provider_id, person)
                connection.execute(
                    "INSERT INTO credit (media_item_id, person_id, role, character, sort_order) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        item_id,
                        person_id,
                        role,
                        person.character if role == "cast" else None,
                        order,
                    ),
                )
                order += 1
        _write_external_ids(connection, item_id, details.external_ids)
        _upsert_source(connection, item_id, details, now)
        return item_id


def _write_tmdb_collection(connection: sqlite3.Connection, details: MetadataRecord) -> None:
    """记下 TMDB 系列的名称和图片，自动合集同步时用。新抓到的值覆盖旧的，但不会用空值覆盖。"""
    collection_id = _tmdb_collection_id(details)
    if collection_id is None or not details.collection_name:
        return
    connection.execute(
        "INSERT INTO tmdb_collection(id, name, poster_path, backdrop_path) VALUES (?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET name = excluded.name, "
        "poster_path = COALESCE(excluded.poster_path, poster_path), "
        "backdrop_path = COALESCE(excluded.backdrop_path, backdrop_path)",
        (
            collection_id,
            details.collection_name,
            details.collection_poster,
            details.collection_backdrop,
        ),
    )


def _tmdb_collection_id(details: MetadataRecord) -> int | None:
    """自动合集按 TMDB 系列分组，其他插件的系列 ID 不入库。"""
    collection = details.collection_id
    if details.provider_id != TMDB_PROVIDER or collection is None or not collection.isdigit():
        return None
    return int(collection)


def _write_external_ids(
    connection: sqlite3.Connection, item_id: int, external_ids: Mapping[str, str]
) -> None:
    """记下主插件给出的外部 ID；已有值不覆盖（补缺只新增）。"""
    for scheme, value in external_ids.items():
        if value:
            connection.execute(
                "INSERT INTO media_item_external_id (media_item_id, scheme, value) "
                "VALUES (?, ?, ?) ON CONFLICT (media_item_id, scheme) DO NOTHING",
                (item_id, str(scheme).lower(), str(value)),
            )


def _upsert_source(
    connection: sqlite3.Connection, item_id: int, details: MetadataRecord, now: str
) -> None:
    connection.execute(
        "INSERT INTO media_item_source (media_item_id, provider_id, remote_id, rating, checked_at) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT (media_item_id, provider_id) DO UPDATE SET "
        "remote_id = excluded.remote_id, rating = excluded.rating, "
        "checked_at = excluded.checked_at",
        (item_id, details.provider_id, details.remote_id, details.rating, now),
    )


def _write_provenance(
    connection: sqlite3.Connection,
    item_id: int,
    fields: Sequence[str],
    now: str,
    *,
    source: str,
) -> None:
    for field in fields:
        connection.execute(
            """
            INSERT INTO metadata_field (entity_kind, entity_id, field, source, updated_at)
            VALUES ('item', ?, ?, ?, ?)
            ON CONFLICT (entity_kind, entity_id, field) DO UPDATE SET
                source = excluded.source, updated_at = excluded.updated_at
            """,
            (item_id, field, source, now),
        )


def require_item(connection: sqlite3.Connection, item_id: int) -> sqlite3.Row:
    row: sqlite3.Row | None = connection.execute(
        "SELECT id, library_id, kind, provider_id, remote_id FROM media_item WHERE id = ?",
        (item_id,),
    ).fetchone()
    if row is None:
        raise NotFound("条目不存在")
    return row


def _item_id(
    connection: sqlite3.Connection, library_id: int, provider_id: str, remote_id: str
) -> int | None:
    row = connection.execute(
        "SELECT id FROM media_item WHERE library_id = ? AND provider_id = ? AND remote_id = ?",
        (library_id, provider_id, remote_id),
    ).fetchone()
    return None if row is None else int(row["id"])


def _upsert_person(connection: sqlite3.Connection, provider_id: str, person: MetadataCredit) -> int:
    # 拉过演员页资料的人物保留资料里的中文名；头像换了的清掉缓存引用
    connection.execute(
        """
        INSERT INTO person (provider_id, remote_id, name, profile_path) VALUES (?, ?, ?, ?)
        ON CONFLICT (provider_id, remote_id) DO UPDATE SET
            name = CASE WHEN detail_at IS NULL THEN excluded.name ELSE name END,
            profile_image_id = CASE WHEN profile_path IS excluded.profile_path
                THEN profile_image_id END,
            profile_path = excluded.profile_path
        """,
        (provider_id, person.remote_id, person.name or person.remote_id, person.profile),
    )
    row = connection.execute(
        "SELECT id FROM person WHERE provider_id = ? AND remote_id = ?",
        (provider_id, person.remote_id),
    ).fetchone()
    return int(row["id"])
