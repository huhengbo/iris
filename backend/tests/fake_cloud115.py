"""宿主测试用的来源假插件控制。

目录树和 provider 都在插件子进程里；这里通过 ``test.control`` 操纵它，并保留原来
``FakeCloud115`` 的测试方法名。来源插件不区分 115 和本地，配置为空，locator 是内部节点 ID。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from tests.fake_tmdb import CallLog
from tests.plugin_env import PluginControl

VALID_COOKIE = "UID=100_R2_1700000000; CID=cid-secret-value; SEID=seid-secret-value; KID=kid"
VIDEO_SIZE = 4 * 1024**3
FINGERPRINT_SIZE = 3072


class FakeCloud115:
    def __init__(self, control: PluginControl) -> None:
        self.control = control
        self._nodes = _Nodes(self)

    def _call(self, op: str, **params: Any) -> Any:
        return self.control.call("source", op, params)

    # ---- 编排目录树 ----

    def add_dir(self, parent: str | None, name: str) -> str:
        return str(self._call("add_dir", parent=parent, name=name))

    def add_file(
        self,
        parent: str | None,
        name: str,
        size: int | None = None,
        sha1: str | None = None,
        content: bytes = b"",
    ) -> str:
        return str(
            self._call(
                "add_file",
                parent=parent,
                name=name,
                size=size,
                sha1=sha1,
                content=content,
            )
        )

    def make_path(self, path: str) -> str:
        return str(self._call("make_path", path=path))

    def make_dir(self, cookie: str, name: str) -> str:
        return str(self._call("make_dir", name=name))

    def child(self, parent: str | None, name: str) -> str | None:
        value = self._call("child", parent=parent, name=name)
        return None if value is None else str(value)

    def remove(self, node_id: str) -> None:
        self._call("remove", node_id=node_id)

    def move(self, node_id: str, new_parent: str | None, new_name: str | None = None) -> None:
        self._call("move", node_id=node_id, new_parent=new_parent, new_name=new_name)

    def files_in(self, node_id: str | None) -> list[str]:
        return [str(name) for name in self._call("files_in", node_id=node_id)]

    @property
    def nodes(self) -> _Nodes:
        return self._nodes

    @property
    def calls(self) -> CallLog:
        return CallLog(self.control, "source")

    @property
    def link_requests(self) -> list[tuple[str, str]]:
        values = self._call("link_requests")
        return [(str(item[0]), str(item[1])) for item in values]

    @property
    def error(self) -> str | None:
        return None

    @error.setter
    def error(self, value: Any) -> None:
        mode = None
        if value is None:
            mode = None
        elif isinstance(value, str):
            mode = value
        else:
            name = type(value).__name__
            if "RateLimited" in name:
                mode = "rate_limited"
            elif "Unavailable" in name:
                mode = "unavailable"
            elif "NotFound" in name:
                mode = "not_found"
            else:
                raise TypeError(f"不支持的假来源错误：{value!r}")
        self._call("set_error", error=mode)

    def set_status(
        self,
        state: str = "ok",
        *,
        message: str | None = None,
        resume_at: str | None = None,
        page: str | None = None,
    ) -> None:
        self._call(
            "set_status",
            state=state,
            message=message,
            resume_at=resume_at,
            page=page,
        )

    def set_selectable(self, selectable: bool) -> None:
        self._call("set_selectable", selectable=selectable)

    def set_multiple(self, multiple: bool) -> None:
        self._call("set_multiple", multiple=multiple)


class _Node:
    def __init__(self, owner: FakeCloud115, node_id: str) -> None:
        self._owner = owner
        self._id = node_id

    def _read(self) -> dict[str, Any]:
        value: dict[str, Any] = self._owner._call("node", node_id=self._id)
        return value

    @property
    def id(self) -> str:
        return self._id

    @property
    def name(self) -> str:
        return str(self._read()["name"])

    @name.setter
    def name(self, value: str) -> None:
        self._owner._call("set_node", node_id=self._id, fields={"name": value})

    @property
    def size(self) -> int:
        return int(self._read()["size"])

    @size.setter
    def size(self, value: int) -> None:
        self._owner._call("set_node", node_id=self._id, fields={"size": value})

    @property
    def sha1(self) -> str:
        return str(self._read()["sha1"])

    @sha1.setter
    def sha1(self, value: str) -> None:
        self._owner._call("set_node", node_id=self._id, fields={"sha1": value})

    @property
    def children(self) -> list[str]:
        return [str(value) for value in self._read()["children"]]


class _Nodes(Mapping[str, _Node]):
    def __init__(self, owner: FakeCloud115) -> None:
        self._owner = owner

    def __getitem__(self, key: str) -> _Node:
        return _Node(self._owner, key)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        try:
            self._owner._call("node", node_id=key)
        except Exception:
            return False
        return True

    def __iter__(self) -> Iterator[str]:
        return iter(())

    def __len__(self) -> int:
        return 0
