from __future__ import annotations

import pytest
from iris_plugin_sdk import (
    SDK_RANGE,
    PluginManifest,
    PluginManifestError,
    PluginSdkIncompatibleError,
    manifest_from_dict,
)


def test_manifest_accepts_declared_capabilities() -> None:
    manifest = PluginManifest(
        id="org.example.subtitles",
        name="Example Subtitles",
        version="1.0.0",
        entry="example_subtitles",
        provides=("subtitle.search", "subtitle.download"),
        capabilities=("http",),
    )
    assert manifest.sdk == SDK_RANGE


def test_manifest_description_is_optional_and_limited_to_200_characters() -> None:
    manifest = PluginManifest(
        id="org.example.described",
        name="Described",
        version="1.0.0",
        entry="described",
        provides=("subtitle.search",),
        description="一句简介",
    )
    assert manifest.description == "一句简介"

    with pytest.raises(PluginManifestError, match="简介"):
        PluginManifest(
            id="org.example.long-description",
            name="Long",
            version="1.0.0",
            entry="long_description",
            provides=("subtitle.search",),
            description="x" * 201,
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"provides": ("unknown.capability",)},
        {"capabilities": ("database",)},
        {"provides": ("subtitle.search", "subtitle.search")},
        {"entry": "../escape"},
    ],
)
def test_manifest_rejects_unknown_or_duplicate_declarations(
    kwargs: dict[str, tuple[str, ...] | str],
) -> None:
    arguments: dict[str, object] = {
        "id": "org.example.invalid",
        "name": "Invalid",
        "version": "1.0.0",
        "entry": "invalid",
        "provides": ("subtitle.search",),
    }
    arguments.update(kwargs)
    with pytest.raises(PluginManifestError):
        PluginManifest(**arguments)  # type: ignore[arg-type]


def test_manifest_loader_rejects_incompatible_sdk() -> None:
    with pytest.raises(PluginSdkIncompatibleError):
        manifest_from_dict(
            {
                "id": "org.example.future",
                "name": "Future",
                "version": "1.0.0",
                "entry": "future",
                "sdk": ">=2.0,<3.0",
                "provides": ["subtitle.search"],
            }
        )
