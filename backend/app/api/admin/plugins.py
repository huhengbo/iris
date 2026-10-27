from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from app.api.schemas import Out
from app.deps import AdminUser, Plugins
from app.errors import AppError, NotFound
from app.settings import clean_proxy, mask_proxy

router = APIRouter(prefix="/admin/plugins", tags=["admin"])


class PluginPageOut(Out):
    id: str
    title: str


class PluginOut(Out):
    id: str
    name: str
    version: str
    sdk_version: str
    description: str
    category: Literal["metadata", "subtitle", "resource", "other"]
    provides: list[str]
    requires: list[str]
    trust: str
    built_in: bool
    enabled: bool
    state: Literal["running", "stopped", "failed", "not_installed"]
    # 发现或启动失败的原因；正常时为空
    error: str | None = None
    proxy_mode: Literal["inherit", "none", "custom"]
    # 只有 custom 模式返回打码后的地址
    proxy: str | None
    updated_at: str
    pages: list[PluginPageOut]


class PluginEnabledRequest(BaseModel):
    enabled: bool


class PluginProxyRequest(BaseModel):
    """custom 时必须给出合法地址；其他模式会清空已保存的地址。"""

    mode: Literal["inherit", "none", "custom"]
    proxy: str | None = Field(default=None, max_length=1024)


class PluginsOut(Out):
    plugins: list[PluginOut]


@router.get("")
def list_plugins(_: AdminUser, plugins: Plugins) -> PluginsOut:
    return PluginsOut(plugins=[PluginOut(**item) for item in plugins.list()])


@router.put("/{plugin_id}/enabled")
def set_plugin_enabled(
    plugin_id: str, body: PluginEnabledRequest, _: AdminUser, plugins: Plugins
) -> PluginOut:
    if not plugins.set_enabled(plugin_id, body.enabled):
        raise NotFound("插件不存在")
    item = next(item for item in plugins.list() if item["id"] == plugin_id)
    return PluginOut(**item)


@router.put("/{plugin_id}/proxy")
def set_plugin_proxy(
    plugin_id: str, body: PluginProxyRequest, _: AdminUser, plugins: Plugins
) -> PluginOut:
    current = plugins.proxy_config(plugin_id)
    if current is None:
        raise NotFound("插件不存在")
    proxy = None
    if body.mode == "custom":
        if not body.proxy or not body.proxy.strip():
            raise AppError(400, "invalid_proxy", "自定义代理地址不能为空")
        current_proxy = current[1]
        # 客户端把读到的打码地址原样传回时，视为没有修改
        if current_proxy and body.proxy == mask_proxy(current_proxy):
            proxy = current_proxy
        else:
            proxy = clean_proxy(body.proxy)
    plugins.set_proxy(plugin_id, body.mode, proxy)
    item = next(item for item in plugins.list() if item["id"] == plugin_id)
    return PluginOut(**item)


@router.delete("/{plugin_id}", status_code=204)
def delete_plugin(plugin_id: str, _: AdminUser, plugins: Plugins) -> Response:
    """只删除当前进程没安装的旧记录；已安装、仍被资源来源引用的插件拒绝删除。"""
    if not plugins.delete(plugin_id):
        raise NotFound("插件不存在")
    return Response(status_code=204)
