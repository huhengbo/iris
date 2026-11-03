"""浏览结果的数据类：影视墙卡片、首页、详情、演员页和搜索的返回结构（ADR 0014）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.library.images import ImageSize
from app.library.recognize import LibraryKind

SortKey = Literal["added", "release", "rating", "name"]
SortOrder = Literal["asc", "desc"]
ProblemStatus = Literal["no_poster", "multiple_versions", "missing", "recent_metadata"]
CreditRole = Literal["cast", "director", "writer"]


@dataclass(frozen=True)
class LibrarySummary:
    id: int
    name: str
    kind: LibraryKind
    item_count: int
    # 横版封面各档尺寸；资料库里还没有图片时为空
    cover: list[ImageSize]
    cover_hash: str | None


@dataclass(frozen=True)
class CategorySummary:
    """「分类」标签的一张类型卡片：类型名、条目数和一张代表图。"""

    name: str
    item_count: int
    # 代表条目的背景图或海报，都没有时为空
    image: list[ImageSize]
    image_hash: str | None


@dataclass(frozen=True)
class CollectionCover:
    """合集对当前用户可见的成员数，和第一个有海报的成员的海报。"""

    item_count: int
    poster: list[ImageSize]
    # 合集详情页的头图
    backdrop: list[ImageSize]


@dataclass(frozen=True)
class LibraryDetail:
    library: LibrarySummary
    # 资料库里出现过的类型和年份，供海报墙筛选
    genres: list[str]
    years: list[int]
    # 「分类」标签的类型卡片，按条目数从多到少
    categories: list[CategorySummary] = field(default_factory=list)


@dataclass(frozen=True)
class DiscoverFilters:
    """按条件找片（AI 找片把问句转成它）。没填的条件不参与筛选；genres 满足任意一个即可。"""

    kind: LibraryKind | None = None
    genres: tuple[str, ...] = ()
    year_from: int | None = None
    year_to: int | None = None
    min_rating: float | None = None
    max_runtime: int | None = None
    watched: bool | None = None
    library_id: int | None = None


@dataclass(frozen=True)
class ItemCard:
    id: int
    library_id: int
    kind: LibraryKind
    title: str
    original_title: str | None
    year: int | None
    rating: float | None
    genres: list[str]
    runtime: int | None
    poster: list[ImageSize]
    backdrop: list[ImageSize]
    # 电影已看；剧集的全部集都已看
    watched: bool


@dataclass(frozen=True)
class Progress:
    """内置播放器上报的续播位置，单位秒。"""

    position: int
    duration: int


@dataclass(frozen=True)
class EpisodeRef:
    id: int
    season_id: int
    season_number: int
    number: int
    name: str | None
    progress: Progress | None


@dataclass(frozen=True)
class RecentPlay:
    item: ItemCard
    # 剧集最近播放的那一集；电影为 None
    episode: EpisodeRef | None
    still: list[ImageSize]
    played_at: str
    # 最近播放的电影或那一集的续播位置
    progress: Progress | None


@dataclass(frozen=True)
class HubRow:
    """资料库「推荐」标签的一行，也是首页各资料库推荐行的内容。"""

    kind: str
    arg: str | None
    title: str
    items: list[ItemCard]
    # 继续观看行用横版剧照卡；其余行为空
    recent: list[RecentPlay] = field(default_factory=list)


@dataclass(frozen=True)
class HubShelf:
    """首页上某个资料库的一个推荐行。"""

    library: LibrarySummary
    hub: HubRow


@dataclass(frozen=True)
class Home:
    libraries: list[LibrarySummary]
    # 每部作品只出现一次，按最近播放时间从新到旧
    recent: list[RecentPlay]
    # 最近收藏的电影和剧集
    favorites: list[ItemCard]
    # 最近加入稍后观看的电影和剧集
    watchlist: list[ItemCard]
    # 各资料库配置为在首页显示的推荐行，按资料库顺序再按行顺序
    shelves: list[HubShelf]


@dataclass(frozen=True)
class Credit:
    person_id: int
    name: str
    character: str | None
    profile: list[ImageSize]


@dataclass(frozen=True)
class Subtitle:
    id: int
    label: str
    language: str | None
    forced: bool
    source: str = "external"


@dataclass(frozen=True)
class AudioTrack:
    language: str | None
    title: str | None
    codec: str | None
    channels: int | None
    default: bool


@dataclass(frozen=True)
class EmbeddedSubtitle:
    language: str | None
    title: str | None
    codec: str | None
    forced: bool
    default: bool


HdrFormat = Literal["hdr10", "hlg", "dolby_vision"]


@dataclass(frozen=True)
class MediaInfo:
    """内置播放器打开文件后上报的流信息，服务端不探测文件。"""

    duration: int | None
    width: int | None
    height: int | None
    video_codec: str | None
    hdr: HdrFormat | None
    audio: list[AudioTrack]
    subtitles: list[EmbeddedSubtitle]


@dataclass(frozen=True)
class Version:
    id: int
    label: str
    size: int | None
    subtitles: list[Subtitle]
    # 还没有人用内置播放器播过，或文件换过之后还没播过时为 None
    media: MediaInfo | None = None


@dataclass(frozen=True)
class SeasonSummary:
    id: int
    number: int
    name: str | None
    air_date: str | None
    poster: list[ImageSize]
    episode_count: int


@dataclass(frozen=True)
class Rating:
    """详情页的一个评分来源，例如 TMDB 7.5、豆瓣 8.6。"""

    source: str
    label: str
    value: float


@dataclass(frozen=True)
class ItemDetail:
    card: ItemCard
    en_title: str | None
    release_date: str | None
    overview: str | None
    # 内容分级，如 TV-MA，只用于展示
    content_rating: str | None
    # 制作公司和制作国家，展示用
    studios: list[str]
    countries: list[str]
    thumb: list[ImageSize]
    logo: list[ImageSize]
    directors: list[Credit]
    writers: list[Credit]
    cast: list[Credit]
    # 电影的版本；剧集的版本在每一集上
    versions: list[Version]
    seasons: list[SeasonSummary]
    next_episode: EpisodeRef | None
    # 电影的续播位置；剧集看 next_episode 的
    progress: Progress | None
    # 各来源的评分，主插件在前
    ratings: list[Rating]
    # 当前用户是否已加入稍后观看
    watchlisted: bool
    # 当前用户是否收藏了这部电影或剧集
    favorite: bool


@dataclass(frozen=True)
class Episode:
    id: int
    number: int
    name: str | None
    overview: str | None
    air_date: str | None
    runtime: int | None
    rating: float | None
    still: list[ImageSize]
    versions: list[Version]
    watched: bool
    progress: Progress | None
    favorite: bool = False


@dataclass(frozen=True)
class SeasonDetail:
    id: int
    item_id: int
    number: int
    name: str | None
    overview: str | None
    air_date: str | None
    poster: list[ImageSize]
    episodes: list[Episode]


@dataclass(frozen=True)
class PersonWork:
    card: ItemCard
    roles: list[CreditRole]
    character: str | None


@dataclass(frozen=True)
class SearchPerson:
    id: int
    name: str
    original_name: str | None
    profile: list[ImageSize]


@dataclass(frozen=True)
class SearchEpisode:
    id: int
    item_id: int
    season_id: int
    season_number: int
    number: int
    name: str | None
    show_title: str
    still: list[ImageSize]


@dataclass(frozen=True)
class SearchPage:
    items: list[ItemCard]
    total: int
    people: list[SearchPerson]
    episodes: list[SearchEpisode]


@dataclass(frozen=True)
class PersonDetail:
    id: int
    provider_id: str
    remote_id: str
    name: str
    original_name: str | None
    biography: str | None
    birthday: str | None
    deathday: str | None
    place_of_birth: str | None
    profile: list[ImageSize]
    # 已经从 TMDB 拉过资料；为 False 时只有姓名和头像
    detailed: bool
    works: list[PersonWork]
    favorite: bool = False
