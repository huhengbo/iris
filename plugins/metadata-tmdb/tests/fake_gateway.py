"""测试用的 TMDB 网关：登记条目、季和人物，记录每次调用。不访问网络。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from iris_plugin_sdk import MetadataUnavailable

from iris_plugin_tmdb.errors import TmdbAuthFailed, TmdbNotFound
from iris_plugin_tmdb.gateway import (
    TmdbAccess,
    TmdbCast,
    TmdbDetails,
    TmdbEpisode,
    TmdbImage,
    TmdbKind,
    TmdbPerson,
    TmdbSearchResult,
    TmdbSeason,
)

VALID_KEY = "tmdb-test-key-0123456789"


@dataclass
class FakeTitle:
    details: TmdbDetails
    # 季号 → 季信息
    seasons: dict[int, TmdbSeason] = field(default_factory=dict)
    imdb_id: str | None = None


class FakeTmdb:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.valid_keys = {VALID_KEY}
        # 设置后每次调用都抛出，直到清除
        self.error: MetadataUnavailable | None = None
        self.titles: dict[tuple[TmdbKind, int], FakeTitle] = {}
        self.people: dict[int, TmdbPerson] = {}
        self.search_results: dict[tuple[TmdbKind, str], list[TmdbSearchResult]] = {}
        self.missing_images: set[str] = set()
        self.accesses: list[TmdbAccess] = []

    # ---- 编排 ----

    def add_movie(
        self,
        tmdb_id: int,
        title: str,
        year: int | None,
        *,
        original_title: str | None = None,
        imdb_id: str | None = None,
        cast: list[TmdbCast] | None = None,
    ) -> TmdbDetails:
        details = TmdbDetails(
            tmdb_id=tmdb_id,
            kind="movie",
            title=title,
            original_title=original_title,
            en_title=original_title,
            year=year,
            release_date=f"{year}-05-01" if year else None,
            overview=f"{title}的简介",
            rating=7.5,
            genres=["剧情"],
            content_rating="PG-13",
            runtime=120,
            poster_path=f"/poster-{tmdb_id}.jpg",
            backdrop_path=f"/backdrop-{tmdb_id}.jpg",
            thumb_path=f"/thumb-{tmdb_id}.jpg",
            logo_path=f"/logo-{tmdb_id}.png",
            cast=cast or [TmdbCast(9000 + tmdb_id, f"演员{tmdb_id}", "/p.jpg", "主角")],
            directors=[TmdbCast(8000 + tmdb_id, f"导演{tmdb_id}", None, None)],
        )
        details = replace(details, external_ids={"imdb": imdb_id} if imdb_id is not None else {})
        self.titles[("movie", tmdb_id)] = FakeTitle(details, imdb_id=imdb_id)
        return details

    def add_series(
        self,
        tmdb_id: int,
        title: str,
        year: int | None,
        seasons: dict[int, int],
        *,
        original_title: str | None = None,
        cast: list[TmdbCast] | None = None,
        imdb_id: str | None = None,
        tvdb_id: str | None = None,
    ) -> TmdbDetails:
        """seasons：季号 → 集数。"""
        details = TmdbDetails(
            tmdb_id=tmdb_id,
            kind="series",
            title=title,
            original_title=original_title,
            en_title=original_title,
            year=year,
            release_date=f"{year}-01-10" if year else None,
            overview=f"{title}的简介",
            rating=8.1,
            genres=["剧情"],
            poster_path=f"/poster-{tmdb_id}.jpg",
            backdrop_path=f"/backdrop-{tmdb_id}.jpg",
            cast=cast or [TmdbCast(9000 + tmdb_id, f"演员{tmdb_id}", "/p.jpg", "主角")],
            directors=[TmdbCast(8000 + tmdb_id, f"主创{tmdb_id}", None, None)],
        )
        external_ids = {
            key: value for key, value in (("imdb", imdb_id), ("tvdb", tvdb_id)) if value is not None
        }
        details = replace(details, external_ids=external_ids)
        title_entry = FakeTitle(details, imdb_id=imdb_id)
        for number, count in seasons.items():
            title_entry.seasons[number] = TmdbSeason(
                number=number,
                name=f"第 {number} 季",
                overview=None,
                air_date=None,
                poster_path=f"/season-{tmdb_id}-{number}.jpg",
                episodes=[
                    TmdbEpisode(
                        number=episode,
                        name=f"第 {episode} 集",
                        overview=None,
                        air_date=None,
                        runtime=45,
                        rating=None,
                        still_path=f"/still-{tmdb_id}-{number}-{episode}.jpg",
                    )
                    for episode in range(1, count + 1)
                ],
            )
        self.titles[("series", tmdb_id)] = title_entry
        return details

    def count(self, prefix: str) -> int:
        return sum(1 for call in self.calls if call.startswith(prefix))

    # ---- TmdbGateway ----

    def validate(self, access: TmdbAccess) -> None:
        self._enter("validate", access)

    def search(self, access: TmdbAccess, kind: TmdbKind, query: str) -> list[TmdbSearchResult]:
        self._enter(f"search:{kind}:{query}", access)
        if (kind, query) in self.search_results:
            return self.search_results[(kind, query)]
        return [
            TmdbSearchResult(
                tmdb_id=entry.details.tmdb_id,
                title=entry.details.title,
                original_title=entry.details.original_title,
                year=entry.details.year,
                overview=entry.details.overview,
            )
            for (entry_kind, _), entry in self.titles.items()
            if entry_kind == kind
            and query.lower()
            in {entry.details.title.lower(), (entry.details.original_title or "").lower()}
        ]

    def find_imdb(self, access: TmdbAccess, kind: TmdbKind, imdb_id: str) -> int | None:
        self._enter(f"find:{imdb_id}", access)
        for (entry_kind, tmdb_id), entry in self.titles.items():
            if entry_kind == kind and entry.imdb_id == imdb_id:
                return tmdb_id
        return None

    def details(self, access: TmdbAccess, kind: TmdbKind, tmdb_id: int) -> TmdbDetails:
        self._enter(f"details:{kind}:{tmdb_id}", access)
        entry = self.titles.get((kind, tmdb_id))
        if entry is None:
            raise TmdbNotFound()
        return entry.details

    def images(self, access: TmdbAccess, kind: TmdbKind, tmdb_id: int) -> list[TmdbImage]:
        self._enter(f"images:{kind}:{tmdb_id}", access)
        if (kind, tmdb_id) not in self.titles:
            raise TmdbNotFound()
        return [
            TmdbImage("poster", f"/poster-{tmdb_id}.jpg", "en", 5.5, 1000, 1500),
            TmdbImage("backdrop", f"/backdrop-{tmdb_id}.jpg", None, 6.0, 1920, 1080),
            TmdbImage("thumb", f"/thumb-{tmdb_id}.jpg", "zh", 4.0, 1920, 1080),
            TmdbImage("logo", f"/logo-{tmdb_id}.png", None, 3.0, 800, 310),
        ]

    def season(self, access: TmdbAccess, tmdb_id: int, number: int) -> TmdbSeason:
        self._enter(f"season:{tmdb_id}:{number}", access)
        entry = self.titles.get(("series", tmdb_id))
        if entry is None or number not in entry.seasons:
            raise TmdbNotFound()
        return entry.seasons[number]

    def person(self, access: TmdbAccess, tmdb_id: int) -> TmdbPerson:
        self._enter(f"person:{tmdb_id}", access)
        if tmdb_id not in self.people:
            raise TmdbNotFound()
        return self.people[tmdb_id]

    def image(self, access: TmdbAccess, path: str, width: int) -> bytes:
        self._enter(f"image:{path}", access)
        if path in self.missing_images:
            raise TmdbNotFound()
        return b"image-bytes"

    def _enter(self, call: str, access: TmdbAccess) -> None:
        self.calls.append(call)
        self.accesses.append(access)
        if self.error is not None:
            raise self.error
        if access.key not in self.valid_keys:
            raise TmdbAuthFailed()
