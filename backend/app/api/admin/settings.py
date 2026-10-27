from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.deps import AdminUser, Plugins, Settings
from app.settings import (
    MAX_EXTRAS_MIN_MB,
    MAX_MISSING_GRACE_DAYS,
    MAX_PUBLIC_URL_LENGTH,
    MAX_SERVER_NAME_LENGTH,
    SettingsService,
    clean_proxy,
    clean_public_url,
    mask_proxy,
)

router = APIRouter(prefix="/admin/settings", tags=["admin"])


class ServerOut(BaseModel):
    id: str
    name: str
    # 对外访问地址，发给播放器的地址都基于它生成；没设置时不能签发播放链接
    public_url: str | None


class LibrarySettingsOut(BaseModel):
    extras_min_mb: int
    # 115 上消失的文件保留多少天后同步删除
    missing_grace_days: int


class PluginSettingsOut(BaseModel):
    # 所有插件默认使用的代理；密码以 *** 代替
    proxy: str | None


class SettingsOut(BaseModel):
    server: ServerOut
    library: LibrarySettingsOut
    plugins: PluginSettingsOut


class ServerRequest(BaseModel):
    """public_url 不传时保持原值，传空字符串或 null 表示清除。"""

    name: str = Field(max_length=MAX_SERVER_NAME_LENGTH * 2)
    public_url: str | None = Field(default=None, max_length=MAX_PUBLIC_URL_LENGTH * 2)


class LibrarySettingsRequest(BaseModel):
    """missing_grace_days 不传时保持原值。"""

    extras_min_mb: int = Field(ge=0, le=MAX_EXTRAS_MIN_MB)
    missing_grace_days: int | None = Field(default=None, ge=1, le=MAX_MISSING_GRACE_DAYS)


class PluginSettingsRequest(BaseModel):
    """proxy 不传时保持原值，空字符串或 null 表示清除。"""

    proxy: str | None = Field(default=None, max_length=1024)


@router.get("")
def get_settings(_: AdminUser, settings: Settings) -> SettingsOut:
    return _settings_out(settings)


@router.put("/server")
def save_server(
    body: ServerRequest, _: AdminUser, settings: Settings, plugins: Plugins
) -> SettingsOut:
    public_url = clean_public_url(body.public_url)
    settings.save_server_name(body.name)
    if "public_url" in body.model_fields_set:
        before = settings.public_url()
        settings.save_public_url(public_url)
        if public_url != before:
            # 插件进程里的 context.host.url 跟着公开地址一起更新
            plugins.config_changed_all()
    return _settings_out(settings)


@router.put("/library")
def save_library(body: LibrarySettingsRequest, _: AdminUser, settings: Settings) -> SettingsOut:
    settings.save_extras_min_mb(body.extras_min_mb)
    if body.missing_grace_days is not None:
        settings.save_missing_grace_days(body.missing_grace_days)
    return _settings_out(settings)


@router.put("/plugins")
def save_plugins(
    body: PluginSettingsRequest, _: AdminUser, settings: Settings, plugins: Plugins
) -> SettingsOut:
    """全局插件代理；已经打码的地址原样传回表示不修改。"""
    current_proxy = settings.plugin_proxy()
    if "proxy" in body.model_fields_set and not (
        current_proxy and body.proxy == mask_proxy(current_proxy)
    ):
        settings.save_plugin_proxy(clean_proxy(body.proxy))
        plugins.config_changed_all()
    return _settings_out(settings)


def _settings_out(settings: SettingsService) -> SettingsOut:
    server = settings.server()
    plugin_proxy = settings.plugin_proxy()
    return SettingsOut(
        server=ServerOut(id=server.id, name=server.name, public_url=settings.public_url()),
        library=LibrarySettingsOut(
            extras_min_mb=settings.extras_min_mb(),
            missing_grace_days=settings.missing_grace_days(),
        ),
        plugins=PluginSettingsOut(
            proxy=mask_proxy(plugin_proxy) if plugin_proxy else None,
        ),
    )
