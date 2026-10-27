"""plugin.json 的读取和校验。

清单是插件唯一的静态声明：宿主按它发现插件、检查 SDK 兼容范围、启动入口和提供的能力。
插件自己也在 initialize 时把同一份清单回给宿主，两边使用完全相同的校验。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from iris_plugin_sdk.types import (
    PluginManifest,
    PluginManifestError,
    PluginPage,
    PluginSdkIncompatibleError,
    sdk_compatible,
)

MANIFEST_FILE = "plugin.json"
_KEYS = frozenset(
    {
        "id",
        "name",
        "version",
        "description",
        "sdk",
        "entry",
        "provides",
        "capabilities",
        "ui",
        "pages",
    }
)
_PAGE_KEYS = frozenset({"id", "title", "entry"})


def load_manifest(plugin_dir: Path) -> PluginManifest:
    """读取插件目录下的 plugin.json，并检查页面文件确实存在。"""
    path = plugin_dir / MANIFEST_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PluginManifestError(f"插件目录缺少 {MANIFEST_FILE}：{plugin_dir}") from None
    except json.JSONDecodeError as exc:
        raise PluginManifestError(f"{MANIFEST_FILE} 不是合法的 JSON：{exc}") from None
    manifest = manifest_from_dict(data)
    if manifest.ui is not None:
        ui_root = (plugin_dir / manifest.ui).resolve()
        if plugin_dir.resolve() not in ui_root.parents:
            raise PluginManifestError("UI 目录越出插件目录")
        if not ui_root.is_dir():
            raise PluginManifestError(f"UI 目录不存在：{manifest.ui}")
        for page in manifest.pages:
            if not (ui_root / page.entry).is_file():
                raise PluginManifestError(f"页面入口不存在：{page.entry}")
    return manifest


def manifest_from_dict(data: Any) -> PluginManifest:
    """校验 plugin.json 的对象结构。未知字段直接拒绝，避免拼写错误被静默忽略。"""
    if not isinstance(data, dict):
        raise PluginManifestError(f"{MANIFEST_FILE} 必须是一个 JSON 对象")
    unknown = set(data) - _KEYS
    if unknown:
        raise PluginManifestError(f"未知的 manifest 字段：{', '.join(sorted(unknown))}")
    for key in ("id", "name", "version", "sdk", "entry", "provides"):
        if key not in data:
            raise PluginManifestError(f"manifest 缺少字段：{key}")
    pages = _pages(data.get("pages", []))
    ui = data.get("ui")
    if ui is not None and not isinstance(ui, str):
        raise PluginManifestError("ui 必须是相对路径")
    if bool(pages) != (ui is not None):
        raise PluginManifestError("有页面时必须声明 ui 目录，反之亦然")
    manifest = PluginManifest(
        id=_text(data, "id"),
        name=_text(data, "name"),
        version=_text(data, "version"),
        provides=tuple(_strings(data, "provides")),
        capabilities=tuple(_strings(data, "capabilities", default=())),
        sdk=_text(data, "sdk"),
        entry=_text(data, "entry"),
        ui=ui,
        pages=pages,
        description=_optional_text(data, "description"),
    )
    # 清单里声明的范围必须覆盖当前 SDK，否则插件和运行时根本对不上。
    if not sdk_compatible(manifest.sdk):
        raise PluginSdkIncompatibleError(f"插件要求的 SDK 范围不包含当前版本：{manifest.sdk}")
    return manifest


def manifest_to_dict(manifest: PluginManifest) -> dict[str, Any]:
    """initialize 回复和宿主落库使用同一份结构；不包含 trust，信任级别由宿主决定。"""
    return {
        "id": manifest.id,
        "name": manifest.name,
        "version": manifest.version,
        "description": manifest.description,
        "sdk": manifest.sdk,
        "entry": manifest.entry,
        "provides": list(manifest.provides),
        "capabilities": list(manifest.capabilities),
        "ui": manifest.ui,
        "pages": [
            {"id": page.id, "title": page.title, "entry": page.entry} for page in manifest.pages
        ],
    }


def _pages(value: Any) -> tuple[PluginPage, ...]:
    if not isinstance(value, list):
        raise PluginManifestError("pages 必须是数组")
    pages: list[PluginPage] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise PluginManifestError("pages 的每一项必须是对象")
        unknown = set(item) - _PAGE_KEYS
        if unknown:
            raise PluginManifestError(f"未知的页面字段：{', '.join(sorted(unknown))}")
        for key in ("id", "title"):
            if key not in item:
                raise PluginManifestError(f"页面缺少字段：{key}")
        entry = item.get("entry", "index.html")
        if not isinstance(entry, str):
            raise PluginManifestError("页面入口必须是文本")
        pages.append(
            PluginPage(
                id=_item_text(item, "id"),
                title=_item_text(item, "title"),
                entry=entry,
            )
        )
    return tuple(pages)


def _text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise PluginManifestError(f"{key} 必须是非空文本")
    return value


def _optional_text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key, "")
    if not isinstance(value, str):
        raise PluginManifestError(f"{key} 必须是文本")
    return value


def _item_text(item: Mapping[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value:
        raise PluginManifestError(f"页面的 {key} 必须是非空文本")
    return value


def _strings(
    data: Mapping[str, Any], key: str, *, default: tuple[str, ...] = ()
) -> tuple[str, ...]:
    value = data.get(key, list(default))
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise PluginManifestError(f"{key} 必须是非空文本数组")
    return tuple(value)
