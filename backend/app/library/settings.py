"""资料库的推荐行和高级设置。"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from app.clock import Clock, to_iso
from app.library.metadata_providers import SETTING_KEY as METADATA_PROVIDER_KEY
from app.library.metadata_providers import library_provider_id
from app.settings import get_setting

# 资料库未单独设置时回退的服务器级设置：资料库设置键 -> 服务器设置键
_SERVER_DEFAULTS = {
    "extras_min_mb": "library.extras_min_mb",
    "missing_grace_days": "sync.missing_grace_days",
}

# 推荐行的种类。genre 行用 arg 记类型名，其余行 arg 为空。
HUB_KINDS = ("continue", "latest", "released", "rating", "genre")

# 推荐行最多的条数；首页片架和资料库「推荐」标签共用
HUB_SIZE = 20
# 没有配置时默认给资料库放几个类型行（按条目数从多到少）
DEFAULT_GENRE_HUBS = 3


@dataclass(frozen=True)
class LibraryHub:
    id: int
    library_id: int
    kind: str
    arg: str | None
    position: int
    show_home: bool
    show_library: bool


@dataclass(frozen=True)
class LibraryHubSpec:
    kind: str
    arg: str | None = None
    position: int = 0
    show_home: bool = True
    show_library: bool = True


def hub_title(library_name: str, kind: str, arg: str | None = None) -> str:
    """推荐行的标题。和服务端返回给客户端的 title 保持一致，客户端不再自己拼。"""
    match kind:
        case "continue":
            return "继续观看"
        case "latest":
            return f"最近添加的{library_name}"
        case "released":
            return f"最近上映的{library_name}"
        case "rating":
            return f"评分最高的{library_name}"
        case "genre" if arg:
            return f"{arg} · {library_name}"
        case _:
            return library_name


def library_hubs(connection: sqlite3.Connection, library_id: int) -> list[LibraryHub]:
    """某个资料库保存的推荐行，按顺序。浏览接口只读，不需要时钟。"""
    rows = connection.execute(
        "SELECT id,library_id,kind,arg,position,show_home,show_library FROM library_hub "
        "WHERE library_id = ? ORDER BY position,id",
        (library_id,),
    ).fetchall()
    return [
        LibraryHub(row[0], row[1], row[2], row[3], row[4], bool(row[5]), bool(row[6]))
        for row in rows
    ]


class LibrarySettings:
    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        self._db = connection
        self._clock = clock

    def hubs(self, library_id: int) -> list[LibraryHub]:
        return library_hubs(self._db, library_id)

    def replace_hubs(self, library_id: int, hubs: Sequence[LibraryHubSpec]) -> None:
        self._db.execute("DELETE FROM library_hub WHERE library_id = ?", (library_id,))
        self._db.executemany(
            "INSERT INTO library_hub(library_id,kind,arg,position,show_home,show_library)"
            " VALUES (?,?,?,?,?,?)",
            [
                (
                    library_id,
                    hub.kind,
                    hub.arg,
                    hub.position,
                    int(hub.show_home),
                    int(hub.show_library),
                )
                for hub in hubs
            ],
        )
        # 服务端总是按提交顺序重排位置，客户端不用自己算
        rows = self._db.execute(
            "SELECT id FROM library_hub WHERE library_id = ? ORDER BY position,id",
            (library_id,),
        ).fetchall()
        for position, row in enumerate(rows):
            self._db.execute("UPDATE library_hub SET position = ? WHERE id = ?", (position, row[0]))

    def effective(self, library_id: int) -> dict[str, str]:
        """资料库自己的设置，缺省项回退到服务器默认值，始终返回最终生效的结果。"""
        values = {
            str(row["key"]): str(row["value"])
            for row in self._db.execute(
                "SELECT key,value FROM library_setting WHERE library_id = ?", (library_id,)
            ).fetchall()
        }
        # 由元数据下载器列表派生，不存在 library_setting 里
        values[METADATA_PROVIDER_KEY] = library_provider_id(self._db, library_id)
        for key, server_key in _SERVER_DEFAULTS.items():
            if key not in values:
                server_value = get_setting(self._db, server_key)
                if server_value is not None:
                    values[key] = server_value
        return values

    def put(self, library_id: int, key: str, value: str) -> None:
        self._db.execute(
            "INSERT INTO library_setting(library_id,key,value,updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(library_id,key) DO UPDATE SET value=excluded.value,"
            "updated_at=excluded.updated_at",
            (library_id, key, value, to_iso(self._clock.now())),
        )
