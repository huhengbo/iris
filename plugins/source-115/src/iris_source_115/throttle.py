"""播放跳转按令牌限流（ADR 0015）：地址泄露后不能被拿来滥用、拖垮 115 配额。"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timedelta

from iris_source_115.clock import Clock

# 每个令牌每分钟最多的跳转次数。拖动进度时有的播放器会重新请求跳转地址，留足余量
MAX_REQUESTS_PER_MINUTE = 120
# 每个令牌每小时最多向 115 取链的次数。缓存命中不计，挡住换着 UA 刷取链
MAX_FETCHES_PER_HOUR = 30
_MINUTE = timedelta(minutes=1)
_HOUR = timedelta(hours=1)


class PlayThrottle:
    """滑动窗口，状态放在内存里；键是令牌的摘要，不保存令牌本身。"""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._requests: dict[str, deque[datetime]] = {}
        self._fetches: dict[str, deque[datetime]] = {}
        self._last_prune = clock.now()

    def request(self, key: str) -> int:
        """记一次跳转请求。超限时返回需要等待的秒数（不计入），否则返回 0。"""
        return self._hit(self._requests, key, MAX_REQUESTS_PER_MINUTE, _MINUTE)

    def fetch(self, key: str) -> int:
        """记一次取链。超限时返回需要等待的秒数（不计入），否则返回 0。"""
        return self._hit(self._fetches, key, MAX_FETCHES_PER_HOUR, _HOUR)

    def _hit(
        self, windows: dict[str, deque[datetime]], key: str, limit: int, span: timedelta
    ) -> int:
        now = self._clock.now()
        with self._lock:
            self._prune(now)
            hits = windows.setdefault(key, deque())
            while hits and now - hits[0] >= span:
                hits.popleft()
            if len(hits) >= limit:
                return max(1, int((hits[0] + span - now).total_seconds()))
            hits.append(now)
            return 0

    def _prune(self, now: datetime) -> None:
        """每小时清一次已经没有记录落在窗口内的令牌，内存不随令牌数一直增长。"""
        if now - self._last_prune < _HOUR:
            return
        self._last_prune = now
        for windows, span in ((self._requests, _MINUTE), (self._fetches, _HOUR)):
            for key in [k for k, hits in windows.items() if not hits or now - hits[-1] >= span]:
                del windows[key]
