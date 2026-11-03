"""宿主测试用的 TMDB 假插件控制。

插件进程里是真正的元数据 provider；这里只通过 RPC 操纵它的条目和调用记录。类型和辅助函数
保留原来的名字，测试写法不用围着进程边界改。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, MutableMapping
from dataclasses import dataclass, field
from typing import Any

from tests.plugin_env import PluginControl

VALID_KEY = "tmdb-test-key-0123456789"


def image_bytes(png: bool = False, width: int = 1000, height: int = 600) -> bytes:
    from tests.fixtures.fake_plugin.fake_state import image_bytes as make_image

    return make_image(png=png, width=width, height=height)


class TmdbUnavailable(Exception):
    pass


class TmdbAuthFailed(Exception):
    pass


class TmdbNotFound(Exception):
    pass


@dataclass(frozen=True)
class TmdbCast:
    tmdb_id: int
    name: str
    profile_path: str | None = None
    character: str | None = None


@dataclass(frozen=True)
class TmdbPerson:
    tmdb_id: int
    name: str
    original_name: str | None = None
    biography: str | None = None
    birthday: str | None = None
    deathday: str | None = None
    place_of_birth: str | None = None
    profile_path: str | None = None


@dataclass(frozen=True)
class TmdbSearchResult:
    tmdb_id: int
    title: str
    original_title: str | None
    year: int | None
    overview: str | None
    kind: str = "movie"


@dataclass
class TmdbDetails:
    tmdb_id: int
    kind: str
    title: str
    original_title: str | None = None
    en_title: str | None = None
    year: int | None = None
    release_date: str | None = None
    overview: str | None = None
    rating: float | None = None
    genres: list[str] = field(default_factory=list)
    content_rating: str | None = None
    runtime: int | None = None
    poster_path: str | None = None
    backdrop_path: str | None = None
    thumb_path: str | None = None
    logo_path: str | None = None
    cast: list[Any] = field(default_factory=list)
    directors: list[Any] = field(default_factory=list)
    writers: list[Any] = field(default_factory=list)
    external_ids: dict[str, str] = field(default_factory=dict)
    fallback_fields: set[str] = field(default_factory=set)
    # 所属 TMDB 系列（电影）：ID 以字符串传给宿主，和插件协议一致
    collection_id: str | None = None
    collection_name: str | None = None
    collection_poster: str | None = None
    collection_backdrop: str | None = None


class CallLog:
    def __init__(self, control: PluginControl, domain: str) -> None:
        self._control = control
        self._domain = domain

    def _read(self) -> list[str]:
        values = self._control.call(self._domain, "calls")
        return [str(value) for value in values]

    def __iter__(self) -> Iterator[str]:
        return iter(self._read())

    def __len__(self) -> int:
        return len(self._read())

    def __contains__(self, value: object) -> bool:
        return value in self._read()

    def __eq__(self, other: object) -> bool:
        return list(self._read()) == list(other) if isinstance(other, list) else NotImplemented

    def __repr__(self) -> str:
        return repr(self._read())

    def count(self, prefix: str) -> int:
        return sum(1 for call in self._read() if call.startswith(prefix))

    def clear(self) -> None:
        self._control.call(self._domain, "clear_calls")


class _Title:
    def __init__(self, owner: FakeTmdb, kind: str, tmdb_id: int) -> None:
        self._owner = owner
        self._kind = kind
        self._tmdb_id = tmdb_id

    @property
    def details(self) -> TmdbDetails:
        raw = self._owner.control.call(
            "metadata", "get_title", {"kind": self._kind, "tmdb_id": self._tmdb_id}
        )
        return _details(raw)

    @details.setter
    def details(self, value: TmdbDetails) -> None:
        self._owner.set_details(value)


class _Titles(Mapping[tuple[str, int], _Title]):
    def __init__(self, owner: FakeTmdb) -> None:
        self._owner = owner

    def __getitem__(self, key: tuple[str, int]) -> _Title:
        return _Title(self._owner, key[0], int(key[1]))

    def clear(self) -> None:
        self._owner.control.call("metadata", "clear_titles")

    def __iter__(self) -> Iterator[tuple[str, int]]:
        return iter(())

    def __len__(self) -> int:
        return 0


class _People(Mapping[int, TmdbPerson]):
    def __init__(self, owner: FakeTmdb) -> None:
        self._owner = owner

    def __getitem__(self, key: int) -> TmdbPerson:
        raise KeyError(key)

    def __setitem__(self, key: int, value: TmdbPerson) -> None:
        self._owner.control.call(
            "metadata",
            "add_person",
            {
                "person": {
                    "remote_id": str(key),
                    "name": value.name,
                    "original_name": value.original_name,
                    "biography": value.biography,
                    "birthday": value.birthday,
                    "deathday": value.deathday,
                    "place_of_birth": value.place_of_birth,
                }
            },
        )

    def __iter__(self) -> Iterator[int]:
        return iter(())

    def __len__(self) -> int:
        return 0


class _SearchResults(MutableMapping[tuple[str, str], list[TmdbSearchResult]]):
    def __init__(self, owner: FakeTmdb) -> None:
        self._owner = owner
        self._values: dict[tuple[str, str], list[TmdbSearchResult]] = {}

    def __getitem__(self, key: tuple[str, str]) -> list[TmdbSearchResult]:
        return self._values[key]

    def __setitem__(self, key: tuple[str, str], value: list[TmdbSearchResult]) -> None:
        self._values[key] = list(value)
        self._owner.control.call(
            "metadata",
            "set_search_results",
            {
                "kind": key[0],
                "query": key[1],
                "results": [
                    {
                        "remote_id": str(item.tmdb_id),
                        "title": item.title,
                        "original_title": item.original_title,
                        "year": item.year,
                        "overview": item.overview,
                        "kind": item.kind,
                    }
                    for item in value
                ],
            },
        )

    def __delitem__(self, key: tuple[str, str]) -> None:
        del self._values[key]

    def __iter__(self) -> Iterator[tuple[str, str]]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


class FakeTmdb:
    def __init__(self, control: PluginControl) -> None:
        self.control = control
        self._titles = _Titles(self)
        self._people = _People(self)
        self._search_results = _SearchResults(self)

    @property
    def calls(self) -> CallLog:
        return CallLog(self.control, "metadata")

    @property
    def titles(self) -> _Titles:
        return self._titles

    @property
    def people(self) -> _People:
        return self._people

    @property
    def search_results(self) -> _SearchResults:
        return self._search_results

    @property
    def error(self) -> Exception | None:
        return None

    @error.setter
    def error(self, value: Exception | None) -> None:
        mode = None
        if isinstance(value, TmdbUnavailable):
            mode = "unavailable"
        elif isinstance(value, TmdbAuthFailed):
            mode = "auth"
        elif isinstance(value, TmdbNotFound):
            mode = "not_found"
        elif value is not None:
            raise TypeError(f"不支持的假 TMDB 错误：{value!r}")
        self.control.call("metadata", "set_error", {"error": mode})

    @property
    def reason(self) -> str | None:
        return None

    @reason.setter
    def reason(self, value: str | None) -> None:
        self.control.call("metadata", "set_reason", {"reason": value})

    @property
    def image_delay(self) -> float:
        return 0.0

    @image_delay.setter
    def image_delay(self, seconds: float) -> None:
        self.control.call("metadata", "set_image_delay", {"seconds": seconds})

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
        raw = self.control.call(
            "metadata",
            "add_movie",
            {
                "tmdb_id": tmdb_id,
                "title": title,
                "year": year,
                "original_title": original_title,
                "imdb_id": imdb_id,
                "cast": [_cast_dict(item) for item in cast or []],
            },
        )
        return _details(raw)

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
        raw = self.control.call(
            "metadata",
            "add_series",
            {
                "tmdb_id": tmdb_id,
                "title": title,
                "year": year,
                "seasons": {str(key): value for key, value in seasons.items()},
                "original_title": original_title,
                "imdb_id": imdb_id,
                "tvdb_id": tvdb_id,
                "cast": [_cast_dict(item) for item in cast or []],
            },
        )
        return _details(raw)

    def rename(self, kind: str, tmdb_id: int, title: str) -> None:
        self.control.call("metadata", "rename", {"kind": kind, "tmdb_id": tmdb_id, "title": title})

    def set_details(self, details: TmdbDetails) -> None:
        fields: dict[str, Any] = {
            "title": details.title,
            "original_title": details.original_title,
            "en_title": details.en_title,
            "year": details.year,
            "release_date": details.release_date,
            "overview": details.overview,
            "rating": details.rating,
            "genres": details.genres,
            "content_rating": details.content_rating,
            "runtime": details.runtime,
            "poster": details.poster_path,
            "backdrop": details.backdrop_path,
            "thumb": details.thumb_path,
            "logo": details.logo_path,
            "cast": [_cast_dict(cast) for cast in details.cast],
            "writers": [_cast_dict(writer) for writer in details.writers],
            "external_ids": details.external_ids,
            "fallback_fields": sorted(details.fallback_fields),
        }
        if details.collection_id is not None:
            fields.update(
                collection_id=details.collection_id,
                collection_name=details.collection_name,
                collection_poster=details.collection_poster,
                collection_backdrop=details.collection_backdrop,
            )
        self.control.call(
            "metadata",
            "set_details",
            {"kind": details.kind, "tmdb_id": details.tmdb_id, "fields": fields},
        )

    def count(self, prefix: str) -> int:
        return self.calls.count(prefix)


def _cast_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {
        "remote_id": str(value.tmdb_id),
        "name": value.name,
        "profile": value.profile_path,
        "character": value.character,
    }


def _details(raw: dict[str, Any]) -> TmdbDetails:
    return TmdbDetails(
        tmdb_id=int(raw["tmdb_id"]),
        kind=str(raw["kind"]),
        title=str(raw["title"]),
        original_title=raw.get("original_title"),
        year=raw.get("year"),
        release_date=raw.get("release_date"),
        overview=raw.get("overview"),
        rating=raw.get("rating"),
        genres=list(raw.get("genres") or []),
        content_rating=raw.get("content_rating"),
        runtime=raw.get("runtime"),
        poster_path=raw.get("poster_path"),
        backdrop_path=raw.get("backdrop_path"),
        thumb_path=raw.get("thumb_path"),
        logo_path=raw.get("logo_path"),
        cast=list(raw.get("cast") or []),
        directors=list(raw.get("directors") or []),
        external_ids=dict(raw.get("external_ids") or {}),
        fallback_fields=set(raw.get("fallback_fields") or []),
    )
