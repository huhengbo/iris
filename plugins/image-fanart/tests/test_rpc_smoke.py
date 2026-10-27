"""真实子进程 RPC 冒烟：握手和缺 Key 时的不可用说明。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from iris_plugin_sdk import PluginRequest, PluginResponse
from iris_plugin_sdk.testing import RpcClient

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def test_real_process_initializes_and_reports_missing_key(tmp_path: Path) -> None:
    client = RpcClient(PLUGIN_ROOT, module="iris_plugin_fanart")
    try:
        initialized = client.initialize(tmp_path / "data")
        manifest = initialized["manifest"]
        assert manifest["id"] == "builtin.fanart"
        assert manifest["provides"] == ["image.fetch", "admin.ui"]

        reason = client.request("provider.unavailable_reason", {"capability": "image.fetch"})
        assert "API Key" in reason

        page = client.request(
            "admin.request", {"request": PluginRequest("GET", "settings", {}, None, 1)}
        )
        assert isinstance(page, PluginResponse)
        assert cast(dict[str, Any], page.body)["values"]["api_key"] == {"set": False}
    finally:
        client.close()
