"""多端同时播放：给第二个设备复制一份文件，用副本的 pickcode 取直链（ADR 0027）。

115 的一个直链最多 3 个并发连接，两台设备同时播一个文件很容易超出。做法参考 p115strmhelper：
复制文件到专用目录，用副本取一个独立的直链，几秒后删掉副本。复制要写 115，默认关闭。
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import timedelta
from functools import partial

from iris_source_115.errors import (
    Cloud115Error,
    Cloud115Unavailable,
    CopyNotReady,
    FileGone,
)
from iris_source_115.gateway import DirectLink
from iris_source_115.scheduler import Priority
from iris_source_115.service import Cloud115Service

logger = logging.getLogger(__name__)

# 取到副本的直链后过这么久删除副本，和 p115strmhelper 一致
COPY_DELETE_DELAY = timedelta(seconds=5)
# 副本还没处理完时，依次等这么久再取直链
COPY_LINK_RETRY_DELAYS = (0.5, 1.0, 2.0)
# 找副本时最多看目录里最新的这么多个文件
RECENT_LIMIT = 32


def _defer(delay: float, action: Callable[[], None]) -> None:
    """过 delay 秒在后台线程执行 action。测试替换它来手动触发删除。"""
    timer = threading.Timer(delay, action)
    timer.daemon = True
    timer.start()


class PlaybackCopies:
    """复制、找副本、取直链、延迟删除。

    同一时间只复制一份：目录里除了已交出去的副本，只应该剩刚复制的那一个。
    """

    def __init__(
        self,
        service: Cloud115Service,
        dir_path: str,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self._service = service
        self._dir_path = dir_path
        self._sleeper = sleeper or time.sleep
        self._lock = threading.Lock()
        self._dir_id: str | None = None
        # 已经交出去、还没删掉的副本 ID；目录里不在这里的旧文件都是没删成功的残留
        self._pending: set[str] = set()

    @property
    def dir_id(self) -> str | None:
        return self._dir_id

    def link(self, pickcode: str, user_agent: str) -> DirectLink:
        """复制 pickcode 对应的文件并取副本的直链。失败时抛出 Cloud115Error，由调用方退回原文件。"""
        with self._lock:
            try:
                dir_id = self._ensure_dir()
                self._service.copy_file(pickcode, dir_id, Priority.INTERACTIVE)
                recent = self._service.recent_files(dir_id, RECENT_LIMIT, Priority.INTERACTIVE)
            except Cloud115Error:
                # 专用目录可能被人删了，下次重新建
                self._dir_id = None
                raise
            fresh = [entry for entry in recent if entry.id not in self._pending]
            for entry in fresh:
                self._pending.add(entry.id)
            if len(fresh) != 1 or not fresh[0].pickcode:
                # 有没删掉的残留时认不准哪个是刚复制的，全部删掉，这次退回原文件
                for entry in fresh:
                    _defer(0, partial(self._delete, entry.id))
                raise Cloud115Unavailable("115 上没有认出刚复制的文件")
            copy = fresh[0]
        try:
            return self._copy_link(copy.pickcode, user_agent)
        finally:
            _defer(COPY_DELETE_DELAY.total_seconds(), partial(self._delete, copy.id))

    def _ensure_dir(self) -> str:
        if self._dir_id is None:
            self._dir_id = self._service.make_dir(self._dir_path, Priority.INTERACTIVE)
        return self._dir_id

    def _copy_link(self, pickcode: str, user_agent: str) -> DirectLink:
        for delay in (*COPY_LINK_RETRY_DELAYS, None):
            try:
                return self._service.direct_link(pickcode, user_agent, Priority.INTERACTIVE)
            except CopyNotReady:
                if delay is None:
                    raise
                self._sleeper(delay)
        raise AssertionError("unreachable")

    def _delete(self, file_id: str) -> None:
        try:
            self._service.delete_file(file_id, Priority.BACKGROUND)
        except FileGone:
            pass
        except Cloud115Error as exc:
            # 留在目录里，下次复制时当作残留再删
            logger.warning("failed to delete 115 playback copy: %s", exc.code)
        finally:
            with self._lock:
                self._pending.discard(file_id)
