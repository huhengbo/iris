"""TMDB Key 最近一次校验结果的持久化和说明文字。"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from iris_plugin_sdk import read_json, write_json

TmdbValidationStatus = Literal["ok", "invalid", "unreachable"]
_VALIDATION_STATUSES = ("ok", "invalid", "unreachable")

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TmdbValidation:
    """ok 可用，invalid 被 TMDB 拒绝（401），unreachable 连不上。"""

    status: TmdbValidationStatus
    checked_at: str


def validation_problem(
    validation: TmdbValidation | None, *, blocking_only: bool = False
) -> str | None:
    """Key 最近一次校验没通过时的说明；没有记录或校验通过时为 None。

    blocking_only 时只返回会让调用失去意义的问题（Key 无效），连不上这类暂时的不返回。
    """
    if validation is None or validation.status == "ok":
        return None
    if blocking_only and validation.status != "invalid":
        return None
    if validation.status == "invalid":
        return "TMDB Key 校验失败：Key 无效或已被吊销"
    return "TMDB Key 校验失败：连不上 TMDB，请检查网络或代理"


class ValidationStore:
    """把最近一次校验结果存在 ``state/validation.json``。

    读取后放在内存，只有状态变化时才写盘；文件损坏按没有记录处理，不让设置页因为一份坏
    状态文件而无法读取。
    """

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / "state" / "validation.json"
        self._lock = threading.Lock()
        self._loaded = False
        self._current: TmdbValidation | None = None

    def get(self) -> TmdbValidation | None:
        with self._lock:
            self._ensure_loaded()
            return self._current

    def record(self, status: TmdbValidationStatus) -> None:
        with self._lock:
            self._ensure_loaded()
            if self._current is not None and self._current.status == status:
                return
            validation = TmdbValidation(status, datetime.now(UTC).isoformat())
            write_json(
                self._path,
                {"status": validation.status, "checked_at": validation.checked_at},
            )
            self._current = validation

    def clear(self) -> None:
        with self._lock:
            self._loaded = True
            self._current = None
            self._path.unlink(missing_ok=True)

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            data = read_json(self._path, None)
        except ValueError:
            logger.warning("校验状态文件已损坏，按没有记录处理：%s", self._path)
            return
        if data is None:
            return
        if not isinstance(data, dict) or data.get("status") not in _VALIDATION_STATUSES:
            logger.warning("校验状态文件格式不正确，按没有记录处理：%s", self._path)
            return
        checked_at = data.get("checked_at")
        if not isinstance(checked_at, str) or not checked_at:
            logger.warning("校验状态文件格式不正确，按没有记录处理：%s", self._path)
            return
        self._current = TmdbValidation(status=data["status"], checked_at=checked_at)
