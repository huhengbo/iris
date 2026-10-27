"""资源来源管理：来源实例的增删改、浏览来源目录，以及查看哪些资料库在用。

资料库目录在资料库接口里选择（来源 + 定位符），这里不单独增删来源目录。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from iris_plugin_sdk import (
    InvalidSourceConfig,
    ResourceNotFound,
    ResourceProvider,
    ResourceUnavailable,
)
from pydantic import BaseModel, Field

from app.api.schemas import Out
from app.deps import AdminUser, AppClock, Db, Plugins, ReadDb, Resources
from app.errors import AppError
from app.resources.repository import ResourceRepository, ResourceSource
from app.resources.service import ResourceSources

router = APIRouter(prefix="/admin/resources", tags=["admin"])

CAPABILITY = "resource.source"


class ConfigFieldOut(Out):
    key: str
    label: str
    help: str
    required: bool
    default: str


class ProviderOut(BaseModel):
    plugin_id: str
    name: str
    # 插件停用时不能新建来源，也看不到配置项
    enabled: bool
    # 能否建多个来源；115 绑定全局账号，只有一个
    multiple: bool
    supports_delete: bool
    config_fields: list[ConfigFieldOut]
    source_count: int


class SourceOut(BaseModel):
    id: int
    plugin_id: str
    plugin_name: str
    plugin_enabled: bool
    name: str
    enabled: bool
    config: dict[str, Any]
    # unknown：还没同步过；ok：上次同步正常；error：上次同步出错
    status: str
    last_error: str | None
    checked_at: str | None
    folder_count: int
    supports_delete: bool


class SourceIn(BaseModel):
    plugin_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    config: dict[str, Any] = Field(default_factory=dict)


class SourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    config: dict[str, Any] | None = None


class SourceEnabledIn(BaseModel):
    enabled: bool


class BrowseEntryOut(BaseModel):
    locator: str
    name: str


class BrowseOut(BaseModel):
    # null 表示来源根目录
    locator: str | None
    path: str
    # 这个目录能否作为资料库目录
    selectable: bool
    entries: list[BrowseEntryOut]


def _resource_plugins(plugins: Plugins) -> dict[str, dict[str, Any]]:
    return {p["id"]: p for p in plugins.list() if CAPABILITY in p["provides"]}


def _source_out(
    source: ResourceSource, plugin: dict[str, Any] | None, provider: ResourceProvider | None
) -> SourceOut:
    return SourceOut(
        id=source.id,
        plugin_id=source.plugin_id,
        plugin_name=plugin["name"] if plugin else source.plugin_id,
        plugin_enabled=bool(plugin and plugin["enabled"]),
        name=source.name,
        enabled=source.enabled,
        config=source.config,
        status=source.status,
        last_error=source.last_error,
        checked_at=source.checked_at,
        folder_count=source.folder_count,
        supports_delete=bool(provider and "delete" in provider.capabilities),
    )


def _out(source: ResourceSource, plugins: Plugins, resources: ResourceSources) -> SourceOut:
    plugin = _resource_plugins(plugins).get(source.plugin_id)
    return _source_out(source, plugin, resources.provider(source.plugin_id))


def _require_provider(
    resources: ResourceSources, plugin_id: str, name: str | None = None
) -> ResourceProvider:
    provider = resources.provider(plugin_id)
    if provider is None:
        label = f"来源「{name}」的插件" if name else "这个插件"
        raise AppError(409, "resource_plugin_disabled", f"{label}没有启用或启动失败")
    return provider


def _validate(provider: ResourceProvider, config: dict[str, Any]) -> dict[str, Any]:
    try:
        return provider.validate_config(config)
    except InvalidSourceConfig as exc:
        raise AppError(400, "invalid_source_config", exc.message) from None


@router.get("/providers")
def list_providers(
    _: AdminUser, db: ReadDb, clock: AppClock, plugins: Plugins, resources: Resources
) -> list[ProviderOut]:
    counts: dict[str, int] = {}
    for source in ResourceRepository(db, clock).sources():
        counts[source.plugin_id] = counts.get(source.plugin_id, 0) + 1
    result = []
    for plugin_id, plugin in _resource_plugins(plugins).items():
        provider = resources.provider(plugin_id) if plugin["enabled"] else None
        result.append(
            ProviderOut(
                plugin_id=plugin_id,
                name=plugin["name"],
                enabled=provider is not None,
                multiple=provider.multiple if provider else False,
                supports_delete="delete" in provider.capabilities if provider else False,
                config_fields=[
                    ConfigFieldOut.model_validate(field)
                    for field in (provider.config_fields if provider else ())
                ],
                source_count=counts.get(plugin_id, 0),
            )
        )
    return result


@router.get("")
def list_sources(
    _: AdminUser, db: ReadDb, clock: AppClock, plugins: Plugins, resources: Resources
) -> list[SourceOut]:
    known = _resource_plugins(plugins)
    return [
        _source_out(source, known.get(source.plugin_id), resources.provider(source.plugin_id))
        for source in ResourceRepository(db, clock).sources()
    ]


@router.post("", status_code=201)
def create_source(
    body: SourceIn,
    _: AdminUser,
    db: Db,
    clock: AppClock,
    plugins: Plugins,
    resources: Resources,
) -> SourceOut:
    if body.plugin_id not in _resource_plugins(plugins):
        raise AppError(422, "resource_plugin_required", "插件不提供资源来源能力")
    provider = _require_provider(resources, body.plugin_id)
    repository = ResourceRepository(db, clock)
    if not provider.multiple and repository.first_of(body.plugin_id) is not None:
        raise AppError(409, "resource_source_singleton", "这个插件只能有一个来源")
    config = _validate(provider, body.config)
    return _out(repository.create_source(body.plugin_id, body.name, config), plugins, resources)


@router.get("/{source_id}")
def get_source(
    source_id: int,
    _: AdminUser,
    db: ReadDb,
    clock: AppClock,
    plugins: Plugins,
    resources: Resources,
) -> SourceOut:
    return _out(ResourceRepository(db, clock).source(source_id), plugins, resources)


@router.patch("/{source_id}")
def update_source(
    source_id: int,
    body: SourceUpdate,
    _: AdminUser,
    db: Db,
    clock: AppClock,
    plugins: Plugins,
    resources: Resources,
) -> SourceOut:
    repository = ResourceRepository(db, clock)
    source = repository.source(source_id)
    if body.config is not None:
        provider = _require_provider(resources, source.plugin_id, source.name)
        source = repository.set_config(source_id, _validate(provider, body.config))
    if body.name is not None:
        source = repository.rename(source_id, body.name)
    return _out(source, plugins, resources)


@router.put("/{source_id}/enabled")
def set_source_enabled(
    source_id: int,
    body: SourceEnabledIn,
    _: AdminUser,
    db: Db,
    clock: AppClock,
    plugins: Plugins,
    resources: Resources,
) -> SourceOut:
    source = ResourceRepository(db, clock).set_enabled(source_id, body.enabled)
    return _out(source, plugins, resources)


@router.delete("/{source_id}", status_code=204)
def delete_source(source_id: int, _: AdminUser, db: Db, clock: AppClock) -> Response:
    ResourceRepository(db, clock).delete_source(source_id)
    return Response(status_code=204)


@router.get("/{source_id}/browse")
def browse_source(
    source_id: int,
    _: AdminUser,
    db: ReadDb,
    clock: AppClock,
    resources: Resources,
    locator: str | None = None,
) -> BrowseOut:
    """列出来源里某个目录下的子目录，供资料库选择目录。"""
    source = ResourceRepository(db, clock).source(source_id)
    provider = _require_provider(resources, source.plugin_id, source.name)
    try:
        listing = provider.open(source.id, source.config).browse(locator)
    except ResourceNotFound as exc:
        raise AppError(404, "resource_not_found", exc.message) from None
    except ResourceUnavailable as exc:
        raise AppError(503, "resource_unavailable", exc.message) from None
    return BrowseOut(
        # 根目录统一用 null 表示，不把插件自己的根标识（115 的 0、本地的空串）暴露给客户端
        locator=listing.locator if locator is not None else None,
        path=listing.path,
        selectable=listing.selectable,
        entries=[
            BrowseEntryOut(locator=entry.locator, name=entry.relative_path)
            for entry in listing.entries
            if entry.is_dir
        ],
    )
