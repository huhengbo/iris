"""管理页面：manifest、静态文件、接口路由和插件生命周期。"""

from __future__ import annotations

from pathlib import Path

import pytest
from iris_plugin_sdk import PluginRequestError, load_manifest
from iris_plugin_sdk.testing import RpcClient

from tests.conftest import Harness, save_cookie, update_settings

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def test_manifest_declares_the_source_and_its_pages() -> None:
    manifest = load_manifest(PLUGIN_ROOT)

    assert manifest.id == "source.115"
    assert manifest.provides == ("resource.source", "admin.ui")
    # 插件直接用 p115client 联网，不需要宿主的 http 能力
    assert manifest.capabilities == ("logger",)
    assert manifest.ui == "ui"
    assert [(page.id, page.title, page.entry) for page in manifest.pages] == [
        ("account", "账号", "index.html"),
        ("settings", "设置", "settings.html"),
    ]
    assert manifest.entry == "iris_source_115"


def test_ui_files_use_the_host_plugin_sdk_instead_of_copying_it() -> None:
    ui = PLUGIN_ROOT / "ui"
    html = {path.name: path.read_text(encoding="utf-8") for path in ui.glob("*.html")}

    assert set(html) == {"index.html", "settings.html"}
    for name, text in html.items():
        assert "/plugins/_sdk/iris-plugin.js" in text, name
        assert "/plugins/_sdk/iris-plugin.css" in text, name
        assert 'src="iris-plugin.js"' not in text, name
    assert not (ui / "iris-plugin.js").exists()
    assert not (ui / "iris-plugin.css").exists()
    assert "生活事件" in html["settings.html"]
    assert "扫码登录" in html["index.html"]


def test_unknown_paths_and_methods_are_rejected(harness: Harness) -> None:
    with pytest.raises(PluginRequestError) as missing:
        harness.request("GET", "nope")
    assert missing.value.status == 404
    assert missing.value.code == "not_found"

    with pytest.raises(PluginRequestError) as method:
        harness.request("POST", "status")
    assert method.value.status == 405
    assert method.value.code == "method_not_allowed"

    with pytest.raises(PluginRequestError) as settings_method:
        harness.request("DELETE", "settings")
    assert settings_method.value.status == 405

    with pytest.raises(PluginRequestError) as empty_session:
        harness.request("GET", "qr-login/")
    assert empty_session.value.status == 405
    with pytest.raises(PluginRequestError) as missing_session:
        harness.request("GET", "qr-login/unknown")
    assert missing_session.value.status == 404
    assert missing_session.value.code == "not_found"

    with pytest.raises(PluginRequestError) as body:
        harness.request("PUT", "cookie", ["not", "an", "object"])
    assert body.value.status == 400
    assert body.value.code == "invalid_request"

    with pytest.raises(PluginRequestError) as no_cookie:
        harness.request("PUT", "cookie", {})
    assert no_cookie.value.status == 400
    assert no_cookie.value.code == "invalid_request"


def test_same_playback_setting_is_wired_to_the_provider(
    harness: Harness, deferred: list[tuple[float, object]]
) -> None:
    save_cookie(harness)
    movie = harness.gateway.make_path("影视/电影/Film (2024)/Film.mkv")
    body = update_settings(harness, {"same_playback": True}).body
    assert body["values"]["same_playback"] is True
    source = harness.provider.open(1, {})

    first = source.resolve(f"pc{movie}", "UA", throttle_key="token-a")
    second = source.resolve(f"pc{movie}", "UA", throttle_key="token-b")

    assert second.url != first.url
    assert harness.gateway.calls.count("copy_file") == 1

    body = update_settings(harness, {"same_playback": False}).body
    assert body["values"]["same_playback"] is False


def test_proxy_changes_are_forwarded_to_the_gateway(harness: Harness) -> None:
    harness.plugin.config_changed(proxy="socks5://127.0.0.1:1080", host_url=None)

    assert harness.gateway.proxies[-1] == "socks5://127.0.0.1:1080"


def test_plugin_lifecycle_and_provider_registration(harness: Harness) -> None:
    providers = harness.plugin.providers()

    assert set(providers) == {"resource.source", "admin.ui"}
    assert providers["resource.source"] is harness.provider
    assert providers["admin.ui"] is harness.admin
    assert harness.admin.ui_root == PLUGIN_ROOT / "ui"

    harness.plugin.flush()
    assert (harness.state_dir / "life.json").is_file()

    harness.plugin.stop()
    assert harness.plugin.providers() == {}


def test_settings_survive_restart(harness: Harness) -> None:
    from tests.conftest import restart

    assert update_settings(harness, {"life_interval": 45}).status == 200
    fresh = restart(harness)
    try:
        body = fresh.request("GET", "settings").body
        assert body["values"]["life_interval"] == 45
    finally:
        fresh.plugin.stop()


def test_real_entry_module_initializes(tmp_path: Path) -> None:
    """宿主实际执行的是 python -m iris_source_115，不只是测试驱动。"""
    client = RpcClient(PLUGIN_ROOT, module="iris_source_115")
    try:
        result = client.initialize(tmp_path / "data")
        assert result["manifest"]["id"] == "source.115"
        status = client.request("resource.status", {"source_id": 1, "config": {}})
        assert status.state == "failed"
        assert status.page == "account"
    finally:
        client.close()
