"""把 TMDB 服务适配成 SDK 的元数据 provider。"""

from __future__ import annotations

from iris_plugin_sdk import (
    ImageLookupRequest,
    MetadataCandidate,
    MetadataCredit,
    MetadataEpisode,
    MetadataKind,
    MetadataNotFound,
    MetadataPerson,
    MetadataRecord,
    MetadataSearchRequest,
    MetadataSeason,
    RemoteImage,
)

from iris_plugin_tmdb.errors import TmdbNotFound
from iris_plugin_tmdb.gateway import TmdbCast, TmdbDetails, tmdb_page_url
from iris_plugin_tmdb.service import TmdbService
from iris_plugin_tmdb.validation import validation_problem

METADATA_CAPABILITIES = (
    "metadata.search",
    "metadata.details",
    "metadata.images",
    "metadata.people",
)


class TmdbMetadataProvider:
    """条目 ID 是 TMDB ID 的字符串形式；图片来源是 TMDB 的图片路径。"""

    def __init__(self, tmdb: TmdbService, plugin_id: str) -> None:
        self.id = plugin_id
        self._tmdb = tmdb

    def unavailable_reason(self) -> str | None:
        status = self._tmdb.status()
        if not status.api_key_set:
            return "还没有配置 TMDB Key，在插件 › TMDB 元数据里填写"
        # 只有 Key 被 TMDB 拒绝才算不可用；连不上是暂时的，必须继续发请求才有机会恢复
        return validation_problem(status.validation, blocking_only=True)

    def search(self, request: MetadataSearchRequest) -> list[MetadataCandidate]:
        query = request.title.strip()
        results = self._tmdb.search(request.kind, query)
        return [
            MetadataCandidate(
                provider_id=self.id,
                remote_id=str(result.tmdb_id),
                title=result.title,
                year=result.year,
                kind=request.kind,
                original_title=result.original_title,
                overview=result.overview,
            )
            for result in results
        ]

    def find(self, kind: MetadataKind, scheme: str, value: str) -> str | None:
        if scheme == "tmdb":
            # 就是 TMDB ID，是否存在由随后的 details 确认
            return value if value.isdigit() else None
        if scheme == "imdb":
            found = self._tmdb.find_imdb(kind, value)
            return None if found is None else str(found)
        return None

    def details(self, remote_id: str, *, kind: MetadataKind) -> MetadataRecord:
        tmdb_id = _tmdb_id(remote_id)
        return _record(self._tmdb.details(kind, tmdb_id), provider_id=self.id)

    def images(self, request: ImageLookupRequest) -> list[RemoteImage]:
        """按 tmdb ID 取图；只有 imdb 时先经 /find 换成 tmdb ID。"""
        external_ids = {scheme.lower(): value for scheme, value in request.external_ids.items()}
        tmdb_id = external_ids.get("tmdb")
        if tmdb_id is not None and tmdb_id.isdigit():
            remote_id = int(tmdb_id)
        else:
            imdb_id = external_ids.get("imdb")
            if not imdb_id:
                return []
            found = self._tmdb.find_imdb(request.kind, imdb_id)
            if found is None:
                return []
            remote_id = found
        try:
            images = self._tmdb.images(request.kind, remote_id)
        except MetadataNotFound:
            return []
        return [
            RemoteImage(
                type=image.type,
                source=image.path,
                language=image.language,
                score=image.score,
                width=image.width,
                height=image.height,
            )
            for image in images
        ]

    def season(self, remote_id: str, number: int) -> MetadataSeason:
        tmdb_id = _tmdb_id(remote_id)
        season = self._tmdb.season(tmdb_id, number)
        return MetadataSeason(
            number=season.number,
            name=season.name,
            overview=season.overview,
            air_date=season.air_date,
            poster=season.poster_path,
            episodes=tuple(
                MetadataEpisode(
                    number=e.number,
                    name=e.name,
                    overview=e.overview,
                    air_date=e.air_date,
                    runtime=e.runtime,
                    rating=e.rating,
                    still=e.still_path,
                )
                for e in season.episodes
            ),
        )

    def page_url(self, kind: MetadataKind, remote_id: str) -> str | None:
        return tmdb_page_url(kind, int(remote_id)) if remote_id.isdigit() else None

    def image(self, source: str, width: int) -> bytes:
        return self._tmdb.image(source, width)

    def person(self, remote_id: str) -> MetadataPerson:
        tmdb_id = _tmdb_id(remote_id)
        info = self._tmdb.person(tmdb_id)
        return MetadataPerson(
            remote_id=str(info.tmdb_id),
            name=info.name,
            original_name=info.original_name,
            biography=info.biography,
            birthday=info.birthday,
            deathday=info.deathday,
            place_of_birth=info.place_of_birth,
        )


def _tmdb_id(remote_id: str) -> int:
    if not remote_id.isdigit():
        raise TmdbNotFound()
    return int(remote_id)


def _record(details: TmdbDetails, *, provider_id: str) -> MetadataRecord:
    external_ids = dict(details.external_ids)
    external_ids.setdefault("tmdb", str(details.tmdb_id))
    return MetadataRecord(
        provider_id=provider_id,
        remote_id=str(details.tmdb_id),
        kind=details.kind,
        title=details.title,
        original_title=details.original_title,
        en_title=details.en_title,
        overview=details.overview,
        year=details.year,
        release_date=details.release_date,
        rating=details.rating,
        tagline=details.tagline,
        genres=tuple(details.genres),
        studios=tuple(details.studios),
        countries=tuple(details.countries),
        content_rating=details.content_rating,
        runtime=details.runtime,
        poster=details.poster_path,
        backdrop=details.backdrop_path,
        thumb=details.thumb_path,
        logo=details.logo_path,
        artwork={image_type: tuple(paths) for image_type, paths in details.artwork.items()},
        cast=tuple(_credit(person) for person in details.cast),
        directors=tuple(_credit(person) for person in details.directors),
        writers=tuple(_credit(person) for person in details.writers),
        collection_id=(
            None if details.tmdb_collection_id is None else str(details.tmdb_collection_id)
        ),
        external_ids=external_ids,
        fallback_fields=details.fallback_fields,
        collection_name=details.tmdb_collection_name,
        collection_poster=details.tmdb_collection_poster,
        collection_backdrop=details.tmdb_collection_backdrop,
    )


def _credit(person: TmdbCast) -> MetadataCredit:
    return MetadataCredit(
        remote_id=str(person.tmdb_id),
        name=person.name,
        profile=person.profile_path,
        character=person.character,
    )
