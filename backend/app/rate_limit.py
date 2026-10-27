"""宿主内的通用限速器。

插件通过宿主 ``http`` 能力发起的请求共用它；115 插件自己还有一份独立实现，两者都不依赖
对方的代码。
"""

from __future__ import annotations

import heapq
import itertools
import threading
import time
from collections.abc import Callable

from app.priority import Priority


class RateLimiter:
    """相邻两次请求至少间隔 interval 秒。

    等待者按（优先级，到达顺序）排队，只有队首可以拿下一个时间片，所以高优先级请求会插到
    所有低优先级请求前面。monotonic 可注入，便于测试。
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
