"""115 凭据：Cookie 和设备身份明文存插件自己的 `state/credential.json`（0600）。

ADR 0028 明确允许插件把凭据明文存在自己的数据目录；Cookie 只在调用网关时读取，
经过任何插件接口都不会返回明文。
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from iris_plugin_sdk import read_json, write_json

from iris_source_115.clock import Clock, from_iso, to_iso

# Cookie 里 UID 形如 "123_R2_1700000000"，中间一段是登录设备的 ssoent
_UID_SSOENT = re.compile(r"(?:^|;)\s*UID=[^_;]+_([A-Za-z0-9]+)_")


@dataclass(frozen=True)
class CredentialMeta:
    device: str | None
    user_name: str | None
    saved_at: datetime
    # 115 明确拒绝过这份凭据的时间；None 表示目前可用
    invalid_at: datetime | None = None


class CredentialStore:
    """一个 115 账号的凭据，保存在插件自己的状态文件里。"""

    def __init__(self, path: Path, clock: Clock) -> None:
        self._path = path
        self._clock = clock
        # 保护凭据文件的读改写；管理员换 Cookie 和旧请求标记失效可能并发
        self._lock = threading.Lock()

    def save(self, cookie: str, device: str | None, user_name: str | None) -> CredentialMeta:
        meta = CredentialMeta(device=device, user_name=user_name, saved_at=self._clock.now())
        with self._lock:
            write_json(self._path, _dump(cookie, meta))
        return meta

    def meta(self) -> CredentialMeta | None:
        with self._lock:
            stored = self._read()
            return None if stored is None else stored[1]

    def cookie(self) -> tuple[str, CredentialMeta] | None:
        with self._lock:
            return self._read()

    def mark_invalid(self, cookie: str) -> None:
        """把 115 拒绝过的 Cookie 标为失效。

        请求期间管理员可能已经换了新 Cookie，只有存储里仍是这份旧 Cookie 时才写，
        避免旧请求的失败把新凭据错标成失效。
        """
        with self._lock:
            stored = self._read()
            if stored is None:
                return
            current, meta = stored
            if current != cookie or meta.invalid_at is not None:
                return
            invalid = CredentialMeta(
                device=meta.device,
                user_name=meta.user_name,
                saved_at=meta.saved_at,
                invalid_at=self._clock.now(),
            )
            write_json(self._path, _dump(current, invalid))

    def clear(self) -> None:
        with self._lock:
            self._path.unlink(missing_ok=True)

    def _read(self) -> tuple[str, CredentialMeta] | None:
        """读取凭据；调用方必须持有 self._lock。"""
        data = read_json(self._path)
        if data is None:
            return None
        if not isinstance(data, dict):
            raise ValueError(f"凭据文件必须是 JSON 对象：{self._path}")
        cookie = data.get("cookie")
        if not isinstance(cookie, str) or not cookie:
            raise ValueError(f"凭据文件缺少 Cookie：{self._path}")
        invalid_at = data.get("invalid_at")
        return cookie, CredentialMeta(
            device=_optional_text(data.get("device")),
            user_name=_optional_text(data.get("user_name")),
            saved_at=from_iso(str(data["saved_at"])),
            invalid_at=from_iso(str(invalid_at)) if invalid_at else None,
        )


def _dump(cookie: str, meta: CredentialMeta) -> dict[str, object]:
    return {
        "cookie": cookie,
        "device": meta.device,
        "user_name": meta.user_name,
        "saved_at": to_iso(meta.saved_at),
        "invalid_at": to_iso(meta.invalid_at) if meta.invalid_at else None,
    }


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def device_from_cookie(cookie: str) -> str | None:
    """从 Cookie 的 UID 推断登录设备，例如 R2 对应 alipaymini。认不出时返回 None。"""
    match = _UID_SSOENT.search(cookie)
    if match is None:
        return None
    return SSOENT_TO_DEVICE.get(match.group(1))


# 摘自 p115client.const.APP_TO_SSOENT（0.0.9.7.2），不直接导入以避开它的导入副作用。
SSOENT_TO_DEVICE = {
    "A1": "web",
    "D1": "ios",
    "D3": "115ios",
    "F1": "android",
    "F3": "115android",
    "H1": "ipad",
    "H3": "115ipad",
    "I1": "tv",
    "I2": "apple_tv",
    "M1": "qandroid",
    "N1": "qios",
    "O1": "qipad",
    "P1": "os_windows",
    "P2": "os_mac",
    "P3": "os_linux",
    "R1": "wechatmini",
    "R2": "alipaymini",
    "S1": "harmony",
}
