"""演员页资料：第一次打开时从人物所属的元数据插件拉取简介、生日等并缓存（ADR 0014、0022）。"""

from __future__ import annotations

from iris_plugin_sdk import MetadataNotFound, MetadataPerson

from app.clock import Clock, to_iso
from app.db import Database
from app.errors import AppError
from app.library.metadata_providers import MetadataProviders
from app.priority import Priority


def ensure_person_detail(
    database: Database, clock: Clock, providers: MetadataProviders, person_id: int
) -> None:
    """还没拉过资料的人物向所属插件请求一次，网络请求不在事务里。

    插件停用、没配置、连不上或不提供人物资料时什么都不做，页面只显示姓名和头像，下次打开再试；
    插件那边已经没有这个人时也记为拉过，不再重复请求。
    """
    with database.read_session() as connection:
        row = connection.execute(
            "SELECT provider_id, remote_id, detail_at FROM person WHERE id = ?", (person_id,)
        ).fetchone()
    if row is None or row["detail_at"] is not None:
        return
    info: MetadataPerson | None
    try:
        provider = providers.get(row["provider_id"])
        if not provider.has_people or provider.unavailable_reason() is not None:
            return
        info = provider.person(row["remote_id"], Priority.INTERACTIVE)
    except MetadataNotFound:
        info = None
    except AppError:
        return
    now = to_iso(clock.now())
    with database.session() as connection:
        if info is None:
            connection.execute("UPDATE person SET detail_at = ? WHERE id = ?", (now, person_id))
            return
        # 头像以条目刮削时的为准，这里不改，免得和已下载的缓存对不上
        connection.execute(
            "UPDATE person SET name = ?, original_name = ?, biography = ?, birthday = ?, "
            "deathday = ?, place_of_birth = ?, detail_at = ? WHERE id = ?",
            (
                info.name,
                info.original_name,
                info.biography,
                info.birthday,
                info.deathday,
                info.place_of_birth,
                now,
                person_id,
            ),
        )
