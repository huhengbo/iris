"""测试假插件的状态和 provider 实现。

这个模块只依赖 ``iris_plugin_sdk`` 和标准库，既不导入宿主的 ``app.*``，也不导入测试代码。
插件子进程和宿主侧测试都从这里取类型：插件在子进程里持有状态，测试通过 ``test.control``
RPC 操纵它。行为覆盖资源来源、元数据、图片和字幕四类能力，供宿主测试按需裁剪 manifest。
"""

from __future__ import annotations

import base64
import hashlib
import itertools
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from iris_plugin_sdk import (
    ImageLookupRequest,
    InvalidSourceConfig,
    MetadataCandidate,
    MetadataCredit,
    MetadataNotFound,
    MetadataPerson,
    MetadataRecord,
    MetadataSeason,
    MetadataUnavailable,
    PlaybackTarget,
    RemoteImage,
    ResourceConfigField,
    ResourceEntry,
    ResourceListing,
    ResourceNotFound,
    ResourceRateLimited,
    ResourceUnavailable,
    SourceStatus,
    SubtitleCandidate,
    SubtitleDownload,
    SubtitleSearchRequest,
    SubtitleUnavailable,
)

VIDEO_EXTENSIONS = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".ts", ".webm"}
SUBTITLE_EXTENSIONS = {".ass", ".srt", ".ssa", ".sub", ".sup", ".vtt"}
NFO_EXTENSIONS = {".nfo"}
VIDEO_SIZE = 4 * 1024**3


_JPEG_1X1 = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIy"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIA"
    "AhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQA"
    "AAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3"
    "ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWm"
    "p6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEA"
    "AwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSEx"
    "BhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElK"
    "U1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3"
    "uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDyOiii"
    "uw5D/9k="
)
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGMQ0bABAADMAHm9FcOvAAAAAElFTkSuQmCC"
)


def image_bytes(png: bool = False, width: int = 1000, height: int = 600) -> bytes:
    """一张可解码的图片；宿主会按需要的宽度缩放，1x1 足够验证链路。

    末尾追加尺寸标记，让不同请求得到的字节哈希不同；解码器会忽略 IEND/EOI 之后的数据。
    """
    base = _PNG_1X1 if png else _JPEG_1X1
    return base + f":{width}x{height}".encode()


def media_kind(name: str) -> Literal["video", "subtitle", "nfo"] | None:
    suffix = Path(name).suffix.casefold()
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in SUBTITLE_EXTENSIONS:
        return "subtitle"
    if suffix in NFO_EXTENSIONS:
        return "nfo"
    return None


# ---- 资源来源 ----


@dataclass
class Node:
    id: str
    name: str
    parent: str | None
    is_dir: bool
    size: int = 0
    sha1: str = ""
    content: bytes = b""
    children: list[str] = field(default_factory=list)


class FakeSourceState:
    """内存里的来源目录树；控制 RPC 和 provider 调用在同一个子进程里。"""

    def __init__(self, plugin_id: str = "fake.source") -> None:
        self.plugin_id = plugin_id
        self._ids = itertools.count(1)
        self.nodes: dict[str, Node] = {"0": Node("0", "根目录", None, is_dir=True)}
        self.calls: list[str] = []
        self.link_requests: list[tuple[str, str]] = []
        self.link_cache: dict[tuple[str, str], str] = {}
        self.error: str | None = None
        self.fail_next: dict[str, int] = {}
        self.wait_once: tuple[str, str] | None = None
        self.progress: Callable[[Any, str], None] = lambda until, reason: None
        self.heartbeat: Callable[[], None] = lambda: None
        self.fatal_scan = False
        self.resolve_error: str | None = None
        self.selectable = True
        self.multiple = False
        self.capabilities = frozenset({"status", "changes", "delete", "known_scan"})
        self.status_state = "ok"
        self.status_message: str | None = None
        self.status_resume_at: str | None = None
        self.status_page: str | None = None
        self.resource_id = "fake.source"
        self.resolve_path: str | None = None
        self.root_locator = "0"
        self._config: dict[str, Any] = {}
        if plugin_id == "org.example.fake":
            self._runtime_tree()

    def _runtime_tree(self) -> None:
        """运行时测试沿用旧假来源的固定目录树和 id。"""
        self.resource_id = "fake.source"
        self.root_locator = "root"
        self.multiple = True
        self.nodes["0"].children = ["d1", "d2"]
        self.nodes["d1"] = Node("d1", "电影", "0", is_dir=True, children=["f1", "f2"])
        self.nodes["d2"] = Node("d2", "其他", "0", is_dir=True, children=["f3"])
        self.nodes["f1"] = Node("f1", "a.mkv", "d1", False, 4, "sha-f1", b"fake video")
        self.nodes["f2"] = Node("f2", "b.srt", "d1", False, 3, "sha-f2", b"fake subs")
        self.nodes["f3"] = Node("f3", "c.nfo", "d2", False, 0, "sha-f3", b"fake nfo")

    # ---- 控制 ----

    def add_dir(self, parent: str | None, name: str) -> str:
        return self._add(Node(self._next_id(), name, parent or "0", is_dir=True))

    def add_file(
        self,
        parent: str | None,
        name: str,
        *,
        size: int | None = None,
        sha1: str | None = None,
        content: bytes = b"",
    ) -> str:
        kind = media_kind(name)
        if size is None:
            size = VIDEO_SIZE if kind == "video" else len(content) or 1024
        if content:
            size = len(content)
            sha1 = sha1 or hashlib.sha1(content).hexdigest().upper()
        sha1 = sha1 or hashlib.sha1(f"{parent or '0'}/{name}".encode()).hexdigest().upper()
        node = Node(self._next_id(), name, parent or "0", False, size, sha1, content)
        return self._add(node)

    def make_path(self, path: str) -> str:
        current = "0"
        parts = path.split("/")
        for index, name in enumerate(parts):
            existing = self.child(current, name)
            if existing is not None:
                current = existing
            elif index == len(parts) - 1 and "." in name:
                current = self.add_file(current, name)
            else:
                current = self.add_dir(current, name)
        return current

    def make_dir(self, _cookie: str, name: str) -> str:
        return self.make_path(name)

    def child(self, parent: str | None, name: str) -> str | None:
        parent = parent or "0"
        for child_id in self.nodes[parent].children:
            if self.nodes[child_id].name == name:
                return child_id
        return None

    def remove(self, node_id: str) -> None:
        node = self.nodes[node_id]
        for child_id in list(node.children):
            self.remove(child_id)
        if node.parent is not None:
            self.nodes[node.parent].children.remove(node_id)
        del self.nodes[node_id]

    def move(self, node_id: str, new_parent: str | None, new_name: str | None = None) -> None:
        node = self.nodes[node_id]
        if node.parent is not None:
            self.nodes[node.parent].children.remove(node_id)
        node.parent = new_parent or "0"
        node.name = new_name or node.name
        self.nodes[node.parent].children.append(node_id)

    def files_in(self, node_id: str | None) -> list[str]:
        return [self.nodes[child].name for child in self.nodes[node_id or "0"].children]

    def set_node(self, node_id: str, **fields: Any) -> None:
        node = self.nodes[node_id]
        for key, value in fields.items():
            setattr(node, key, value)

    def node(self, node_id: str) -> dict[str, Any]:
        node = self.nodes[node_id]
        return {
            "id": node.id,
            "name": node.name,
            "parent": node.parent,
            "is_dir": node.is_dir,
            "size": node.size,
            "sha1": node.sha1,
            "children": list(node.children),
        }

    # ---- provider ----

    def _enter(self, name: str) -> None:
        self.calls.append(name)
        remaining = self.fail_next.get(name, 0)
        if remaining > 0:
            self.fail_next[name] = remaining - 1
            if name == "resolve":
                raise ResourceRateLimited("来源限流", retry_after=1800, status_code=503)
            raise ResourceUnavailable("来源临时失败")
        if self.error == "unavailable":
            raise ResourceUnavailable("来源暂时不可用")
        if self.error == "rate_limited":
            raise ResourceRateLimited("来源限流", retry_after=1800, status_code=503)
        if self.error == "not_found":
            raise ResourceNotFound("找不到")

    def validate_config(self, config: Mapping[str, Any]) -> dict[str, Any]:
        self._enter("validate_config")
        if self.plugin_id == "org.example.fake":
            root = config.get("root")
            if not isinstance(root, str) or not root:
                raise InvalidSourceConfig("根目录不能为空")
            if root == "invalid":
                raise InvalidSourceConfig("这个目录不合法")
        return dict(config)

    def check(self, config: Mapping[str, Any]) -> None:
        self._enter("check")
        if self.plugin_id == "org.example.fake" and config.get("root") == "bad":
            raise ResourceUnavailable("来源不可用")

    def status(self, config: Mapping[str, Any]) -> SourceStatus:
        self._enter("status")
        if self.plugin_id == "org.example.fake" and config.get("root") == "bad":
            return SourceStatus("failed", message="来源不可用", page="settings")
        return SourceStatus(
            self.status_state,  # type: ignore[arg-type]
            message=self.status_message,
            resume_at=self.status_resume_at,
            page=self.status_page,
        )

    def open(self, source_id: int, config: Mapping[str, Any]) -> FakeSource:
        return FakeSource(self, source_id, config)

    @property
    def config_fields(self) -> tuple[ResourceConfigField, ...]:
        if self.plugin_id != "org.example.fake":
            return ()
        return (ResourceConfigField("root", "根目录", help="测试配置"),)

    # ---- 目录树 ----

    def _next_id(self) -> str:
        return str(next(self._ids) + 1000)

    def _add(self, node: Node) -> str:
        assert node.parent is not None
        self.nodes[node.parent].children.append(node.id)
        self.nodes[node.id] = node
        return node.id

    def display_path(self, node_id: str | None) -> str:
        names: list[str] = []
        current = node_id or "0"
        while current != "0":
            node = self.nodes[current]
            names.append(node.name)
            assert node.parent is not None
            current = node.parent
        return "/" + "/".join(reversed(names)) if names else "/"

    def ancestors(self, node_id: str | None) -> list[tuple[str, str]]:
        found: list[tuple[str, str]] = []
        current = node_id or "0"
        while current != "0":
            node = self.nodes[current]
            found.append((node.id, node.name))
            assert node.parent is not None
            current = node.parent
        return list(reversed(found))


class FakeResourceProvider:
    def __init__(self, state: FakeSourceState) -> None:
        self._state = state
        self.id = state.resource_id

    @property
    def multiple(self) -> bool:
        return self._state.multiple

    @property
    def capabilities(self) -> frozenset[str]:
        return self._state.capabilities

    @property
    def config_fields(self) -> tuple[ResourceConfigField, ...]:
        return self._state.config_fields

    def validate_config(self, config: Mapping[str, Any]) -> dict[str, Any]:
        return self._state.validate_config(config)

    def check(self, config: Mapping[str, Any]) -> None:
        self._state.check(config)

    def status(self, config: Mapping[str, Any]) -> SourceStatus:
        return self._state.status(config)

    def open(self, source_id: int, config: Mapping[str, Any]) -> FakeSource:
        return self._state.open(source_id, config)


class FakeSource:
    def __init__(self, state: FakeSourceState, source_id: int, config: Mapping[str, Any]) -> None:
        self._state = state
        self._source_id = source_id
        self._config = dict(config)

    def browse(self, locator: str | None = None) -> ResourceListing:
        self._state._enter("browse")
        node_id = locator or "0"
        if node_id == "rejected":
            return ResourceListing(
                "rejected",
                "/受限目录",
                (),
                (("root", "库"),),
                selectable=False,
            )
        if node_id == "root":
            node_id = "0"
        if node_id not in self._state.nodes or not self._state.nodes[node_id].is_dir:
            raise ResourceNotFound("目录不存在")
        node = self._state.nodes[node_id]
        entries = tuple(
            ResourceEntry(
                child.id,
                child.name,
                media_kind(child.name) or "video",
                size=None if child.is_dir else child.size,
                sha1=None if child.is_dir else child.sha1,
                is_dir=child.is_dir,
                playback_locator=None if child.is_dir else child.id,
                key=None if child.is_dir else child.id,
                identity=None if child.is_dir else child.id,
            )
            for child in (self._state.nodes[item] for item in node.children)
        )
        return ResourceListing(
            locator=(self._state.root_locator if node_id == "0" else node_id),
            path=self._state.display_path(node_id),
            entries=entries,
            ancestors=(
                (("root", "库"), *self._state.ancestors(node_id))
                if self._state.root_locator == "root" and node_id != "0"
                else tuple(self._state.ancestors(node_id))
            ),
            selectable=self._state.selectable,
        )

    def scan(
        self,
        locator: str | None,
        known: Mapping[str, ResourceEntry] | None = None,
    ) -> list[ResourceEntry]:
        if self._state.wait_once is not None:
            until, reason = self._state.wait_once
            self._state.wait_once = None
            self._state.progress(until, reason)
        if self._state.fatal_scan:
            raise ResourceUnavailable("凭据已失效", fatal=True)
        self._state._enter("scan")
        progress = int(self._config.get("scan_progress", 0))
        for _ in range(progress):
            self._state.heartbeat()
            time.sleep(0.03)
        root_id = locator or "0"
        if root_id == "root":
            root_id = "0"
        if root_id not in self._state.nodes or not self._state.nodes[root_id].is_dir:
            raise ResourceNotFound("目录不存在")
        root_path = self._state.display_path(root_id)
        found: list[ResourceEntry] = []
        stack = [(root_id, "")]
        while stack:
            node_id, prefix = stack.pop()
            for child_id in self._state.nodes[node_id].children:
                child = self._state.nodes[child_id]
                relative = f"{prefix}/{child.name}" if prefix else child.name
                if child.is_dir:
                    stack.append((child_id, relative))
                    continue
                kind = media_kind(child.name)
                if kind is None:
                    continue
                found.append(
                    ResourceEntry(
                        child.id,
                        relative,
                        kind,
                        child.size,
                        child.sha1,
                        False,
                        child.id,
                        child.id,
                        child.id,
                        root_path,
                    )
                )
        found.sort(key=lambda entry: entry.relative_path)
        return found

    def read(self, locator: str, max_bytes: int) -> bytes:
        self._state._enter("read")
        node = self._state.nodes.get(locator)
        if node is None or node.is_dir:
            raise ResourceNotFound("文件不存在")
        if node.size > max_bytes:
            raise ResourceUnavailable("文件超过读取上限")
        return node.content

    def resolve(
        self, locator: str, user_agent: str, throttle_key: str | None = None
    ) -> PlaybackTarget:
        self._state._enter("resolve")
        if self._state.resolve_error == "unavailable":
            raise ResourceUnavailable("115 登录已失效")
        node = self._state.nodes.get(locator)
        if node is None or node.is_dir:
            raise ResourceNotFound("文件不存在")
        if self._state.resolve_path is not None:
            return PlaybackTarget(path=Path(self._state.resolve_path))
        key = (locator, user_agent)
        url = self._state.link_cache.get(key)
        if url is None:
            self._state.link_requests.append(key)
            ua_hash = hashlib.sha1(user_agent.encode()).hexdigest()[:8]
            url = f"https://cdn.example/{node.id}/{quote(node.name)}?ua={ua_hash}"
            self._state.link_cache[key] = url
        return PlaybackTarget(url=url)

    def delete(self, locator: str) -> None:
        self._state._enter("delete")
        if locator not in self._state.nodes or locator == "0":
            raise ResourceNotFound("文件不存在")
        self._state.remove(locator)


# ---- 元数据 ----


@dataclass
class Title:
    record: MetadataRecord
    seasons: dict[int, MetadataSeason] = field(default_factory=dict)


class FakeMetadataState:
    """内存里的元数据插件；支持 TMDB 风格的条目、季、人物和图片。"""

    def __init__(self, plugin_id: str) -> None:
        self.plugin_id = plugin_id
        self.reason: str | None = (
            "还没有配置 TMDB Key，在插件 › TMDB 元数据里填写"
            if plugin_id == "builtin.tmdb"
            else None
        )
        self.error: str | None = None
        self.titles: dict[tuple[str, int], Title] = {}
        self.find_results: dict[tuple[str, str], str | None] = {}
        self.find_calls: list[tuple[str, str, str]] = []
        self.search_results: dict[tuple[str, str], list[MetadataCandidate]] = {}
        self.people: dict[str, MetadataPerson] = {}
        self.missing_images: set[str] = set()
        self.image_delay = 0.0
        self.page_url_template = "https://www.themoviedb.org/{kind}/{remote_id}"
        self.calls: list[str] = []
        # image.fetch 用的候选图
        self.images_available: list[RemoteImage] = []
        self.records: dict[str, MetadataRecord] = {}
        if plugin_id == "org.example.fake":
            self.records["remote-1"] = MetadataRecord(
                provider_id=plugin_id,
                remote_id="remote-1",
                title="条目 remote-1",
                kind="movie",
                poster="poster-source",
                genres=("剧情",),
            )

    # ---- 控制 ----

    def add_movie(
        self,
        tmdb_id: int,
        title: str,
        year: int | None,
        *,
        original_title: str | None = None,
        imdb_id: str | None = None,
        cast: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        record = MetadataRecord(
            provider_id=self.plugin_id,
            remote_id=str(tmdb_id),
            title=title,
            kind="movie",
            original_title=original_title,
            en_title=original_title,
            year=year,
            release_date=f"{year}-05-01" if year else None,
            overview=f"{title}的简介",
            rating=7.5,
            genres=("剧情",),
            content_rating="PG-13",
            runtime=120,
            poster=f"/poster-{tmdb_id}.jpg",
            backdrop=f"/backdrop-{tmdb_id}.jpg",
            thumb=f"/thumb-{tmdb_id}.jpg",
            logo=f"/logo-{tmdb_id}.png",
            cast=tuple(_credit(item, self.plugin_id) for item in cast or [_default_cast(tmdb_id)]),
            directors=(_credit(_default_director(tmdb_id), self.plugin_id),),
            external_ids={"imdb": imdb_id} if imdb_id is not None else {},
        )
        self.titles[("movie", tmdb_id)] = Title(record)
        return detail_dict(record)

    def add_series(
        self,
        tmdb_id: int,
        title: str,
        year: int | None,
        seasons: dict[int, int],
        *,
        original_title: str | None = None,
        cast: list[dict[str, Any]] | None = None,
        imdb_id: str | None = None,
        tvdb_id: str | None = None,
    ) -> dict[str, Any]:
        external_ids = {
            key: value for key, value in (("imdb", imdb_id), ("tvdb", tvdb_id)) if value is not None
        }
        record = MetadataRecord(
            provider_id=self.plugin_id,
            remote_id=str(tmdb_id),
            title=title,
            kind="series",
            original_title=original_title,
            en_title=original_title,
            year=year,
            release_date=f"{year}-01-10" if year else None,
            overview=f"{title}的简介",
            rating=8.1,
            genres=("剧情",),
            poster=f"/poster-{tmdb_id}.jpg",
            backdrop=f"/backdrop-{tmdb_id}.jpg",
            cast=tuple(_credit(item, self.plugin_id) for item in cast or [_default_cast(tmdb_id)]),
            directors=(_credit(_default_creator(tmdb_id), self.plugin_id),),
            external_ids=external_ids,
        )
        title_entry = Title(record)
        for number, count in seasons.items():
            title_entry.seasons[number] = season_info(tmdb_id, number, count)
        self.titles[("series", tmdb_id)] = title_entry
        return detail_dict(record)

    def clear_titles(self) -> None:
        self.titles.clear()

    def rename(self, kind: str, tmdb_id: int, title: str) -> None:
        entry = self.titles[(kind, tmdb_id)]
        entry.record = replace(entry.record, title=title)

    def set_details(self, kind: str, tmdb_id: int, fields: dict[str, Any]) -> None:
        entry = self.titles[(kind, tmdb_id)]
        values = dict(fields)
        for key in ("genres", "studios", "countries"):
            if key in values:
                values[key] = tuple(values[key])
        for key in ("cast", "directors", "writers"):
            if key in values:
                values[key] = tuple(_credit(item, self.plugin_id) for item in values[key])
        if "fallback_fields" in values:
            values["fallback_fields"] = frozenset(values["fallback_fields"])
        entry.record = replace(entry.record, **values)

    def add_person(self, person: dict[str, Any]) -> None:
        self.people[str(person["remote_id"])] = MetadataPerson(**person)

    def set_record(self, record: MetadataRecord) -> None:
        self.records[record.remote_id] = record

    def delete_record(self, remote_id: str) -> None:
        self.records.pop(remote_id, None)

    def record(self, remote_id: str) -> MetadataRecord | None:
        if remote_id in self.records:
            return self.records[remote_id]
        for entry in self.titles.values():
            if entry.record.remote_id == remote_id:
                return entry.record
        return None

    def set_find(self, scheme: str, value: str, remote_id: str | None) -> None:
        self.find_results[(scheme, value)] = remote_id

    def set_search_results(self, kind: str, query: str, results: list[dict[str, Any]]) -> None:
        self.search_results[(kind, query)] = [
            MetadataCandidate(
                self.plugin_id,
                str(item.get("remote_id", item.get("tmdb_id"))),
                str(item.get("title", "")),
                item.get("year"),
                item.get("kind", kind),
                item.get("score"),
                item.get("original_title"),
                item.get("overview"),
            )
            for item in results
        ]

    def title(self, kind: str, tmdb_id: int) -> dict[str, Any]:
        entry = self.titles.get((kind, tmdb_id))
        if entry is None:
            raise MetadataNotFound("条目不存在")
        return detail_dict(entry.record)

    def clear_calls(self) -> None:
        self.calls.clear()

    # ---- provider ----

    def _enter(self, name: str) -> None:
        self.calls.append(name)
        if self.error == "unavailable":
            raise MetadataUnavailable("元数据暂时不可用")
        if self.error == "not_found":
            raise MetadataNotFound("找不到")
        if self.error == "auth":
            raise MetadataUnavailable("Key 无效")

    def unavailable_reason(self) -> str | None:
        return self.reason

    def search(self, request: Any) -> list[MetadataCandidate]:
        self._enter(f"search:{request.kind}:{request.title}")
        if self.plugin_id == "org.example.fake":
            return [
                MetadataCandidate(
                    self.plugin_id, "remote-1", request.title, request.year, request.kind
                )
            ]
        if (request.kind, request.title) in self.search_results:
            return list(self.search_results[(request.kind, request.title)])
        results: list[MetadataCandidate] = []
        for (kind, _), entry in self.titles.items():
            record = entry.record
            if kind != request.kind:
                continue
            if any(
                request.title.lower() in candidate
                for candidate in (record.title.lower(), (record.original_title or "").lower())
            ):
                results.append(
                    MetadataCandidate(
                        self.plugin_id,
                        record.remote_id,
                        record.title,
                        record.year,
                        record.kind,
                        original_title=record.original_title,
                        overview=record.overview,
                    )
                )
        for record in self.records.values():
            if record.kind != request.kind:
                continue
            if any(
                request.title.lower() in candidate
                for candidate in (record.title.lower(), (record.original_title or "").lower())
            ):
                results.append(
                    MetadataCandidate(
                        self.plugin_id,
                        record.remote_id,
                        record.title,
                        record.year,
                        record.kind,
                        original_title=record.original_title,
                        overview=record.overview,
                    )
                )
        return results

    def find(self, kind: str, scheme: str, value: str) -> str | None:
        self.find_calls.append((kind, scheme, value))
        self._enter(f"find:{value}")
        if self.plugin_id == "org.example.fake":
            return None if value == "missing" else "remote-1"
        if (scheme, value) in self.find_results:
            return self.find_results[(scheme, value)]
        for (entry_kind, tmdb_id), entry in self.titles.items():
            if entry_kind != kind:
                continue
            if scheme == "tmdb" and str(tmdb_id) == value:
                return str(tmdb_id)
            if scheme == "imdb" and entry.record.external_ids.get("imdb") == value:
                return str(tmdb_id)
        return None

    def details(self, remote_id: str, *, kind: str) -> MetadataRecord:
        self._enter(f"details:{kind}:{remote_id}")
        if self.plugin_id == "org.example.fake":
            if remote_id == "standard-error":
                raise RuntimeError("运行时内部错误")
            if remote_id == "missing":
                raise MetadataNotFound("条目不存在")
        record = self.record(remote_id)
        if record is None or record.kind != kind:
            raise MetadataNotFound("条目不存在")
        return record

    def images(self, request: ImageLookupRequest) -> list[RemoteImage]:
        external = ",".join(f"{key}={value}" for key, value in sorted(request.external_ids.items()))
        self._enter(f"image-list:{request.kind}:{request.language}:{external}")
        return list(self.images_available)

    def image(self, source: str, width: int) -> bytes:
        if self.image_delay:
            time.sleep(self.image_delay)
        self._enter(f"image:{source}:{width}")
        if source in self.missing_images:
            raise MetadataNotFound("图片不存在")
        return image_bytes(png=source.endswith(".png"))

    def season(self, remote_id: str, number: int) -> MetadataSeason:
        self._enter(f"season:{remote_id}:{number}")
        if self.plugin_id == "org.example.fake":
            return MetadataSeason(number=number, name=f"第 {number} 季")
        for (kind, _), entry in self.titles.items():
            if kind == "series" and entry.record.remote_id == remote_id:
                if number not in entry.seasons:
                    raise MetadataNotFound("季不存在")
                return entry.seasons[number]
        raise MetadataNotFound("季不存在")

    def page_url(self, kind: str, remote_id: str) -> str | None:
        if self.plugin_id == "org.example.fake":
            return f"https://example.test/{kind}/{remote_id}"
        segment = "movie" if kind == "movie" else "tv"
        return self.page_url_template.format(kind=segment, remote_id=remote_id)

    def person(self, remote_id: str) -> MetadataPerson:
        self._enter(f"person:{remote_id}")
        person = self.people.get(remote_id)
        if person is None:
            raise MetadataNotFound("人物不存在")
        return person


class FakeMetadataProvider:
    def __init__(self, state: FakeMetadataState) -> None:
        self._state = state
        self.id = state.plugin_id

    def unavailable_reason(self) -> str | None:
        return self._state.unavailable_reason()

    def search(self, request: Any) -> list[MetadataCandidate]:
        return self._state.search(request)

    def find(self, kind: str, scheme: str, value: str) -> str | None:
        return self._state.find(kind, scheme, value)

    def details(self, remote_id: str, *, kind: str) -> MetadataRecord:
        return self._state.details(remote_id, kind=kind)

    def season(self, remote_id: str, number: int) -> MetadataSeason:
        return self._state.season(remote_id, number)

    def page_url(self, kind: str, remote_id: str) -> str | None:
        return self._state.page_url(kind, remote_id)


class FakeMetadataImages:
    def __init__(self, state: FakeMetadataState) -> None:
        self._state = state

    def image(self, source: str, width: int) -> bytes:
        return self._state.image(source, width)


class FakeMetadataPeople:
    def __init__(self, state: FakeMetadataState) -> None:
        self._state = state

    def person(self, remote_id: str) -> MetadataPerson:
        return self._state.person(remote_id)


class FakeImageFetcher:
    def __init__(self, state: FakeMetadataState) -> None:
        self._state = state
        self.id = state.plugin_id

    def unavailable_reason(self) -> str | None:
        return self._state.unavailable_reason()

    def images(self, request: ImageLookupRequest) -> list[RemoteImage]:
        return self._state.images(request)

    def image(self, source: str, width: int) -> bytes:
        return self._state.image(source, width)


# ---- 字幕 ----


class FakeSubtitleState:
    def __init__(self, plugin_id: str) -> None:
        self.plugin_id = plugin_id
        self.reason: str | None = "还没有填写 API Token" if plugin_id == "builtin.assrt" else None
        self.error: str | None = "failed" if plugin_id == "test.failing" else None
        if plugin_id == "test.unavailable":
            self.reason = "测试插件还没有配置"
        self.error_message = "字幕站不可用"
        self.error_retry_after: int | None = None
        self.candidates: list[SubtitleCandidate] = []
        self.results: dict[str, list[SubtitleCandidate]] = {}
        self.results_configured = False
        self.search_languages: list[str | None] = []
        self.download_candidates: list[SubtitleCandidate] = []
        self.calls: list[str] = []
        self.media_requests: list[Any] = []
        self.same_content = False

    def unavailable_reason(self) -> str | None:
        return self.reason

    def search(self, request: SubtitleSearchRequest) -> list[SubtitleCandidate]:
        self.calls.append("search")
        self.media_requests.append(request.media)
        self.search_languages.append(request.language)
        if self.error == "failed":
            raise RuntimeError("boom secret-token-should-not-leak")
        if self.error is not None:
            raise SubtitleUnavailable(self.error_message, self.error_retry_after)
        if self.results_configured:
            return list(self.results.get(request.language or "", []))
        if self.candidates:
            return list(self.candidates)
        if self.plugin_id == "org.example.fake":
            return [
                SubtitleCandidate(
                    self.plugin_id,
                    "sub-1",
                    "测试字幕",
                    request.language or "zh",
                    "srt",
                )
            ]
        return [
            SubtitleCandidate(
                self.plugin_id,
                "remote/one.srt",
                "测试在线字幕",
                request.language or "zh-Hans",
                "srt",
                release="Fake.Release.1080p",
                uploaded_at="2024-01-02 03:04:05",
                downloads=7,
                score=1.5,
            )
        ]

    def download(self, candidate: SubtitleCandidate) -> SubtitleDownload:
        self.calls.append("download")
        self.download_candidates.append(candidate)
        if self.error == "failed":
            raise RuntimeError("boom secret-token-should-not-leak")
        if self.error is not None:
            raise SubtitleUnavailable(self.error_message, self.error_retry_after)
        content = "same" if self.same_content else candidate.remote_id
        if self.plugin_id == "org.example.fake":
            content = "测试"
        return SubtitleDownload(
            content=f"1\n00:00:01,000 --> 00:00:02,000\n{content}\n".encode(),
            language=candidate.language,
            format=candidate.format,
        )


class FakeSubtitleProvider:
    def __init__(self, state: FakeSubtitleState) -> None:
        self._state = state
        self.id = state.plugin_id

    def unavailable_reason(self) -> str | None:
        return self._state.unavailable_reason()

    def search(self, request: SubtitleSearchRequest) -> list[SubtitleCandidate]:
        return self._state.search(request)

    def download(self, candidate: SubtitleCandidate) -> SubtitleDownload:
        return self._state.download(candidate)


def _credit(item: dict[str, Any], provider_id: str) -> MetadataCredit:
    remote_id = str(item.get("remote_id", item.get("id", "person")))
    return MetadataCredit(
        remote_id,
        str(item.get("name", remote_id)),
        item.get("profile"),
        item.get("character"),
    )


def _default_cast(tmdb_id: int) -> dict[str, Any]:
    return {"remote_id": str(9000 + tmdb_id), "name": f"演员{tmdb_id}", "character": "主角"}


def _default_director(tmdb_id: int) -> dict[str, Any]:
    return {"remote_id": str(8000 + tmdb_id), "name": f"导演{tmdb_id}"}


def _default_creator(tmdb_id: int) -> dict[str, Any]:
    return {"remote_id": str(8000 + tmdb_id), "name": f"主创{tmdb_id}"}


def season_info(tmdb_id: int, number: int, count: int) -> MetadataSeason:
    from iris_plugin_sdk import MetadataEpisode

    return MetadataSeason(
        number=number,
        name=f"第 {number} 季",
        poster=f"/season-{tmdb_id}-{number}.jpg",
        episodes=tuple(
            MetadataEpisode(
                number=episode,
                name=f"第 {episode} 集",
                runtime=45,
                still=f"/still-{tmdb_id}-{number}-{episode}.jpg",
            )
            for episode in range(1, count + 1)
        ),
    )


def detail_dict(record: MetadataRecord) -> dict[str, Any]:
    """给宿主侧测试代理用的普通字段，不经过 codec 的 dataclass 标记。"""
    return {
        "tmdb_id": int(record.remote_id),
        "kind": record.kind,
        "title": record.title,
        "original_title": record.original_title,
        "year": record.year,
        "release_date": record.release_date,
        "overview": record.overview,
        "rating": record.rating,
        "genres": list(record.genres),
        "content_rating": record.content_rating,
        "runtime": record.runtime,
        "poster_path": record.poster,
        "backdrop_path": record.backdrop,
        "thumb_path": record.thumb,
        "logo_path": record.logo,
        "cast": [
            {
                "remote_id": credit.remote_id,
                "name": credit.name,
                "profile": credit.profile,
                "character": credit.character,
            }
            for credit in record.cast
        ],
        "directors": [
            {
                "remote_id": credit.remote_id,
                "name": credit.name,
                "profile": credit.profile,
                "character": credit.character,
            }
            for credit in record.directors
        ],
        "external_ids": dict(record.external_ids),
        "fallback_fields": sorted(record.fallback_fields),
    }


# 避免类型检查器把 MetadataUnavailable 当成未使用
__all__ = [
    "FakeImageFetcher",
    "FakeMetadataImages",
    "FakeMetadataPeople",
    "FakeMetadataProvider",
    "FakeMetadataState",
    "FakeResourceProvider",
    "FakeSource",
    "FakeSourceState",
    "FakeSubtitleProvider",
    "FakeSubtitleState",
    "MetadataUnavailable",
    "detail_dict",
    "image_bytes",
    "media_kind",
]
