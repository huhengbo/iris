from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence

from iris_plugin_sdk import (
    ResourceUnavailable,
)

from app.clock import Clock
from app.db import Database
from app.errors import AppError
from app.library.metadata import Metadata
from app.library.repository import LibraryRepository
from app.ops.webhooks import emit as emit_webhook
from app.resources.repository import ResourceRepository
from app.resources.service import ResourceSources
from app.settings import SettingsService
from app.sync.jobs import (
    SyncBusy,
    SyncCancelled,
    SyncJob,
    SyncJobRepository,
    Trigger,
)
from app.sync.run import Sleeper, SyncRun
from app.sync.targets import DirTarget, SourceDir

logger = logging.getLogger(__name__)

Executor = Callable[[Callable[[], None]], None]


def thread_executor(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="sync-job", daemon=True).start()


class SyncEngine:
    """同步任务的入口：校验后建任务记录，在后台执行扫描、比对、补全、识别和刮削（ADR 0007）。

    来源请求由插件自行调度；同步只负责阶段、快照和媒体处理。sleeper 和 executor 可注入，
    测试里用它们推进假时钟、控制任务何时执行。
    """

    def __init__(
        self,
        database: Database,
        clock: Clock,
        metadata: Metadata,
        settings: SettingsService,
        resources: ResourceSources,
        *,
        sleeper: Sleeper | None = None,
        executor: Executor | None = None,
    ) -> None:
        self._database = database
        self._clock = clock
        self._metadata = metadata
        self._settings = settings
        self._resources = resources
        self._sleeper = sleeper or time.sleep
        self._executor = executor or thread_executor

    def start(self, trigger: Trigger, library_id: int | None = None) -> SyncJob:
        return self._begin(trigger, library_id, None, None)

    def start_targeted(self, trigger: Trigger, targets: Sequence[DirTarget], label: str) -> SyncJob:
        """定向同步：只列出指定目录比对，不做整库扫描和同步删除。label 是给人看的范围说明。"""
        if not targets:
            raise ValueError("targets must not be empty")
        return self._begin(trigger, None, label, list(targets))

    def source_changed(self, source_id: int, locators: list[str]) -> None:
        """来源插件报告目录变动：按定位符找出受影响的资料库目录做定向同步。

        通知可能来自插件的后台线程；同一时间只跑一个同步任务，正在同步时留给定时的全量
        同步，不排队。
        """
        targets = [SourceDir(source_id, locator) for locator in dict.fromkeys(locators)]
        if not targets:
            return
        try:
            self.start_targeted("event", targets, f"来源目录变动（{len(targets)} 个目录）")
        except SyncBusy:
            logger.info(
                "source %s changed; a sync is running, waiting for scheduled sync", source_id
            )
        except ResourceUnavailable as exc:
            logger.info("source %s changed; source unavailable: %s", source_id, exc.message)
        except AppError as exc:
            logger.info("source %s changed; targeted sync skipped: %s", source_id, exc.message)

    def _begin(
        self,
        trigger: Trigger,
        library_id: int | None,
        label: str | None,
        targets: list[DirTarget] | None,
    ) -> SyncJob:
        # 资源插件的检查可能要走网络，不能放在持有写锁的事务里：先读出要检查的来源，
        # 在事务外检查，通过后再开写事务建任务
        with self._database.read_session() as connection:
            libraries = LibraryRepository(connection, self._clock)
            name = libraries.require(library_id).name if library_id is not None else None
            folders = libraries.folders(library_id)
            configs = (
                []
                if folders
                else [
                    (source.plugin_id, source.config)
                    for source in ResourceRepository(connection, self._clock).sources()
                    if source.enabled
                ]
            )
            sources = ResourceSources.folders(connection, [folder.id for folder in folders])
        try:
            for plugin_id, config in configs:
                self._resources.check_config(plugin_id, config)
            if not folders:
                raise AppError(400, "no_library", "还没有资料库，先创建资料库再同步")
            for source in sources.values():
                if source.enabled:
                    self._resources.check(source)
        except ResourceUnavailable as exc:
            raise AppError(exc.status_code, exc.code, exc.message) from exc
        with self._database.session() as connection:
            job = SyncJobRepository(connection, self._clock).create(
                trigger, library_id, name, label
            )
        logger.info("sync job %s started (%s)", job.id, trigger)
        self._executor(lambda: self._run(job, targets))
        return job

    def _run(self, job: SyncJob, targets: list[DirTarget] | None = None) -> None:
        run = SyncRun(
            self._database,
            self._clock,
            self._metadata,
            self._settings,
            self._resources,
            self._sleeper,
            job,
        )
        error = None
        cancelled = False
        try:
            if targets is None:
                run.execute()
            else:
                run.execute_targeted(targets)
        except SyncCancelled:
            cancelled = True
            error = "同步已取消"
        except AppError as exc:
            # 来源或其他服务报告的无法继续的情况
            error = exc.message
        except Exception:
            logger.exception("sync job %s crashed", job.id)
            error = "同步出错，详见服务日志"
        with self._database.session() as connection:
            SyncJobRepository(connection, self._clock).finish(
                job.id, run.stats, error, cancelled=cancelled
            )
            emit_webhook(
                connection,
                self._database,
                self._clock,
                "sync_failed" if error else "sync_completed",
                {
                    "job_id": job.id,
                    "library_id": job.library_id,
                    "status": "cancelled" if cancelled else "failed" if error else "succeeded",
                    "stats": run.stats.__dict__,
                    "error": error,
                },
            )
            if not error and run.stats.added:
                emit_webhook(
                    connection,
                    self._database,
                    self._clock,
                    "new_content",
                    {
                        "job_id": job.id,
                        "library_id": job.library_id,
                        "added": run.stats.added,
                        "matched": run.stats.matched,
                    },
                )
        logger.info("sync job %s finished: %s", job.id, error or run.stats.to_json())
