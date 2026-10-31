"""插件仓库管理和只读目录（ADR 0029）。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas import Out
from app.deps import AdminUser, PluginRepositoriesDep
from app.plugins.repositories import PluginRepositories

router = APIRouter(prefix="/admin/plugin-repositories", tags=["admin"])
catalog_router = APIRouter(prefix="/admin/plugin-catalog", tags=["admin"])

RepositoryStatus = Literal["pending", "ok", "error", "unsupported"]


class RepositoryOut(Out):
    id: int
    name: str
    url: str
    official: bool
    enabled: bool
    status: RepositoryStatus
    error: str | None
    # schema 在 BaseModel 上是已弃用的方法名，用别名保持接口字段名不变
    schema_version: int | None = Field(default=None, alias="schema")
    plugin_count: int
    invalid_count: int
    last_checked_at: str | None
    last_success_at: str | None
    created_at: str

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class RepositoriesOut(Out):
    repositories: list[RepositoryOut]


class AddRepositoryRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    accept_risk: bool = False


class PatchRepositoryRequest(BaseModel):
    enabled: bool


class CatalogPluginOut(Out):
    id: str
    name: str
    description: str
    version: str
    repository_id: int
    repository_name: str
    category: Literal["metadata", "subtitle", "resource", "other"]
    sdk: str
    provides: list[str]
    capabilities: list[str]
    homepage: str | None
    size: int
    status: Literal["available", "installed", "update_available", "conflict", "incompatible"]
    installed_version: str | None
    reason: str | None


class CatalogOut(Out):
    sdk_version: str
    repositories: list[RepositoryOut]
    plugins: list[CatalogPluginOut]


def _repositories_out(repositories: PluginRepositories) -> list[RepositoryOut]:
    return [RepositoryOut.model_validate(item) for item in repositories.list()]


@router.get("")
def list_repositories(_: AdminUser, repositories: PluginRepositoriesDep) -> RepositoriesOut:
    return RepositoriesOut(repositories=_repositories_out(repositories))


@router.post("", status_code=201)
def add_repository(
    body: AddRepositoryRequest, _: AdminUser, repositories: PluginRepositoriesDep
) -> RepositoryOut:
    return RepositoryOut.model_validate(repositories.add(body.url.strip(), body.accept_risk))


@router.post("/refresh")
def refresh_repositories(_: AdminUser, repositories: PluginRepositoriesDep) -> RepositoriesOut:
    """同步并发拉取已启用的仓库；单个失败只影响自己的状态。"""
    repositories.refresh_all()
    return RepositoriesOut(repositories=_repositories_out(repositories))


@router.patch("/{repository_id}")
def patch_repository(
    repository_id: int,
    body: PatchRepositoryRequest,
    _: AdminUser,
    repositories: PluginRepositoriesDep,
) -> RepositoryOut:
    return RepositoryOut.model_validate(repositories.set_enabled(repository_id, body.enabled))


@router.delete("/{repository_id}", status_code=204)
def delete_repository(
    repository_id: int, _: AdminUser, repositories: PluginRepositoriesDep
) -> Response:
    repositories.delete(repository_id)
    return Response(status_code=204)


@catalog_router.get("")
def plugin_catalog(_: AdminUser, repositories: PluginRepositoriesDep) -> CatalogOut:
    return CatalogOut.model_validate(repositories.catalog())
