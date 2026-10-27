"""插件端请求优先级。

宿主在每次 ``metadata.*`` / ``image.*`` RPC 的 params 里带 ``priority``，运行时在调用
provider 前设置这个 contextvar；插件用 ``current_priority()`` 决定限速队列。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from iris_plugin_sdk.types import MetadataPriority

_PRIORITIES = ("interactive", "background")
_CURRENT: ContextVar[MetadataPriority] = ContextVar("metadata_priority", default="interactive")


def current_priority() -> MetadataPriority:
    return _CURRENT.get()


@contextmanager
def priority_scope(priority: MetadataPriority) -> Iterator[None]:
    """在 with 块内切换优先级；退出时恢复之前的值。"""
    if priority not in _PRIORITIES:
        raise ValueError(f"未知的优先级：{priority}")
    token = _CURRENT.set(priority)
    try:
        yield
    finally:
        _CURRENT.reset(token)
