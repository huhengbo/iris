"""基于 p115client 的 115 网关。

p115client 在导入时会创建缓存目录、修改全局 warnings 设置，所以延迟到第一次调用时才导入，
测试和只用假网关的场景不受影响。p115client 的方法默认不检查业务错误，每个 JSON 响应都要显式
check_response。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from urllib3_future import PoolManager, ProxyManager
from urllib3_future.exceptions import HTTPError as Urllib3HTTPError

from iris_source_115.errors import (
    Cloud115Error,
    Cloud115Unavailable,
    CopyNotReady,
    CredentialInvalid,
    DirNotFound,
    ExportBusy,
    FileGone,
    LifeApiRejected,
    RateLimited,
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

if TYPE_CHECKING:
    from p115client import P115Client

logger = logging.getLogger(__name__)

# 关闭 urllib3 的自动重试：重试会绕过请求调度器的限速，还会放大风控
_HTTP: dict[str, Any] = {"timeout": 30, "retries": False}
# 扫码状态是长轮询，状态不变时 115 大约 30 秒后才返回空 data
_QR_STATUS_HTTP: dict[str, Any] = {"timeout": 45, "retries": False}
# 刚复制出来的文件还没处理完时，取直链返回这个错误（参考 p115strmhelper 的多端播放）
_COPY_NOT_READY = "文件上传不完整"
# 列目录用大分页减少请求次数（ADR 0007），1150 是实际验证过 115 能接受的值
FS_PAGE_SIZE = 1150


def _build_session(proxy: str | None) -> PoolManager:
    """按代理协议建连接池；115 的请求和直链下载共用同一个 session。"""
    if proxy is None:
        session = PoolManager(num_pools=10, maxsize=64)
    elif (scheme := urlsplit(proxy).scheme.casefold()) in {"http", "https"}:
        session = ProxyManager(proxy, num_pools=10, maxsize=64)
    elif scheme in {"socks4", "socks4a", "socks5", "socks5h"}:
        from urllib3_future.contrib.socks import SOCKSProxyManager

        session = SOCKSProxyManager(proxy, num_pools=10, maxsize=64)
    else:
        raise ValueError(f"不支持的代理协议：{scheme}")
    return session


class P115Gateway:
    """基于 p115client 的 Cloud115 网关；代理变化时替换底层连接池。"""

    def __init__(self, proxy: str | None = None) -> None:
        self._lock = threading.Lock()
        self._session = _build_session(proxy)

    def configure(self, proxy: str | None) -> None:
        """按宿主下发的代理重建连接池；旧连接池没有请求引用后由 GC 关闭。"""
        try:
            session = _build_session(proxy)
        except ValueError as exc:
            logger.warning("invalid 115 proxy: %s", exc)
            return
        with self._lock:
            self._session = session

    def close(self) -> None:
        with self._lock:
            self._session.clear()

    def _http(self, *, qr: bool = False) -> dict[str, Any]:
        with self._lock:
            session = self._session
        return {**(_QR_STATUS_HTTP if qr else _HTTP), "session": session}

    def qr_token(self, device: str) -> QrToken:
        from p115client import P115Client

        with _translate_errors():
            data = _check(P115Client.login_qrcode_token(**self._http()))["data"]
            return QrToken(uid=str(data["uid"]), time=int(data["time"]), sign=str(data["sign"]))

    def qr_image(self, uid: str) -> bytes:
        from p115client import P115Client

        with _translate_errors():
            # p115client 0.0.9.7.2 的 login_qrcode 构造了 uid 参数却没有传给请求，
            # 拿到的是不绑定会话的通用图，App 扫了会提示「扫描失败」，所以显式传 params
            image = P115Client.login_qrcode(uid, params={"uid": uid}, **self._http())
            if not isinstance(image, bytes | bytearray):
                raise Cloud115Unavailable("115 没有返回二维码图片")
            return bytes(image)

    def qr_status(self, token: QrToken) -> int:
        from p115client import P115Client

        payload = {"uid": token.uid, "time": token.time, "sign": token.sign}
        with _translate_errors():
            response = P115Client.login_qrcode_scan_status(payload, **self._http(qr=True))
            data = _check(response)["data"]
            return int(data.get("status", QrStatus.WAITING))

    def qr_result(self, uid: str, device: str) -> str:
        from p115client import P115Client

        try:
            with _translate_errors():
                response = P115Client.login_qrcode_scan_result(uid, app=device, **self._http())
                return _cookie_text(_check(response)["data"]["cookie"])
        except CredentialInvalid as exc:
            # 换取凭据时的登录类错误（如「IP 登录异常」）是这次登录被拒，不是已保存的凭据失效
            raise Cloud115Unavailable(
                "115 拒绝了这次登录，可能是该设备身份触发了 IP 登录异常，换一个设备身份再试"
            ) from exc

    def account_info(self, cookie: str) -> AccountInfo:
        with _translate_errors():
            client = _client(cookie)
            # 先查空间：凭据失效时它返回 JSON 的「登录超时」(990001)，能归为凭据失效；
            # my.115.com 的 user_my_info 此时返回 HTML 登录页，只能当作暂时性错误
            space = _check(client.fs_space_summury(**self._http()))
            identity = _check(client.user_my_info(**self._http()))["data"]
            user_id = str(identity["uid"])
            # user_info 既可当静态方法也可当实例方法，mypy 认不出实例调用
            user = _check(
                client.user_info(  # type: ignore[misc]
                    {"uid": user_id}, **self._http()
                )
            )["data"]
            summary = space["space_summury"]
            total = int(summary["all_total"]["size"])
            remain = int(summary["all_remain"]["size"])
            vip = identity.get("vip")
            is_vip = bool(int(user.get("is_vip") or 0))
            return AccountInfo(
                user_id=user_id,
                user_name=str(user.get("user_name") or user.get("nickname") or user_id),
                is_vip=is_vip,
                vip_expires=str(vip["expire_str"])
                if is_vip and isinstance(vip, dict) and vip.get("expire_str")
                else None,
                space_total=total,
                space_used=max(total - remain, 0),
            )

    def list_dir(self, cookie: str, cid: str) -> DirListing:
        entries: list[CloudEntry] = []
        with _translate_errors():
            client = _client(cookie)
            while True:
                payload = {
                    "cid": cid,
                    "limit": FS_PAGE_SIZE,
                    "offset": len(entries),
                    "show_dir": 1,
                }
                page = _check(client.fs_files(payload, **self._http()))
                path = _dir_path(page, cid)
                items = page.get("data") or []
                entries.extend(_entry(item) for item in items)
                if not items or len(entries) >= int(page.get("count") or 0):
                    return DirListing(path=path, entries=entries)

    def dir_path(self, cookie: str, cid: str) -> list[DirRef]:
        with _translate_errors():
            payload = {"cid": cid, "limit": 1, "show_dir": 1}
            return _dir_path(_check(_client(cookie).fs_files(payload, **self._http())), cid)

    def make_dir(self, cookie: str, name: str) -> str:
        with _translate_errors():
            # fs_makedirs 是「按路径取 ID，不存在就创建」，重复调用返回同一个目录
            data = _check(_client(cookie).fs_makedirs(name, **self._http()))["data"]
            return str(data["file_id"])

    def export_start(self, cookie: str, cid: str, target_cid: str) -> str:
        from p115client.exception import P115BusyOSError

        payload = {"file_ids": cid, "target": f"U_1_{target_cid}"}
        with _translate_errors():
            try:
                response = _check(_client(cookie).fs_export_dir(payload, **self._http()))
            except P115BusyOSError as exc:
                # errno 990005「你的账号有类似任务正在处理」
                raise ExportBusy() from exc
            return str(response["data"]["export_id"])

    def export_status(self, cookie: str, export_id: str) -> str | None:
        with _translate_errors():
            data = _check(_client(cookie).fs_export_dir_status(export_id, **self._http()))["data"]
            # 进行中时 data 是空列表
            return str(data["file_id"]) if data else None

    def export_read(self, cookie: str, file_id: str) -> list[str]:
        from p115client.tool.export_dir import (
            export_dir_iter_line,
            export_dir_parse_iter_depth_name,
        )

        with _translate_errors():
            lines = export_dir_iter_line([_download(cookie, file_id, None, self._http())])
            return _export_paths(export_dir_parse_iter_depth_name(lines))

    def read_file(self, cookie: str, file_id: str, max_bytes: int) -> bytes:
        with _translate_errors():
            try:
                data = _download(cookie, file_id, max_bytes + 1, self._http())
            except OSError as exc:
                # 115 把不存在的文件归成 OSError（ENOENT），对宿主是 ResourceNotFound
                if isinstance(exc, (FileNotFoundError, IsADirectoryError)):
                    raise FileGone() from exc
                raise
        if len(data) > max_bytes:
            raise Cloud115Unavailable("文件太大，没有读取")
        return data

    def delete_file(self, cookie: str, file_id: str) -> None:
        with _translate_errors():
            try:
                _check(_client(cookie).fs_delete(file_id, **self._http()))
            except (FileNotFoundError, IsADirectoryError) as exc:
                # 删除任务允许把云端已经消失的目标视为幂等成功。
                raise FileGone() from exc

    def copy_file(self, cookie: str, pickcode: str, target_cid: str) -> None:
        with _translate_errors():
            client = _client(cookie)
            try:
                _check(client.fs_copy(client.to_id(pickcode), pid=target_cid, **self._http()))
            except (FileNotFoundError, IsADirectoryError) as exc:
                raise FileGone() from exc

    def recent_files(self, cookie: str, cid: str, limit: int) -> list[CloudEntry]:
        with _translate_errors():
            payload = {
                "cid": cid,
                "limit": limit,
                "offset": 0,
                "show_dir": 0,
                "o": "user_ptime",
                "asc": 0,
            }
            page = _check(_client(cookie).fs_files(payload, **self._http()))
            return [_entry(item) for item in page.get("data") or []]

    def direct_link(self, cookie: str, pickcode: str, user_agent: str) -> DirectLink:
        with _translate_errors():
            try:
                # 安卓端下载接口：传 UA 时返回 f=1 直链，None 时不绑定 UA。
                url = _client(cookie).download_url(
                    pickcode, user_agent=user_agent, app="android", **self._http()
                )
            except OSError as exc:
                # 先认副本未就绪：p115client 可能把它归成「文件不存在」
                if _error_text(exc) == _COPY_NOT_READY:
                    raise CopyNotReady() from exc
                if isinstance(exc, (FileNotFoundError, IsADirectoryError)):
                    raise FileGone() from exc
                raise
            if not url:
                raise FileGone()
            return DirectLink(url=str(url), expires_at=_link_expiry(str(url)))

    def life_events(
        self, cookie: str, after_id: int | None, after_time: int | None, web: bool
    ) -> list[LifeEvent]:
        events: list[LifeEvent] = []
        with _translate_errors():
            client = _client(cookie)
            # 平时只有几条新事件，第一页取少一点
            limit = 1 if after_id is None else 64
            while True:
                payload = {"type": "", "date": "", "limit": limit, "offset": len(events)}
                if web:
                    resp = client.life_behavior_detail(payload, **self._http())
                else:
                    try:
                        resp = client.life_behavior_detail_app(
                            payload, app="android", **self._http()
                        )
                    except HTTPError as exc:
                        if exc.code != 405:
                            raise
                        logger.warning("115 life app api rejected: HTTP 405")
                        raise LifeApiRejected() from exc
                data = _check(resp)["data"]
                page = [_life_event(item) for item in data.get("list") or []]
                for event in page:
                    if after_id is not None and event.id <= after_id:
                        return events
                    # 参考 p115strmhelper：ID 之外再看时间，比上次处理到的还早的事件不再处理
                    if after_time is not None and event.time and event.time < after_time:
                        return events
                    events.append(event)
                if (
                    after_id is None
                    or not page
                    or len(events) >= min(int(data.get("count") or 0), LIFE_EVENT_LIMIT)
                ):
                    return events[:LIFE_EVENT_LIMIT]
                limit = 1000

    def life_show(self, cookie: str) -> None:
        with _translate_errors():
            _check(_client(cookie).life_calendar_setoption(1, **self._http()))


def _client(cookie: str) -> P115Client:
    from p115client import P115Client

    # 构造函数只解析 Cookie，不发请求；cookies 不能传 None，否则会触发交互式登录
    return P115Client(cookie)


def _download(cookie: str, file_id: str, limit: int | None, http: dict[str, Any]) -> bytes:
    """取下载地址并读出内容；limit 为 None 时读完整个文件。"""
    from p115client.exception import P115FileTooBig

    client = _client(cookie)
    try:
        url = client.download_url(int(file_id), app="web", **http)
    except P115FileTooBig:
        url = client.download_url(int(file_id), app="android", **http)
    with client.open(url, **http) as response:
        data: bytes = response.read() if limit is None else response.read(limit)
    return data


def _check(response: Any) -> dict[str, Any]:
    from p115client.client import check_response

    result: dict[str, Any] = check_response(response)
    return result


def _entry(item: dict[str, Any]) -> CloudEntry:
    # 115 网页接口里文件有 fid（cid 是所在目录），目录只有 cid
    is_dir = "fid" not in item
    return CloudEntry(
        id=str(item["cid"] if is_dir else item["fid"]),
        name=str(item["n"]),
        is_dir=is_dir,
        size=int(item.get("s") or 0),
        pickcode=str(item.get("pc") or ""),
        sha1="" if is_dir else str(item.get("sha") or "").upper(),
    )


def _life_event(item: dict[str, Any]) -> LifeEvent:
    # 字段名参考 p115client 的 updatedb；file_category 为 0 是目录。拿不准时按目录处理：
    # 目录事件总会触发同步，多列几次目录总比漏掉变动好
    category = item.get("file_category")
    return LifeEvent(
        id=int(item["id"]),
        type=int(item["type"]),
        file_id=str(item["file_id"]),
        parent_id=str(item.get("parent_id") or ""),
        name=str(item.get("file_name") or ""),
        is_dir=category is None or str(category) == "0",
        time=int(item.get("update_time") or 0),
    )


def _dir_path(page: dict[str, Any], cid: str) -> list[DirRef]:
    # 115 把不存在的 cid 当作根目录处理，只能靠返回路径的最后一项来确认
    path = [DirRef(id=str(p["cid"]), name=str(p["name"])) for p in page.get("path") or []]
    if not path or path[-1].id != str(cid):
        raise DirNotFound()
    return path[1:]


def _export_paths(nodes: Iterable[tuple[int, str]]) -> list[str]:
    """把导出文件的（层级，名字）序列转成相对路径。

    第 0 层是被导出目录的上一级（在根目录下时为空），第 1 层是被导出的目录本身，
    从第 2 层开始才是它的内容。
    """
    stack: list[str] = []
    paths: list[str] = []
    for depth, name in nodes:
        if depth < 2:
            continue
        del stack[depth - 2 :]
        stack.append(name)
        paths.append("/".join(stack))
    return paths


def _link_expiry(url: str) -> datetime | None:
    """直链的 t 参数是过期时间的 Unix 时间戳。"""
    values = parse_qs(urlsplit(url).query).get("t")
    if not values or not values[0].isdigit():
        return None
    return datetime.fromtimestamp(int(values[0]), UTC)


def _error_text(exc: BaseException) -> str:
    """p115client 把 115 的错误响应放在异常参数里，取出其中的错误说明。"""
    for arg in exc.args:
        if isinstance(arg, dict):
            return str(arg.get("error") or arg.get("message") or "")
    return ""


def _cookie_text(cookie: Any) -> str:
    if isinstance(cookie, dict):
        return "; ".join(f"{key}={value}" for key, value in cookie.items())
    if isinstance(cookie, str) and cookie.strip():
        return cookie.strip()
    raise Cloud115Unavailable("115 没有返回登录凭据")


@contextmanager
def _translate_errors() -> Iterator[None]:
    from p115client.exception import P115AuthenticationError, P115OSError

    try:
        yield
    except Cloud115Error:
        raise
    except HTTPError as exc:
        if exc.code == 405:
            logger.warning("115 risk control: HTTP 405")
            raise RateLimited() from exc
        logger.warning("115 request failed: HTTP %s", exc.code)
        raise Cloud115Unavailable() from exc
    except P115AuthenticationError as exc:
        logger.warning("115 rejected the credential: %s", type(exc).__name__)
        raise CredentialInvalid() from exc
    except (P115OSError, Urllib3HTTPError, ConnectionError, TimeoutError) as exc:
        # 只记录异常类型：p115client 的异常消息里带完整响应，不能保证不含敏感信息
        logger.warning("115 request failed: %s", type(exc).__name__)
        raise Cloud115Unavailable() from exc
