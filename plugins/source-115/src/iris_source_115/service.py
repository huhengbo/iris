"""115 接入的唯一入口：凭据、扫码登录和所有经调度器的网关调用。"""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from iris_source_115.clock import Clock
from iris_source_115.credential import CredentialMeta, CredentialStore, device_from_cookie
from iris_source_115.errors import (
    CredentialInvalid,
    CredentialMissing,
    InvalidCookie,
    QrSessionMissing,
    UnsupportedDevice,
)
from iris_source_115.gateway import (
    AccountInfo,
    Cloud115Gateway,
    CloudEntry,
    DirectLink,
    DirListing,
    DirRef,
    LifeEvent,
    QrStatus,
    QrToken,
)
from iris_source_115.scheduler import (
    ApiGroup,
    BreakerState,
    Priority,
    RequestScheduler,
)

# 115 的二维码大约 5 分钟失效，这里提前收掉，避免用户扫一个快过期的码
QR_SESSION_TTL = timedelta(minutes=3)


@dataclass(frozen=True)
class Device:
    id: str
    label: str
    recommended: bool = False
    note: str | None = None


# 扫码时可选的设备身份。同一身份同时只能有一个会话，由管理员避开其他工具在用的身份（ADR 0013）。
DEVICES = (
    Device("alipaymini", "支付宝小程序", recommended=True),
    Device("wechatmini", "微信小程序"),
    Device("tv", "安卓电视"),
    Device("apple_tv", "Apple TV"),
    Device("harmony", "鸿蒙"),
    Device("qandroid", "115 管理（安卓）"),
    Device("os_windows", "Windows 客户端"),
    Device("os_mac", "macOS 客户端"),
    Device("os_linux", "Linux 客户端"),
    Device("android", "安卓 App", note="可能挤掉手机上的 115 App"),
    Device("ios", "iPhone App", note="可能挤掉手机上的 115 App"),
    Device("ipad", "iPad App", note="可能挤掉平板上的 115 App"),
    Device("web", "网页版", note="最容易触发「IP 登录异常」，不建议"),
)
_DEVICE_IDS = {device.id for device in DEVICES}


@dataclass(frozen=True)
class QrLogin:
    session_id: str
    image_png: bytes
    expires_at: datetime


@dataclass(frozen=True)
class QrProgress:
    status: str
    message: str
    account: AccountInfo | None = None


@dataclass
class _QrSession:
    token: QrToken
    device: str
    expires_at: datetime


@dataclass(frozen=True)
class Cloud115Status:
    credential: CredentialMeta | None
    # 两组接口各自的熔断状态
    states: dict[ApiGroup, BreakerState]
    # 受限的分组里恢复得最晚的那组；都没受限时是网页版那组
    breaker: BreakerState
    # 至少有一组接口在熔断
    rate_limited: bool
    # 正在熔断的分组和各自的状态
    limited: dict[ApiGroup, BreakerState]


class Cloud115Service:
    """115 接入的唯一入口：凭据、扫码登录和所有经调度器的网关调用。"""

    def __init__(
        self,
        gateway: Cloud115Gateway,
        scheduler: RequestScheduler,
        credentials: CredentialStore,
        clock: Clock,
    ) -> None:
        self._gateway = gateway
        self.scheduler = scheduler
        self._credentials = credentials
        self._clock = clock
        self._lock = threading.Lock()
        self._qr_sessions: dict[str, _QrSession] = {}

    def status(self) -> Cloud115Status:
        now = self._clock.now()
        states = {group: breaker.state for group, breaker in self.scheduler.breakers.items()}
        limited = {
            group: state
            for group, state in states.items()
            if state.resume_at is not None and now < state.resume_at
        }
        breaker = max(
            limited.values(),
            key=lambda state: state.resume_at or now,
            default=states[ApiGroup.WEB],
        )
        return Cloud115Status(
            credential=self._credentials.meta(),
            states=states,
            breaker=breaker,
            rate_limited=bool(limited),
            limited=limited,
        )

    def call[T](
        self, priority: Priority, call: Callable[[str], T], group: ApiGroup = ApiGroup.WEB
    ) -> T:
        """用已保存的凭据调用网关，group 是这次请求走的接口分组。

        凭据被 115 拒绝后标记失效，之后的调用直接失败，等管理员重新登录。
        """
        stored = self._credentials.cookie()
        if stored is None:
            raise CredentialMissing()
        cookie, meta = stored
        if meta.invalid_at is not None:
            raise CredentialInvalid()
        try:
            return self.scheduler.run(priority, lambda: call(cookie), group)
        except CredentialInvalid:
            self._credentials.mark_invalid(cookie)
            raise

    def account(self) -> AccountInfo:
        return self.call(Priority.INTERACTIVE, self._gateway.account_info)

    # ---- 来源接口使用的网关操作 ----

    def list_dir(self, cid: str, priority: Priority = Priority.INTERACTIVE) -> DirListing:
        return self.call(priority, lambda cookie: self._gateway.list_dir(cookie, cid))

    def dir_path(self, cid: str, priority: Priority = Priority.INTERACTIVE) -> list[DirRef]:
        return self.call(priority, lambda cookie: self._gateway.dir_path(cookie, cid))

    def make_dir(self, name: str, priority: Priority = Priority.INTERACTIVE) -> str:
        return self.call(priority, lambda cookie: self._gateway.make_dir(cookie, name))

    def export_start(
        self, cid: str, target_cid: str, priority: Priority = Priority.INTERACTIVE
    ) -> str:
        return self.call(
            priority, lambda cookie: self._gateway.export_start(cookie, cid, target_cid)
        )

    def export_status(
        self, export_id: str, priority: Priority = Priority.INTERACTIVE
    ) -> str | None:
        return self.call(priority, lambda cookie: self._gateway.export_status(cookie, export_id))

    def export_read(self, file_id: str, priority: Priority = Priority.INTERACTIVE) -> list[str]:
        return self.call(priority, lambda cookie: self._gateway.export_read(cookie, file_id))

    def read_file(
        self, file_id: str, max_bytes: int, priority: Priority = Priority.INTERACTIVE
    ) -> bytes:
        return self.call(
            priority, lambda cookie: self._gateway.read_file(cookie, file_id, max_bytes)
        )

    def delete_file(self, file_id: str, priority: Priority = Priority.INTERACTIVE) -> None:
        self.call(priority, lambda cookie: self._gateway.delete_file(cookie, file_id))

    def copy_file(
        self, pickcode: str, target_cid: str, priority: Priority = Priority.INTERACTIVE
    ) -> None:
        self.call(priority, lambda cookie: self._gateway.copy_file(cookie, pickcode, target_cid))

    def recent_files(
        self, cid: str, limit: int, priority: Priority = Priority.INTERACTIVE
    ) -> list[CloudEntry]:
        return self.call(priority, lambda cookie: self._gateway.recent_files(cookie, cid, limit))

    def direct_link(
        self, pickcode: str, user_agent: str, priority: Priority = Priority.INTERACTIVE
    ) -> DirectLink:
        return self.call(
            priority,
            lambda cookie: self._gateway.direct_link(cookie, pickcode, user_agent),
            ApiGroup.APP,
        )

    def life_show(self, priority: Priority = Priority.INTERACTIVE) -> None:
        self.call(priority, self._gateway.life_show)

    def life_events(
        self,
        after_id: int | None,
        after_time: int | None,
        web: bool,
        priority: Priority = Priority.INTERACTIVE,
    ) -> list[LifeEvent]:
        return self.call(
            priority,
            lambda cookie: self._gateway.life_events(cookie, after_id, after_time, web),
            ApiGroup.WEB if web else ApiGroup.APP,
        )

    def save_cookie(self, cookie: str) -> AccountInfo:
        cookie = _normalize_cookie(cookie)
        try:
            account = self._run(lambda: self._gateway.account_info(cookie))
        except CredentialInvalid:
            raise CredentialInvalid("这份 Cookie 已失效或不完整，115 拒绝了它") from None
        self._credentials.save(cookie, device_from_cookie(cookie), account.user_name)
        return account

    def clear_credential(self) -> None:
        self._credentials.clear()

    def start_qr(self, device: str) -> QrLogin:
        if device not in _DEVICE_IDS:
            raise UnsupportedDevice()
        token = self._run(lambda: self._gateway.qr_token(device))
        image = self._run(lambda: self._gateway.qr_image(token.uid))
        session_id = secrets.token_urlsafe(16)
        expires_at = self._clock.now() + QR_SESSION_TTL
        with self._lock:
            self._prune()
            self._qr_sessions[session_id] = _QrSession(token, device, expires_at)
        return QrLogin(session_id, image, expires_at)

    def poll_qr(self, session_id: str) -> QrProgress:
        session = self._qr_session(session_id)
        if self._clock.now() >= session.expires_at:
            self.cancel_qr(session_id)
            return QrProgress("expired", "二维码已过期，请重新获取")
        status = self._run(lambda: self._gateway.qr_status(session.token))
        if status == QrStatus.WAITING:
            return QrProgress("waiting", "请用 115 App 扫码")
        if status == QrStatus.SCANNED:
            return QrProgress("scanned", "已扫码，请在手机上确认登录")
        if status == QrStatus.CONFIRMED:
            # 同一个会话只换取一次凭据，失败就让管理员重新扫码
            self.cancel_qr(session_id)
            cookie = self._run(lambda: self._gateway.qr_result(session.token.uid, session.device))
            account = self._run(lambda: self._gateway.account_info(cookie))
            self._credentials.save(cookie, session.device, account.user_name)
            return QrProgress("succeeded", "115 登录成功", account)
        self.cancel_qr(session_id)
        if status == QrStatus.CANCELLED:
            return QrProgress("cancelled", "已在手机上取消登录")
        return QrProgress("expired", "二维码已过期，请重新获取")

    def cancel_qr(self, session_id: str) -> None:
        with self._lock:
            self._qr_sessions.pop(session_id, None)

    def _qr_session(self, session_id: str) -> _QrSession:
        with self._lock:
            self._prune()
            session = self._qr_sessions.get(session_id)
        if session is None:
            raise QrSessionMissing()
        return session

    def _prune(self) -> None:
        # 过期一段时间后才清理，让轮询方先拿到一次「已过期」
        cutoff = self._clock.now() - QR_SESSION_TTL
        for key in [k for k, s in self._qr_sessions.items() if s.expires_at < cutoff]:
            del self._qr_sessions[key]

    def _run[T](self, call: Callable[[], T]) -> T:
        return self.scheduler.run(Priority.INTERACTIVE, call)


def _normalize_cookie(cookie: str) -> str:
    cookie = cookie.strip()
    # 从浏览器开发者工具复制时常带着请求头名
    if cookie.lower().startswith("cookie:"):
        cookie = cookie[len("cookie:") :].strip()
    if "UID=" not in cookie or any(ch in cookie for ch in "\r\n"):
        raise InvalidCookie()
    return cookie
