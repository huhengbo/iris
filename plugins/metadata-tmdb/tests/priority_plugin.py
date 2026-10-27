"""子进程测试入口：用真实 TmdbPlugin 和假网关确认运行时下发了正确的优先级。"""

from __future__ import annotations

from dataclasses import replace

from iris_plugin_sdk import current_priority, run

from iris_plugin_tmdb.gateway import TmdbAccess, TmdbDetails, TmdbKind
from iris_plugin_tmdb.plugin import TmdbPlugin
from tests.fake_gateway import FakeTmdb


class PriorityGateway(FakeTmdb):
    def details(self, access: TmdbAccess, kind: TmdbKind, tmdb_id: int) -> TmdbDetails:
        details = super().details(access, kind, tmdb_id)
        return replace(details, title=current_priority())


if __name__ == "__main__":
    gateway = PriorityGateway()
    gateway.add_movie(1, "优先级探针", 2024)
    run(TmdbPlugin(gateway))
