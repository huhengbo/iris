"""真实子进程 RPC 下的资源来源契约；同时覆盖 manifest 与协议握手。"""

from __future__ import annotations

import atexit
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
from iris_plugin_sdk import (
    PlaybackTarget,
    ResourceConfigField,
    ResourceEntry,
    ResourceListing,
    ResourceProvider,
    ResourceSource,
    ResourceUnavailable,
    SourceStatus,
)
from iris_plugin_sdk.testing import ResourceSourceContract, RpcClient

from tests.sample import FILES, build_tree

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class _Shared:
    client: RpcClient
    root: Path
    config: dict[str, Any]


_shared: _Shared | None = None


def shared() -> _Shared:
    global _shared
    if _shared is None:
        root = Path(tempfile.mkdtemp(prefix="iris-local-rpc-"))
        build_tree(root)
        data_dir = Path(tempfile.mkdtemp(prefix="iris-local-rpc-data-"))
        client = RpcClient(PLUGIN_ROOT, module="iris_plugin_source_local")
        atexit.register(client.close)
        client.initialize(data_dir, media_dir=root)
        _shared = _Shared(client, root, {"root": str(root)})
    return _shared


class RpcSource(ResourceSource):
    def __init__(self) -> None:
        state = shared()
        self._client = state.client
        self._config = state.config

    def _call(self, method: str, params: dict[str, Any]) -> Any:
        return self._client.request(method, {"source_id": 7, "config": self._config, **params})

    def browse(self, locator: str | None = None) -> ResourceListing:
        return cast(ResourceListing, self._call("resource.browse", {"locator": locator}))

    def scan(
        self,
        locator: str | None,
        known: Mapping[str, ResourceEntry] | None = None,
    ) -> list[ResourceEntry]:
        return list(self._call("resource.scan", {"locator": locator, "known": known}))

    def read(self, locator: str, max_bytes: int) -> bytes:
        return cast(
            bytes, self._call("resource.read", {"locator": locator, "max_bytes": max_bytes})
        )

    def resolve(
        self, locator: str, user_agent: str, throttle_key: str | None = None
    ) -> PlaybackTarget:
        return cast(
            PlaybackTarget,
            self._call(
                "resource.resolve",
                {"locator": locator, "user_agent": user_agent, "throttle_key": throttle_key},
            ),
        )

    def delete(self, locator: str) -> None:
        self._call("resource.delete", {"locator": locator})


class RpcProvider(ResourceProvider):
    def __init__(self) -> None:
        self._client = shared().client
        described = self._client.request("resource.describe", {})
        self.id = str(described["id"])
        self.multiple = bool(described["multiple"])
        self.capabilities = frozenset(str(item) for item in described["capabilities"])
        self.config_fields = tuple(
            item for item in described["config_fields"] if isinstance(item, ResourceConfigField)
        )

    def validate_config(self, config: Mapping[str, Any]) -> dict[str, Any]:
        return dict(self._client.request("resource.validate_config", {"config": config}))

    def check(self, config: Mapping[str, Any]) -> None:
        self._client.request("resource.check", {"source_id": 7, "config": config})

    def open(self, source_id: int, config: Mapping[str, Any]) -> ResourceSource:
        return RpcSource()

    def status(self, config: Mapping[str, Any]) -> SourceStatus:
        return cast(
            SourceStatus,
            self._client.request("resource.status", {"source_id": 7, "config": config}),
        )


class TestLocalSourceRpcContract(ResourceSourceContract):
    def make_source(self) -> ResourceSource:
        return RpcSource()

    def make_provider(self) -> ResourceProvider:
        return RpcProvider()

    def expected_paths(self) -> set[str]:
        return set(FILES)


def test_manifest_matches_runtime() -> None:
    from iris_plugin_sdk.manifest import load_manifest

    plugin_dir = Path(__file__).resolve().parents[1]
    manifest = load_manifest(plugin_dir)

    assert manifest.id == "source.local"
    assert manifest.provides == ("resource.source",)
    assert manifest.capabilities == ("logger",)


def test_initialize_requires_media_dir(tmp_path: Path) -> None:
    client = RpcClient(PLUGIN_ROOT, module="iris_plugin_source_local")
    try:
        with pytest.raises(ResourceUnavailable):
            client.request(
                "initialize",
                {
                    "sdk_version": "1.0.0",
                    "data_dir": str(tmp_path / "data"),
                    "proxy": None,
                    "host": {"version": "test", "url": "http://host.test"},
                },
            )
    finally:
        client.close()
