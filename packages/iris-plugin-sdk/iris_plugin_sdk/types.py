"""插件 SDK 的公共类型：协议、数据类和插件可以抛出的异常。

插件只依赖这个模块（以及其他 SDK 模块），不得导入宿主的 app.*。所有跨进程传递的值
都要经过 codec；这里的 Protocol 只在插件和宿主各自的进程内使用。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from iris_plugin_sdk.protocol import WaitReason

# 插件 manifest 声明的 sdk 范围必须包含这个版本。宿主和插件都按它做兼容检查。
SDK_VERSION = "1.0.0"
# 当前 SDK 的兼容范围；插件可以声明更窄的范围，但不能包含不兼容的大版本。
SDK_RANGE = ">=1.0,<2.0"

PROVIDED_CAPABILITIES = frozenset(
    {
        "metadata.search",
        "metadata.details",
        "metadata.images",
        "metadata.people",
        "image.fetch",
        "subtitle.search",
        "subtitle.download",
        "resource.source",
        # 管理后台的自定义页面和页面使用的接口
        "admin.ui",
    }
)
# 宿主能力只剩 logger 和可选的 http。logger 总是注入；http 由插件申请。
HOST_CAPABILITIES = frozenset({"http", "logger"})
# 资源来源可以按需声明的方法。核心只调用声明过的能力，不再用 inspect.signature 猜测。
RESOURCE_CAPABILITIES = frozenset({"status", "changes", "delete", "known_scan"})
# 插件 id 会用作目录名和日志名，只允许小写字母、数字、点、下划线和连字符
_PLUGIN_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_PAGE_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
_UI_PATH = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*")
_ENTRY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_SDK_CLAUSE = re.compile(r"(>=|<=|==|>|<)\s*([0-9]+(?:\.[0-9]+)*)")


class PluginManifestError(ValueError):
    """插件声明不符合 SDK 契约。"""


class PluginSdkIncompatibleError(PluginManifestError):
    """插件的 SDK 兼容范围不包含当前 SDK。"""


class InvalidSourceConfig(Exception):
    """来源配置不合法；validate_config 抛它，宿主映射为 invalid_source_config。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def safe_ui_path(path: str) -> bool:
    """插件 UI 目录内的相对路径：不允许绝对路径、.. 和隐藏文件。"""
    return bool(_UI_PATH.fullmatch(path)) and ".." not in path.split("/")


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = value.split(".")
    if not parts or any(not part.isdigit() for part in parts):
        raise PluginManifestError(f"版本号不合法：{value}")
    return tuple(int(part) for part in parts)


def _compare(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    length = max(len(left), len(right))
    padded_left = left + (0,) * (length - len(left))
    padded_right = right + (0,) * (length - len(right))
    return (padded_left > padded_right) - (padded_left < padded_right)


def sdk_compatible(spec: str, version: str = SDK_VERSION) -> bool:
    """检查 version 是否落在 spec 里。

    spec 是逗号分隔的比较式（>=1.0,<2.0）；不支持 ~=、通配符和本地版本号。写错格式
    直接抛 PluginManifestError，避免把非法范围当成「无限制」。
    """
    clauses = [clause.strip() for clause in spec.split(",")]
    if not spec.strip() or any(not clause for clause in clauses):
        raise PluginManifestError(f"SDK 兼容范围不合法：{spec}")
    current = _version_tuple(version)
    for clause in clauses:
        match = _SDK_CLAUSE.fullmatch(clause)
        if match is None:
            raise PluginManifestError(f"SDK 兼容范围不合法：{spec}")
        operator, raw = match.groups()
        target = _version_tuple(raw)
        result = _compare(current, target)
        if operator == ">=" and result < 0:
            return False
        if operator == ">" and result <= 0:
            return False
        if operator == "<=" and result > 0:
            return False
        if operator == "<" and result >= 0:
            return False
        if operator == "==" and result != 0:
            return False
    return True


@dataclass(frozen=True)
class PluginPage:
    """管理后台里插件的一个页面；entry 是 UI 目录内的入口文件。"""

    id: str
    title: str
    entry: str = "index.html"

    def __post_init__(self) -> None:
        if not _PAGE_ID.fullmatch(self.id):
            raise PluginManifestError("页面 id 只能包含小写字母、数字和连字符")
        if not self.title:
            raise PluginManifestError("页面标题不能为空")
        if not safe_ui_path(self.entry):
            raise PluginManifestError("页面入口必须是 UI 目录内的相对路径")


@dataclass(frozen=True)
class PluginManifest:
    """插件清单；唯一来源是插件目录下的 plugin.json。"""

    id: str
    name: str
    version: str
    provides: tuple[str, ...]
    # entry 是 `python -m <entry>` 使用的模块名。
    entry: str
    capabilities: tuple[str, ...] = ()
    sdk: str = SDK_RANGE
    # ui 是相对插件目录的静态文件目录；有页面时必须提供。
    ui: str | None = None
    pages: tuple[PluginPage, ...] = ()
    # 插件列表里展示的一句话简介
    description: str = ""

    def __post_init__(self) -> None:
        if not self.id or not self.name or not self.version:
            raise PluginManifestError("插件 id、名称和版本不能为空")
        if not _PLUGIN_ID.fullmatch(self.id) or ".." in self.id:
            raise PluginManifestError("插件 id 只能包含小写字母、数字、点、下划线和连字符")
        if not _ENTRY.fullmatch(self.entry):
            raise PluginManifestError("插件入口必须是点分模块名")
        if len(set(self.provides)) != len(self.provides):
            raise PluginManifestError("插件提供的能力不能重复")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise PluginManifestError("插件申请的宿主能力不能重复")
        if len(self.description) > 200:
            raise PluginManifestError("插件简介不能超过 200 个字符")
        unknown_provides = set(self.provides) - PROVIDED_CAPABILITIES
        if unknown_provides:
            raise PluginManifestError(f"未知的插件能力：{', '.join(sorted(unknown_provides))}")
        unknown_capabilities = set(self.capabilities) - HOST_CAPABILITIES
        if unknown_capabilities:
            raise PluginManifestError(f"未知的宿主能力：{', '.join(sorted(unknown_capabilities))}")
        if self.ui is not None and not safe_ui_path(self.ui):
            raise PluginManifestError("UI 目录必须是插件目录内的相对路径")
        has_pages = bool(self.pages)
        if has_pages != ("admin.ui" in self.provides):
            raise PluginManifestError("声明 admin.ui 能力时必须提供页面，反之亦然")
        if len({page.id for page in self.pages}) != len(self.pages):
            raise PluginManifestError("页面 id 不能重复")


class PluginLogger(Protocol):
    """宿主日志能力。参数和 logging.Logger 一致，插件不需要自己配置 handler。"""

    def debug(self, message: str, *args: Any, **kwargs: Any) -> None: ...

    def info(self, message: str, *args: Any, **kwargs: Any) -> None: ...

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None: ...

    def error(self, message: str, *args: Any, **kwargs: Any) -> None: ...

    def exception(self, message: str, *args: Any, **kwargs: Any) -> None: ...


@dataclass(frozen=True)
class PluginHttpResponse:
    """宿主 http 能力返回的响应。只提供插件常用的只读接口，不暴露连接对象。"""

    status_code: int
    headers: Mapping[str, str]
    content: bytes
    url: str
    history: tuple[PluginHttpResponse, ...] = ()

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.content)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise PluginHttpError(f"HTTP {self.status_code}", status_code=self.status_code)


class HostCallError(Exception):
    """插件调用宿主失败；code 是宿主给出的原因（例如 host_rejected、host_http_failed）。"""

    def __init__(self, message: str, *, code: str = "host_call_failed") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class PluginHttpError(Exception):
    """插件用 raise_for_status 主动抛出的 HTTP 错误。"""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class PluginHttpClient(Protocol):
    """插件可用的受控 HTTP 客户端；实现负责超时、限速、重定向和审计。

    只接受 params、headers、json、data、content 参数；method 限定为
    GET/HEAD/POST/PUT/PATCH/DELETE。
    """

    def request(self, method: str, url: str, **kwargs: Any) -> PluginHttpResponse: ...

    def get(self, url: str, **kwargs: Any) -> PluginHttpResponse: ...

    def post(self, url: str, **kwargs: Any) -> PluginHttpResponse: ...

    def put(self, url: str, **kwargs: Any) -> PluginHttpResponse: ...

    def delete(self, url: str, **kwargs: Any) -> PluginHttpResponse: ...


class PluginNotifier(Protocol):
    """插件主动发给宿主的通知；运行时总是注入。"""

    def resource_changed(self, source_id: int, locators: Sequence[str]) -> None:
        """告诉宿主这些目录有变动，由宿主做定向同步。"""
        ...

    def progress(self, until: datetime, reason: WaitReason) -> None:
        """报告等待到某个时间；until 必须带时区，reason 必须在协议集合内。"""
        ...

    def heartbeat(self) -> None:
        """纯心跳：只续期扫描的空闲超时，不把任务标记为等待。"""
        ...


@dataclass(frozen=True)
class HostInfo:
    """initialize 下发的宿主信息。"""

    version: str
    url: str | None = None


@dataclass(frozen=True)
class PluginContext:
    """宿主交给插件的全部能力；manifest 没有申请 http 时它是 None。"""

    plugin_id: str
    logger: PluginLogger
    data_dir: Path
    host: HostInfo
    # 宿主的本地媒体挂载目录；插件只用于解析本地来源的根目录。
    media_dir: Path
    notify: PluginNotifier
    proxy: str | None = None
    http: PluginHttpClient | None = None


class Plugin(Protocol):
    """插件入口对象。插件作者实现它，并在模块里调用 run(plugin)。"""

    def start(self, context: PluginContext) -> None: ...

    def stop(self) -> None: ...

    def providers(self) -> Mapping[str, object]:
        """启动后可用的 provider，键必须是 manifest.provides 里声明过的能力。"""
        ...


@runtime_checkable
class Flushable(Protocol):
    """可选钩子：备份前宿主调用 flush，插件把内存里的状态写进 data_dir。"""

    def flush(self) -> None: ...


@runtime_checkable
class ConfigAware(Protocol):
    """可选钩子：代理或公开地址变化时宿主发 host.config_changed。"""

    def config_changed(self, *, proxy: str | None, host_url: str | None) -> None: ...


@dataclass(frozen=True)
class PluginRequest:
    """插件页面发给插件接口的请求；宿主已完成鉴权，path 不含前导斜杠。"""

    method: str
    path: str
    query: Mapping[str, str]
    body: Any
    user_id: int


@dataclass(frozen=True)
class PluginResponse:
    body: Any = None
    status: int = 200


class PluginRequestError(Exception):
    """插件拒绝请求；message 会原样展示给管理员。"""

    def __init__(self, message: str, *, status: int = 400, code: str = "plugin_error") -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


class AdminUiProvider(Protocol):
    """admin.ui 能力：ui_root 是页面静态文件目录，handle 处理 /api/plugins/{id}/ 下的请求。"""

    @property
    def ui_root(self) -> Path: ...

    def handle(self, request: PluginRequest) -> PluginResponse: ...


@dataclass(frozen=True)
class MediaContext:
    title: str
    original_title: str | None = None
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    filename: str | None = None
    # 在线字幕插件用来判断这是电影还是剧集，以及按外部 ID 精确匹配
    kind: MetadataKind | None = None
    imdb_id: str | None = None
    tmdb_id: int | None = None


MetadataKind = Literal["movie", "series"]
# 管理员操作和播放取链是有人在等的；同步任务让行。
MetadataPriority = Literal["interactive", "background"]


class MetadataError(Exception):
    """元数据插件的错误基类；message 会展示给管理员。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class MetadataNotFound(MetadataError):
    """插件上没有这个条目、季、人物或图片。匹配时换下一个线索，刮削季时记为已拉取。"""


class MetadataUnavailable(MetadataError):
    """插件暂时不能用（没配置、凭据失效、网络或配额问题）。同步中止这个插件的后续请求，下次重试。"""


@dataclass(frozen=True)
class MetadataSearchRequest:
    title: str
    year: int | None = None
    kind: MetadataKind = "movie"
    language: str = "zh-CN"


@dataclass(frozen=True)
class MetadataCandidate:
    provider_id: str
    remote_id: str
    title: str
    year: int | None
    kind: MetadataKind
    score: float | None = None
    original_title: str | None = None
    overview: str | None = None


@dataclass(frozen=True)
class MetadataCredit:
    """演职员。profile 是头像的图片来源，由同一插件的 metadata.images 下载。"""

    remote_id: str
    name: str
    profile: str | None = None
    character: str | None = None


@dataclass(frozen=True)
class MetadataRecord:
    """一个条目的完整元数据。图片字段都是图片来源（路径或 URL），由同一插件的
    metadata.images 下载；文字优先给中文。"""

    provider_id: str
    remote_id: str
    title: str
    kind: MetadataKind = "movie"
    original_title: str | None = None
    en_title: str | None = None
    overview: str | None = None
    year: int | None = None
    release_date: str | None = None
    rating: float | None = None
    tagline: str | None = None
    genres: tuple[str, ...] = ()
    studios: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    content_rating: str | None = None
    runtime: int | None = None
    poster: str | None = None
    backdrop: str | None = None
    # 带片名的横版图和透明底的片名图，参考 Emby 的 Thumb 和 Logo
    thumb: str | None = None
    logo: str | None = None
    # 供管理员手动挑选的候选图，键为 poster/backdrop/thumb/logo
    artwork: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    cast: tuple[MetadataCredit, ...] = ()
    # 电影导演，剧集主创
    directors: tuple[MetadataCredit, ...] = ()
    # 编剧
    writers: tuple[MetadataCredit, ...] = ()
    # 所属系列；目前只有 TMDB 的系列用于自动合集
    collection_id: str | None = None
    # 外部 ID，键为小写 scheme（tmdb、imdb、tvdb、douban），值是字符串；用于补缺和取图
    external_ids: Mapping[str, str] = field(default_factory=dict)
    # 不是请求语言、由插件拿别的语言顶上的字段（overview、tagline、title），补缺时当作缺失
    fallback_fields: frozenset[str] = frozenset()


@dataclass(frozen=True)
class MetadataEpisode:
    number: int
    name: str | None = None
    overview: str | None = None
    air_date: str | None = None
    runtime: int | None = None
    rating: float | None = None
    still: str | None = None


@dataclass(frozen=True)
class MetadataSeason:
    number: int
    name: str | None = None
    overview: str | None = None
    air_date: str | None = None
    poster: str | None = None
    episodes: tuple[MetadataEpisode, ...] = ()


@dataclass(frozen=True)
class MetadataPerson:
    remote_id: str
    name: str
    original_name: str | None = None
    biography: str | None = None
    birthday: str | None = None
    deathday: str | None = None
    place_of_birth: str | None = None


class MetadataProvider(Protocol):
    """metadata.search 和 metadata.details 能力（同一个对象）。资料库排在第一位的元数据下载器
    必须同时提供这两项；只提供 metadata.details 的是「补缺插件」，宿主只调用
    unavailable_reason、find、details 和 page_url，不会调用 search。

    找不到时抛出 MetadataNotFound，暂时不可用时抛出 MetadataUnavailable；其他异常宿主按
    暂时不可用处理。优先级由 RPC params 提供，运行时用 ``current_priority()`` 暴露。
    """

    id: str

    def unavailable_reason(self) -> str | None:
        """不能发请求的原因（例如还没填 Key），可以用时返回 None。不应发起网络请求。"""
        ...

    def search(self, request: MetadataSearchRequest) -> list[MetadataCandidate]: ...

    def find(self, kind: MetadataKind, scheme: str, value: str) -> str | None:
        """按外部 ID 找插件里的条目 ID。scheme 目前有 tmdb（路径里的 {tmdb-ID}、NFO）和
        imdb（NFO）；不支持或找不到时返回 None。"""
        ...

    def details(self, remote_id: str, *, kind: MetadataKind) -> MetadataRecord: ...

    def season(self, remote_id: str, number: int) -> MetadataSeason:
        """剧集一季的信息和这一季的各集。"""
        ...

    def page_url(self, kind: MetadataKind, remote_id: str) -> str | None:
        """条目在提供方网站上的页面，管理员核对用；没有时返回 None。"""
        ...


class MetadataImageProvider(Protocol):
    """metadata.images 能力：下载本插件给出的图片来源，width 是期望的宽度。"""

    def image(self, source: str, width: int) -> bytes: ...


ImageType = Literal["poster", "backdrop", "thumb", "logo"]


@dataclass(frozen=True)
class RemoteImage:
    """图片获取器给出的一张候选图。language 是 ISO 639-1 小写，None 表示无文字。"""

    type: ImageType
    source: str
    language: str | None = None
    score: float | None = None
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class ImageLookupRequest:
    """按外部 ID 取图；language 是资料库的元数据语言（例如 zh-CN），只用于插件自己排序。"""

    kind: MetadataKind
    external_ids: Mapping[str, str]
    language: str = "zh-CN"


class ImageFetcher(Protocol):
    """image.fetch 能力：按外部 ID 列出图片并下载。只认外部 ID，绝不按片名搜索。

    没有可用的外部 ID 或找不到时返回空列表；暂时不可用时抛出 MetadataUnavailable；
    image() 找不到图片时抛出 MetadataNotFound。
    """

    id: str

    def unavailable_reason(self) -> str | None:
        """不能发请求的原因（例如还没填 Key），可以用时返回 None。不应发起网络请求。"""
        ...

    def images(self, request: ImageLookupRequest) -> list[RemoteImage]: ...

    def image(self, source: str, width: int) -> bytes: ...


class MetadataPeopleProvider(Protocol):
    """metadata.people 能力：演员页的简介、生日等资料。"""

    def person(self, remote_id: str) -> MetadataPerson: ...


@dataclass(frozen=True)
class SubtitleSearchRequest:
    media: MediaContext
    language: str | None = None
    forced: bool = False
    hearing_impaired: bool = False


@dataclass(frozen=True)
class SubtitleCandidate:
    provider_id: str
    remote_id: str
    label: str
    language: str
    format: str
    forced: bool = False
    hearing_impaired: bool = False
    score: float | None = None
    # 在线字幕站的信息，插件有就填
    release: str | None = None
    uploaded_at: str | None = None
    downloads: int | None = None


@dataclass(frozen=True)
class SubtitleDownload:
    content: bytes
    language: str
    format: str
    forced: bool = False
    hearing_impaired: bool = False


class SubtitleUnavailable(Exception):
    """字幕站暂时不能用（没配置、凭据失效、限流等）；retry_after 是建议等待秒数。"""

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after


class SubtitleProvider(Protocol):
    id: str

    def search(self, request: SubtitleSearchRequest) -> list[SubtitleCandidate]: ...

    def download(self, candidate: SubtitleCandidate) -> SubtitleDownload: ...


class OnlineSubtitleProvider(SubtitleProvider, Protocol):
    """在线字幕下载器：能搜索和下载，并报告当前能不能用。"""

    def unavailable_reason(self) -> str | None:
        """不能搜索或下载的原因；不应发起网络请求。"""
        ...


@dataclass(frozen=True)
class ResourceEntry:
    """来源扫描或浏览出的一个条目。

    locator 只能由来源插件解释，核心原样保存、原样传回；relative_path 相对于扫描（或浏览）的
    那个目录，用 / 分隔，是同步比对的键。key/identity 用于跨目录移动时保持文件身份，
    playback_locator 用于播放，未提供时都回退到 locator。
    """

    locator: str
    relative_path: str
    kind: Literal["video", "subtitle", "nfo"]
    size: int | None = None
    sha1: str | None = None
    is_dir: bool = False
    # 播放时使用的定位符；为空时与 locator 相同。
    playback_locator: str | None = None
    # 跨目录移动时保持身份的稳定键；为空时由宿主按来源和 locator 生成。
    key: str | None = None
    # 来源 API 需要的原始文件身份，例如云盘的文件 ID。
    identity: str | None = None
    # 扫描根目录的展示路径；宿主用它刷新资料库目录名称。
    root_path: str | None = None


@dataclass(frozen=True)
class ResourceListing:
    """一个目录的直接子项。path 是这个目录给人看的完整路径（以 / 开头）。"""

    locator: str
    path: str
    entries: tuple[ResourceEntry, ...]
    # 从来源根到当前目录的定位符和名称，供定向同步判断目录归属。
    ancestors: tuple[tuple[str, str], ...] = ()
    # 这个目录能否作为资料库目录；来源根、插件专用目录等为 False。
    # 不能选的位置照常返回列表，由宿主据此拒绝添加，不抛异常。
    selectable: bool = True


@dataclass(frozen=True)
class PlaybackTarget:
    """来源解析后的播放目标，url 和 path 二选一；核心层负责令牌、权限和响应。

    url 是播放器可以直接访问的地址（核心返回 302）；path 是服务器上的本地文件（核心直接
    提供文件内容）。expires_at 为 None 表示不缓存。
    """

    url: str | None = None
    path: Path | None = None
    expires_at: Any | None = None
    headers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (self.url is None) == (self.path is None):
            raise ValueError("PlaybackTarget 需要 url 或 path 之一")


SourceState = Literal["ok", "pending", "failed", "limited"]


@dataclass(frozen=True)
class SourceStatus:
    """来源给健康检查和向导看的当前状态；resume_at 是建议重试时间（ISO 8601）。"""

    state: SourceState
    message: str | None = None
    resume_at: str | None = None
    # 插件设置页的页面 id，客户端据此跳到「插件 › 该插件 › 该页」
    page: str | None = None

    def __post_init__(self) -> None:
        if self.state not in ("ok", "pending", "failed", "limited"):
            raise ValueError(f"未知的来源状态：{self.state}")


class ResourceUnavailable(Exception):
    """来源暂时不可用（未挂载、网络或凭据问题）。

    默认同步跳过这个目录，不标记任何文件失效；fatal 表示来源拒绝继续创建同步任务。
    """

    def __init__(
        self,
        message: str,
        *,
        fatal: bool = False,
        status_code: int = 503,
        code: str = "resource_unavailable",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.fatal = fatal
        self.status_code = status_code
        self.code = code


class ResourceNotFound(Exception):
    """定位符指向的目录或文件不存在。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ResourceRateLimited(Exception):
    """来源请求被限流，retry_after 为建议等待秒数。"""

    def __init__(
        self,
        message: str,
        retry_after: int | None = None,
        status_code: int = 429,
        code: str = "resource_rate_limited",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True)
class ResourceConfigField:
    """来源配置表单的一项，管理后台据此渲染表单。"""

    key: str
    label: str
    help: str = ""
    required: bool = True
    default: str = ""


class ResourceSource(Protocol):
    """绑定了某个来源实例配置的访问对象。所有方法失败时抛出 ResourceUnavailable 或
    ResourceNotFound；scan 要么返回完整结果，要么抛错，不能返回部分结果。

    locator 为 None 表示来源根目录。插件用 ``context.notify.progress`` 报告等待；运行时
    不再向来源注入 waiter 回调。
    """

    def browse(self, locator: str | None = None) -> ResourceListing: ...

    def scan(
        self,
        locator: str | None,
        known: Mapping[str, ResourceEntry] | None = None,
    ) -> list[ResourceEntry]:
        """locator 为 None 表示来源根目录。

        known 是宿主库里已有记录的条目（relative_path → 条目）。访问代价高的来源可以直接复用
        它们，只去访问有新路径的位置；返回的仍要是完整结果，其中已不存在的路径不能出现。
        声明 known_scan 能力后宿主才会传。"""
        ...

    def read(self, locator: str, max_bytes: int) -> bytes: ...

    def resolve(
        self, locator: str, user_agent: str, throttle_key: str | None = None
    ) -> PlaybackTarget: ...

    def delete(self, locator: str) -> None: ...


class ResourceProvider(Protocol):
    """resource.source 能力：按来源配置打开来源实例。

    multiple 为 False 时只允许一个来源实例（例如绑定全局账号的云盘）。
    capabilities 是 RESOURCE_CAPABILITIES 的子集：宿主只调用声明过的能力。
    - status：宿主可以调用 status(config) 获取 SourceStatus
    - changes：插件可以发 resource.changed 通知
    - delete：管理后台可以提供删除，宿主才会调用 source.delete
    - known_scan：宿主可以把已知条目传给 scan(known=...)
    """

    id: str
    multiple: bool
    capabilities: frozenset[str]
    config_fields: tuple[ResourceConfigField, ...]

    def validate_config(self, config: Mapping[str, Any]) -> dict[str, Any]:
        """校验并规范化配置，不合法时抛出 InvalidSourceConfig（消息给管理员看）。"""
        ...

    def check(self, config: Mapping[str, Any]) -> None:
        """检查来源当前是否可用；不可用时抛出 ResourceUnavailable。"""
        ...

    def open(self, source_id: int, config: Mapping[str, Any]) -> ResourceSource:
        """按来源实例打开访问对象；source_id 由宿主分配，插件发 resource.changed 时使用。"""
        ...

    def status(self, config: Mapping[str, Any]) -> SourceStatus:
        """声明 status 能力时由宿主调用；否则宿主不会调用。"""
        ...
