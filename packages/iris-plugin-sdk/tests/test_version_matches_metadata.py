from __future__ import annotations

from importlib.metadata import version

from iris_plugin_sdk.types import SDK_VERSION


def test_sdk_version_matches_distribution_metadata() -> None:
    """发布包的版本必须和运行时自报的 SDK_VERSION 一致。"""
    assert version("iris-plugin-sdk") == SDK_VERSION
