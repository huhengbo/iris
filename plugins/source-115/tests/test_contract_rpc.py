"""通过真实子进程和 stdio JSON-RPC 跑同一套资源协议契约测试。"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from iris_plugin_sdk import (
    PluginRequest,
    PluginResponse,
    ResourceEntry,
    ResourceSource,
    SourceStatus,
)
from iris_plugin_sdk.testing import ResourceSourceContract, RpcClient

from tests.fake_gateway import CONTRACT_PATHS, VALID_COOKIE, FakeCloud115, build_contract_tree
from tests.subprocess_driver import FixedClock

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


class RpcSource:
    """把 ResourceSource 协议逐个方法转发给插件子进程。"""

    def __init__(self, client: RpcClient) -> None:
        self._client = client

    def browse(self, locator: str | None = None) -> Any:
        return self._client.request(
            "resource.browse", {"source_id": 1, "config": {}, "locator": locator}
        )

    def scan(
        self,
        locator: str | None,
        known: Mapping[str, ResourceEntry] | None = None,
    ) -> Any:
        return self._client.request(
            "resource.scan",
            {
                "source_id": 1,
                "config": {},
                "locator": locator,
                "known": dict(known) if known else None,
            },
        )

    def read(self, locator: str, max_bytes: int) -> bytes:
        result = self._client.request(
            "resource.read",
            {"source_id": 1, "config": {}, "locator": locator, "max_bytes": max_bytes},
        )
        assert isinstance(result, bytes)
        return result

    def resolve(self, locator: str, user_agent: str, throttle_key: str | None = None) -> Any:
        return self._client.request(
            "resource.resolve",
            {
                "source_id": 1,
                "config": {},
                "locator": locator,
                "user_agent": user_agent,
                "throttle_key": throttle_key,
            },
        )

    def delete(self, locator: str) -> None:
        self._client.request("resource.delete", {"source_id": 1, "config": {}, "locator": locator})


class _Capabilities:
    capabilities = frozenset({"status", "changes", "delete", "known_scan"})


class Test115SubprocessContract(ResourceSourceContract):
    client: RpcClient
    ids: dict[str, str]

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path: Path) -> Iterator[None]:
        self.client = RpcClient(PLUGIN_ROOT, module="tests.subprocess_driver")
        try:
            result = self.client.initialize(tmp_path / "data")
            assert result["manifest"]["id"] == "source.115"
            assert (tmp_path / "data" / "state").is_dir()
            saved = self.client.request(
                "admin.request",
                {
                    "request": PluginRequest(
                        method="PUT",
                        path="cookie",
                        query={},
                        body={"cookie": VALID_COOKIE},
                        user_id=1,
                    )
                },
            )
            assert saved.status == 200
            local = FakeCloud115(FixedClock())
            self.ids = build_contract_tree(local)
            yield
        finally:
            self.client.close()

    def make_source(self) -> ResourceSource:
        return RpcSource(self.client)

    def make_provider(self) -> Any:
        return _Capabilities()

    def expected_paths(self) -> set[str]:
        return set(CONTRACT_PATHS)

    def delete_target(self) -> str:
        for entry in self.source.scan(None):
            if entry.relative_path == "删除我.mkv":
                return entry.locator
        raise AssertionError("契约测试的删除目标不存在")

    def unselectable_locator(self) -> str:
        return self.ids["scratch"]


def test_initialize_describe_and_admin_over_stdio(tmp_path: Path) -> None:
    client = RpcClient(PLUGIN_ROOT, module="tests.subprocess_driver")
    try:
        result = client.initialize(tmp_path / "data")
        assert result["sdk_version"] == "1.0.0"
        assert result["manifest"]["provides"] == ["resource.source", "admin.ui"]
        assert result["manifest"]["pages"] == [
            {"id": "account", "title": "账号", "entry": "index.html"},
            {"id": "settings", "title": "设置", "entry": "settings.html"},
        ]

        described = client.request("resource.describe", {})
        assert described["id"] == "source.115"
        assert described["multiple"] is False
        assert described["capabilities"] == ["changes", "delete", "known_scan", "status"]
        assert described["config_fields"] == []

        status = client.request("resource.status", {"source_id": 1, "config": {}})
        assert isinstance(status, SourceStatus)
        assert status.state == "failed"
        assert status.page == "account"

        response = client.request(
            "admin.request",
            {
                "request": PluginRequest(
                    method="PUT",
                    path="cookie",
                    query={},
                    body={"cookie": VALID_COOKIE},
                    user_id=1,
                )
            },
        )
        assert isinstance(response, PluginResponse)
        assert response.status == 200
        assert response.body["user_name"] == "家庭影院"

        status = client.request("resource.status", {"source_id": 1, "config": {}})
        assert status.state == "ok"

        # source_id 显式传参，插件按它发 resource.changed（生活事件关闭时没有通知）
        assert client.changes == []
    finally:
        client.close()
