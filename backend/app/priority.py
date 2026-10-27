from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from enum import IntEnum


class Priority(IntEnum):
    """数值越小越优先。播放取链和管理员操作是有人在等的，同步任务让行。"""

    INTERACTIVE = 0
    BACKGROUND = 1


# 元数据插件的 SDK 方法不带优先级；宿主调用前设置，内置 TMDB 插件据此排队限速，
# 管理员操作优先于同步任务。外部插件不需要关心。
_CURRENT: ContextVar[Priority] = ContextVar("metadata_priority", default=Priority.INTERACTIVE)


def current_priority() -> Priority:
    return _CURRENT.get()


@contextmanager
def priority_scope(priority: Priority) -> Iterator[None]:
    token = _CURRENT.set(priority)
    try:
        yield
    finally:
        _CURRENT.reset(token)
