from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.clock import Clock, to_iso
from app.db import Database
from app.errors import AppError

# 所有插件默认使用的代理；插件可以选择不走或自定义（ADR 0025）
PLUGIN_PROXY_SETTING = "plugin.proxy"
_PROXY_SCHEMES = ("http://", "https://", "socks5://", "socks5h://")
_EXTRAS_SETTING = "library.extras_min_mb"
_GRACE_SETTING = "sync.missing_grace_days"
_SERVER_ID_SETTING = "server.id"
_SERVER_NAME_SETTING = "server.name"
_PUBLIC_URL_SETTING = "server.public_url"
DEFAULT_SERVER_NAME = "光圈"
MAX_SERVER_NAME_LENGTH = 64
MAX_PUBLIC_URL_LENGTH = 512
# 电影资料库里小于这个体积的视频按附加内容（样片、预告片）忽略（ADR 0012）
DEFAULT_EXTRAS_MIN_MB = 200
MAX_EXTRAS_MIN_MB = 10240
# 115 上消失的文件保留这么多天再同步删除，期间移回来还能恢复播放记录（ADR 0007）
DEFAULT_MISSING_GRACE_DAYS = 7
MAX_MISSING_GRACE_DAYS = 365


@dataclass(frozen=True)
class ServerIdentity:
    """客户端用来识别服务器：同一台服务器换了地址（局域网、公网）也认得出来（ADR 0017）。"""

    id: str
    name: str


class SettingsService:
    """系统设置：服务器身份、对外地址、资料库阈值和插件全局代理。

    各插件自己的账号、Key 和校验状态都存在插件目录，宿主不再读写。
    """

    def __init__(self, database: Database, clock: Clock) -> None:
        self._database = database
        self._clock = clock

    def ensure_server_id(self) -> None:
        """首次启动时生成服务器 ID，之后不再改变。"""
        with self._database.session() as connection:
            if get_setting(connection, _SERVER_ID_SETTING) is None:
                put_setting(
                    connection, _SERVER_ID_SETTING, uuid.uuid4().hex, to_iso(self._clock.now())
                )

    def server(self) -> ServerIdentity:
        with self._database.read_session() as connection:
            server_id = get_setting(connection, _SERVER_ID_SETTING)
            name = get_setting(connection, _SERVER_NAME_SETTING)
        if server_id is None:
            raise RuntimeError("server id is not initialized")
        return ServerIdentity(id=server_id, name=name or DEFAULT_SERVER_NAME)

    def save_server_name(self, name: str) -> ServerIdentity:
        name = name.strip()
        if not name or len(name) > MAX_SERVER_NAME_LENGTH:
            message = f"服务器名称不能为空，最多 {MAX_SERVER_NAME_LENGTH} 个字"
            raise AppError(400, "invalid_server_name", message)
        with self._database.session() as connection:
            put_setting(connection, _SERVER_NAME_SETTING, name, to_iso(self._clock.now()))
        return self.server()

    def public_url(self) -> str | None:
        """对外访问地址：发给播放器的地址都基于它生成，不用请求的 Host 推断（ADR 0015）。"""
        with self._database.read_session() as connection:
            return get_setting(connection, _PUBLIC_URL_SETTING)

    def save_public_url(self, url: str | None) -> None:
        """url 为 None 表示清除。传入前先经过 clean_public_url。"""
        with self._database.session() as connection:
            if url is None:
                delete_setting(connection, _PUBLIC_URL_SETTING)
            else:
                put_setting(connection, _PUBLIC_URL_SETTING, url, to_iso(self._clock.now()))

    def plugin_proxy(self) -> str | None:
        """全局插件代理；插件没有自定义代理时使用它。"""
        with self._database.read_session() as connection:
            return get_setting(connection, PLUGIN_PROXY_SETTING) or None

    def save_plugin_proxy(self, proxy: str | None) -> None:
        """proxy 为 None 表示清除；传入前先经过 clean_proxy。"""
        with self._database.session() as connection:
            if proxy is None:
                delete_setting(connection, PLUGIN_PROXY_SETTING)
            else:
                put_setting(connection, PLUGIN_PROXY_SETTING, proxy, to_iso(self._clock.now()))

    def extras_min_mb(self) -> int:
        with self._database.read_session() as connection:
            value = get_setting(connection, _EXTRAS_SETTING)
        return int(value) if value is not None else DEFAULT_EXTRAS_MIN_MB

    def save_extras_min_mb(self, value: int) -> int:
        if not 0 <= value <= MAX_EXTRAS_MIN_MB:
            raise AppError(
                400, "invalid_extras_threshold", f"阈值应在 0 到 {MAX_EXTRAS_MIN_MB} MB 之间"
            )
        with self._database.session() as connection:
            put_setting(connection, _EXTRAS_SETTING, str(value), to_iso(self._clock.now()))
        return value

    def missing_grace_days(self) -> int:
        with self._database.read_session() as connection:
            value = get_setting(connection, _GRACE_SETTING)
        return int(value) if value is not None else DEFAULT_MISSING_GRACE_DAYS

    def save_missing_grace_days(self, value: int) -> int:
        if not 1 <= value <= MAX_MISSING_GRACE_DAYS:
            raise AppError(
                400, "invalid_grace_days", f"保留天数应在 1 到 {MAX_MISSING_GRACE_DAYS} 之间"
            )
        with self._database.session() as connection:
            put_setting(connection, _GRACE_SETTING, str(value), to_iso(self._clock.now()))
        return value

    def library_subtitle_language(self, library_id: int) -> str | None:
        with self._database.read_session() as connection:
            return get_setting(connection, f"library.{library_id}.subtitle_language")

    def save_library_subtitle_language(self, library_id: int, language: str | None) -> None:
        key = f"library.{library_id}.subtitle_language"
        with self._database.session() as connection:
            if language:
                put_setting(connection, key, language.strip(), to_iso(self._clock.now()))
            else:
                delete_setting(connection, key)


def clean_public_url(url: str | None) -> str | None:
    """校验并规范对外访问地址：只保留协议、主机和路径前缀，去掉末尾的斜杠。空值表示清除。"""
    url = (url or "").strip()
    if not url:
        return None
    parts = urlsplit(url)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username is not None
        or parts.query
        or parts.fragment
        or any(c.isspace() for c in url)
        or len(url) > MAX_PUBLIC_URL_LENGTH
    ):
        raise AppError(
            400,
            "invalid_public_url",
            "对外访问地址应以 http:// 或 https:// 开头，例如 https://movie.example.com",
        )
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"


def is_valid_proxy(proxy: str) -> bool:
    """插件代理只支持 http 和 socks5；这里只校验协议前缀，连通性由插件自己验证。"""
    return proxy.startswith(_PROXY_SCHEMES) and len(proxy) > len("http://")


def clean_proxy(proxy: str | None) -> str | None:
    proxy = (proxy or "").strip()
    if not proxy:
        return None
    if not is_valid_proxy(proxy) or any(c.isspace() for c in proxy):
        raise AppError(
            400,
            "invalid_proxy",
            "代理地址应以 http://、https:// 或 socks5:// 开头，例如 http://127.0.0.1:7890",
        )
    return proxy


def mask_proxy(proxy: str) -> str:
    """读取接口里隐藏代理地址中的密码，例如 http://user:***@127.0.0.1:7890。"""
    parts = urlsplit(proxy)
    if parts.password is None:
        return proxy
    host = parts.netloc.rpartition("@")[2]
    return parts._replace(netloc=f"{parts.username or ''}:{MASK}@{host}").geturl()


MASK = "***"


def get_setting(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute("SELECT value FROM setting WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def put_setting(
    connection: sqlite3.Connection, key: str, value: str, now: str, *, secret: bool = False
) -> None:
    connection.execute(
        """
        INSERT INTO setting (key, value, is_secret, updated_at) VALUES (?, ?, ?, ?)
        ON CONFLICT (key) DO UPDATE SET value = excluded.value, is_secret = excluded.is_secret,
            updated_at = excluded.updated_at
        """,
        (key, value, int(secret), now),
    )


def delete_setting(connection: sqlite3.Connection, key: str) -> None:
    connection.execute("DELETE FROM setting WHERE key = ?", (key,))
