"""真实子进程 RPC 冒烟：握手、缺 Token 的搜索错误和设置页。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from iris_plugin_sdk import (
    MediaContext,
    PluginRequest,
    PluginResponse,
    SubtitleSearchRequest,
    SubtitleUnavailable,
)
from iris_plugin_sdk.testing import RpcClient

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def test_real_process_initializes_and_reports_missing_token(tmp_path: Path) -> None:
    client = RpcClient(PLUGIN_ROOT, module="iris_plugin_assrt")
    try:
        initialized = client.initialize(tmp_path / "data")
        manifest = initialized["manifest"]
        assert manifest["id"] == "builtin.assrt"
        assert manifest["provides"] == [
            "subtitle.search",
            "subtitle.download",
            "admin.ui",
        ]

        reason = client.request("provider.unavailable_reason", {"capability": "subtitle.search"})
        assert reason == "还没有填写 API Token"

        request = SubtitleSearchRequest(
            media=MediaContext(title="流浪地球", year=2019, kind="movie"),
            language="zh-Hans",
        )
        with pytest.raises(SubtitleUnavailable):
            client.request("subtitle.search", {"request": request})

        page = client.request(
            "admin.request", {"request": PluginRequest("GET", "settings", {}, None, 1)}
        )
        assert isinstance(page, PluginResponse)
        body = cast(dict[str, Any], page.body)
        assert body["values"]["api_token"] == {"set": False}
        assert body["values"]["api_host"] == "api.assrt.net"
    finally:
        client.close()
