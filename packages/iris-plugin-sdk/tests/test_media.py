from __future__ import annotations

import pytest

from iris_plugin_sdk import SUBTITLE_EXTENSIONS, VIDEO_EXTENSIONS, media_kind


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("movie.MKV", "video"),
        ("dir/剧集.m2ts", "video"),
        ("movie.rmvb", "video"),
        ("movie.iso", "video"),
        ("movie.zh.srt", "subtitle"),
        ("movie.sup", "subtitle"),
        ("movie.idx", "subtitle"),
        ("movie.smi", "subtitle"),
        ("movie.nfo", "nfo"),
        ("movie.NFO", "nfo"),
        ("movie.txt", None),
        ("no-extension", None),
        (".hidden", None),
    ],
)
def test_media_kind(name: str, expected: str | None) -> None:
    assert media_kind(name) == expected


def test_extension_tables_cover_plugins_union() -> None:
    assert {"m2ts", "iso", "rmvb", "wmv", "flv", "vob"} <= VIDEO_EXTENSIONS
    assert {"idx", "smi"} <= SUBTITLE_EXTENSIONS
