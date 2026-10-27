from __future__ import annotations

import sqlite3
from dataclasses import dataclass

_COLUMNS = (
    "id, kind, client_name, device_name, platform, client_version, ip, created_at, expires_at, "
    "last_seen_at"
)


@dataclass(frozen=True)
class DeviceSession:
    id: int
    # app：Web/App 登录会话；emby：第三方客户端设备
    kind: str
    client_name: str | None
    device_name: str | None
    platform: str | None
    client_version: str | None
    ip: str | None
    created_at: str
    expires_at: str
    last_seen_at: str | None


class DeviceSessionRepository:
    """登录设备的列出与下线。token_hash 等凭据字段不读出，只暴露给用户看得懂的设备信息。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def list_by_activity(self, user_id: int) -> list[DeviceSession]:
        """用户自己看：最近活跃的设备排在前面。"""
        return self._list(user_id, "last_seen_at DESC, id DESC")

    def list_by_creation(self, user_id: int) -> list[DeviceSession]:
        """管理员看：按登录先后，新的在前。"""
        return self._list(user_id, "id DESC")

    def revoke(self, user_id: int, session_id: int) -> None:
        """下线一台设备。带上 user_id 才不会误删别人的会话；会话不存在时视为已下线。"""
        self._db.execute(
            "DELETE FROM auth_session WHERE id = ? AND user_id = ?", (session_id, user_id)
        )

    def revoke_all(self, user_id: int) -> None:
        self._db.execute("DELETE FROM auth_session WHERE user_id = ?", (user_id,))

    def _list(self, user_id: int, order: str) -> list[DeviceSession]:
        rows = self._db.execute(
            f"SELECT {_COLUMNS} FROM auth_session WHERE user_id = ? ORDER BY {order}", (user_id,)
        ).fetchall()
        return [
            DeviceSession(
                id=row["id"],
                kind=row["kind"],
                client_name=row["client_name"],
                device_name=row["device_name"],
                platform=row["platform"],
                client_version=row["client_version"],
                ip=row["ip"],
                created_at=row["created_at"],
                expires_at=row["expires_at"],
                last_seen_at=row["last_seen_at"],
            )
            for row in rows
        ]
