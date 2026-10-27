"""子进程测试入口：用假网关跑真实的 stdio JSON-RPC 运行时。

测试用 `python -m tests.subprocess_driver` 启动它，宿主侧只通过 NDJSON 调用插件。
"""

from __future__ import annotations

from datetime import UTC, datetime

from iris_plugin_sdk import run

from iris_source_115.plugin import Source115Plugin
from tests.fake_gateway import FakeCloud115, build_contract_tree


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 1, 1, tzinfo=UTC)


def main() -> None:
    clock = FixedClock()
    gateway = FakeCloud115(clock)
    build_contract_tree(gateway)
    plugin = Source115Plugin(
        gateway=gateway,
        clock=clock,
        life_tick_seconds=3600,
        request_interval_seconds=0,
    )
    run(plugin)


if __name__ == "__main__":
    main()
