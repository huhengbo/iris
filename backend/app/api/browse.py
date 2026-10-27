"""浏览接口：普通用户和管理员共用，按可见范围过滤，范围外的内容一律返回 404（ADR 0009、0014）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.schemas import ImageOut, Out
from app.deps import (
    AppClock,
    AppDatabase,
    CurrentUser,
    MetadataPlugins,
    ReadDb,
)
from app.errors import NotFound
from app.library.browse import BrowseQueries
from app.library.browse_models import (
    CreditRole,
    HdrFormat,
    HubRow,
    ItemCard,
    ItemDetail,
    PersonDetail,
    ProblemStatus,
    SearchPage,
    SortKey,
    SortOrder,
)
from app.library.people import ensure_person_detail
from app.library.recognize import LibraryKind
from app.library.review import Page

router = APIRouter(tags=["browse"])

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 60
MAX_QUERY_LENGTH = 100

Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]


class LibraryOut(Out):
    id: int
    name: str
    kind: LibraryKind
    item_count: int
    # 横版封面：管理员上传的，没有时取最近入库条目的背景图；都没有时为空
    cover: list[ImageOut]


class CategoryOut(Out):
    name: str
    item_count: int
    # 代表图：优先背景图，没有时用海报
    image: list[ImageOut]


class LibraryDetailOut(LibraryOut):
    genres: list[str]
    years: list[int]
    # 「分类」标签的类型卡片，按条目数从多到少
    categories: list[CategoryOut]


class ItemCardOut(Out):
    id: int
    library_id: int
    kind: LibraryKind
    title: str
    original_title: str | None
    year: int | None
    rating: float | None
    genres: list[str]
    runtime: int | None
    poster: list[ImageOut]
    backdrop: list[ImageOut]
    # 电影已看；剧集的全部集都已看。只反映当前用户自己的标记
    watched: bool


class SearchPersonOut(Out):
    id: int
    name: str
    original_name: str | None
    profile: list[ImageOut]


class SearchEpisodeOut(Out):
    id: int
    item_id: int
    season_id: int
    season_number: int
    number: int
    name: str | None
    show_title: str
    still: list[ImageOut]


class ItemCardPage(Out):
    items: list[ItemCardOut]
    total: int
    people: list[SearchPersonOut]
    episodes: list[SearchEpisodeOut]

    @classmethod
    def of(cls, page: Page[ItemCard]) -> ItemCardPage:
        return cls(
            items=[ItemCardOut.model_validate(i) for i in page.items],
            total=page.total,
            people=[],
            episodes=[],
        )

    @classmethod
    def of_search(cls, page: SearchPage) -> ItemCardPage:
        return cls(
            items=[ItemCardOut.model_validate(i) for i in page.items],
            total=page.total,
            people=[SearchPersonOut.model_validate(i) for i in page.people],
            episodes=[SearchEpisodeOut.model_validate(i) for i in page.episodes],
        )


class ProgressOut(Out):
    """App 内置播放器上报的续播位置（秒）。没有续播位置时整个字段为 null。"""

    position: int
    duration: int


class EpisodeRefOut(Out):
    id: int
    season_id: int
    season_number: int
    number: int
    name: str | None
    progress: ProgressOut | None


class RecentPlayOut(Out):
    item: ItemCardOut
    # 剧集最近播放的那一集和它的剧照；电影为空
    episode: EpisodeRefOut | None
    still: list[ImageOut]
    played_at: str
    # 最近播放的电影或那一集的续播位置
    progress: ProgressOut | None


class HubRowOut(Out):
    """资料库「推荐」标签的一行。继续观看行用 recent，其余用 items。"""

    kind: str
    arg: str | None
    title: str
    items: list[ItemCardOut]
    recent: list[RecentPlayOut]

    @classmethod
    def of(cls, row: HubRow) -> HubRowOut:
        return cls(
            kind=row.kind,
            arg=row.arg,
            title=row.title,
            items=[ItemCardOut.model_validate(item) for item in row.items],
            recent=[RecentPlayOut.model_validate(play) for play in row.recent],
        )


class HomeHubShelfOut(Out):
    library: LibraryOut
    hub: HubRowOut


class HomeOut(Out):
    libraries: list[LibraryOut]
    # 当前用户最近播放的作品，每部只出现一次，从新到旧
    recent: list[RecentPlayOut]
    # 最近收藏的电影和剧集，最多 20 条
    favorites: list[ItemCardOut]
    # 最近加入稍后观看的电影和剧集，最多 20 条
    watchlist: list[ItemCardOut]
    # 各资料库配置为在首页显示的推荐行，按资料库顺序再按行顺序
    shelves: list[HomeHubShelfOut]


class CreditOut(Out):
    person_id: int
    name: str
    character: str | None
    profile: list[ImageOut]


class AudioTrackOut(Out):
    language: str | None
    title: str | None
    codec: str | None
    channels: int | None
    default: bool


class EmbeddedSubtitleOut(Out):
    language: str | None
    title: str | None
    codec: str | None
    forced: bool
    default: bool


class MediaInfoOut(Out):
    """内置播放器播过之后才有（ADR 0019）。"""

    # 秒
    duration: int | None
    width: int | None
    height: int | None
    video_codec: str | None
    hdr: HdrFormat | None
    audio: list[AudioTrackOut]
    # 内封字幕，外挂字幕在 VersionOut.subtitles
    subtitles: list[EmbeddedSubtitleOut]


class VersionOut(Out):
    id: int
    label: str
    size: int | None
    subtitles: list[SubtitleOut]
    # 没人用内置播放器播过、或文件换过之后还没播过时为 null
    media: MediaInfoOut | None


class SubtitleOut(Out):
    id: int
    label: str
    language: str | None
    forced: bool
    source: str


class SeasonOut(Out):
    id: int
    number: int
    name: str | None
    air_date: str | None
    poster: list[ImageOut]
    episode_count: int


class RatingOut(Out):
    # 评分来源的插件 ID，例如 builtin.tmdb
    source: str
    # 展示名，例如 TMDB、豆瓣
    label: str
    value: float


class ItemDetailOut(ItemCardOut):
    en_title: str | None
    release_date: str | None
    overview: str | None
    # 内容分级，如 TV-MA、PG-13；只用于展示，没有访问限制
    content_rating: str | None
    # 制作公司和制作国家；没有时为空列表
    studios: list[str]
    countries: list[str]
    thumb: list[ImageOut]
    logo: list[ImageOut]
    directors: list[CreditOut]
    writers: list[CreditOut]
    cast: list[CreditOut]
    # 电影的版本，大的在前；剧集为空，版本在每一集上
    versions: list[VersionOut]
    # 剧集的季，特别篇在最后；电影为空
    seasons: list[SeasonOut]
    # 剧集推荐接着看的一集：最近播放且没看完的那集，否则往后第一集没看的
    next_episode: EpisodeRefOut | None
    # 电影的续播位置；剧集看 next_episode.progress
    progress: ProgressOut | None
    # 当前用户是否收藏了这部电影或剧集
    favorite: bool
    # 当前用户是否已加入稍后观看
    watchlisted: bool
    # 各来源的评分，主插件在前；没有多来源评分时只有主插件一条或为空
    ratings: list[RatingOut]

    @classmethod
    def of(cls, detail: ItemDetail) -> ItemDetailOut:
        return cls(
            **ItemCardOut.model_validate(detail.card).model_dump(),
            en_title=detail.en_title,
            release_date=detail.release_date,
            overview=detail.overview,
            content_rating=detail.content_rating,
            studios=detail.studios,
            countries=detail.countries,
            thumb=[ImageOut.model_validate(i) for i in detail.thumb],
            logo=[ImageOut.model_validate(i) for i in detail.logo],
            directors=[CreditOut.model_validate(c) for c in detail.directors],
            writers=[CreditOut.model_validate(c) for c in detail.writers],
            cast=[CreditOut.model_validate(c) for c in detail.cast],
            versions=[VersionOut.model_validate(v) for v in detail.versions],
            seasons=[SeasonOut.model_validate(s) for s in detail.seasons],
            next_episode=(
                EpisodeRefOut.model_validate(detail.next_episode) if detail.next_episode else None
            ),
            progress=ProgressOut.model_validate(detail.progress) if detail.progress else None,
            favorite=detail.favorite,
            watchlisted=detail.watchlisted,
            ratings=[RatingOut.model_validate(rating) for rating in detail.ratings],
        )


class EpisodeOut(Out):
    id: int
    number: int
    name: str | None
    overview: str | None
    air_date: str | None
    runtime: int | None
    rating: float | None
    still: list[ImageOut]
    versions: list[VersionOut]
    watched: bool
    progress: ProgressOut | None
    # 当前用户是否收藏了这一集
    favorite: bool


class SeasonDetailOut(Out):
    id: int
    item_id: int
    number: int
    name: str | None
    overview: str | None
    air_date: str | None
    poster: list[ImageOut]
    episodes: list[EpisodeOut]


class PersonWorkOut(ItemCardOut):
    roles: list[CreditRole]
    character: str | None


class PersonOut(Out):
    id: int
    # 人物所属的元数据插件和插件里的人物 ID
    provider_id: str
    remote_id: str
    name: str
    original_name: str | None
    biography: str | None
    birthday: str | None
    deathday: str | None
    place_of_birth: str | None
    profile: list[ImageOut]
    # 为 false 表示还没拿到插件上的资料（插件不可用或请求失败），只有姓名和头像
    detailed: bool
    # 可见范围内参演或执导的作品，按年份从新到旧
    works: list[PersonWorkOut]
    # 当前用户是否收藏了这个人
    favorite: bool

    @classmethod
    def of(cls, person: PersonDetail) -> PersonOut:
        return cls(
            id=person.id,
            provider_id=person.provider_id,
            remote_id=person.remote_id,
            name=person.name,
            original_name=person.original_name,
            biography=person.biography,
            birthday=person.birthday,
            deathday=person.deathday,
            place_of_birth=person.place_of_birth,
            profile=[ImageOut.model_validate(i) for i in person.profile],
            detailed=person.detailed,
            works=[
                PersonWorkOut(
                    **ItemCardOut.model_validate(w.card).model_dump(),
                    roles=w.roles,
                    character=w.character,
                )
                for w in person.works
            ],
            favorite=person.favorite,
        )


@router.get("/home")
def home(user: CurrentUser, db: ReadDb) -> HomeOut:
    return HomeOut.model_validate(BrowseQueries(db, user).home())


@router.get("/libraries")
def list_libraries(user: CurrentUser, db: ReadDb) -> list[LibraryOut]:
    return [LibraryOut.model_validate(lib) for lib in BrowseQueries(db, user).libraries()]


@router.get("/libraries/{library_id}")
def get_library(library_id: int, user: CurrentUser, db: ReadDb) -> LibraryDetailOut:
    detail = BrowseQueries(db, user).library(library_id)
    return LibraryDetailOut(
        **LibraryOut.model_validate(detail.library).model_dump(),
        genres=detail.genres,
        years=detail.years,
        categories=[CategoryOut.model_validate(category) for category in detail.categories],
    )


@router.get("/libraries/{library_id}/hubs")
def library_hubs(library_id: int, user: CurrentUser, db: ReadDb) -> list[HubRowOut]:
    """资料库「推荐」标签的行；空行和服务端配置里关掉的行不返回。"""
    return [HubRowOut.of(row) for row in BrowseQueries(db, user).library_hubs(library_id)]


@router.get("/libraries/{library_id}/items")
def list_library_items(
    library_id: int,
    user: CurrentUser,
    db: ReadDb,
    sort: SortKey = "added",
    # 不传时按排序方式的默认方向：名称从 A 到 Z，其余从新到旧、从高到低
    order: SortOrder | None = None,
    genre: Annotated[str | None, Query(max_length=50)] = None,
    year: Annotated[int | None, Query(ge=1800, le=3000)] = None,
    # 只看已看完（true）或还没看完（false）的；不传时不过滤
    watched: bool | None = None,
    # 只看已收藏的条目；不传时不过滤
    favorite: bool | None = None,
    # 只看稍后观看里的条目；不传时不过滤
    watchlist: bool | None = None,
    status: ProblemStatus | None = None,
    # 按名称排序时的 A-Z 跳转：拉丁字母或 #（非拉丁字母）
    letter: Annotated[str | None, Query(pattern="^[A-Za-z#]$")] = None,
    offset: Offset = 0,
    limit: Limit = DEFAULT_PAGE_SIZE,
) -> ItemCardPage:
    if status is not None and not user.is_admin:
        from app.errors import AppError

        raise AppError(403, "forbidden", "问题筛选仅管理员可用")
    page = BrowseQueries(db, user).library_items(
        library_id,
        sort=sort,
        order=order,
        genre=genre or None,
        year=year,
        watched=watched,
        favorite=favorite,
        watchlist=watchlist,
        status=status,
        letter=letter.upper() if letter else None,
        offset=offset,
        limit=limit,
    )
    return ItemCardPage.of(page)


@router.get("/items/{item_id}")
def get_item(item_id: int, user: CurrentUser, db: ReadDb) -> ItemDetailOut:
    return ItemDetailOut.of(BrowseQueries(db, user).item(item_id))


@router.get("/seasons/{season_id}")
def get_season(season_id: int, user: CurrentUser, db: ReadDb) -> SeasonDetailOut:
    return SeasonDetailOut.model_validate(BrowseQueries(db, user).season(season_id))


@router.get("/people/{person_id}")
def get_person(
    person_id: int,
    user: CurrentUser,
    database: AppDatabase,
    clock: AppClock,
    providers: MetadataPlugins,
) -> PersonOut:
    """第一次打开时从元数据插件拉取简介等资料并缓存；拉取前先确认人物在可见范围内。"""
    with database.read_session() as connection:
        if not BrowseQueries(connection, user).person_visible(person_id):
            raise NotFound("找不到这个人")
    ensure_person_detail(database, clock, providers, person_id)
    with database.read_session() as connection:
        return PersonOut.of(BrowseQueries(connection, user).person(person_id))


@router.get("/search")
def search(
    user: CurrentUser,
    db: ReadDb,
    q: Annotated[str, Query(min_length=1, max_length=MAX_QUERY_LENGTH)],
    offset: Offset = 0,
    limit: Limit = DEFAULT_PAGE_SIZE,
    favorite: bool | None = None,
) -> ItemCardPage:
    query = q.strip()
    if not query:
        return ItemCardPage(items=[], total=0, people=[], episodes=[])
    return ItemCardPage.of_search(
        BrowseQueries(db, user).search_all(query, offset, limit, favorite=favorite)
    )
