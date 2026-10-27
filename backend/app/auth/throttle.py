from __future__ import annotations

import threading
from collections import defaultdict, deque
from datetime import datetime, timedelta

from app.clock import Clock

LOGIN_WINDOW = timedelta(minutes=15)
MAX_FAILURES_PER_USERNAME = 5
MAX_FAILURES_PER_CLIENT = 20


class LoginThrottle:
    """登录失败限流：滑动窗口内同一用户名或同一来源失败过多时，暂时拒绝登录。

    单进程部署，状态放在内存里即可；重启后清零可以接受。
    """

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: defaultdict[str, deque[datetime]] = defaultdict(deque)

    def retry_after_seconds(self, username: str, client: str) -> int:
        """仍处于限流中时返回需要等待的秒数，否则返回 0。"""
        now = self._clock.now()
        with self._lock:
            waits = [
                self._wait(
                    self._key("u", username.strip().lower()), MAX_FAILURES_PER_USERNAME, now
                ),
                self._wait(self._key("c", client), MAX_FAILURES_PER_CLIENT, now),
            ]
        return max(waits)

    def record_failure(self, username: str, client: str) -> None:
        now = self._clock.now()
        with self._lock:
            self._failures[self._key("u", username.strip().lower())].append(now)
            self._failures[self._key("c", client)].append(now)

    def record_success(self, username: str) -> None:
        with self._lock:
            self._failures.pop(self._key("u", username.strip().lower()), None)

    def _wait(self, key: str, limit: int, now: datetime) -> int:
        failures = self._failures.get(key)
        if not failures:
            return 0
        while failures and now - failures[0] >= LOGIN_WINDOW:
            failures.popleft()
        if len(failures) < limit:
            return 0
        return max(1, int((failures[0] + LOGIN_WINDOW - now).total_seconds()))

    @staticmethod
    def _key(kind: str, value: str) -> str:
        return f"{kind}:{value}"
