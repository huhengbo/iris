"""宿主测试的插件安装和 RPC 控制。

每个测试用自己的临时插件根目录，安装 ``tests/fixtures/fake_plugin`` 的副本，并把解释器
指向当前测试环境。插件在真实子进程里运行，测试通过 ``test.control`` RPC 操纵状态。
"""

from __future__ import annotations

import json
import shutil
import sys
import sysconfig
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import iris_plugin_sdk

FIXTURES = Path(__file__).with_name("fixtures")
FAKE_PLUGIN = FIXTURES / "fake_plugin"
REPO_ROOT = Path(__file__).parents[2]
REAL_PLUGINS = REPO_ROOT / "plugins"


class PluginControl:
    """一个插件进程的控制通道；App 启动后由 client fixture 绑定。"""

    def __init__(self, plugin_root: Path, plugin_id: str) -> None:
        self.plugin_root = plugin_root
        self.plugin_id = plugin_id
        self._supervisor: Any = None

    def bind(self, client: Any) -> None:
        self._supervisor = client.app.state.container.supervisor

    def call(self, domain: str, op: str, params: dict[str, Any] | None = None) -> Any:
        if self._supervisor is None:
            raise RuntimeError(f"插件 {self.plugin_id} 的控制通道还没有绑定到测试客户端")
        client = self._supervisor.client(self.plugin_id)
        return client.call(
            "test.control",
            {"domain": domain, "op": op, "params": params or {}},
            timeout=30.0,
        )


_CONTROLS: dict[tuple[Path, str], PluginControl] = {}


def control_for(plugin_root: Path, plugin_id: str) -> PluginControl:
    key = (plugin_root.resolve(), plugin_id)
    control = _CONTROLS.get(key)
    if control is None:
        control = PluginControl(plugin_root, plugin_id)
        _CONTROLS[key] = control
    return control


def bind_controls(plugin_root: Path, client: Any) -> None:
    root = plugin_root.resolve()
    for (registered, _), control in _CONTROLS.items():
        if registered == root:
            control.bind(client)


@dataclass(frozen=True)
class FakePluginSpec:
    plugin_id: str
    name: str
    provides: tuple[str, ...]
    version: str = "1.0.0"
    description: str = ""
    capabilities: tuple[str, ...] = ("logger",)
    pages: tuple[dict[str, str], ...] = ()
    ui: str | None = None
    mode: str = ""


def install_venv(plugin_root: Path, extra_paths: tuple[Path, ...] = ()) -> None:
    """用当前测试解释器充当插件虚拟环境，并通过 .pth 暴露 SDK 和额外源码路径。"""
    venv = plugin_root / ".venv"
    binary = venv / "bin"
    binary.mkdir(parents=True)
    (binary / "python").symlink_to(sys.executable)
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
    # SDK 的运行依赖装在宿主测试环境里；真实部署时由插件自己的 venv 安装。
    (site_packages / "iris-host-deps.pth").write_text(str(sysconfig.get_path("purelib")))
    for path in extra_paths:
        (site_packages / f"iris-test-{path.name}.pth").write_text(str(path), encoding="utf-8")


def install_fake_plugin(root: Path, spec: FakePluginSpec) -> PluginControl:
    target = root / spec.plugin_id
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(FAKE_PLUGIN, target, ignore=shutil.ignore_patterns(".venv", "__pycache__"))
    manifest_path = target / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "id": spec.plugin_id,
            "name": spec.name,
            "version": spec.version,
            "description": spec.description,
            "provides": list(spec.provides),
            "capabilities": list(spec.capabilities),
            "pages": list(spec.pages),
            "ui": spec.ui,
        }
    )
    if not spec.pages:
        manifest.pop("ui", None)
        manifest.pop("pages", None)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (target / "mode.txt").write_text(spec.mode, encoding="utf-8")
    install_venv(target)
    return control_for(root, spec.plugin_id)


def install_real_plugin(
    root: Path, source: Path, *, plugin_id: str | None = None, name: str | None = None
) -> None:
    """安装仓库里的真实插件包（目前只用于没有三方依赖的 source-local）。"""
    target = root / (plugin_id or source.name)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", "tests", "uv.lock"),
    )
    manifest_path = target / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if plugin_id is not None:
        manifest["id"] = plugin_id
    if name is not None:
        manifest["name"] = name
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    install_venv(target, extra_paths=(target / "src",))


BUILTIN_SPECS: dict[str, FakePluginSpec] = {
    "source.115": FakePluginSpec(
        "source.115",
        "115 网盘",
        ("resource.source", "admin.ui"),
        version="1.2.0",
        description="浏览、播放和同步 115 网盘中的文件。",
        pages=(
            {"id": "account", "title": "账号", "entry": "index.html"},
            {"id": "settings", "title": "设置", "entry": "settings.html"},
        ),
        ui="ui",
    ),
    "builtin.tmdb": FakePluginSpec(
        "builtin.tmdb",
        "TMDB 元数据",
        (
            "metadata.search",
            "metadata.details",
            "metadata.images",
            "metadata.people",
            "image.fetch",
            "admin.ui",
        ),
        version="5",
        description="从 TMDB 获取影片资料、演职员和图片。",
        pages=({"id": "settings", "title": "设置", "entry": "index.html"},),
        ui="ui",
    ),
    "builtin.douban": FakePluginSpec(
        "builtin.douban",
        "豆瓣",
        ("metadata.details", "metadata.images", "admin.ui"),
        version="1",
        description="从豆瓣补充中文片名、简介、评分和海报。",
        pages=({"id": "settings", "title": "设置", "entry": "index.html"},),
        ui="ui",
    ),
    "builtin.fanart": FakePluginSpec(
        "builtin.fanart",
        "Fanart.tv",
        ("image.fetch", "admin.ui"),
        version="1",
        description="从 Fanart.tv 获取高清海报、背景图、片名图和 Logo。",
        pages=({"id": "settings", "title": "设置", "entry": "index.html"},),
        ui="ui",
    ),
    "builtin.assrt": FakePluginSpec(
        "builtin.assrt",
        "射手网 assrt",
        ("subtitle.search", "subtitle.download", "admin.ui"),
        version="1",
        description="从射手网搜索和下载在线字幕。",
        pages=({"id": "settings", "title": "设置", "entry": "index.html"},),
        ui="ui",
    ),
}


def install_builtin(plugins_dir: Path, plugin_id: str) -> PluginControl:
    return install_fake_plugin(plugins_dir, BUILTIN_SPECS[plugin_id])


def install_source_local(plugins_dir: Path) -> None:
    install_real_plugin(plugins_dir, REAL_PLUGINS / "source-local")


@dataclass
class PluginEnvironment:
    root: Path
    controls: dict[str, PluginControl] = field(default_factory=dict)

    def install(self, spec: FakePluginSpec) -> PluginControl:
        control = install_fake_plugin(self.root, spec)
        self.controls[spec.plugin_id] = control
        return control

    def control(self, plugin_id: str) -> PluginControl:
        control = self.controls.get(plugin_id)
        if control is None:
            control = control_for(self.root, plugin_id)
            self.controls[plugin_id] = control
        return control
