from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cache

from app.auth.tokens import TokenSigner, hash_opaque_token, new_opaque_token
from app.auth.users import User, UserRepository, hash_password, verify_password
from app.clock import Clock, to_iso
from app.errors import AppError

ACCESS_TOKEN_TTL = timedelta(minutes=15)
REFRESH_TOKEN_TTL = timedelta(days=30)
# 第三方客户端设备令牌长期有效；用户撤销设备、改密码或改可见范围时立即失效（ADR 0024）
DEVICE_TOKEN_EXPIRES_AT = datetime(9999, 12, 31, tzinfo=UTC)
# 设备最近活动时间最多每 60 秒写一次，避免每个播放请求都占写事务
DEVICE_LAST_SEEN_INTERVAL = timedelta(seconds=60)


@cache
def _dummy_hash() -> str:
    # 用户不存在时也做一次同等代价的校验，避免通过响应时间探测用户名是否存在
    return hash_password(new_opaque_token())


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int


@dataclass(frozen=True)
class DeviceToken:
    """第三方客户端登录后拿到的长期令牌；明文只在登录响应里出现一次。"""

    token: str
    session_id: int
    user: User


@dataclass(frozen=True)
class DeviceProfile:
    """设备令牌对应的会话信息，登录时的客户端名和设备名都在这里。"""

    session_id: int
    client_name: str | None
    device_name: str | None
    device_id: str | None
    client_version: str | None
    last_seen_at: str | None


class Unauthorized(AppError):
    def __init__(self, message: str = "登录已失效，请重新登录") -> None:
        super().__init__(401, "unauthorized", message)


class SessionService:
    """登录会话：短期 access token（签名、无状态）加长期 refresh token。

    refresh token 在数据库中只保存哈希，每次刷新都轮换。
    access token 携带 token_version，每次请求都与数据库比对，禁用或改密码后立即失效。
    """

    def __init__(self, connection: sqlite3.Connection, clock: Clock, signer: TokenSigner) -> None:
        self._db = connection
        self._clock = clock
        self._signer = signer
        self._users = UserRepository(connection, clock)

    def check_credentials(self, username: str, password: str) -> User | None:
        """校验用户名密码，只读。密码哈希很耗时，调用方应在只读连接上做，不要占着写事务。"""
        found = self._users.find_credentials(username.strip())
        if found is None:
            verify_password(_dummy_hash(), password)
            return None
        user, password_hash = found
        if not verify_password(password_hash, password) or user.disabled:
            return None
        return user

    def start_session(
        self,
        user: User,
        *,
        device_name: str | None = None,
        platform: str | None = None,
        client_version: str | None = None,
        device_id: str | None = None,
        ip: str | None = None,
    ) -> TokenPair:
        return self._issue(
            user,
            session_id=None,
            device_name=device_name,
            platform=platform,
            client_version=client_version,
            device_id=device_id,
            ip=ip,
        )

    def start_device_session(
        self,
        username: str,
        password: str,
        *,
        client_name: str | None = None,
        device_name: str | None = None,
        device_id: str | None = None,
        client_version: str | None = None,
        ip: str | None = None,
    ) -> DeviceToken:
        """校验账号密码并签发长期设备令牌。同一用户同一 device_id 再次登录只保留最新一行。"""
        user = self.check_credentials(username, password)
        if user is None:
            raise Unauthorized("用户名或密码错误")
        return self.start_device_session_for(
            user,
            client_name=client_name,
            device_name=device_name,
            device_id=device_id,
            client_version=client_version,
            ip=ip,
        )

    def start_device_session_for(
        self,
        user: User,
        *,
        client_name: str | None = None,
        device_name: str | None = None,
        device_id: str | None = None,
        client_version: str | None = None,
        ip: str | None = None,
    ) -> DeviceToken:
        """密码已在只读连接上校验过时用它，避免在写事务里做耗时的密码哈希。"""
        now = self._clock.now()
        self._users.touch(user.id)
        if device_id is not None:
            self._db.execute(
                "DELETE FROM auth_session WHERE user_id = ? AND kind = 'emby' AND device_id = ?",
                (user.id, device_id),
            )
        token = new_opaque_token()
        cursor = self._db.execute(
            "INSERT INTO auth_session "
            "(user_id, token_hash, token_version, kind, client_name, device_id, created_at, "
            "expires_at, device_name, platform, client_version, ip, last_seen_at) "
            "VALUES (?, ?, ?, 'emby', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user.id,
                hash_opaque_token(token),
                user.token_version,
                client_name,
                device_id,
                to_iso(now),
                to_iso(DEVICE_TOKEN_EXPIRES_AT),
                device_name,
                client_name,
                client_version,
                ip,
                to_iso(now),
            ),
        )
        assert cursor.lastrowid is not None
        return DeviceToken(token=token, session_id=cursor.lastrowid, user=user)

    def authenticate_device(self, token: str) -> tuple[User, int]:
        """每次请求都查会话行：撤销设备、禁用用户、改密码或改可见范围后立即失效。"""
        row = self._db.execute(
            "SELECT id, user_id, token_version, expires_at FROM auth_session "
            "WHERE token_hash = ? AND kind = 'emby'",
            (hash_opaque_token(token),),
        ).fetchone()
        if row is None:
            raise Unauthorized()
        user = self._users.get(row["user_id"])
        expired = datetime.fromisoformat(row["expires_at"]) <= self._clock.now()
        if user is None or user.disabled or expired or row["token_version"] != user.token_version:
            raise Unauthorized()
        return user, int(row["id"])

    def device_profile(self, session_id: int) -> DeviceProfile | None:
        row = self._db.execute(
            "SELECT id, client_name, device_name, device_id, client_version, last_seen_at "
            "FROM auth_session WHERE id = ? AND kind = 'emby'",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return DeviceProfile(
            session_id=row["id"],
            client_name=row["client_name"],
            device_name=row["device_name"],
            device_id=row["device_id"],
            client_version=row["client_version"],
            last_seen_at=row["last_seen_at"],
        )

    def touch_device(self, session_id: int) -> None:
        """更新设备最近活动时间；会话已被撤销时什么也不做。"""
        self._db.execute(
            "UPDATE auth_session SET last_seen_at = ? WHERE id = ? AND kind = 'emby'",
            (to_iso(self._clock.now()), session_id),
        )

    def fill_device(
        self,
        session_id: int,
        *,
        client_name: str | None = None,
        device_name: str | None = None,
        device_id: str | None = None,
        client_version: str | None = None,
    ) -> None:
        """补记登录时没认出的客户端信息，已有的值不覆盖；顺带更新最近活动时间。"""
        self._db.execute(
            "UPDATE auth_session SET client_name = COALESCE(client_name, ?), "
            "device_name = COALESCE(device_name, ?), device_id = COALESCE(device_id, ?), "
            "client_version = COALESCE(client_version, ?), platform = COALESCE(platform, ?), "
            "last_seen_at = ? WHERE id = ? AND kind = 'emby'",
            (
                client_name,
                device_name,
                device_id,
                client_version,
                client_name,
                to_iso(self._clock.now()),
                session_id,
            ),
        )

    def end_device_session(self, user_id: int, session_id: int) -> None:
        self._db.execute(
            "DELETE FROM auth_session WHERE id = ? AND user_id = ? AND kind = 'emby'",
            (session_id, user_id),
        )

    def refresh(
        self,
        refresh_token: str,
        *,
        device_name: str | None = None,
        platform: str | None = None,
        client_version: str | None = None,
        device_id: str | None = None,
        ip: str | None = None,
    ) -> TokenPair:
        row = self._db.execute(
            "SELECT id, user_id, token_version, expires_at FROM auth_session "
            "WHERE token_hash = ? AND kind = 'app'",
            (hash_opaque_token(refresh_token),),
        ).fetchone()
        if row is None:
            raise Unauthorized()
        user = self._users.get(row["user_id"])
        expired = datetime.fromisoformat(row["expires_at"]) <= self._clock.now()
        if user is None or user.disabled or expired or row["token_version"] != user.token_version:
            self._db.execute("DELETE FROM auth_session WHERE id = ?", (row["id"],))
            raise Unauthorized()
        if any(value is not None for value in (device_name, platform, client_version, ip)):
            self._db.execute(
                "UPDATE auth_session SET device_name = COALESCE(?, device_name), "
                "platform = COALESCE(?, platform), client_version = COALESCE(?, client_version), "
                "ip = COALESCE(?, ip) WHERE id = ?",
                (device_name, platform, client_version, ip, row["id"]),
            )
        if device_id is not None:
            # 升级前登录的会话没有 device_id，续期时补上，之后这台设备重新登录才能替换它
            self._db.execute(
                "UPDATE auth_session SET device_id = ? WHERE id = ? AND device_id IS NULL",
                (device_id, row["id"]),
            )
        return self._issue(user, session_id=row["id"])

    def logout(self, refresh_token: str) -> None:
        self._db.execute(
            "DELETE FROM auth_session WHERE token_hash = ? AND kind = 'app'",
            (hash_opaque_token(refresh_token),),
        )

    def issue_for(self, user: User) -> TokenPair:
        """为当前用户签发新会话，用于改密码后让本设备保持登录。"""
        return self._issue(user, session_id=None)

    def authenticate(self, access_token: str) -> User:
        payload = self._signer.verify(access_token)
        if payload is None or payload.get("typ") != "access":
            raise Unauthorized()
        if payload.get("exp", 0) <= self._clock.now().timestamp():
            raise Unauthorized()
        user = self._users.get(int(payload.get("sub", 0)))
        if user is None or user.disabled or payload.get("tv") != user.token_version:
            raise Unauthorized()
        return user

    def _issue(
        self,
        user: User,
        session_id: int | None,
        *,
        device_name: str | None = None,
        platform: str | None = None,
        client_version: str | None = None,
        device_id: str | None = None,
        ip: str | None = None,
    ) -> TokenPair:
        now = self._clock.now()
        self._users.touch(user.id)
        refresh_token = new_opaque_token()
        active_session_id = session_id
        if session_id is None:
            # 顺带清理过期会话，避免表无限增长
            self._db.execute("DELETE FROM auth_session WHERE expires_at <= ?", (to_iso(now),))
            # 改密码后旧 token_version 的会话再也用不了，不留在设备列表里
            self._db.execute(
                "DELETE FROM auth_session WHERE user_id = ? AND token_version <> ?",
                (user.id, user.token_version),
            )
            if device_id is not None:
                # 同一设备重新登录：旧会话的令牌已经被客户端丢弃，替换掉而不是多出一台
                self._db.execute(
                    "DELETE FROM auth_session WHERE user_id = ? AND kind = 'app' AND device_id = ?",
                    (user.id, device_id),
                )
            cursor = self._db.execute(
                "INSERT INTO auth_session "
                "(user_id, token_hash, token_version, kind, created_at, expires_at, "
                "device_name, platform, client_version, device_id, ip, last_seen_at) "
                "VALUES (?, ?, ?, 'app', ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user.id,
                    hash_opaque_token(refresh_token),
                    user.token_version,
                    to_iso(now),
                    to_iso(now + REFRESH_TOKEN_TTL),
                    device_name,
                    platform,
                    client_version,
                    device_id,
                    ip,
                    to_iso(now),
                ),
            )
            active_session_id = cursor.lastrowid
        else:
            self._db.execute(
                "UPDATE auth_session SET token_hash = ?, expires_at = ?, last_seen_at = ? "
                "WHERE id = ?",
                (
                    hash_opaque_token(refresh_token),
                    to_iso(now + REFRESH_TOKEN_TTL),
                    to_iso(now),
                    session_id,
                ),
            )
        access_token = self._signer.sign(
            {
                "typ": "access",
                "sub": user.id,
                "tv": user.token_version,
                "sid": active_session_id,
                "exp": int((now + ACCESS_TOKEN_TTL).timestamp()),
            }
        )
        return TokenPair(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=int(ACCESS_TOKEN_TTL.total_seconds()),
        )
