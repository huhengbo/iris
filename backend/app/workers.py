"""后台线程：定时同步和计划维护任务。

线程都是守护线程，由应用的 lifespan 启动，并在退出时通知停止、等待结束。
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from app.container import AppContainer
from app.ops.maintenance import run_maintenance
from app.sync.schedule import run_schedule

JOIN_TIMEOUT_SECONDS = 5.0


class BackgroundWorkers:
    def __init__(self, container: AppContainer) -> None:
        self._stop = threading.Event()
        self._threads = [
            self._thread("sync-schedule", run_schedule, container.sync_schedule),
            self._thread(
                "maintenance-schedule",
                run_maintenance,
                container.database,
                container.backups,
                container.images,
                container.metadata,
                container.subtitle_auto,
                container.plugin_repositories,
                container.clock,
            ),
            # 全新安装的官方仓库一直是 pending，启动后马上拉一次索引；需要网络，不能阻塞启动
            threading.Thread(
                target=container.plugin_repositories.refresh_pending,
                name="plugin-repository-refresh",
                daemon=True,
            ),
            # 恢复备份或上次安装中断后补装缺失的插件；需要网络，不能阻塞启动
            threading.Thread(
                target=container.plugin_packages.reinstall_missing,
                name="plugin-reinstall",
                daemon=True,
            ),
        ]

    def _thread(self, name: str, target: Callable[..., None], *args: object) -> threading.Thread:
        return threading.Thread(target=target, args=(*args, self._stop), name=name, daemon=True)

    def start(self) -> None:
        for thread in self._threads:
            thread.start()

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            if thread.is_alive():
                thread.join(JOIN_TIMEOUT_SECONDS)
