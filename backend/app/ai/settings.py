"""AI 助手的开关和阈值，存在系统设置表里（ADR 0030）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.clock import Clock, to_iso
from app.db import Database
from app.errors import AppError
from app.settings import delete_setting, get_setting, put_setting

ApprovalMode = Literal["confirm", "auto"]

_APPROVAL = "ai.approval.reversible_write"
_SUGGEST = "ai.suggest.enabled"
_AUTO_MATCH = "ai.auto_match.enabled"
_THRESHOLD = "ai.auto_match.threshold"

DEFAULT_THRESHOLD = 0.9
MIN_THRESHOLD = 0.5


@dataclass(frozen=True)
class AiSettingsValues:
    # 可逆写工具是否需要确认；默认需要
    reversible_write: ApprovalMode
    # 同步后是否自动为未匹配的作品生成匹配建议（需要配置后台任务模型）
    suggest_matches: bool
    # 高置信度且通过硬校验的建议是否自动写入匹配；默认关闭
    auto_match: bool
    auto_match_threshold: float


class AiSettings:
    def __init__(self, database: Database, clock: Clock) -> None:
        self._database = database
        self._clock = clock

    def get(self) -> AiSettingsValues:
        with self._database.read_session() as connection:
            approval = get_setting(connection, _APPROVAL)
            suggest = get_setting(connection, _SUGGEST)
            auto = get_setting(connection, _AUTO_MATCH)
            threshold = get_setting(connection, _THRESHOLD)
        return AiSettingsValues(
            reversible_write="auto" if approval == "auto" else "confirm",
            suggest_matches=suggest != "0",
            auto_match=auto == "1",
            auto_match_threshold=float(threshold) if threshold else DEFAULT_THRESHOLD,
        )

    def save(
        self,
        *,
        reversible_write: ApprovalMode | None = None,
        suggest_matches: bool | None = None,
        auto_match: bool | None = None,
        auto_match_threshold: float | None = None,
    ) -> AiSettingsValues:
        """不传的项保持原值。"""
        if auto_match_threshold is not None and not MIN_THRESHOLD <= auto_match_threshold <= 1:
            raise AppError(
                400, "invalid_ai_setting", f"自动匹配的置信度阈值应在 {MIN_THRESHOLD} 到 1 之间"
            )
        now = to_iso(self._clock.now())
        with self._database.session() as connection:
            if reversible_write is not None:
                put_setting(connection, _APPROVAL, reversible_write, now)
            if suggest_matches is not None:
                put_setting(connection, _SUGGEST, "1" if suggest_matches else "0", now)
            if auto_match is not None:
                put_setting(connection, _AUTO_MATCH, "1" if auto_match else "0", now)
            if auto_match_threshold is not None:
                if auto_match_threshold == DEFAULT_THRESHOLD:
                    delete_setting(connection, _THRESHOLD)
                else:
                    put_setting(connection, _THRESHOLD, str(auto_match_threshold), now)
        return self.get()
