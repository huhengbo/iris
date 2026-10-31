"""插件仓库 index.json 的解析和校验（schema 1）。

解析器是纯函数：不访问网络，不读写数据库。条目逐条校验，不合格的计入 invalid_count
并跳过；未知字段一律忽略，保证仓库可以向后兼容地增加字段。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from iris_plugin_sdk import (
    SDK_VERSION,
    PluginManifest,
    PluginManifestError,
    sdk_compatible,
)

from app.plugins.fetch import FetchError, resolve_url

INDEX_SCHEMA = 1
MAX_INDEX_BYTES = 2 * 1024 * 1024
MAX_INDEX_ENTRIES = 1000
MAX_PACKAGE_BYTES = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 5000
# 与备份包、SDK 的插件 id 规则一致；builtin. 前缀保留给系统插件。
PLUGIN_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
# 每段最多 9 位 ASCII 数字：超长数字会让 int() 抛 ValueError，全角数字也不是合法版本
VERSION = re.compile(r"^\d{1,9}(\.\d{1,9}){0,3}$", re.ASCII)
SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
BUILTIN_PREFIX = "builtin."


class IndexError(ValueError):
    """index.json 整体不可用。"""


class IndexInvalid(IndexError):
    """index.json 结构不合法。"""


class IndexUnsupported(IndexError):
    """index.json 的 schema 版本不受支持。"""

    def __init__(self, schema: int) -> None:
        super().__init__(f"不支持的插件仓库 schema：{schema}")
        self.schema = schema


class _InvalidEntry(ValueError):
    pass


@dataclass(frozen=True)
class IndexEntry:
    id: str
    name: str
    description: str
    version: str
    sdk: str
    provides: tuple[str, ...]
    capabilities: tuple[str, ...]
    url: str
    sha256: str
    size: int
    homepage: str | None = None
    signature: Any | None = None


@dataclass(frozen=True)
class ParsedIndex:
    schema: int
    name: str
    generated_at: str | None
    entries: tuple[IndexEntry, ...]
    invalid_count: int


def parse_index(data: Any, base_url: str) -> ParsedIndex:
    """解析仓库返回的 JSON 对象。schema 不支持时抛 IndexUnsupported。"""
    if not isinstance(data, dict):
        raise IndexInvalid("index.json 必须是一个对象")
    raw_schema = data.get("schema")
    if not isinstance(raw_schema, int) or isinstance(raw_schema, bool):
        raise IndexInvalid("index.json 缺少整数 schema")
    if raw_schema != INDEX_SCHEMA:
        raise IndexUnsupported(raw_schema)
    raw_plugins = data.get("plugins")
    if not isinstance(raw_plugins, list):
        raise IndexInvalid("index.json 的 plugins 必须是数组")
    if len(raw_plugins) > MAX_INDEX_ENTRIES:
        raise IndexInvalid(f"index.json 的条目超过 {MAX_INDEX_ENTRIES} 个")
    raw_name = data.get("name")
    name: str = raw_name if isinstance(raw_name, str) else ""
    generated_at = data.get("generated_at")
    entries: list[IndexEntry] = []
    invalid_count = 0
    seen: set[tuple[str, str]] = set()
    for raw in raw_plugins:
        try:
            entry = _parse_entry(raw, base_url)
        except (_InvalidEntry, FetchError, PluginManifestError):
            invalid_count += 1
            continue
        key = (entry.id, entry.version)
        if key in seen:
            invalid_count += 1
            continue
        seen.add(key)
        entries.append(entry)
    entries.sort(key=lambda item: (item.id, _version_tuple(item.version)))
    return ParsedIndex(
        schema=raw_schema,
        name=name,
        generated_at=generated_at if isinstance(generated_at, str) else None,
        entries=tuple(entries),
        invalid_count=invalid_count,
    )


def parse_stored(payload: Any) -> ParsedIndex | None:
    """读取数据库里缓存的规范化索引；内容损坏时返回 None，调用方按缓存不可用处理。"""
    if not isinstance(payload, dict) or payload.get("schema") != INDEX_SCHEMA:
        return None
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        return None
    entries: list[IndexEntry] = []
    try:
        for raw in raw_entries:
            entry = _entry_from_dict(raw)
            if VERSION.fullmatch(entry.version) is None:
                raise ValueError("缓存里的版本号不合法")
            entries.append(entry)
    except (KeyError, TypeError, ValueError):
        return None
    invalid_count = payload.get("invalid_count")
    raw_name = payload.get("name")
    name: str = raw_name if isinstance(raw_name, str) else ""
    raw_generated_at = payload.get("generated_at")
    return ParsedIndex(
        schema=INDEX_SCHEMA,
        name=name,
        generated_at=raw_generated_at if isinstance(raw_generated_at, str) else None,
        entries=tuple(entries),
        invalid_count=invalid_count if isinstance(invalid_count, int) else 0,
    )


def dump_index(index: ParsedIndex) -> str:
    """存进 plugin_repository.index_json 的规范化结构。"""
    return json.dumps(
        {
            "schema": index.schema,
            "name": index.name,
            "generated_at": index.generated_at,
            "invalid_count": index.invalid_count,
            "entries": [_entry_to_dict(entry) for entry in index.entries],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def load_dumped_index(raw: str | None) -> ParsedIndex | None:
    if not raw:
        return None
    try:
        return parse_stored(json.loads(raw))
    except ValueError:
        return None


def compare_versions(left: str, right: str) -> int:
    """点分数字版本比较；无效版本抛 ValueError。"""
    a = _version_tuple(left)
    b = _version_tuple(right)
    length = max(len(a), len(b))
    padded_a = a + (0,) * (length - len(a))
    padded_b = b + (0,) * (length - len(b))
    return (padded_a > padded_b) - (padded_a < padded_b)


def is_compatible(entry: IndexEntry, sdk_version: str = SDK_VERSION) -> bool:
    try:
        return sdk_compatible(entry.sdk, sdk_version)
    except PluginManifestError:
        return False


def best_compatible(entries: tuple[IndexEntry, ...]) -> IndexEntry | None:
    compatible = [entry for entry in entries if is_compatible(entry)]
    if not compatible:
        return None
    return max(compatible, key=lambda entry: _version_tuple(entry.version))


def best_entry(entries: tuple[IndexEntry, ...]) -> IndexEntry | None:
    """优先返回兼容的最高版本；都不兼容时返回最高版本，供目录展示“不兼容”。"""
    compatible = best_compatible(entries)
    if compatible is not None:
        return compatible
    if not entries:
        return None
    return max(entries, key=lambda entry: _version_tuple(entry.version))


def manifest_matches(entry: IndexEntry, manifest: PluginManifest) -> bool:
    """压缩包里的声明必须和索引条目一致（能力顺序不敏感）。"""
    return (
        manifest.id == entry.id
        and manifest.version == entry.version
        and manifest.sdk == entry.sdk
        and sorted(manifest.provides) == sorted(entry.provides)
        and sorted(manifest.capabilities) == sorted(entry.capabilities)
    )


def _parse_entry(raw: Any, base_url: str) -> IndexEntry:
    if not isinstance(raw, dict):
        raise _InvalidEntry("条目必须是对象")
    plugin_id = _required_text(raw, "id")
    if PLUGIN_ID.fullmatch(plugin_id) is None or plugin_id.startswith(BUILTIN_PREFIX):
        raise _InvalidEntry("插件 id 不合法或使用了保留前缀")
    version = _required_text(raw, "version")
    if VERSION.fullmatch(version) is None:
        raise _InvalidEntry("版本号不合法")
    sdk = _required_text(raw, "sdk")
    # 只校验格式；是否兼容宿主要看目录状态，条目本身仍然可以展示
    sdk_compatible(sdk, SDK_VERSION)
    provides = _text_list(raw, "provides", required=True)
    capabilities = _text_list(raw, "capabilities", required=False)
    url = resolve_url(base_url, _required_text(raw, "url"))
    sha256 = _required_text(raw, "sha256")
    if SHA256.fullmatch(sha256) is None:
        raise _InvalidEntry("sha256 不合法")
    size = raw.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= MAX_PACKAGE_BYTES:
        raise _InvalidEntry("size 不合法")
    homepage = raw.get("homepage")
    if homepage is not None and not isinstance(homepage, str):
        raise _InvalidEntry("homepage 必须是文本")
    return IndexEntry(
        id=plugin_id,
        name=_required_text(raw, "name"),
        description=_optional_text(raw, "description"),
        version=version,
        sdk=sdk,
        provides=provides,
        capabilities=capabilities,
        url=url,
        sha256=sha256,
        size=size,
        homepage=homepage,
        signature=raw.get("signature"),
    )


def _entry_to_dict(entry: IndexEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "name": entry.name,
        "description": entry.description,
        "version": entry.version,
        "sdk": entry.sdk,
        "provides": list(entry.provides),
        "capabilities": list(entry.capabilities),
        "url": entry.url,
        "sha256": entry.sha256,
        "size": entry.size,
        "homepage": entry.homepage,
        "signature": entry.signature,
    }


def _entry_from_dict(raw: Any) -> IndexEntry:
    if not isinstance(raw, dict):
        raise TypeError("entry must be an object")
    provides = raw["provides"]
    capabilities = raw["capabilities"]
    if not isinstance(provides, list) or not isinstance(capabilities, list):
        raise TypeError("entry lists are invalid")
    return IndexEntry(
        id=str(raw["id"]),
        name=str(raw["name"]),
        description=str(raw["description"]),
        version=str(raw["version"]),
        sdk=str(raw["sdk"]),
        provides=tuple(str(item) for item in provides),
        capabilities=tuple(str(item) for item in capabilities),
        url=str(raw["url"]),
        sha256=str(raw["sha256"]),
        size=int(raw["size"]),
        homepage=raw.get("homepage") if isinstance(raw.get("homepage"), str) else None,
        signature=raw.get("signature"),
    )


def _required_text(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise _InvalidEntry(f"{key} 必须是非空文本")
    return value


def _optional_text(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key, "")
    if not isinstance(value, str):
        raise _InvalidEntry(f"{key} 必须是文本")
    return value


def _text_list(raw: dict[str, Any], key: str, *, required: bool) -> tuple[str, ...]:
    value = raw.get(key, [] if not required else None)
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise _InvalidEntry(f"{key} 必须是非空文本数组")
    if required and not value:
        raise _InvalidEntry(f"{key} 不能为空")
    return tuple(value)


def _version_tuple(value: str) -> tuple[int, ...]:
    if VERSION.fullmatch(value) is None:
        raise ValueError(f"版本号不合法：{value}")
    return tuple(int(part) for part in value.split("."))
