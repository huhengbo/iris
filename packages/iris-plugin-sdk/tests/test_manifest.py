from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from iris_plugin_sdk import (
    PluginManifestError,
    load_manifest,
    manifest_to_dict,
    sdk_compatible,
)

BASE = {
    "id": "org.example.fake",
    "name": "假插件",
    "version": "1.0.0",
    "sdk": ">=1.0,<2.0",
    "entry": "fake_plugin",
    "provides": ["resource.source"],
}


def _write(tmp_path: Path, data: dict[str, Any], ui: bool = True) -> Path:
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "plugin.json").write_text(json.dumps(data), encoding="utf-8")
    if ui:
        (plugin / "ui").mkdir()
        (plugin / "ui" / "index.html").write_text("ok", encoding="utf-8")
    return plugin


def test_load_minimal_manifest(tmp_path: Path) -> None:
    plugin = _write(tmp_path, BASE)

    manifest = load_manifest(plugin)

    assert manifest.id == "org.example.fake"
    assert manifest.entry == "fake_plugin"
    assert manifest.capabilities == ()


def test_load_manifest_with_pages(tmp_path: Path) -> None:
    data = {
        **BASE,
        "provides": ["resource.source", "admin.ui"],
        "capabilities": ["logger", "http"],
        "ui": "ui",
        "pages": [{"id": "settings", "title": "设置"}],
    }
    plugin = _write(tmp_path, data, ui=True)

    manifest = load_manifest(plugin)

    assert manifest.ui == "ui"
    assert manifest.pages[0].entry == "index.html"
    assert manifest_to_dict(manifest)["pages"][0]["id"] == "settings"


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        ({**BASE, "extra": 1}, "未知的 manifest 字段"),
        ({k: v for k, v in BASE.items() if k != "entry"}, "缺少字段"),
        ({**BASE, "id": "Bad ID"}, "插件 id"),
        ({**BASE, "sdk": "1.0"}, "SDK 兼容范围"),
        ({**BASE, "sdk": ">=2.0,<3.0"}, "不包含当前版本"),
        ({**BASE, "provides": ["unknown.capability"]}, "未知的插件能力"),
        ({**BASE, "capabilities": ["secrets"]}, "未知的宿主能力"),
        (
            {**BASE, "provides": ["resource.source", "admin.ui"], "pages": []},
            "admin.ui",
        ),
        ({**BASE, "ui": "ui"}, "ui 目录"),
        (
            {
                **BASE,
                "provides": ["resource.source", "admin.ui"],
                "ui": "ui",
                "pages": [{"id": "settings", "title": "设置", "entry": "missing.html"}],
            },
            "页面入口不存在",
        ),
    ],
)
def test_manifest_rejects_invalid_declarations(
    tmp_path: Path, data: dict[str, Any], reason: str
) -> None:
    plugin = _write(tmp_path, data)

    with pytest.raises(PluginManifestError, match=reason):
        load_manifest(plugin)


def test_sdk_range_supports_comparisons() -> None:
    assert sdk_compatible(">=1.0,<2.0", "1.0.0")
    assert sdk_compatible(">=1.0,<2.0", "1.9.9")
    assert not sdk_compatible(">=1.0,<2.0", "2.0.0")
    assert sdk_compatible("==1.0.0", "1.0")
    assert not sdk_compatible(">1.0", "1.0.0")
    with pytest.raises(PluginManifestError):
        sdk_compatible("~=1.0")
