from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_DATA_DIR = "data"
DEFAULT_ADMIN_USERNAME = "admin"
# 开发时默认从仓库根目录的 plugins/ 加载；镜像里由 APP_PLUGINS_DIR 指到 /app/plugins。
DEFAULT_PLUGINS_DIR = Path(__file__).resolve().parents[2] / "plugins"
# 定时同步的时间点按这个时区解释，通过 TZ 环境变量修改
DEFAULT_TIMEZONE = "Asia/Shanghai"
# 应用密钥用于签名登录、播放和插件页面令牌；插件凭据由插件自己保存
MIN_SECRET_KEY_LENGTH = 32
# 宿主默认配置的官方插件仓库；APP_DEFAULT_PLUGIN_REPOS 可覆盖（例如内网镜像）
OFFICIAL_PLUGIN_REPOSITORY = "https://huhengbo.github.io/iris-plugins/index.json"


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    secret_key: str
    admin_username: str
    admin_password: str
    # Flutter Web 构建产物目录；不存在时只提供 API
    static_dir: Path
    # 插件根目录：每个子目录一个 plugin.json，宿主为插件建立独立子进程
    plugins_dir: Path = DEFAULT_PLUGINS_DIR
    timezone: str = DEFAULT_TIMEZONE
    # 允许跨域访问 API 的网页来源，例如单独部署的 Web 客户端。同源托管的前端和 App 不需要
    cors_origins: tuple[str, ...] = ()
    # 默认插件仓库（官方仓库），按顺序对齐 plugin_repository.default_slot；空表示没有默认仓库
    default_plugin_repos: tuple[str, ...] = ()

    @property
    def database_path(self) -> Path:
        return self.data_dir / "iris.sqlite3"

    @property
    def media_dir(self) -> Path:
        """本地来源和宿主直接提供的文件都限制在这个目录内。"""
        return self.data_dir / "media"

    @property
    def plugin_packages_dir(self) -> Path:
        """仓库和本地安装的插件代码、虚拟环境和缓存；状态仍在 data/plugins/<id>/state。"""
        return self.data_dir / "plugin-packages"


def load_config() -> AppConfig:
    secret_key = _require_env("APP_SECRET_KEY")
    if len(secret_key) < MIN_SECRET_KEY_LENGTH:
        raise RuntimeError(f"APP_SECRET_KEY must be at least {MIN_SECRET_KEY_LENGTH} characters")
    return AppConfig(
        data_dir=Path(os.getenv("APP_DATA_DIR", DEFAULT_DATA_DIR)),
        secret_key=secret_key,
        admin_username=os.getenv("APP_ADMIN_USERNAME", DEFAULT_ADMIN_USERNAME),
        admin_password=_require_env("APP_ADMIN_PASSWORD"),
        static_dir=Path(os.getenv("APP_STATIC_DIR", str(Path(__file__).with_name("static")))),
        plugins_dir=Path(os.getenv("APP_PLUGINS_DIR", str(DEFAULT_PLUGINS_DIR))),
        timezone=_timezone(os.getenv("TZ") or DEFAULT_TIMEZONE),
        cors_origins=_origins(os.getenv("APP_CORS_ORIGINS", "")),
        default_plugin_repos=_plugin_repos(os.getenv("APP_DEFAULT_PLUGIN_REPOS")),
    )


def _origins(value: str) -> tuple[str, ...]:
    origins = tuple(dict.fromkeys(o.strip().rstrip("/") for o in value.split(",") if o.strip()))
    for origin in origins:
        if not origin.startswith(("http://", "https://")) or origin.count("/") != 2:
            raise RuntimeError(
                f"APP_CORS_ORIGINS must be comma separated origins like https://web.example.com: "
                f"{origin}"
            )
    return origins


def _plugin_repos(value: str | None) -> tuple[str, ...]:
    """APP_DEFAULT_PLUGIN_REPOS：逗号分隔的 index.json 地址。

    未设置时使用官方仓库；显式设为空字符串表示没有默认仓库。
    """
    if value is None:
        return (OFFICIAL_PLUGIN_REPOSITORY,)
    repos = tuple(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))
    for repo in repos:
        _check_repository_url(repo)
    return repos


def _check_repository_url(url: str) -> None:
    """仓库地址只允许 HTTPS；本机开发可以用 localhost/127.0.0.1/::1 的 HTTP。"""
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise RuntimeError(f"APP_DEFAULT_PLUGIN_REPOS 里的地址无效：{url}") from exc
    if parts.scheme == "https" and parts.hostname:
        return
    if parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1"):
        return
    raise RuntimeError(f"APP_DEFAULT_PLUGIN_REPOS 只能使用 HTTPS 地址（本机可用 localhost）：{url}")


def _timezone(name: str) -> str:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise RuntimeError(f"TZ must be an IANA time zone such as Asia/Shanghai: {name}") from exc
    return name


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if value:
        return value
    raise RuntimeError(f"Missing required environment variable: {name}")
