"""TMDB 的数据结构与网关协议。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

TmdbKind = Literal["movie", "series"]
TmdbImageType = Literal["poster", "backdrop", "thumb", "logo"]
TMDB_SITE = "https://www.themoviedb.org"


def tmdb_page_url(kind: TmdbKind, tmdb_id: int) -> str:
    """TMDB 网站上的条目页，管理员手动指定前可以打开核对。"""
    return f"{TMDB_SITE}/{'movie' if kind == 'movie' else 'tv'}/{tmdb_id}"


@dataclass(frozen=True)
class TmdbAccess:
    """访问 TMDB 所需的凭据。key 可以是 v3 API Key，也可以是 v4 读取令牌。"""

    key: str


@dataclass(frozen=True)
class TmdbSearchResult:
    tmdb_id: int
    title: str
    original_title: str | None
    year: int | None
    overview: str | None


@dataclass(frozen=True)
class TmdbCast:
    tmdb_id: int
    name: str
    profile_path: str | None
    character: str | None


@dataclass(frozen=True)
class TmdbDetails:
    tmdb_id: int
    kind: TmdbKind
    # 中文标题，没有中文时是英文标题，再没有就是原名
    title: str
    original_title: str | None
    en_title: str | None
    year: int | None
    release_date: str | None
    overview: str | None
    rating: float | None
    tagline: str | None = None
    genres: list[str] = field(default_factory=list)
    studios: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    tmdb_collection_id: int | None = None
    # 所属系列的名称和图片（电影详情里随带，不用另外请求）
    tmdb_collection_name: str | None = None
    tmdb_collection_poster: str | None = None
    tmdb_collection_backdrop: str | None = None
    # 内容分级，如 TV-MA、PG-13，只用于展示
    content_rating: str | None = None
    runtime: int | None = None
    poster_path: str | None = None
    backdrop_path: str | None = None
    # 带片名的横版图（中文或英文的背景图）和透明底的片名图，参考 Emby 的 Thumb 和 Logo
    thumb_path: str | None = None
    logo_path: str | None = None
    # TMDB 返回的候选图片，供管理员手动选择；键为 poster/backdrop/thumb/logo。
    artwork: dict[str, list[str]] = field(default_factory=dict)
    # 演员按 TMDB 的顺序；导演（剧集是主创）
    cast: list[TmdbCast] = field(default_factory=list)
    directors: list[TmdbCast] = field(default_factory=list)
    # 编剧：crew 里 job 为 Writer、Screenplay、Story 的人物
    writers: list[TmdbCast] = field(default_factory=list)
    # 详情里的其他外部 ID（imdb、tvdb），用于补缺和按 ID 取图
    external_ids: dict[str, str] = field(default_factory=dict)
    # 没有请求语言的中文、由英文或原名顶上的文字字段（title、overview、tagline）
    fallback_fields: frozenset[str] = frozenset()


@dataclass(frozen=True)
class TmdbImage:
    """TMDB /images 返回的一张图；type 与 SDK 的 ImageType 一致。"""

    type: TmdbImageType
    path: str
    language: str | None
    score: float | None
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class TmdbEpisode:
    number: int
    name: str | None
    overview: str | None
    air_date: str | None
    runtime: int | None
    rating: float | None
    still_path: str | None


@dataclass(frozen=True)
class TmdbSeason:
    number: int
    name: str | None
    overview: str | None
    air_date: str | None
    poster_path: str | None
    episodes: list[TmdbEpisode] = field(default_factory=list)


@dataclass(frozen=True)
class TmdbPerson:
    tmdb_id: int
    # 中文名（TMDB 的 also_known_as 里有中文写法时），否则是 TMDB 的姓名
    name: str
    original_name: str | None
    biography: str | None
    birthday: str | None
    deathday: str | None
    place_of_birth: str | None


class TmdbGateway(Protocol):
    """访问 TMDB 的唯一入口。文字字段优先 zh-CN，为空时由实现回退到 en-US。

    错误归为 TmdbAuthFailed（Key 无效）、TmdbNotFound（条目不存在）和 TmdbUnavailable（其他）。
    网关自己不限速，调用方经过 TmdbService 统一限速。
    """

    def validate(self, access: TmdbAccess) -> None:
        """用一次轻量请求确认 Key 和代理可用。"""
        ...

    def search(self, access: TmdbAccess, kind: TmdbKind, query: str) -> list[TmdbSearchResult]:
        """按片名搜索，返回第一页结果（TMDB 按相关度排序）。"""
        ...

    def find_imdb(self, access: TmdbAccess, kind: TmdbKind, imdb_id: str) -> int | None:
        """按 IMDb ID 查 TMDB ID，没有对应条目时返回 None。"""
        ...

    def details(self, access: TmdbAccess, kind: TmdbKind, tmdb_id: int) -> TmdbDetails: ...

    def images(self, access: TmdbAccess, kind: TmdbKind, tmdb_id: int) -> list[TmdbImage]:
        """条目的全部候选图，不按语言过滤；解释 type 和语言由调用方决定。"""
        ...

    def season(self, access: TmdbAccess, tmdb_id: int, number: int) -> TmdbSeason: ...

    def person(self, access: TmdbAccess, tmdb_id: int) -> TmdbPerson: ...

    def image(self, access: TmdbAccess, path: str, width: int) -> bytes:
        """下载 TMDB 图片的 w{width} 尺寸。"""
        ...
