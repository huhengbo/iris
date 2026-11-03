from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx2
from iris_plugin_sdk import (
    MetadataUnavailable,
    PluginHttpClient,
    PluginHttpResponse,
    json_object,
    raise_for_status,
)

from iris_plugin_tmdb.errors import TmdbAuthFailed, TmdbNotFound, TmdbUnavailable
from iris_plugin_tmdb.gateway import (
    TmdbAccess,
    TmdbCast,
    TmdbDetails,
    TmdbEpisode,
    TmdbImage,
    TmdbImageType,
    TmdbKind,
    TmdbPerson,
    TmdbSearchResult,
    TmdbSeason,
)

API_BASE = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p"
LANGUAGE = "zh-CN"
FALLBACK_LANGUAGE = "en-US"
TIMEOUT_SECONDS = 20.0
# 429 时按 Retry-After 等一次再重试，等待时间封顶，避免一个请求卡住整个同步
MAX_RETRY_AFTER_SECONDS = 10.0
# 每部作品保留的演员数：详情页只展示前几位，多留一些让演员页列出的本地作品更完整（ADR 0014）
MAX_CAST = 20
# 编剧取 crew 里这些职务的人物
WRITER_JOBS = frozenset({"Writer", "Screenplay", "Story"})
# 分级按这些地区的顺序取：美国的分级最常见、覆盖最全，没有再看中文地区
_RATING_REGIONS = ("US", "CN", "HK", "TW")
# 横版图和片名图只要中文或英文的，没有语言的背景图不带片名
_IMAGE_LANGUAGES = ("zh", "en")
_PATHS: dict[TmdbKind, str] = {"movie": "movie", "series": "tv"}


class TmdbHttpGateway:
    """TMDB 的请求构造和响应解析；连接池、代理和日志脱敏由 DirectHttpClient 负责。"""

    def __init__(
        self,
        http: PluginHttpClient,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http
        self._sleep = sleep

    def validate(self, access: TmdbAccess) -> None:
        self._get(access, "/configuration")

    def search(self, access: TmdbAccess, kind: TmdbKind, query: str) -> list[TmdbSearchResult]:
        data = self._get(
            access,
            f"/search/{_PATHS[kind]}",
            {"query": query, "language": LANGUAGE, "include_adult": "false"},
        )
        results = (_search_result(item, kind) for item in _list(data.get("results")))
        return [result for result in results if result is not None]

    def find_imdb(self, access: TmdbAccess, kind: TmdbKind, imdb_id: str) -> int | None:
        data = self._get(access, f"/find/{imdb_id}", {"external_source": "imdb_id"})
        results = _list(data.get("movie_results" if kind == "movie" else "tv_results"))
        for item in results:
            tmdb_id = _integer(item.get("id"))
            if tmdb_id is not None:
                return tmdb_id
        return None

    def details(self, access: TmdbAccess, kind: TmdbKind, tmdb_id: int) -> TmdbDetails:
        # 剧集用 aggregate_credits：credits 只有最新一季的演员
        credits_key = "credits" if kind == "movie" else "aggregate_credits"
        ratings_key = "release_dates" if kind == "movie" else "content_ratings"
        append = f"{credits_key},translations,images,{ratings_key}"
        if kind == "series":
            # 剧集的外部 ID 只在 external_ids 里，详情顶层没有 imdb_id
            append += ",external_ids"
        data = self._get(
            access,
            f"/{_PATHS[kind]}/{tmdb_id}",
            {
                "language": LANGUAGE,
                "append_to_response": append,
                "include_image_language": "zh,en,null",
            },
        )
        title_key, original_key, date_key = _keys(kind)
        translations = _translations(data)
        zh = translations.get(("zh", "CN"), {})
        en = translations.get(("en", "US"), {})
        original = _text(data.get(original_key))
        en_title = _text(en.get(title_key))
        # 没有中文时依次用英文、原名顶上，顶上的字段要记下来，补缺插件可以再覆盖
        fallback: set[str] = set()
        title = _text(zh.get(title_key))
        if title is None and str(data.get("original_language", "")).startswith(("zh", "cn")):
            # 华语片的原名就是中文，TMDB 常常不再单独填 zh-CN 的译名
            title = original
        if title is None:
            if en_title is not None:
                title = en_title
                fallback.add("title")
            elif original is not None:
                title = original
                fallback.add("title")
            else:
                title = _text(data.get(title_key))
                if title is not None:
                    fallback.add("title")
        overview = _text(data.get("overview"))
        zh_overview = _text(zh.get("overview"))
        en_overview = _text(en.get("overview"))
        if overview is None:
            if zh_overview is not None:
                overview = zh_overview
            elif en_overview is not None:
                overview = en_overview
                fallback.add("overview")
        elif zh_overview is None and en_overview is not None and overview == en_overview:
            # TMDB 在请求语言缺失时会直接在顶层返回英文
            fallback.add("overview")
        tagline = _text(data.get("tagline"))
        zh_tagline = _text(zh.get("tagline"))
        en_tagline = _text(en.get("tagline"))
        if tagline is None:
            if zh_tagline is not None:
                tagline = zh_tagline
            elif en_tagline is not None:
                tagline = en_tagline
                fallback.add("tagline")
        elif zh_tagline is None and en_tagline is not None and tagline == en_tagline:
            fallback.add("tagline")
        credits = _obj(data.get(credits_key))
        if kind == "movie":
            runtime = _integer(data.get("runtime"))
            directors = _casts(
                [person for person in _list(credits.get("crew")) if person.get("job") == "Director"]
            )
        else:
            runtimes = _items(data.get("episode_run_time"))
            runtime = _integer(runtimes[0]) if runtimes else None
            directors = _casts(_list(data.get("created_by")))
        cast = _casts(_list(credits.get("cast")))
        writers = _casts([person for person in _list(credits.get("crew")) if _is_writer(person)])
        images = _obj(data.get("images"))
        external = _obj(data.get("external_ids"))
        imdb_id = (
            _id_text(data.get("imdb_id")) if kind == "movie" else _id_text(external.get("imdb_id"))
        )
        tvdb_id = None if kind == "movie" else _id_text(external.get("tvdb_id"))
        external_ids: dict[str, str] = {}
        if imdb_id is not None:
            external_ids["imdb"] = imdb_id
        if tvdb_id is not None:
            external_ids["tvdb"] = tvdb_id
        return TmdbDetails(
            tmdb_id=_required_id(data),
            kind=kind,
            title=title or str(tmdb_id),
            original_title=original,
            en_title=en_title,
            year=_year(data.get(date_key)),
            release_date=_text(data.get(date_key)),
            overview=overview,
            rating=_rating(data.get("vote_average")),
            tagline=tagline,
            genres=[
                name
                for genre in _list(data.get("genres"))
                if (name := _text(genre.get("name"))) is not None
            ],
            studios=[
                name
                for company in _list(data.get("production_companies"))
                if (name := _text(company.get("name"))) is not None
            ],
            countries=[
                name
                for country in _list(data.get("production_countries"))
                if (name := _text(country.get("name")) or _text(country.get("iso_3166_1")))
                is not None
            ],
            tmdb_collection_id=(
                _integer(_obj(data.get("belongs_to_collection")).get("id"))
                if kind == "movie"
                else None
            ),
            tmdb_collection_name=_text(_obj(data.get("belongs_to_collection")).get("name"))
            if kind == "movie"
            else None,
            tmdb_collection_poster=_text(_obj(data.get("belongs_to_collection")).get("poster_path"))
            if kind == "movie"
            else None,
            tmdb_collection_backdrop=_text(
                _obj(data.get("belongs_to_collection")).get("backdrop_path")
            )
            if kind == "movie"
            else None,
            content_rating=_content_rating(data, kind),
            runtime=runtime,
            poster_path=_text(data.get("poster_path")),
            backdrop_path=_text(data.get("backdrop_path")),
            thumb_path=_pick_image(_list(images.get("backdrops"))),
            logo_path=_pick_image(_list(images.get("logos")), png=True),
            artwork={
                "poster": _image_paths(_list(images.get("posters"))),
                "backdrop": _image_paths(_list(images.get("backdrops"))),
                "thumb": _image_paths(_list(images.get("backdrops"))),
                "logo": _image_paths(_list(images.get("logos")), png=True),
            },
            cast=cast[:MAX_CAST],
            directors=_unique(directors),
            writers=_unique(writers),
            external_ids=external_ids,
            fallback_fields=frozenset(fallback),
        )

    def images(self, access: TmdbAccess, kind: TmdbKind, tmdb_id: int) -> list[TmdbImage]:
        """不按语言过滤；有语言的横版图按现有 Thumb 的语义归类。"""
        data = self._get(access, f"/{_PATHS[kind]}/{tmdb_id}/images")
        result: list[TmdbImage] = []
        for item in _list(data.get("posters")):
            _append_image(result, item, "poster")
        for item in _list(data.get("backdrops")):
            if _text(item.get("iso_639_1")) is not None:
                _append_image(result, item, "thumb")
            else:
                _append_image(result, item, "backdrop")
        for item in _list(data.get("logos")):
            _append_image(result, item, "logo")
        return result

    def season(self, access: TmdbAccess, tmdb_id: int, number: int) -> TmdbSeason:
        path = f"/tv/{tmdb_id}/season/{number}"
        data = self._get(access, path, {"language": LANGUAGE})
        episodes = _list(data.get("episodes"))
        fallback: dict[int, dict[str, Any]] = {}
        if any(not _text(e.get("overview")) or not _text(e.get("name")) for e in episodes):
            english = self._get(access, path, {"language": FALLBACK_LANGUAGE})
            fallback = {
                episode_number: episode
                for episode in _list(english.get("episodes"))
                if (episode_number := _integer(episode.get("episode_number"))) is not None
            }
        season_number = _integer(data.get("season_number"))
        return TmdbSeason(
            number=season_number if season_number is not None else number,
            name=_text(data.get("name")),
            overview=_text(data.get("overview")),
            air_date=_text(data.get("air_date")),
            poster_path=_text(data.get("poster_path")),
            episodes=[
                episode for item in episodes if (episode := _episode(item, fallback)) is not None
            ],
        )

    def person(self, access: TmdbAccess, tmdb_id: int) -> TmdbPerson:
        data = self._get(
            access,
            f"/person/{tmdb_id}",
            {"language": LANGUAGE, "append_to_response": "translations"},
        )
        translations = _translations(data)
        zh = translations.get(("zh", "CN"), {})
        en = translations.get(("en", "US"), {})
        name = str(data.get("name") or "")
        aliases = [a for a in _items(data.get("also_known_as")) if isinstance(a, str)]
        # TMDB 的姓名多是英文写法，中文写法常见于 zh-CN 翻译或别名
        local = _text(zh.get("name")) or next((a.strip() for a in aliases if _has_cjk(a)), None)
        return TmdbPerson(
            tmdb_id=_required_id(data),
            name=local or name or str(tmdb_id),
            original_name=name if local and local != name else None,
            biography=_text(data.get("biography"))
            or _text(zh.get("biography"))
            or _text(en.get("biography")),
            birthday=_text(data.get("birthday")),
            deathday=_text(data.get("deathday")),
            place_of_birth=_text(data.get("place_of_birth")),
        )

    def image(self, access: TmdbAccess, path: str, width: int) -> bytes:
        response = self._send(access, f"{IMAGE_BASE}/w{width}{path}", {}, authorize=False)
        return response.content

    # ---- 请求 ----

    def _get(
        self, access: TmdbAccess, path: str, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        response = self._send(access, f"{API_BASE}{path}", dict(params or {}), authorize=True)
        try:
            return json_object(response, service="TMDB")
        except MetadataUnavailable as exc:
            raise TmdbUnavailable(exc.message) from exc

    def _send(
        self, access: TmdbAccess, url: str, params: dict[str, str], *, authorize: bool
    ) -> PluginHttpResponse:
        headers = {"Accept": "application/json"}
        if authorize:
            if _is_bearer(access.key):
                headers["Authorization"] = f"Bearer {access.key}"
            else:
                params["api_key"] = access.key
        for attempt in range(2):
            try:
                response = self._http.get(url, params=params, headers=headers)
            except httpx2.ProxyError as exc:
                raise TmdbUnavailable("代理不可用，请检查代理地址") from exc
            except httpx2.TimeoutException as exc:
                raise TmdbUnavailable("连接 TMDB 超时，请检查网络或代理") from exc
            except httpx2.RequestError as exc:
                raise TmdbUnavailable("连不上 TMDB，请检查网络或代理") from exc
            if response.status_code == 429 and attempt == 0:
                self._sleep(_retry_after(response.headers))
                continue
            break
        _raise_for_status(response)
        return response


_JWT = re.compile(r"^[\w-]+\.[\w-]+\.[\w-]+$")


def _raise_for_status(response: PluginHttpResponse) -> None:
    """TMDB 只对 401 和 404 有专门语义，其余交给 SDK 的状态码映射。"""
    if response.status_code == 401:
        raise TmdbAuthFailed()
    if response.status_code == 404:
        raise TmdbNotFound()
    try:
        raise_for_status(response, service="TMDB")
    except MetadataUnavailable as exc:
        raise TmdbUnavailable(exc.message) from exc


def _is_bearer(key: str) -> bool:
    """v4 读取令牌是 JWT（三段点分），v3 API Key 是 32 位十六进制。"""
    return _JWT.match(key) is not None


def _keys(kind: TmdbKind) -> tuple[str, str, str]:
    if kind == "movie":
        return "title", "original_title", "release_date"
    return "name", "original_name", "first_air_date"


def _search_result(item: dict[str, Any], kind: TmdbKind) -> TmdbSearchResult | None:
    tmdb_id = _integer(item.get("id"))
    if tmdb_id is None:
        return None
    title_key, original_key, date_key = _keys(kind)
    return TmdbSearchResult(
        tmdb_id=tmdb_id,
        title=_text(item.get(title_key)) or _text(item.get(original_key)) or "",
        original_title=_text(item.get(original_key)),
        year=_year(item.get(date_key)),
        overview=_text(item.get("overview")),
    )


def _obj(value: Any) -> dict[str, Any]:
    """TMDB 的容器字段可能是 null 或缺省，非 dict 一律按空对象处理。"""
    return value if isinstance(value, dict) else {}


def _items(value: Any) -> list[Any]:
    """列表字段可能是 null 或缺省，非 list 一律按空列表处理。"""
    return value if isinstance(value, list) else []


def _list(value: Any) -> list[dict[str, Any]]:
    """只保留列表里的 dict 项，单个坏条目不影响整份响应。"""
    return [item for item in _items(value) if isinstance(item, dict)]


def _required_id(data: dict[str, Any]) -> int:
    """顶层详情自身必须带整数 id；否则视为条目不可用，不让 KeyError 冒出去。"""
    tmdb_id = _integer(data.get("id"))
    if tmdb_id is None:
        raise TmdbNotFound()
    return tmdb_id


def _translations(data: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for item in _list(_obj(data.get("translations")).get("translations")):
        key = (str(item.get("iso_639_1", "")), str(item.get("iso_3166_1", "")))
        result[key] = _obj(item.get("data"))
    return result


def _content_rating(data: dict[str, Any], kind: TmdbKind) -> str | None:
    """电影的分级在 release_dates 的各次上映里，剧集的在 content_ratings 里。"""
    by_region: dict[str, str] = {}
    if kind == "movie":
        for region in _list(_obj(data.get("release_dates")).get("results")):
            for release in _list(region.get("release_dates")):
                rating = _text(release.get("certification"))
                if rating:
                    by_region.setdefault(str(region.get("iso_3166_1", "")), rating)
                    break
    else:
        for region in _list(_obj(data.get("content_ratings")).get("results")):
            rating = _text(region.get("rating"))
            if rating:
                by_region.setdefault(str(region.get("iso_3166_1", "")), rating)
    return next((by_region[r] for r in _RATING_REGIONS if r in by_region), None)


def _episode(data: dict[str, Any], fallback: dict[int, dict[str, Any]]) -> TmdbEpisode | None:
    number = _integer(data.get("episode_number"))
    if number is None:
        return None
    translated = fallback.get(number, {})
    return TmdbEpisode(
        number=number,
        name=_text(data.get("name")) or _text(translated.get("name")),
        overview=_text(data.get("overview")) or _text(translated.get("overview")),
        air_date=_text(data.get("air_date")),
        runtime=_integer(data.get("runtime")),
        rating=_rating(data.get("vote_average")),
        still_path=_text(data.get("still_path")),
    )


def _is_writer(person: dict[str, Any]) -> bool:
    # 剧集的 aggregate_credits 把职务放在 jobs 列表里
    if person.get("job") in WRITER_JOBS:
        return True
    return any(job.get("job") in WRITER_JOBS for job in _list(person.get("jobs")))


def _cast(data: dict[str, Any]) -> TmdbCast | None:
    tmdb_id = _integer(data.get("id"))
    if tmdb_id is None:
        return None
    character = _text(data.get("character"))
    if character is None:
        # aggregate_credits 的角色在 roles 里，按出场集数排序
        roles = _list(data.get("roles"))
        character = _text(roles[0].get("character")) if roles else None
    return TmdbCast(
        tmdb_id=tmdb_id,
        name=str(data.get("name") or ""),
        profile_path=_text(data.get("profile_path")),
        character=character,
    )


def _casts(items: list[dict[str, Any]]) -> list[TmdbCast]:
    people = (_cast(item) for item in items)
    return [person for person in people if person is not None]


def _pick_image(images: list[dict[str, Any]], *, png: bool = False) -> str | None:
    """按语言（中文优先，其次英文）和评分挑一张图。片名图要 PNG：TMDB 也有 SVG。"""
    best: tuple[int, float] | None = None
    path = None
    for image in images:
        language = image.get("iso_639_1")
        file_path = _text(image.get("file_path"))
        if language not in _IMAGE_LANGUAGES or file_path is None:
            continue
        if png and not file_path.lower().endswith(".png"):
            continue
        score = (-_IMAGE_LANGUAGES.index(language), float(image.get("vote_average") or 0))
        if best is None or score > best:
            best, path = score, file_path
    return path


def _image_paths(images: list[dict[str, Any]], *, png: bool = False) -> list[str]:
    """候选图按中文、无文字、英文和其他语言排序，去重并限制数量。"""
    ranked: list[tuple[int, float, str]] = []
    for image in images:
        path = _text(image.get("file_path"))
        if path is None or (png and not path.lower().endswith(".png")):
            continue
        language = image.get("iso_639_1")
        rank = {"zh": 0, None: 1, "en": 2}.get(language, 3)
        ranked.append((rank, -float(image.get("vote_average") or 0), path))
    ranked.sort()
    return list(dict.fromkeys(path for _, _, path in ranked))[:30]


def _append_image(result: list[TmdbImage], item: dict[str, Any], image_type: TmdbImageType) -> None:
    path = _text(item.get("file_path"))
    if path is None:
        return
    result.append(
        TmdbImage(
            type=image_type,
            path=path,
            language=_text(item.get("iso_639_1")),
            score=_score(item.get("vote_average")),
            width=_integer(item.get("width")),
            height=_integer(item.get("height")),
        )
    )


def _id_text(value: Any) -> str | None:
    """外部 ID 在 TMDB 里可能是数字（tvdb_id）或字符串（imdb_id）。"""
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return _text(value)


def _score(value: Any) -> float | None:
    return (
        round(float(value), 3)
        if isinstance(value, int | float) and not isinstance(value, bool)
        else None
    )


def _integer(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return int(value) if isinstance(value, float) and value.is_integer() else None


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= c <= "\u9fff" for c in text)


def _unique(people: list[TmdbCast]) -> list[TmdbCast]:
    seen: set[int] = set()
    result = []
    for person in people:
        if person.tmdb_id not in seen:
            seen.add(person.tmdb_id)
            result.append(person)
    return result


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _year(value: Any) -> int | None:
    text = _text(value)
    return int(text[:4]) if text and text[:4].isdigit() else None


def _rating(value: Any) -> float | None:
    # TMDB 用 0 表示还没有人评分
    return round(float(value), 1) if isinstance(value, int | float) and value > 0 else None


def _retry_after(headers: Mapping[str, str]) -> float:
    value = next((item for key, item in headers.items() if key.casefold() == "retry-after"), "1")
    try:
        seconds = float(value)
    except ValueError:
        seconds = 1.0
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_SECONDS)
