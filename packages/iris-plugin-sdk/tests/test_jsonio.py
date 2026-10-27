from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from iris_plugin_sdk import read_json, write_json


def test_missing_file_returns_default(tmp_path: Path) -> None:
    assert read_json(tmp_path / "missing.json") is None
    assert read_json(tmp_path / "missing.json", {}) == {}


def test_write_json_is_atomic_private_and_readable(tmp_path: Path) -> None:
    path = tmp_path / "state" / "settings.json"

    write_json(path, {"token": "secret", "中文": True})

    assert read_json(path) == {"token": "secret", "中文": True}
    assert json.loads(path.read_text(encoding="utf-8")) == {"token": "secret", "中文": True}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(path.parent.glob(f".{path.name}-*"))


def test_write_json_replaces_existing_file_and_cleans_temp_on_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "settings.json"
    write_json(path, {"old": True})

    def broken_replace(source: str, target: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(OSError):
        write_json(path, {"new": True})

    assert read_json(path) == {"old": True}
    assert not list(tmp_path.glob(".settings.json-*"))


def test_corrupt_json_raises_value_error(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(ValueError, match="损坏"):
        read_json(path)


def test_json_store_reads_values_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from iris_plugin_sdk import JsonSettingsStore, SettingField, SettingsEndpoint

    store = JsonSettingsStore(tmp_path)
    store.put("a", "1")
    store.put("b", 2)
    calls = 0
    original = store._load

    def counted() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return original()

    monkeypatch.setattr(store, "_load", counted)
    endpoint = SettingsEndpoint(
        (
            SettingField("a", "A", "text"),
            SettingField("b", "B", "number"),
        ),
        store=store,
    )

    assert endpoint.values() == {"a": "1", "b": 2}
    assert calls == 1
