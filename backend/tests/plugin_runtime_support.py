"""进程外运行时测试的插件安装和等待工具。"""

from __future__ import annotations

import json
import shutil
import sys
import sysconfig
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import iris_plugin_sdk

from app.plugins.supervisor import PluginState, PluginSupervisor

FIXTURE = Path(__file__).with_name("fixtures") / "fake_plugin"
# 测试方法不在协议的超时表里，由测试显式给出；这样也能验证按方法超时。
_TEST_TIMEOUTS = {
    "test.echo": 5.0,
    "test.sleep": 5.0,
    "test.raise": 5.0,
    "test.context": 5.0,
    "test.config_changes": 5.0,
    "test.changed": 5.0,
    "test.http": 5.0,
    "test.progress": 5.0,
    "test.progress_request": 5.0,
    "test.host_rejected": 5.0,
    "test.control": 5.0,
    "test.log": 5.0,
    "test.std_log": 5.0,
    "test.initialize_params": 5.0,
    "test.last_priority": 5.0,
    "test.crash": 5.0,
    "test.standard_error": 5.0,
}


def install_plugin(
    plugins_dir: Path,
    plugin_id: str = "org.example.fake",
    *,
    mode: str = "",
    sdk: str | None = None,
) -> Path:
    """把假插件复制成独立插件目录，并用指向当前解释器的符号链接充当它的 venv。"""
    target = plugins_dir / plugin_id
    shutil.copytree(FIXTURE, target)
    manifest_path = target / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["id"] = plugin_id
    manifest["name"] = f"测试假插件 {plugin_id}"
    if sdk is not None:
        manifest["sdk"] = sdk
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (target / "mode.txt").write_text(mode, encoding="utf-8")
    venv = target / ".venv"
    binary = venv / "bin"
    binary.mkdir(parents=True)
    (binary / "python").symlink_to(sys.executable)
    # 符号链接的解释器需要一个 pyvenv.cfg 才会把插件目录当成虚拟环境；SDK 通过 .pth
    # 指向当前测试环境里的那份，插件代码本身仍然只写 iris_plugin_sdk。
    (venv / "pyvenv.cfg").write_text(
        f"home = {Path(sys.base_prefix) / 'bin'}\n"
        "include-system-site-packages = false\n"
        f"version_info = {sys.version_info.major}.{sys.version_info.minor}\n",
        encoding="utf-8",
    )
    site_packages = (
        venv / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    )
    site_packages.mkdir(parents=True)
    (site_packages / "iris-plugin-sdk.pth").write_text(
        str(Path(iris_plugin_sdk.__file__).resolve().parent.parent), encoding="utf-8"
    )
    # SDK 的运行依赖（httpx2 等）装在宿主测试环境里；插件 venv 是符号链接出来的，
    # 再把宿主 site-packages 加进来，等价于真实部署时插件自己安装依赖。
    (site_packages / "iris-host-deps.pth").write_text(
        sysconfig.get_path("purelib"), encoding="utf-8"
    )
    return target


@dataclass
class Harness:
    supervisor: PluginSupervisor
    plugins_dir: Path
    data_dir: Path
    proxy: dict[str, str | None] = field(default_factory=lambda: {"value": None})
    changed: list[tuple[str, int, list[str]]] = field(default_factory=list)

    def client(self, plugin_id: str = "org.example.fake") -> Any:
        return self.supervisor.client(plugin_id)

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        plugin_id: str = "org.example.fake",
        timeout: float | None = None,
    ) -> Any:
        return self.client(plugin_id).call(method, params or {}, timeout=timeout)


def make_harness(
    tmp_path: Path,
    *,
    modes: dict[str, str] | None = None,
    sdk: str | None = None,
    timeouts: dict[str, float] | None = None,
    max_failures: int = 5,
    http_transport: Any = None,
    media_dir: Path | None = None,
) -> Harness:
    plugins_dir = tmp_path / "plugins"
    data_dir = tmp_path / "data"
    media_dir = media_dir or tmp_path / "media"
    plugins_dir.mkdir()
    proxy: dict[str, str | None] = {"value": None}
    changed: list[tuple[str, int, list[str]]] = []
    for plugin_id, mode in (modes or {"org.example.fake": ""}).items():
        install_plugin(plugins_dir, plugin_id, mode=mode, sdk=sdk)
    supervisor = PluginSupervisor(
        plugins_dir,
        data_dir,
        host_url=lambda: "http://127.0.0.1:8080",
        media_dir=media_dir,
        proxy=lambda plugin_id: proxy["value"],
        on_resource_changed=lambda plugin_id, source_id, locators: changed.append(
            (plugin_id, source_id, locators)
        ),
        http_transport=http_transport,
        timeouts={**_TEST_TIMEOUTS, **(timeouts or {})},
        backoff_initial=0.01,
        backoff_max=0.05,
        max_failures=max_failures,
        stable_runtime=60.0,
    )
    supervisor.discover()
    return Harness(
        supervisor=supervisor,
        plugins_dir=plugins_dir,
        data_dir=data_dir,
        proxy=proxy,
        changed=changed,
    )


def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 10.0,
    interval: float = 0.02,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError("等待条件超时")


def wait_state(
    supervisor: PluginSupervisor,
    plugin_id: str,
    state: PluginState,
    *,
    timeout: float = 10.0,
) -> None:
    wait_until(lambda: supervisor.state(plugin_id) == state, timeout=timeout)
