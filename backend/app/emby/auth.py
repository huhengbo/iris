"""Emby 兼容接口的鉴权：解析设备令牌和 Emby 的客户端信息头（ADR 0024）。"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Annotated
from urllib.parse import unquote

from fastapi import Depends, Request

from app.auth.sessions import (
    DEVICE_LAST_SEEN_INTERVAL,
    DeviceProfile,
    SessionService,
    Unauthorized,
)
from app.auth.users import User
from app.deps import container_of
from app.errors import AppError

# MediaBrowser Client="Infuse", Device="iPhone", DeviceId="...", Version="7.8", Token="..."
# 老客户端用 Emby 开头，格式相同
_AUTH_SCHEMES = ("mediabrowser", "emby")
# 不用授权头的客户端把信息拆成单独的请求头，或者放在同名查询参数里
_CLIENT_FIELDS = (
    ("client_name", "X-Emby-Client"),
    ("device_name", "X-Emby-Device-Name"),
    ("device_id", "X-Emby-Device-Id"),
    ("client_version", "X-Emby-Client-Version"),
)
# 授权头里没有 Client 时按 User-Agent 的产品名认客户端（网易爆米花只在 UA 里写 Filmly/版本号）；
# 通用 HTTP 库和浏览器的产品名认不出是哪个客户端，不采用
_UA_PRODUCT = re.compile(r"^\s*([A-Za-z][\w.\- ]{0,39}?)/")
_GENERIC_AGENTS = frozenset(
    {"mozilla", "okhttp", "dalvik", "dart", "cfnetwork", "curl", "python-httpx"}
)
_CLIENT_ALIASES = {"filmly": "网易爆米花"}
_AUTH_PART = re.compile(r'([A-Za-z]+)="([^"]*)"|([A-Za-z]+)=([^,]+)')


@dataclass(frozen=True)
class EmbyClient:
    client_name: str | None = None
    device_name: str | None = None
    device_id: str | None = None
    client_version: str | None = None
    token: str | None = None


@dataclass(frozen=True)
class EmbySession:
    user: User
    session_id: int
    client_name: str | None
    device_name: str | None
    device_id: str | None
    client_version: str | None


def parse_authorization(value: str | None) -> EmbyClient:
    """解析 Emby 的 MediaBrowser 授权头；Bearer 令牌和空值都返回空信息。"""
    if not value:
        return EmbyClient()
    scheme, _, rest = value.strip().partition(" ")
    if scheme.lower() not in _AUTH_SCHEMES:
        return EmbyClient()
    parts: dict[str, str] = {}
    for match in _AUTH_PART.finditer(rest):
        key = (match.group(1) or match.group(3) or "").lower()
        part = match.group(2) if match.group(1) else match.group(4)
        if key and part is not None:
            # 设备名常含中文，客户端按 URL 编码后才能放进请求头
            parts[key] = unquote(part.strip())
    return EmbyClient(
        client_name=parts.get("client") or None,
        device_name=parts.get("device") or None,
        device_id=parts.get("deviceid") or None,
        client_version=parts.get("version") or None,
        token=parts.get("token") or None,
    )


def client_info(request: Request) -> EmbyClient:
    """客户端名、设备名和 DeviceId：X-Emby-Authorization 优先，缺的再看单独的请求头和查询参数。"""
    info = EmbyClient()
    for name in ("X-Emby-Authorization", "Authorization"):
        info = parse_authorization(request.headers.get(name))
        if info != EmbyClient():
            break
    for field, name in _CLIENT_FIELDS:
        if getattr(info, field) is not None:
            continue
        value = request.headers.get(name) or request.query_params.get(name)
        if value and value.strip():
            info = replace(info, **{field: unquote(value.strip())})
    if info.client_name is None:
        info = replace(info, client_name=_client_from_agent(request.headers.get("User-Agent")))
    return info


def _client_from_agent(agent: str | None) -> str | None:
    match = _UA_PRODUCT.match(agent or "")
    if match is None:
        return None
    product = match.group(1).strip()
    if product.lower() in _GENERIC_AGENTS:
        return None
    return _CLIENT_ALIASES.get(product.lower(), product)


def token_from(request: Request) -> str | None:
    """按 ADR 0024 的顺序取令牌：请求头、MediaBrowser 头、查询参数。"""
    token = request.headers.get("X-Emby-Token")
    if token:
        return token
    for name in ("X-Emby-Authorization", "Authorization"):
        parsed = parse_authorization(request.headers.get(name))
        if parsed.token:
            return parsed.token
    return (
        request.query_params.get("api_key")
        or request.query_params.get("X-Emby-Token")
        or request.query_params.get("x-emby-token")
    )


def get_emby_session(request: Request) -> EmbySession:
    """每个请求都查设备会话行；撤销设备、禁用用户、改密码或改可见范围后立即 401。"""
    token = token_from(request)
    if not token:
        raise Unauthorized("请先登录")
    return session_for_token(request, token)


def session_for_token(request: Request, token: str) -> EmbySession:
    container = container_of(request)
    with container.database.read_session() as connection:
        service = SessionService(connection, container.clock, container.access_signer)
        user, session_id = service.authenticate_device(token)
        profile = service.device_profile(session_id)
    if profile is None:
        raise Unauthorized()
    profile = _fill_missing_client(request, profile) or _touch_if_stale(request, profile)
    return EmbySession(
        user=user,
        session_id=session_id,
        client_name=profile.client_name,
        device_name=profile.device_name,
        device_id=profile.device_id,
        client_version=profile.client_version,
    )


PLAYBACK_GRANT_TTL = 12 * 3600


class PlaybackGrants:
    """记住哪台设备最近在播哪个版本，让不带令牌的字幕请求沿用这台设备的身份。

    有的客户端（网易爆米花）拉字幕不带令牌；只放行最近 12 小时请求过播放的版本，
    其余版本仍然 401，避免任何人都能拿 ID 触发 115 取链。只存在内存里，重启后
    客户端下次请求播放时会重新登记。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tokens: dict[int, tuple[str, float]] = {}

    def remember(self, version_id: int, token: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._tokens[version_id] = (token, now + PLAYBACK_GRANT_TTL)
            if len(self._tokens) > 10_000:
                self._tokens = {key: value for key, value in self._tokens.items() if value[1] > now}

    def token_for(self, version_id: int) -> str | None:
        with self._lock:
            found = self._tokens.get(version_id)
        if found is None or found[1] <= time.monotonic():
            return None
        return found[0]


def playback_grants(request: Request) -> PlaybackGrants:
    state = request.app.state
    grants = getattr(state, "emby_playback_grants", None)
    if grants is None:
        grants = PlaybackGrants()
        state.emby_playback_grants = grants
    return grants


def _fill_missing_client(request: Request, profile: DeviceProfile) -> DeviceProfile | None:
    """登录时没认出客户端信息的会话，之后的请求带上了就补记；没有可补的返回 None。"""
    info = client_info(request)
    missing = {
        field: getattr(info, field)
        for field, _ in _CLIENT_FIELDS
        if getattr(profile, field) is None and getattr(info, field) is not None
    }
    if not missing:
        return None
    container = container_of(request)
    with container.database.session() as connection:
        SessionService(connection, container.clock, container.access_signer).fill_device(
            profile.session_id, **missing
        )
    return replace(profile, **missing)


def _touch_if_stale(request: Request, profile: DeviceProfile) -> DeviceProfile:
    container = container_of(request)
    if profile.last_seen_at is not None:
        last_seen = datetime.fromisoformat(profile.last_seen_at)
        if container.clock.now() - last_seen < DEVICE_LAST_SEEN_INTERVAL:
            return profile
    # 最多每 60 秒写一次；会话恰好被撤销时 UPDATE 什么也不做
    with container.database.session() as connection:
        SessionService(connection, container.clock, container.access_signer).touch_device(
            profile.session_id
        )
    return profile


def require_user(session: EmbySession, uid: str) -> None:
    """Emby 的用户 ID 就是本服务的用户 ID；别人的用户路径一律 403。"""
    try:
        expected = int(uid)
    except ValueError:
        raise AppError(403, "forbidden", "不能访问其他用户的数据") from None
    if expected != session.user.id:
        raise AppError(403, "forbidden", "不能访问其他用户的数据")


EmbyUser = Annotated[EmbySession, Depends(get_emby_session)]


def login_client(request: Request) -> EmbyClient:
    """登录时客户端信息来自请求头；旧客户端没带 Token。"""
    return client_info(request)
