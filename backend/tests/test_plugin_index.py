from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from app.plugins.index_schema import (
    MAX_INDEX_ENTRIES,
    IndexInvalid,
    IndexUnsupported,
    best_compatible,
    best_entry,
    compare_versions,
    dump_index,
    load_dumped_index,
    manifest_matches,
    parse_index,
)

BASE = "https://plugins.example.com/repo/index.json"
SHA = hashlib.sha256(b"package").hexdigest()


def entry(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "example.hello",
        "name": "示例插件",
        "description": "测试",
        "version": "1.0.0",
        "sdk": ">=1.0,<2.0",
        "provides": ["metadata.search"],
        "capabilities": ["logger"],
        "url": "packages/example.hello-1.0.0.zip",
        "sha256": SHA,
        "size": 100,
        "homepage": None,
        "signature": None,
    }
    data.update(overrides)
    return data


def index(*entries: dict[str, Any], schema: int = 1) -> dict[str, Any]:
    return {"schema": schema, "name": "测试仓库", "plugins": list(entries)}


def test_parse_valid_index_resolves_relative_url() -> None:
    parsed = parse_index(index(entry()), BASE)
    assert parsed.schema == 1
    assert parsed.name == "测试仓库"
    assert parsed.invalid_count == 0
    assert len(parsed.entries) == 1
    item = parsed.entries[0]
    assert item.id == "example.hello"
    assert item.url == "https://plugins.example.com/repo/packages/example.hello-1.0.0.zip"
    assert item.provides == ("metadata.search",)


def test_unknown_fields_are_ignored() -> None:
    data = index(entry())
    data["future"] = {"x": 1}
    data["plugins"][0]["future"] = "ignored"
    parsed = parse_index(data, BASE)
    assert len(parsed.entries) == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": "Builtin.bad"},
        {"id": "builtin.fake"},
        {"id": "bad id"},
        {"id": "UPPER"},
        {"version": "v1"},
        {"version": "1.0.0.0.0"},
        {"sdk": ">=1.0,<"},
        {"provides": []},
        {"provides": "metadata.search"},
        {"url": "http://example.com/package.zip"},
        {"url": ""},
        {"sha256": "ABC"},
        {"sha256": "0" * 63},
        {"size": 0},
        {"size": 50 * 1024 * 1024 + 1},
        {"size": "100"},
        {"name": ""},
    ],
)
def test_invalid_entries_are_counted_and_skipped(overrides: dict[str, Any]) -> None:
    parsed = parse_index(index(entry(**overrides)), BASE)
    assert parsed.entries == ()
    assert parsed.invalid_count == 1


def test_local_http_is_allowed_only_for_localhost() -> None:
    parsed = parse_index(
        index(entry(url="http://localhost:8765/package.zip")), "http://localhost:8765/index.json"
    )
    assert parsed.entries[0].url == "http://localhost:8765/package.zip"
    invalid = parse_index(index(entry(url="http://example.com/package.zip")), BASE)
    assert invalid.entries == ()
    assert invalid.invalid_count == 1


def test_duplicate_id_version_is_invalid() -> None:
    parsed = parse_index(index(entry(), entry()), BASE)
    assert len(parsed.entries) == 1
    assert parsed.invalid_count == 1


def test_identity_fields_must_match() -> None:
    parsed = parse_index(index(entry()), BASE)
    assert parsed.entries[0].id == "example.hello"


def test_unsupported_schema_is_reported() -> None:
    with pytest.raises(IndexUnsupported) as caught:
        parse_index(index(schema=2), BASE)
    assert caught.value.schema == 2


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"schema": 1},
        {"schema": "1", "plugins": []},
        {"schema": 1, "plugins": {}},
        [],
    ],
)
def test_structurally_invalid_index(data: Any) -> None:
    with pytest.raises(IndexInvalid):
        parse_index(data, BASE)


def test_too_many_entries_are_rejected() -> None:
    plugins = [entry(id=f"example.p{i}") for i in range(MAX_INDEX_ENTRIES + 1)]
    with pytest.raises(IndexInvalid):
        parse_index({"schema": 1, "plugins": plugins}, BASE)


def test_dump_and_load_round_trip() -> None:
    parsed = parse_index(index(entry()), BASE)
    loaded = load_dumped_index(dump_index(parsed))
    assert loaded is not None
    assert loaded.entries == parsed.entries
    assert loaded.invalid_count == 0
    assert load_dumped_index("not json") is None
    assert load_dumped_index(None) is None


def test_version_compare_pads_missing_components() -> None:
    assert compare_versions("1.0", "1.0.0") == 0
    assert compare_versions("1.10", "1.9") > 0
    assert compare_versions("2", "1.99") > 0
    with pytest.raises(ValueError):
        compare_versions("v1", "1")


def test_best_entry_prefers_compatible_version() -> None:
    parsed = parse_index(
        index(
            entry(version="1.0.0"),
            entry(version="1.2.0"),
            entry(version="2.0.0", sdk=">=2.0,<3.0"),
        ),
        BASE,
    )
    assert best_compatible(parsed.entries) is not None
    assert best_compatible(parsed.entries).version == "1.2.0"  # type: ignore[union-attr]
    assert best_entry(parsed.entries).version == "1.2.0"  # type: ignore[union-attr]
    incompatible = parse_index(index(entry(version="9.0", sdk=">=9.0,<10.0")), BASE)
    assert best_compatible(incompatible.entries) is None
    assert best_entry(incompatible.entries).version == "9.0"  # type: ignore[union-attr]


def test_manifest_match_is_order_insensitive() -> None:
    import iris_plugin_sdk

    parsed = parse_index(index(entry(provides=["admin.ui"], capabilities=["logger", "http"])), BASE)
    directory = Path(__file__).with_name("fixtures") / "fake_plugin"
    manifest = iris_plugin_sdk.load_manifest(directory)
    candidate = copy.copy(parsed.entries[0])
    assert candidate.id != manifest.id
    assert not manifest_matches(candidate, manifest)
    matched = type(parsed.entries[0])(
        id=manifest.id,
        name=manifest.name,
        description=manifest.description,
        version=manifest.version,
        sdk=manifest.sdk,
        provides=tuple(reversed(manifest.provides)),
        capabilities=tuple(reversed(manifest.capabilities)),
        url=parsed.entries[0].url,
        sha256=parsed.entries[0].sha256,
        size=parsed.entries[0].size,
    )
    assert manifest_matches(matched, manifest)


def test_json_schema_file_is_valid_json() -> None:
    schema_path = Path(__file__).parents[2] / "docs" / "api" / "plugin-index.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["$schema"].startswith("https://json-schema.org/")
    assert schema["properties"]["schema"]["const"] == 1
    assert "plugins" in schema["required"]


@pytest.mark.parametrize(
    "version",
    [
        "1" * 5000,
        "1." + "9" * 5000,
        "1234567890",
        "1.2.3.4.5",
        "１.０.０",
        "١.٠.٠",
        "1.0.0\n",
    ],
)
def test_oversized_or_non_ascii_versions_are_invalid_entries(version: str) -> None:
    good = entry(id="example.good", version="2.0.0")
    parsed = parse_index(index(entry(version=version), good), BASE)
    assert [item.id for item in parsed.entries] == ["example.good"]
    assert parsed.invalid_count == 1


def test_corrupt_cached_version_makes_cache_unusable() -> None:
    parsed = parse_index(index(entry()), BASE)
    payload = json.loads(dump_index(parsed))
    payload["entries"][0]["version"] = "9" * 5000
    assert load_dumped_index(json.dumps(payload)) is None
