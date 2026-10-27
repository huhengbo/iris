"""真实子进程 RPC 冒烟：握手、不可用原因、设置页和落盘。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from iris_plugin_sdk import PluginRequest, PluginResponse
from iris_plugin_sdk.testing import RpcClient

from tests.fake_gateway import VALID_KEY

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def test_real_process_initializes_and_saves_settings(tmp_path: Path) -> None:
    client = RpcClient(PLUGIN_ROOT, module="iris_plugin_tmdb")
    try:
        initialized = client.initialize(tmp_path / "data")
        manifest = initialized["manifest"]
        assert manifest["id"] == "builtin.tmdb"
        assert manifest["version"] == "5"
        assert "image.fetch" in manifest["provides"]

        reason = client.request("provider.unavailable_reason", {"capability": "metadata.details"})
        assert "TMDB Key" in reason

        page = client.request(
            "admin.request", {"request": PluginRequest("GET", "settings", {}, None, 1)}
        )
        assert isinstance(page, PluginResponse)
        body = cast(dict[str, Any], page.body)
        assert body["values"]["api_key"] == {"set": False}

        saved = client.request(
            "admin.request",
            {"request": PluginRequest("PUT", "settings", {}, {"api_key": VALID_KEY}, 1)},
        )
        assert isinstance(saved, PluginResponse)
        status = client.request(
            "admin.request", {"request": PluginRequest("GET", "status", {}, None, 1)}
        )
        assert isinstance(status, PluginResponse)
        assert cast(dict[str, Any], status.body)["api_key_set"] is True
        assert json.loads((tmp_path / "data" / "state" / "settings.json").read_text()) == {
            "api_key": VALID_KEY
        }
    finally:
        client.close()
