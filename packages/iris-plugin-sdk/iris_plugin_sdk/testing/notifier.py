"""测试用的通知记录器，替代运行时注入的 PluginNotifier。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from iris_plugin_sdk.protocol import WaitReason


@dataclass
class RecordingNotifier:
    changes: list[tuple[int, list[str]]] = field(default_factory=list)
    waits: list[tuple[datetime, WaitReason]] = field(default_factory=list)
    heartbeats: int = 0

    def resource_changed(self, source_id: int, locators: Sequence[str]) -> None:
        self.changes.append((source_id, list(locators)))

    def progress(self, until: datetime, reason: WaitReason) -> None:
        self.waits.append((until, reason))

    def heartbeat(self) -> None:
        self.heartbeats += 1


def notifier() -> Any:
    """给不关心通知的插件测试用的默认实例。"""
    return RecordingNotifier()
