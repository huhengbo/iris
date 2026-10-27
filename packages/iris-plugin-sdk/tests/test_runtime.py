from __future__ import annotations

import shutil
import time
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from iris_plugin_sdk import (
    InvalidSourceConfig,
    PluginContext,
    PluginManifest,
    PluginResponse,
    PluginRuntime,
    ResourceNotFound,
    ResourceUnavailable,
)
from iris_plugin_sdk.testing import RpcClient


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    source = Path(__file__).resolve().parent / "fake_plugin.py"
    shutil.copy(source, plugin / "fake_plugin.py")
    ui = plugin / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<html></html>", encoding="utf-8")
    (plugin / "plugin.json").write_text(
        '{"id":"test.fake","name":"假资源插件","version":"1.0.0",'
        '"sdk":">=1.0,<2.0","entry":"fake_plugin",'
        '"provides":["resource.source","metadata.details","admin.ui"],'
        '"capabilities":["http","logger"],"ui":"ui",'
        '"pages":[{"id":"settings","title":"设置"}]}',
        encoding="utf-8",
    )
    return plugin


@pytest.fixture
def rpc(plugin_dir: Path) -> Iterator[RpcClient]:
    client = RpcClient(plugin_dir, module="fake_plugin")
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def host(rpc: RpcClient, tmp_path: Path) -> RpcClient:
    data_dir = tmp_path / "data"
    result = rpc.initialize(data_dir, proxy="http://proxy.test:8080")
    assert result["sdk_version"] == "1.0.0"
    assert result["manifest"]["id"] == "test.fake"
    assert (data_dir / "state").is_dir()
    return rpc


def test_initialize_runs_plugin_and_host_capabilities(host: RpcClient) -> None:
    assert any("fake started" in item["message"] for item in host.logs)


def test_initialize_exposes_media_dir(plugin_dir: Path, tmp_path: Path) -> None:
    client = RpcClient(plugin_dir, module="fake_plugin")
    media = tmp_path / "media"
    try:
        client.initialize(tmp_path / "data", media_dir=media)
        status = _admin(client, "status")
        assert status.body["media_dir"] == str(media)
    finally:
        client.close()


def test_resource_protocol_round_trip(host: RpcClient) -> None:
    described = host.request("resource.describe", {})
    assert described["id"] == "fake.source"
    assert described["capabilities"] == ["changes", "delete", "known_scan", "status"]

    assert host.request("resource.validate_config", {"config": {}}) == {}
    with pytest.raises(InvalidSourceConfig, match="配置不合法"):
        host.request("resource.validate_config", {"config": {"bad": True}})

    status = host.request("resource.status", {"source_id": 1, "config": {}})
    assert status.state == "ok" and status.page == "settings"

    listing = host.request("resource.browse", {"source_id": 1, "config": {}, "locator": None})
    assert listing.path == "/"
    assert listing.selectable is True
    assert {entry.relative_path for entry in listing.entries} == {"movie.mkv", "notes.nfo", "subs"}

    entries = host.request(
        "resource.scan", {"source_id": 1, "config": {}, "locator": None, "known": None}
    )
    assert {entry.relative_path for entry in entries} == set(FILES)

    assert host.request(
        "resource.read", {"source_id": 1, "config": {}, "locator": "notes.nfo", "max_bytes": 100}
    ).startswith(b"<movie")

    target = host.request(
        "resource.resolve",
        {"source_id": 1, "config": {}, "locator": "movie.mkv", "user_agent": "test"},
    )
    assert target.url == "https://cdn.test/movie.mkv"

    host.request("resource.delete", {"source_id": 1, "config": {}, "locator": "subs/movie.srt"})
    with pytest.raises(ResourceNotFound):
        host.request(
            "resource.read",
            {"source_id": 1, "config": {}, "locator": "subs/movie.srt", "max_bytes": 100},
        )

    # 同一个 source_id + config 复用同一个来源实例；source_id 是显式参数。
    assert _admin(host, "opens").body == {"opens": 1, "source_id": 1}
    host.request("resource.browse", {"source_id": 1, "config": {"changed": True}, "locator": None})
    assert _admin(host, "opens").body == {"opens": 2, "source_id": 1}
    host.request("resource.browse", {"source_id": 2, "config": {}, "locator": None})
    assert _admin(host, "opens").body == {"opens": 3, "source_id": 2}


def test_unknown_exception_is_unavailable(host: RpcClient) -> None:
    with pytest.raises(ResourceUnavailable) as error:
        host.request(
            "resource.read", {"source_id": 1, "config": {}, "locator": "boom", "max_bytes": 10}
        )

    assert "exploded" not in error.value.message
    # 调用方只看到通用说明，堆栈通过日志留给宿主排查
    logged = [item for item in host.logs if "resource.read" in item["message"]]
    assert logged and logged[-1]["level"] == "error"
    assert "Traceback" in logged[-1]["message"]
    assert "exploded" in logged[-1]["message"]


def test_metadata_priority_and_admin_round_trip(host: RpcClient) -> None:
    # 运行时在调用 provider 前设置 contextvar；假插件把 current_priority() 放进标题。
    background = host.request(
        "metadata.details",
        {"remote_id": "1", "kind": "movie", "priority": "background"},
    )
    assert background.title == "background"

    default = host.request("metadata.details", {"remote_id": "1", "kind": "movie"})
    assert default.title == "interactive"

    response = host.request(
        "admin.request",
        {
            "request": {
                "$type": "PluginRequest",
                "fields": {
                    "method": "GET",
                    "path": "status",
                    "query": {},
                    "body": None,
                    "user_id": 1,
                },
            }
        },
    )
    assert response.status == 200
    assert response.body["path"] == "status"
    assert response.body["method"] == "GET"
    assert response.body["proxy"] == "http://proxy.test:8080"
    assert response.body["media_dir"].endswith("/media")


def test_progress_and_config_changed(host: RpcClient) -> None:
    host.notify(
        "host.config_changed",
        {"proxy": "socks5://proxy.test:1080", "host_url": "https://new-host.test"},
    )

    def proxy() -> str | None:
        response = host.request(
            "admin.request",
            {
                "request": {
                    "$type": "PluginRequest",
                    "fields": {
                        "method": "GET",
                        "path": "proxy",
                        "query": {},
                        "body": None,
                        "user_id": 1,
                    },
                }
            },
        )
        return cast(str | None, response.body["proxy"])

    for _ in range(50):
        if proxy() == "socks5://proxy.test:1080":
            break
        time.sleep(0.02)
    assert proxy() == "socks5://proxy.test:1080"
    status = _admin(host, "status")
    assert status.body["host_url"] == "https://new-host.test"
    assert host.request("resource.describe", {})["id"] == "fake.source"


def test_flush_writes_state(host: RpcClient, tmp_path: Path) -> None:
    host.request("plugin.flush", {})
    assert (tmp_path / "data" / "state" / "flushed").read_text(encoding="utf-8") == "yes"


def _admin(host: RpcClient, path: str) -> PluginResponse:
    return cast(
        PluginResponse,
        host.request(
            "admin.request",
            {
                "request": {
                    "$type": "PluginRequest",
                    "fields": {
                        "method": "POST",
                        "path": path,
                        "query": {},
                        "body": None,
                        "user_id": 1,
                    },
                }
            },
        ),
    )


def test_notifications_reach_host(host: RpcClient) -> None:
    _admin(host, "changed")
    _admin(host, "progress")

    assert host.changes == [{"source_id": 7, "locators": ["12", "34"]}]
    assert host.progress == [{"reason": "rate_limited", "until": "2026-01-02T03:04:05+00:00"}]


def test_shutdown_flushes_and_exits(host: RpcClient) -> None:
    assert host.request("plugin.shutdown", {}) is None
    assert host.process.wait(timeout=5) is not None


FILES = {"movie.mkv", "notes.nfo", "subs/movie.srt"}


class _MinimalPlugin:
    manifest = PluginManifest(
        id="test.minimal",
        name="最小插件",
        version="1.0.0",
        provides=(),
        entry="minimal",
    )

    def start(self, context: PluginContext) -> None:
        return None

    def stop(self) -> None:
        return None

    def providers(self) -> Mapping[str, Any]:
        return {}


def test_progress_rejects_naive_time_and_unknown_reason(
    plugin_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(plugin_dir)
    runtime = PluginRuntime(_MinimalPlugin())

    with pytest.raises(ValueError, match="时区"):
        runtime._progress(datetime(2026, 1, 2, 3, 4, 5), "rate_limited")
    with pytest.raises(ValueError, match="等待原因"):
        runtime._progress(
            datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
            "unknown_reason",  # type: ignore[arg-type]
        )
