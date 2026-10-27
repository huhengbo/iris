"""在线字幕内容处理：编码、格式、大小和位图拒绝。"""

from __future__ import annotations

import pytest

from app.errors import AppError
from app.subtitles.content import (
    MAX_SUBTITLE_BYTES,
    compress,
    decompress,
    detect_format,
    prepare_subtitle,
)

SRT = "1\n00:00:01,000 --> 00:00:02,000\n你好\n"


def test_detects_text_formats_by_content() -> None:
    assert detect_format(SRT) == "srt"
    assert detect_format("[Script Info]\nScriptType: v4.00+\n") == "ass"
    assert detect_format("[Script Info]\nScriptType: v4.00\n") == "ssa"
    assert detect_format("WEBVTT\n\n00:00.000 --> 00:01.000\nHi\n") == "vtt"
    assert detect_format("not a subtitle") is None


def test_decodes_bom_gb18030_and_big5() -> None:
    utf8 = prepare_subtitle(("\ufeff" + SRT).encode("utf-8"))
    assert (utf8.format, utf8.original_encoding) == ("srt", "utf-8-sig")
    assert not utf8.content.startswith(b"\xef\xbb\xbf")

    utf16 = prepare_subtitle(SRT.encode("utf-16"))
    assert utf16.original_encoding == "utf-16"
    assert "你好" in utf16.content.decode()

    gb = prepare_subtitle(SRT.encode("gb18030"))
    assert gb.original_encoding == "gb18030"
    assert "你好" in gb.content.decode()

    traditional = "1\n00:00:01,000 --> 00:00:02,000\n繁體中文\n"
    big5 = prepare_subtitle(traditional.encode("big5"), language="zh-Hant")
    assert big5.original_encoding == "big5"
    assert "繁體中文" in big5.content.decode()
    # 同一个文件按简体偏好解码时 GB18030 会先成功，但内容仍可读；记录实际编码
    assert big5.format == "srt"


def test_language_aliases_are_normalized() -> None:
    assert prepare_subtitle(SRT.encode(), language="zh-CN").language == "zh-Hans"
    assert prepare_subtitle(SRT.encode(), language="zh-TW").language == "zh-Hant"


def test_rejects_bitmap_binary_and_oversize() -> None:
    with pytest.raises(AppError) as bitmap:
        prepare_subtitle(b"PG" + b"\x00" * 20, format_hint="sup")
    assert bitmap.value.code == "invalid_subtitle"

    with pytest.raises(AppError) as binary:
        prepare_subtitle(b"garbage\x00\x00", format_hint="srt")
    assert binary.value.code == "invalid_subtitle"

    with pytest.raises(AppError) as unknown:
        prepare_subtitle(b"no timecode at all", format_hint="srt")
    assert unknown.value.code == "invalid_subtitle"

    with pytest.raises(AppError):
        prepare_subtitle(b"x" * (MAX_SUBTITLE_BYTES + 1), format_hint="srt")


def test_round_trip_compression() -> None:
    prepared = prepare_subtitle(SRT.encode())
    assert decompress(compress(prepared.content)) == prepared.content
