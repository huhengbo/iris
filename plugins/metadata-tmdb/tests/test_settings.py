"""TMDB 设置页、state 文件格式和测试连接。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, cast

import pytest
from iris_plugin_sdk import (
    DirectHttpClient,
    HostInfo,
    PluginContext,
    PluginRequest,
    PluginRequestError,
)
from iris_plugin_sdk.testing import RecordingNotifier

from iris_plugin_tmdb.errors import TmdbUnavailable
from iris_plugin_tmdb.plugin import TmdbPlugin
from iris_plugin_tmdb.provider import TmdbMetadataProvider
from tests.fake_gateway import VALID_KEY, FakeTmdb

PROXY = "http://user:secret-pass@127.0.0.1:7890"


def make_context(tmp_path: Path, *, proxy: str | None = PROXY) -> PluginContext:
    return PluginContext(
        plugin_id="builtin.tmdb",
        logger=logging.getLogger("test.tmdb"),
        data_dir=tmp_path,
        host=HostInfo(version="test"),
        media_dir=tmp_path / "media",
        notify=RecordingNotifier(),
        proxy=proxy,
    )


def make_plugin(
    tmp_path: Path, gateway: FakeTmdb | None = None, *, proxy: str | None = PROXY
) -> tuple[TmdbPlugin, FakeTmdb, Any]:
    fake = gateway or FakeTmdb()
    plugin = TmdbPlugin(fake)
    plugin.start(make_context(tmp_path, proxy=proxy))
    return plugin, fake, cast(Any, plugin.providers()["admin.ui"])


def request(method: str, path: str = "settings", body: Any = None) -> PluginRequest:
    return PluginRequest(method=method, path=path, query={}, body=body, user_id=1)


def test_settings_page_hides_key_and_declares_field(tmp_path: Path) -> None:
    _, _, ui = make_plugin(tmp_path)

    response = ui.handle(request("GET"))
    body = cast(dict[str, Any], response.body)
    fields = {field["name"]: field for field in body["fields"]}

    assert fields["api_key"]["kind"] == "secret"
    assert fields["api_key"]["required"] is False
    assert body["values"]["api_key"] == {"set": False}
    assert body["testable"] is True


def test_saving_key_writes_state_and_makes_plugin_configured(tmp_path: Path) -> None:
    _, fake, ui = make_plugin(tmp_path)

    response = ui.handle(request("PUT", body={"api_key": VALID_KEY}))

    assert response.status == 200
    assert json.loads((tmp_path / "state" / "settings.json").read_text()) == {"api_key": VALID_KEY}
    status = cast(dict[str, Any], ui.handle(request("GET", "status")).body)
    assert status == {"api_key_set": True, "validation": None}
    assert fake.accesses == []


def test_requests_update_validation_and_page_shows_it(tmp_path: Path) -> None:
    plugin, fake, ui = make_plugin(tmp_path)
    fake.add_movie(157336, "星际穿越", 2014)
    ui.handle(request("PUT", body={"api_key": VALID_KEY}))

    provider = cast(TmdbMetadataProvider, plugin.providers()["metadata.details"])
    provider.details("157336", kind="movie")

    status = cast(dict[str, Any], ui.handle(request("GET", "status")).body)
    assert status["validation"]["status"] == "ok"
    assert status["validation"]["checked_at"]
    assert status["validation"]["message"] == "连接正常"
    validation = json.loads((tmp_path / "state" / "validation.json").read_text())
    assert validation["status"] == "ok"
    assert validation["checked_at"]


def test_changing_key_clears_previous_validation(tmp_path: Path) -> None:
    plugin, fake, ui = make_plugin(tmp_path)
    fake.add_movie(157336, "星际穿越", 2014)
    ui.handle(request("PUT", body={"api_key": VALID_KEY}))
    provider = cast(TmdbMetadataProvider, plugin.providers()["metadata.details"])
    provider.details("157336", kind="movie")
    assert cast(dict[str, Any], ui.handle(request("GET", "status")).body)["validation"]

    ui.handle(request("PUT", body={"api_key": "replacement-key"}))

    status = cast(dict[str, Any], ui.handle(request("GET", "status")).body)
    assert status == {"api_key_set": True, "validation": None}


def test_clearing_key_removes_it_from_state(tmp_path: Path) -> None:
    _, _, ui = make_plugin(tmp_path)
    ui.handle(request("PUT", body={"api_key": VALID_KEY}))

    ui.handle(request("PUT", body={"api_key": None}))

    status = cast(dict[str, Any], ui.handle(request("GET", "status")).body)
    assert status == {"api_key_set": False, "validation": None}
    assert json.loads((tmp_path / "state" / "settings.json").read_text()) == {}


def test_test_connection_uses_unsaved_key(tmp_path: Path) -> None:
    _, fake, ui = make_plugin(tmp_path)
    fake.valid_keys.add("unsaved-key")

    response = ui.handle(request("POST", "settings/test", {"api_key": "unsaved-key"}))

    assert response.body == {"ok": True}
    assert fake.accesses[-1].key == "unsaved-key"
    # 测试连接不落盘
    assert not (tmp_path / "state" / "settings.json").exists()


def test_test_connection_reports_rejected_key(tmp_path: Path) -> None:
    _, fake, ui = make_plugin(tmp_path)
    fake.valid_keys.clear()

    response = ui.handle(request("POST", "settings/test", {"api_key": VALID_KEY}))

    body = cast(dict[str, Any], response.body)
    assert body["ok"] is False
    assert "无效或已被吊销" in body["message"]
    assert VALID_KEY not in str(body)


def test_test_connection_requires_key(tmp_path: Path) -> None:
    _, _, ui = make_plugin(tmp_path)

    response = ui.handle(request("POST", "settings/test", {}))

    assert response.body == {"ok": False, "message": "请填写 API Key"}


def test_unreachable_test_connection_returns_message(tmp_path: Path) -> None:
    _, fake, ui = make_plugin(tmp_path)
    fake.error = TmdbUnavailable("连不上 TMDB")

    response = ui.handle(request("POST", "settings/test", {"api_key": VALID_KEY}))

    assert response.body == {"ok": False, "message": "连不上 TMDB"}


def test_unknown_page_request_is_not_found(tmp_path: Path) -> None:
    _, _, ui = make_plugin(tmp_path)

    with pytest.raises(PluginRequestError):
        ui.handle(request("GET", "other"))


def test_config_changed_reaches_direct_http_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changes: list[str | None] = []

    def record(self: DirectHttpClient, *, proxy: str | None) -> None:
        changes.append(proxy)

    monkeypatch.setattr(DirectHttpClient, "config_changed", record)
    plugin = TmdbPlugin()
    plugin.start(make_context(tmp_path))
    try:
        plugin.config_changed(proxy="http://127.0.0.1:1080", host_url="http://host.test")
    finally:
        plugin.stop()

    assert changes == ["http://127.0.0.1:1080"]


def test_start_uses_host_proxy(tmp_path: Path) -> None:
    plugin = TmdbPlugin()
    plugin.start(make_context(tmp_path))
    try:
        assert plugin._http is not None
        assert plugin._http._proxy == PROXY
    finally:
        plugin.stop()
