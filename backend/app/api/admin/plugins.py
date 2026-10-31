from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.api.schemas import Out
from app.deps import AdminUser, PluginPackagesDep, Plugins
from app.errors import AppError, NotFound
from app.plugins.index_schema import MAX_PACKAGE_BYTES
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
    source: Literal["system", "repository", "local"]
    repository_id: int | None
    repository_name: str | None
    available_version: str | None
    package_status: Literal["ok", "installing", "missing", "failed"]
    package_error: str | None


class PluginEnabledRequest(BaseModel):
    enabled: bool


class PluginProxyRequest(BaseModel):
    """custom 时必须给出合法地址；其他模式会清空已保存的地址。"""

    mode: Literal["inherit", "none", "custom"]
    proxy: str | None = Field(default=None, max_length=1024)


class PluginsOut(Out):
    plugins: list[PluginOut]


class PluginInstallRequest(BaseModel):
    repository_id: int
    plugin_id: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=32)
    accept_permissions: bool = False


class PluginUpgradeRequest(BaseModel):
    version: str = Field(min_length=1, max_length=32)
    accept_permissions: bool = False


class PluginUninstallRequest(BaseModel):
    delete_data: bool = False


class UploadInstallRequest(BaseModel):
    accept_risk: bool = False


class TaskStartedOut(Out):
    task_id: int


class UploadManifestOut(Out):
    id: str
    name: str
    version: str
    description: str
    sdk: str
    provides: list[str]
    capabilities: list[str]


class UploadOut(Out):
    upload_id: str
    sha256: str
    size: int
    manifest: UploadManifestOut
    category: Literal["metadata", "subtitle", "resource", "other"]
    action: Literal["install", "upgrade"]
    installed_version: str | None
    expires_at: str


@router.get("")
def list_plugins(_: AdminUser, plugins: Plugins) -> PluginsOut:
    return PluginsOut(plugins=[PluginOut(**item) for item in plugins.list()])


@router.post("/install", status_code=202)
def install_plugin(
    body: PluginInstallRequest,
    admin: AdminUser,
    packages: PluginPackagesDep,
) -> TaskStartedOut:
    task_id = packages.install(
        body.repository_id,
        body.plugin_id,
        body.version,
        body.accept_permissions,
        created_by=admin.id,
    )
    return TaskStartedOut(task_id=task_id)


@router.post("/uploads", status_code=201)
async def upload_plugin(
    request: Request,
    _: AdminUser,
    packages: PluginPackagesDep,
) -> UploadOut:
    """接收原始 zip；边读边计数，超过 50 MiB 立即拒绝。"""
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in ("", "application/zip", "application/octet-stream"):
        raise AppError(422, "package_invalid", "请求体必须是 zip 压缩包")
    path = packages.uploads_dir / f".stream-{uuid.uuid4().hex}"
    size = 0
    try:
        with path.open("wb") as file:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_PACKAGE_BYTES:
                    raise AppError(413, "package_too_large", "插件包不能超过 50 MiB")
                await run_in_threadpool(file.write, chunk)
        # 校验要算 sha256、解压并解析 toml，放进线程池，不阻塞事件循环
        record = await run_in_threadpool(packages.create_upload, path)
        return UploadOut.model_validate(record)
    finally:
        path.unlink(missing_ok=True)


@router.post("/uploads/{upload_id}/install", status_code=202)
def install_uploaded_plugin(
    upload_id: str,
    body: UploadInstallRequest,
    admin: AdminUser,
    packages: PluginPackagesDep,
) -> TaskStartedOut:
    task_id = packages.install_upload(upload_id, body.accept_risk, created_by=admin.id)
    return TaskStartedOut(task_id=task_id)


@router.delete("/uploads/{upload_id}", status_code=204)
def delete_uploaded_plugin(upload_id: str, _: AdminUser, packages: PluginPackagesDep) -> Response:
    packages.delete_upload(upload_id)
    return Response(status_code=204)


@router.put("/{plugin_id}/enabled")
def set_plugin_enabled(
    plugin_id: str, body: PluginEnabledRequest, _: AdminUser, plugins: Plugins
) -> PluginOut:
    if not plugins.set_enabled(plugin_id, body.enabled):
        raise NotFound("插件不存在")
    item = next(item for item in plugins.list() if item["id"] == plugin_id)
    return PluginOut(**item)


@router.post("/{plugin_id}/upgrade", status_code=202)
def upgrade_plugin(
    plugin_id: str,
    body: PluginUpgradeRequest,
    admin: AdminUser,
    packages: PluginPackagesDep,
) -> TaskStartedOut:
    task_id = packages.upgrade(
        plugin_id, body.version, body.accept_permissions, created_by=admin.id
    )
    return TaskStartedOut(task_id=task_id)


@router.post("/{plugin_id}/uninstall", status_code=204)
def uninstall_plugin(
    plugin_id: str,
    body: PluginUninstallRequest,
    _: AdminUser,
    packages: PluginPackagesDep,
) -> Response:
    packages.uninstall(plugin_id, body.delete_data)
    return Response(status_code=204)


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
