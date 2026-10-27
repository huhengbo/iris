"""请求限速和按接口分组的熔断（ADR 0007、ADR 0027）。

所有 115 请求共用每秒 2 次的全局限速；风控熔断按网页版、App 版接口分组，状态存插件
自己的 `state/breaker-<group>.json`，插件重启不会提前解除熔断。
"""

from __future__ import annotations

import heapq
import itertools
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import IntEnum, StrEnum
from pathlib import Path

from iris_plugin_sdk import read_json, write_json

from iris_source_115.clock import Clock, from_iso, to_iso
from iris_source_115.errors import RateLimited

DEFAULT_INTERVAL_SECONDS = 0.5  # 每秒 2 次，见 ADR 0007
BACKOFF_START = timedelta(minutes=30)
BACKOFF_MAX = timedelta(hours=6)


class Priority(IntEnum):
    """数值越小越优先。播放取链和管理员操作是有人在等的，同步任务让行。"""

    INTERACTIVE = 0
    BACKGROUND = 1


class ApiGroup(StrEnum):
    """115 按接口分组做风控拦截，一组被拦不影响另一组（ADR 0027）。

    WEB 是网页版接口（webapi.115.com 等），列目录、导出、复制、删除都走它；
    APP 是 App 版接口（proapi.115.com），取直链和 App 版生活事件走它。
    """

    WEB = "web"
    APP = "app"


class RateLimiter:
    """全局限速：相邻两次请求至少间隔 interval 秒。

    等待者按（优先级，到达顺序）排队，只有队首可以拿下一个时间片，
    所以高优先级请求会插到所有低优先级请求前面。monotonic 可注入，便于测试。
    """

    def __init__(self, interval: float, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._interval = interval
        self._monotonic = monotonic
        self._cond = threading.Condition()
        self._queue: list[tuple[int, int]] = []
        self._seq = itertools.count()
        self._next_slot = 0.0

    @property
    def waiting(self) -> int:
        with self._cond:
            return len(self._queue)

    def acquire(self, priority: Priority) -> None:
        with self._cond:
            ticket = (int(priority), next(self._seq))
            heapq.heappush(self._queue, ticket)
            self._cond.notify_all()
            try:
                while True:
                    if self._queue[0] == ticket:
                        delay = self._next_slot - self._monotonic()
                        if delay <= 0:
                            break
                        self._cond.wait(delay)
                    else:
                        self._cond.wait()
                self._next_slot = max(self._monotonic(), self._next_slot) + self._interval
            finally:
                self._queue.remove(ticket)
                heapq.heapify(self._queue)
                self._cond.notify_all()


@dataclass(frozen=True)
class BreakerState:
    # 连续触发风控的次数，决定下一次退避时长
    level: int = 0
    resume_at: datetime | None = None


class CircuitBreaker:
    """一组接口的风控熔断：触发后在 resume_at 之前这组请求立即失败。

    退避从 30 分钟起逐次加倍，上限 6 小时；恢复后第一次请求成功才清零。
    状态写入插件自己的状态文件，插件重启不会提前解除熔断。
    """

    def __init__(self, clock: Clock, path: Path, group: ApiGroup = ApiGroup.WEB) -> None:
        self._clock = clock
        self._path = path
        self._group = group
        self._lock = threading.Lock()
        self._state = self._load()

    @property
    def group(self) -> ApiGroup:
        return self._group

    @property
    def state(self) -> BreakerState:
        with self._lock:
            return self._state

    def check(self) -> None:
        with self._lock:
            resume_at = self._state.resume_at
        if resume_at is not None and self._clock.now() < resume_at:
            raise RateLimited(resume_at)

    def trip(self) -> datetime:
        now = self._clock.now()
        with self._lock:
            current = self._state
            # 并发请求同时撞上风控时只算一次
            if current.resume_at is not None and now < current.resume_at:
                return current.resume_at
            # 指数先封顶再乘，避免连续多次风控后 timedelta 溢出
            backoff = min(BACKOFF_START * (1 << min(current.level, 16)), BACKOFF_MAX)
            self._state = BreakerState(level=current.level + 1, resume_at=now + backoff)
            self._save(self._state)
            return now + backoff

    def record_success(self) -> None:
        with self._lock:
            if self._state.level == 0:
                return
            self._state = BreakerState()
            self._save(self._state)

    def _load(self) -> BreakerState:
        data = read_json(self._path)
        if data is None:
            return BreakerState()
        if not isinstance(data, dict):
            raise ValueError(f"熔断状态文件必须是 JSON 对象：{self._path}")
        resume_at = data.get("resume_at")
        return BreakerState(
            level=int(data.get("level", 0)),
            resume_at=from_iso(str(resume_at)) if resume_at else None,
        )

    def _save(self, state: BreakerState) -> None:
        write_json(
            self._path,
            {
                "level": state.level,
                "resume_at": to_iso(state.resume_at) if state.resume_at else None,
            },
        )


class RequestScheduler:
    """所有 115 请求的必经之路：先过所属分组的熔断，再排队限速，最后按结果更新熔断状态。

    限速是全局共用的；熔断按接口分组，同步撞上风控时不连累取直链。
    """

    def __init__(self, limiter: RateLimiter, breakers: Mapping[ApiGroup, CircuitBreaker]) -> None:
        self.limiter = limiter
        self.breakers = dict(breakers)

    def run[T](
        self, priority: Priority, call: Callable[[], T], group: ApiGroup = ApiGroup.WEB
    ) -> T:
        breaker = self.breakers[group]
        breaker.check()
        self.limiter.acquire(priority)
        # 排队期间可能已经有别的请求触发了风控
        breaker.check()
        try:
            result = call()
        except RateLimited:
            raise RateLimited(breaker.trip()) from None
        breaker.record_success()
        return result
