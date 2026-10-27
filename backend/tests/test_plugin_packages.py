"""真实插件包的可选集成测试：没有同步过插件环境时跳过。

Docker 构建会对 plugins/* 逐个 uv sync；本地执行 scripts/sync-plugins.sh 后，这里用宿主
supervisor 真实拉起每个包并完成 initialize 握手。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.plugins.supervisor import PluginSupervisor

PLUGIN_ROOT = Path(__file__).parents[2] / "plugins"


def _plugin_ids() -> list[str]:
    ids: list[str] = []
    if not PLUGIN_ROOT.is_dir():
        return ids
    for directory in sorted(PLUGIN_ROOT.iterdir()):
        if not (directory / "plugin.json").is_file():
            continue
        if not (directory / ".venv" / "bin" / "python").is_file():
            continue
        manifest = json.loads((directory / "plugin.json").read_text(encoding="utf-8"))
        ids.append(manifest["id"])
    return ids


def test_real_plugin_packages_initialize(tmp_path: Path) -> None:
    plugin_ids = _plugin_ids()
    if not plugin_ids:
        pytest.skip("plugins/* 还没有同步虚拟环境，先执行 scripts/sync-plugins.sh")

    supervisor = PluginSupervisor(
        PLUGIN_ROOT,
        tmp_path / "data",
        media_dir=tmp_path / "media",
        host_url=lambda: "http://127.0.0.1:8080",
    )
    supervisor.discover()
    try:
        for plugin_id in plugin_ids:
            try:
                supervisor.start(plugin_id)
            except Exception as exc:
                pytest.fail(f"插件 {plugin_id} 初始化失败：{type(exc).__name__}: {exc}")
            assert supervisor.is_running(plugin_id), plugin_id
            assert (tmp_path / "data" / "plugins" / plugin_id / "state").is_dir(), plugin_id
    finally:
        supervisor.shutdown()
