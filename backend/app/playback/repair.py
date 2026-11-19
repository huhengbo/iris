"""播放时发现来源上的文件已经变了：定向同步它所在的目录，把播放定位符刷新回来。

115 之类的网盘把文件重传、替换之后路径不变，播放定位符（pickcode）却换了，库里的记录
就指向一个已经失效的文件，播放只能 404。生活事件会触发同一次定向同步，但监听关闭、
插件重启或事件漏掉时就没人知道；播放失败是最直接的信号，顺手补一次，下次播放就正常了。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Protocol

from iris_plugin_sdk import ResourceUnavailable

from app.clock import Clock
from app.db import Database
from app.errors import AppError
from app.sync.jobs import SyncBusy, Trigger
from app.sync.targets import DirTarget, LibraryDir

logger = logging.getLogger(__name__)

# 同一个版本这么久内只补一次：播放器失败后会连着重试，不能每次都开一个同步任务
REPAIR_COOLDOWN = timedelta(minutes=10)


class SyncStarter(Protocol):
    def start_targeted(self, trigger: Trigger, targets: list[DirTarget], label: str) -> object: ...


class StaleFileRepair:
    """播放 404 时的补救：按版本找到文件所在目录，做一次定向同步。

    返回 True 表示已经排上了同步。任何失败都只记日志，不影响播放接口本来的 404。
    """

    def __init__(
        self,
        database: Database,
        clock: Clock,
        sync: SyncStarter,
        *,
        cooldown: timedelta = REPAIR_COOLDOWN,
    ) -> None:
        self._database = database
        self._clock = clock
        self._sync = sync
        self._cooldown = cooldown
        self._last: dict[int, datetime] = {}
        self._lock = threading.Lock()

    def __call__(self, version_id: int) -> bool:
        if not self._allow(version_id):
            return False
        with self._database.read_session() as connection:
            row = connection.execute(
                "SELECT c.folder_id, c.rel_path FROM version v JOIN media_file c "
                "ON c.id = v.media_file_id WHERE v.id = ?",
                (version_id,),
            ).fetchone()
        if row is None:
            return False
        rel_path = str(row["rel_path"])
        rel_dir = rel_path.rpartition("/")[0]
        name = rel_dir.rpartition("/")[2] or rel_path
        try:
            self._sync.start_targeted(
                "event",
                [LibraryDir(int(row["folder_id"]), rel_dir, deep=True)],
                f"播放时发现文件已变化：{name}",
            )
        except SyncBusy:
            logger.info("playback repair skipped for version %s: a sync is running", version_id)
            return False
        except (AppError, ResourceUnavailable) as exc:
            logger.info("playback repair skipped for version %s: %s", version_id, exc)
            return False
        logger.info("playback repair scheduled for version %s (%s)", version_id, rel_dir)
        return True

    def _allow(self, version_id: int) -> bool:
        """按版本限流；顺手清掉已经过期的记录，字典不会一直长。"""
        now = self._clock.now()
        with self._lock:
            last = self._last.get(version_id)
            if last is not None and now - last < self._cooldown:
                return False
            self._last = {key: at for key, at in self._last.items() if now - at < self._cooldown}
            self._last[version_id] = now
            return True
