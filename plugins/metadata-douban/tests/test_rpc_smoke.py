"""真实子进程 RPC 冒烟：握手、补缺 find 和设置页。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from iris_plugin_sdk import PluginRequest, PluginResponse
from iris_plugin_sdk.testing import RpcClient

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def test_real_process_initializes_and_serves_settings(tmp_path: Path) -> None:
    client = RpcClient(PLUGIN_ROOT, module="iris_plugin_douban")
    try:
        initialized = client.initialize(tmp_path / "data")
        manifest = initialized["manifest"]
        assert manifest["id"] == "builtin.douban"
        assert manifest["capabilities"] == ["http", "logger"]

        assert (
            client.request("provider.unavailable_reason", {"capability": "metadata.details"})
            is None
        )
        # 不支持的外部 ID 直接返回 None，不经过宿主 HTTP
        assert (
            client.request("metadata.find", {"kind": "movie", "scheme": "tmdb", "value": "157336"})
            is None
        )

        status = client.request(
            "admin.request", {"request": PluginRequest("GET", "status", {}, None, 1)}
        )
        assert isinstance(status, PluginResponse)
        body = cast(dict[str, Any], status.body)
        assert body["cookie_set"] is False
        assert body["request_interval"] == 3
    finally:
        client.close()
