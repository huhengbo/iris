"""测试用的内存 115 网关：一棵可编辑的目录树，可以编排风控、凭据失效、导出忙碌等情况。"""

from __future__ import annotations

import hashlib
import itertools
from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import quote

from iris_plugin_sdk import media_kind

from iris_source_115.clock import Clock
from iris_source_115.errors import (
    Cloud115Error,
    Cloud115Unavailable,
    CopyNotReady,
    CredentialInvalid,
    DirNotFound,
    ExportBusy,
    FileGone,
    LifeApiRejected,
)
from iris_source_115.gateway import (
    LIFE_EVENT_LIMIT,
    AccountInfo,
    CloudEntry,
    DirectLink,
    DirListing,
    DirRef,
    LifeEvent,
    QrStatus,
    QrToken,
)

VALID_COOKIE = "UID=100_R2_1700000000; CID=cid-secret-value; SEID=seid-secret-value; KID=kid"
QR_COOKIE = "UID=100_I1_1700000001; CID=qr-cid-secret; SEID=qr-seid-secret; KID=kid"
QR_IMAGE = b"\x89PNG fake qr image"
VIDEO_SIZE = 4 * 1024**3
# 假直链的有效期，和真实 115 的量级相当
LINK_TTL = timedelta(hours=2)

# 契约测试扫描根目录时期望的全部媒体相对路径
CONTRACT_PATHS = frozenset(
    {
        "影视/电影/Film (2024)/Film.mkv",
        "影视/电影/Film (2024)/Film.srt",
        "影视/电影/Film (2024)/Film.nfo",
        "删除我.mkv",
    }
)

ACCOUNT = AccountInfo(
    user_id="100",
    user_name="家庭影院",
    is_vip=True,
    vip_expires="2027-05-01",
    space_total=10 * 1024**4,
    space_used=3 * 1024**4,
)


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


class FakeCloud115:
    """内存里的 115，记录每次调用，供测试断言。"""

    def __init__(self, clock: Clock | None = None, first_id: int = 1000) -> None:
        # 用来给假直链算过期时间；为 None 时直链不带过期时间
        self.clock = clock
        self.calls: list[str] = []
        # 宿主下发的代理，按 configure 调用顺序记录
        self.proxies: list[str | None] = []
        self.closed = False
        # 每次取直链的（pickcode，UA）
        self.link_requests: list[tuple[str, str]] = []
        # 设置后每次调用都抛出，直到清除，模拟持续的风控或网络故障
        self.error: Cloud115Error | None = None
        # 只对指定方法生效的错误，例如 {"export_start": RateLimited()}，触发一次后移除
        self.errors_once: dict[str, Cloud115Error] = {}
        self.valid_cookies = {VALID_COOKIE, QR_COOKIE}
        self.qr_statuses: list[int] = []
        # 接下来提交导出时，前几次返回「已有导出任务」
        self.export_busy_times = 0
        # 每个导出任务要轮询几次才完成
        self.export_polls = 1
        # 列这些目录时返回暂时性错误
        self.broken_dirs: set[str] = set()
        # 生活事件，从旧到新；life_apis 记录每次拉取用的接口（app 或 web）
        self.life: list[LifeEvent] = []
        self.life_apis: list[str] = []
        self.life_shown = False
        # 为 True 时 App 版生活事件接口返回 405
        self.life_app_rejected = False
        # 副本刚复制出来后，前几次取直链返回「文件上传不完整」
        self.copy_not_ready_times = 0
        self._not_ready: dict[str, int] = {}
        self._ids = itertools.count(first_id)
        self.nodes: dict[str, Node] = {"0": Node("0", "根目录", None, is_dir=True)}
        self._exports: dict[str, tuple[str, str, int]] = {}
        self._export_results: dict[str, list[str]] = {}

    # ---- 编排目录树 ----

    def add_dir(self, parent: str, name: str) -> str:
        return self._add(Node(self._next_id(), name, parent, is_dir=True))

    def add_file(
        self,
        parent: str,
        name: str,
        size: int | None = None,
        sha1: str | None = None,
        content: bytes = b"",
    ) -> str:
        if size is None:
            # 默认体积贴近真实：视频按正片的大小，不会被当作附加内容
            size = VIDEO_SIZE if media_kind(name) == "video" else 1024
        if content:
            size = len(content)
            sha1 = sha1 or hashlib.sha1(content).hexdigest().upper()
        sha1 = sha1 or hashlib.sha1(f"{parent}/{name}".encode()).hexdigest().upper()
        node = Node(self._next_id(), name, parent, is_dir=False, size=size, sha1=sha1)
        node.content = content
        return self._add(node)

    def make_path(self, path: str) -> str:
        """按「a/b/c.mkv」建出整条路径，最后一段带扩展名时建成文件，返回最后一段的 ID。"""
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

    def child(self, parent: str, name: str) -> str | None:
        for child_id in self.nodes[parent].children:
            if self.nodes[child_id].name == name:
                return child_id
        return None

    def remove(self, node_id: str) -> None:
        node = self.nodes[node_id]
        for child_id in list(node.children):
            self.remove(child_id)
        assert node.parent is not None
        self.nodes[node.parent].children.remove(node_id)
        del self.nodes[node_id]

    def move(self, node_id: str, new_parent: str, new_name: str | None = None) -> None:
        node = self.nodes[node_id]
        assert node.parent is not None
        self.nodes[node.parent].children.remove(node_id)
        node.parent = new_parent
        node.name = new_name or node.name
        self.nodes[new_parent].children.append(node_id)

    def files_in(self, cid: str) -> list[str]:
        return [self.nodes[c].name for c in self.nodes[cid].children]

    # ---- 网关接口 ----

    def configure(self, proxy: str | None) -> None:
        self.proxies.append(proxy)

    def close(self) -> None:
        self.closed = True

    def qr_token(self, device: str) -> QrToken:
        self._enter("qr_token")
        return QrToken(uid="qr-uid", time=1700000000, sign="sign")

    def qr_image(self, uid: str) -> bytes:
        self._enter("qr_image")
        return QR_IMAGE

    def qr_status(self, token: QrToken) -> int:
        self._enter("qr_status")
        return self.qr_statuses.pop(0) if self.qr_statuses else QrStatus.WAITING

    def qr_result(self, uid: str, device: str) -> str:
        self._enter("qr_result")
        return QR_COOKIE

    def account_info(self, cookie: str) -> AccountInfo:
        self._enter("account_info", cookie)
        return ACCOUNT

    def list_dir(self, cookie: str, cid: str) -> DirListing:
        self._enter("list_dir", cookie)
        if cid in self.broken_dirs:
            raise Cloud115Unavailable()
        node = self._dir(cid)
        entries = []
        for child_id in node.children:
            child = self.nodes[child_id]
            entries.append(
                CloudEntry(
                    id=child.id,
                    name=child.name,
                    is_dir=child.is_dir,
                    size=child.size,
                    pickcode=f"pc{child.id}",  # 真实 115 的目录也带 pickcode
                    sha1=child.sha1,
                )
            )
        return DirListing(path=self._path(cid), entries=entries)

    def dir_path(self, cookie: str, cid: str) -> list[DirRef]:
        self._enter("dir_path", cookie)
        self._dir(cid)
        return self._path(cid)

    def make_dir(self, cookie: str, name: str) -> str:
        self._enter("make_dir", cookie)
        current = "0"
        for part in name.split("/"):
            current = self.child(current, part) or self.add_dir(current, part)
        return current

    def copy_file(self, cookie: str, pickcode: str, target_cid: str) -> None:
        self._enter("copy_file", cookie)
        node = self.nodes.get(pickcode.removeprefix("pc"))
        if node is None or node.is_dir:
            raise FileGone()
        self._dir(target_cid)
        copy_id = self.add_file(target_cid, node.name, node.size, node.sha1)
        self._not_ready[copy_id] = self.copy_not_ready_times

    def recent_files(self, cookie: str, cid: str, limit: int) -> list[CloudEntry]:
        self._enter("recent_files", cookie)
        files = [self.nodes[i] for i in self._dir(cid).children if not self.nodes[i].is_dir]
        # 节点 ID 递增，后建的就是最近的
        files.sort(key=lambda node: int(node.id), reverse=True)
        return [
            CloudEntry(node.id, node.name, False, node.size, f"pc{node.id}", node.sha1)
            for node in files[:limit]
        ]

    def export_start(self, cookie: str, cid: str, target_cid: str) -> str:
        self._enter("export_start", cookie)
        if self.export_busy_times > 0:
            self.export_busy_times -= 1
            raise ExportBusy()
        self._dir(cid)
        export_id = self._next_id()
        self._exports[export_id] = (cid, target_cid, self.export_polls)
        return export_id

    def export_status(self, cookie: str, export_id: str) -> str | None:
        self._enter("export_status", cookie)
        cid, target, polls = self._exports[export_id]
        if polls > 1:
            self._exports[export_id] = (cid, target, polls - 1)
            return None
        # 和 115 一样把导出结果写成目标目录里的一个文件，读完要由调用方删除
        file_id = self.add_file(target, f"{self.nodes[cid].name}_目录树.txt")
        self._export_results[file_id] = self._walk(cid, "")
        del self._exports[export_id]
        return file_id

    def export_read(self, cookie: str, file_id: str) -> list[str]:
        self._enter("export_read", cookie)
        return self._export_results[file_id]

    def read_file(self, cookie: str, file_id: str, max_bytes: int) -> bytes:
        self._enter("read_file", cookie)
        node = self.nodes.get(file_id)
        if node is None or node.is_dir:
            raise FileGone()
        if len(node.content) > max_bytes:
            raise Cloud115Unavailable("文件太大，没有读取")
        return node.content

    def delete_file(self, cookie: str, file_id: str) -> None:
        self._enter("delete_file", cookie)
        if file_id in self.nodes:
            self.remove(file_id)

    def direct_link(self, cookie: str, pickcode: str, user_agent: str) -> DirectLink:
        self._enter("direct_link", cookie)
        self.link_requests.append((pickcode, user_agent))
        node = self.nodes.get(pickcode.removeprefix("pc"))
        if node is None or node.is_dir:
            raise FileGone()
        if self._not_ready.get(node.id):
            self._not_ready[node.id] -= 1
            raise CopyNotReady()
        expires_at = self.clock.now() + LINK_TTL if self.clock else None
        expiry = f"t={int(expires_at.timestamp())}&" if expires_at else ""
        # 每次取链得到不同的地址，测试据此判断是否命中缓存
        ua = hashlib.sha1(user_agent.encode()).hexdigest()[:8]
        url = (
            f"https://cdn.fake115.test/{pickcode}/{quote(node.name)}"
            f"?{expiry}ua={ua}&n={len(self.link_requests)}"
        )
        return DirectLink(url=url, expires_at=expires_at)

    def life_events(
        self, cookie: str, after_id: int | None, after_time: int | None, web: bool
    ) -> list[LifeEvent]:
        self._enter("life_events", cookie)
        if not web and self.life_app_rejected:
            raise LifeApiRejected()
        self.life_apis.append("web" if web else "app")
        newest_first = self.life[::-1]
        if after_id is None:
            return newest_first[:1]
        events = []
        # 和真实网关一样从新到旧翻，碰到旧的就停
        for event in newest_first:
            if event.id <= after_id or (after_time and event.time and event.time < after_time):
                break
            events.append(event)
        return events[:LIFE_EVENT_LIMIT]

    def life_show(self, cookie: str) -> None:
        self._enter("life_show", cookie)
        self.life_shown = True

    def event(self, type_: int, node_id: str, time: int = 0) -> LifeEvent:
        """按节点当前的位置记一条生活事件。删除事件要在删除节点之前记。"""
        node = self.nodes[node_id]
        assert node.parent is not None
        event = LifeEvent(
            id=len(self.life) + 1,
            type=type_,
            file_id=node.id,
            parent_id=node.parent,
            name=node.name,
            is_dir=node.is_dir,
            time=time,
        )
        self.life.append(event)
        return event

    # ---- 内部 ----

    def _walk(self, cid: str, prefix: str) -> list[str]:
        paths = []
        for child_id in self.nodes[cid].children:
            child = self.nodes[child_id]
            path = f"{prefix}{child.name}"
            # 和 115 一样，空目录不会出现在导出结果里
            if child.is_dir:
                below = self._walk(child_id, f"{path}/")
                if below:
                    paths.append(path)
                    paths.extend(below)
            else:
                paths.append(path)
        return paths

    def _dir(self, cid: str) -> Node:
        node = self.nodes.get(cid)
        if node is None or not node.is_dir:
            raise DirNotFound()
        return node

    def _path(self, cid: str) -> list[DirRef]:
        refs = []
        node = self.nodes[cid]
        while node.parent is not None:
            refs.append(DirRef(node.id, node.name))
            node = self.nodes[node.parent]
        return refs[::-1]

    def _add(self, node: Node) -> str:
        assert node.parent is not None
        self.nodes[node.id] = node
        self.nodes[node.parent].children.append(node.id)
        return node.id

    def _next_id(self) -> str:
        return str(next(self._ids))

    def _enter(self, name: str, cookie: str | None = None) -> None:
        self.calls.append(name)
        if self.error is not None:
            raise self.error
        if name in self.errors_once:
            raise self.errors_once.pop(name)
        if cookie is not None and cookie not in self.valid_cookies:
            raise CredentialInvalid()


def build_contract_tree(gateway: FakeCloud115) -> dict[str, str]:
    """契约测试用的固定目录树；父子进程各自构建时节点 ID 相同。

    返回几个关键位置的节点 ID，供 unselectable_locator 和 delete_target 使用。
    """
    movie = gateway.make_path("影视/电影/Film (2024)/Film.mkv")
    video = gateway.nodes[movie]
    video.content = b"video-bytes"
    video.size = len(video.content)
    movie_dir = video.parent
    assert movie_dir is not None
    gateway.add_file(movie_dir, "Film.srt", content=b"subtitle")
    gateway.add_file(movie_dir, "Film.nfo", content=b"<movie/>")
    gateway.add_file("0", "删除我.mkv", content=b"temporary")
    gateway.make_path("光圈临时目录/多端播放/旧副本.mkv")
    return {
        "movie": movie,
        "movie_dir": movie_dir,
        "scratch": gateway.child("0", "光圈临时目录") or "",
        "delete": gateway.child("0", "删除我.mkv") or "",
    }
