"""115 网关的数据类型和协议。网关是访问 115 的唯一入口。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class AccountInfo:
    user_id: str
    user_name: str
    is_vip: bool
    # 115 返回的到期日期文本，例如 "2027-05-01"；非 VIP 时为 None
    vip_expires: str | None
    space_total: int
    space_used: int


@dataclass(frozen=True)
class LifeEvent:
    """115 生活事件（操作记录）里的一条。

    file_id 是文件或目录的 ID，parent_id 是事件发生后它所在的目录（删除时是删除前所在的目录）。
    """

    id: int
    type: int
    file_id: str
    parent_id: str
    name: str
    is_dir: bool
    # 事件时间（Unix 秒）；115 没给时为 0
    time: int = 0


@dataclass(frozen=True)
class QrToken:
    uid: str
    time: int
    sign: str


@dataclass(frozen=True)
class CloudEntry:
    """列目录得到的一项。目录没有 pickcode 以外的内容信息，size 为 0、sha1 为空。"""

    id: str
    name: str
    is_dir: bool
    size: int
    pickcode: str
    sha1: str = ""


@dataclass(frozen=True)
class DirRef:
    id: str
    name: str


@dataclass(frozen=True)
class DirListing:
    # 从根目录往下到当前目录的路径，不含根目录本身，最后一项就是当前目录；根目录时为空
    path: list[DirRef]
    entries: list[CloudEntry]


@dataclass(frozen=True)
class DirectLink:
    """115 的带时效 CDN 下载地址，访问时的 User-Agent 必须与取链时一致。"""

    url: str
    # 地址里带的过期时间；115 没给时为 None
    expires_at: datetime | None


class QrStatus:
    WAITING = 0
    SCANNED = 1
    CONFIRMED = 2
    EXPIRED = -1
    CANCELLED = -2


class Cloud115Gateway(Protocol):
    """访问 115 的唯一入口。实现内部把错误归为三类再抛出：

    - CredentialInvalid：凭据失效（JSON 形式的未登录、被踢下线）；
    - RateLimited：风控（HTTP 405 加 HTML 拦截页）；
    - Cloud115Unavailable：其他暂时性错误。

    不做自动重新登录。所有调用都要经过请求调度器，网关自己不限速。
    """

    def configure(self, proxy: str | None) -> None:
        """宿主代理变化时重建直连客户端。"""
        ...

    def close(self) -> None:
        """插件停止时关闭直连连接池。"""
        ...

    def qr_token(self, device: str) -> QrToken: ...

    def qr_image(self, uid: str) -> bytes: ...

    def qr_status(self, token: QrToken) -> int: ...

    def qr_result(self, uid: str, device: str) -> str:
        """扫码确认后换取 Cookie。"""
        ...

    def account_info(self, cookie: str) -> AccountInfo: ...

    def list_dir(self, cookie: str, cid: str) -> DirListing:
        """列出目录的全部内容（自动翻页）。目录不存在时抛出 DirNotFound。"""
        ...

    def dir_path(self, cookie: str, cid: str) -> list[DirRef]:
        """只取目录的路径（含它自己，不含根目录），一次请求。目录不存在时抛出 DirNotFound。"""
        ...

    def make_dir(self, cookie: str, name: str) -> str:
        """按根目录下的路径（如「a/b」）建目录并返回 ID，缺的上级一起建；已存在时返回已有的 ID。"""
        ...

    def export_start(self, cookie: str, cid: str, target_cid: str) -> str:
        """提交导出目录树任务，返回任务 ID。115 上已有导出任务时抛出 ExportBusy。"""
        ...

    def export_status(self, cookie: str, export_id: str) -> str | None:
        """导出完成时返回导出文件的 ID，还在进行时返回 None。"""
        ...

    def export_read(self, cookie: str, file_id: str) -> list[str]:
        """下载并解析导出文件，返回被导出目录下全部节点的相对路径（目录和文件不区分）。"""
        ...

    def read_file(self, cookie: str, file_id: str, max_bytes: int) -> bytes:
        """下载一个小文件（例如 NFO）的内容。超过 max_bytes 时抛出 Cloud115Unavailable。"""
        ...

    def delete_file(self, cookie: str, file_id: str) -> None:
        """删除文件或目录；删除目录时由 115 递归删除其全部内容。"""
        ...

    def copy_file(self, cookie: str, pickcode: str, target_cid: str) -> None:
        """把文件复制到 target_cid 目录。115 不返回副本的 ID，要列目录找。"""
        ...

    def recent_files(self, cookie: str, cid: str, limit: int) -> list[CloudEntry]:
        """目录里最近修改的文件，从新到旧，最多 limit 个，不含子目录。"""
        ...

    def direct_link(self, cookie: str, pickcode: str, user_agent: str) -> DirectLink:
        """用播放器的 User-Agent 取直链（App 版接口）。

        文件不存在时抛出 FileGone；刚复制出来的副本还没处理完时抛出 CopyNotReady。
        """
        ...

    def life_events(
        self, cookie: str, after_id: int | None, after_time: int | None, web: bool
    ) -> list[LifeEvent]:
        """比 (after_time, after_id) 新的生活事件，从新到旧，最多 LIFE_EVENT_LIMIT 条。

        ID 不大于 after_id，或时间早于 after_time（同一秒的仍算新）的事件不返回；after_time
        为 None 时只看 ID。after_id 为 None 时只返回最新的一条，用来确定从哪里开始监听。
        默认用 App 版接口，它返回 405 时抛出 LifeApiRejected；web 为 True 时用网页版接口
        （慢，但风控轻）。
        """
        ...

    def life_show(self, cookie: str) -> None:
        """打开 115 生活的「最近记录」开关：关着时 115 不记录生活事件。会修改账号设置。"""
        ...


# 一次最多取这么多条生活事件。超过时较早的变动由定时同步兜底
LIFE_EVENT_LIMIT = 5000
