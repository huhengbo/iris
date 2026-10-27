"""真实子进程 RPC：background details 请求必须让插件端拿到 background。"""

from __future__ import annotations

from pathlib import Path

from iris_plugin_sdk import PluginRequest
from iris_plugin_sdk.testing import RpcClient

from tests.fake_gateway import VALID_KEY

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def test_details_priority_reaches_plugin_process(tmp_path: Path) -> None:
    client = RpcClient(PLUGIN_ROOT, module="tests.priority_plugin")
    try:
        client.initialize(tmp_path / "data")
        client.request(
            "admin.request",
            {"request": PluginRequest("PUT", "settings", {}, {"api_key": VALID_KEY}, 1)},
        )

        background = client.request(
            "metadata.details",
            {"remote_id": "1", "kind": "movie", "priority": "background"},
        )
        assert background.title == "background"

        default = client.request("metadata.details", {"remote_id": "1", "kind": "movie"})
        assert default.title == "interactive"
    finally:
        client.close()
