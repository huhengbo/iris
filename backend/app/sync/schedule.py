from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Sequence
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.clock import Clock, to_iso
from app.db import Database
from app.errors import AppError
from app.settings import get_setting, put_setting
from app.sync.engine import SyncEngine
from app.sync.jobs import SyncJob

logger = logging.getLogger(__name__)

_TIMES_SETTING = "sync.schedule"
# 最近一个已处理的时间点。服务停机期间错过的时间点在启动后只补跑一次
_LAST_SETTING = "sync.schedule_last"
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
MAX_TIMES = 12
TICK_SECONDS = 30.0


class SyncSchedule:
    """定时同步：每天在配置的时间点（按 TZ 时区）触发一次全部资料库的同步。"""

    def __init__(
        self, database: Database, clock: Clock, timezone: ZoneInfo, engine: SyncEngine
    ) -> None:
        self._database = database
        self._clock = clock
        self.timezone = timezone
        self._engine = engine

    def times(self) -> list[str]:
        with self._database.read_session() as connection:
            value = get_setting(connection, _TIMES_SETTING)
        return list(json.loads(value)) if value else []

    def save(self, times: Sequence[str]) -> list[str]:
        cleaned = sorted({time.strip() for time in times})
        if any(_TIME.match(time) is None for time in cleaned):
            raise AppError(400, "invalid_schedule", "时间格式应为 HH:MM，例如 03:30")
        if len(cleaned) > MAX_TIMES:
            raise AppError(400, "invalid_schedule", f"每天最多设置 {MAX_TIMES} 个时间点")
        now = to_iso(self._clock.now())
        with self._database.session() as connection:
            put_setting(connection, _TIMES_SETTING, json.dumps(cleaned), now)
            # 从现在开始算，保存时已经过去的时间点不补跑
            put_setting(connection, _LAST_SETTING, now, now)
        return cleaned

    def next_run(self) -> datetime | None:
        times = self.times()
        if not times:
            return None
        local = self._clock.now().astimezone(self.timezone)
        day = timedelta(days=1)
        return min(slot if slot > local else slot + day for slot in _slots(times, local))

    def tick(self) -> SyncJob | None:
        """检查是否到了时间点，到了就触发同步。由后台线程定期调用。"""
        times = self.times()
        if not times:
            return None
        now = self._clock.now()
        local = now.astimezone(self.timezone)
        day = timedelta(days=1)
        due = max(slot if slot <= local else slot - day for slot in _slots(times, local))
        with self._database.session() as connection:
            last = get_setting(connection, _LAST_SETTING)
            if last is not None and due <= datetime.fromisoformat(last):
                return None
            put_setting(connection, _LAST_SETTING, to_iso(due), to_iso(now))
        if last is None:
            return None
        try:
            return self._engine.start("scheduled")
        except AppError as exc:
            # 已有任务在跑、凭据失效或还没有资料库：跳过这个时间点，等下一个
            logger.warning("scheduled sync skipped: %s", exc.code)
            return None


def run_schedule(schedule: SyncSchedule, stop: threading.Event) -> None:
    while not stop.wait(TICK_SECONDS):
        try:
            schedule.tick()
        except Exception:
            logger.exception("sync schedule tick failed")


def _slots(times: Sequence[str], local: datetime) -> list[datetime]:
    slots = []
    for time in times:
        hour, minute = (int(part) for part in time.split(":"))
        slots.append(local.replace(hour=hour, minute=minute, second=0, microsecond=0))
    return slots
