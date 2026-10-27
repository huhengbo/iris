"""删除来源文件前的统一检查：来源已停用或不支持删除时拒绝。"""

from __future__ import annotations

import sqlite3

from app.errors import AppError
from app.library.deletion import file_folder_ids
from app.resources.service import ResourceSources


def require_deletable(
    connection: sqlite3.Connection,
    folder_ids: set[int],
    identities: list[str],
    resources: ResourceSources,
) -> None:
    """删除只会调用来源的接口，涉及不支持删除的来源时拒绝，避免误删或只删了记录。"""
    folder_ids = folder_ids | file_folder_ids(connection, identities)
    for source in ResourceSources.folders(connection, sorted(folder_ids)).values():
        if not source.enabled:
            raise AppError(409, "resource_source_disabled", source.disabled_reason())
        provider = resources.provider(source.plugin_id)
        if provider is None or "delete" not in provider.capabilities:
            raise AppError(
                409,
                "resource_delete_unsupported",
                f"资源来源「{source.name}」不支持从这里删除文件，请到存储里直接删除后重新同步",
            )
