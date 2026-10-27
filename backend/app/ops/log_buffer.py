"""进程内日志环形缓冲区；只保留可供管理员排障的最近记录。"""

from __future__ import annotations

import logging
import re
import threading
from collections import deque
from datetime import UTC, datetime
from typing import Any

_SECRET = re.compile(r"(?i)(cookie|token|password|authorization|signature)([=:]\s*)([^\s,;]+)")


def _safe(value: str) -> str:
    return _SECRET.sub(r"\1\2***", value)


class LogBuffer(logging.Handler):
    def __init__(self, capacity: int = 5000) -> None:
        super().__init__()
        self._rows: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            row = {
                "id": record.created,
                "at": datetime.fromtimestamp(record.created, UTC).isoformat(),
                "level": record.levelname.lower(),
                "module": record.name,
                "message": _safe(record.getMessage()),
            }
            with self._lock:
                self._rows.append(row)
        except Exception:
            self.handleError(record)

    def rows(self, level: str | None = None, after: float | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._rows)
        if level:
            rows = [row for row in rows if row["level"] == level.lower()]
        if after is not None:
            rows = [row for row in rows if row["id"] > after]
        return rows

    def text(self, level: str | None = None) -> str:
        return "".join(
            f"{row['at']} {row['level'].upper()} {row['module']}: {row['message']}\n"
            for row in self.rows(level)
        )


_buffer: LogBuffer | None = None


def install() -> LogBuffer:
    global _buffer
    if _buffer is None:
        _buffer = LogBuffer()
        logging.getLogger("app").addHandler(_buffer)
    return _buffer


def current() -> LogBuffer:
    return _buffer or install()
