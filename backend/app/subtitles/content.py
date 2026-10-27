"""在线字幕的内容处理：编码、格式、大小和去重用的哈希。

字幕站给的文件可能是 UTF-8、GB18030 或 Big5，也可能带 BOM；统一转成无 BOM 的 UTF-8
再压缩入库。格式按内容判断，不信任扩展名（位图字幕直接拒绝）。
"""

from __future__ import annotations

import codecs
import hashlib
import re
import zlib
from dataclasses import dataclass

from app.errors import AppError

# 原始上传和最终文本都限制 5 MiB，避免一次请求把数据库撑大
MAX_SUBTITLE_BYTES = 5 * 1024 * 1024
MAX_ONLINE_PER_VERSION = 20

SubtitleFormat = str
_FORMATS = frozenset({"srt", "ass", "ssa", "vtt"})
_BITMAP_EXTENSIONS = frozenset({"sup", "sub", "idx"})
_MEDIA_TYPES = {
    "srt": "application/x-subrip; charset=utf-8",
    "ass": "text/x-ssa; charset=utf-8",
    "ssa": "text/x-ssa; charset=utf-8",
    "vtt": "text/vtt; charset=utf-8",
}
_SRT_TIMECODE = re.compile(r"(?m)^\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*\S")
_ASS_PLUS = re.compile(r"(?im)^\s*scripttype\s*:\s*v4\.00\s*\+")
_SSA = re.compile(r"(?im)^\s*scripttype\s*:\s*v4\.00[ \t]*$")


@dataclass(frozen=True)
class PreparedSubtitle:
    """校验并转码后的字幕；content 是无 BOM 的 UTF-8 文本。"""

    content: bytes
    format: SubtitleFormat
    language: str | None
    original_encoding: str
    sha256: str
    size: int

    @property
    def media_type(self) -> str:
        return subtitle_media_type(self.format)


def normalize_language_code(language: str | None) -> str | None:
    """客户端偏好里用 zh-CN/zh-TW，字幕和详情里用 zh-Hans/zh-Hant。"""
    if language is None:
        return None
    value = language.strip()
    if not value:
        return None
    aliases = {
        "zh-cn": "zh-Hans",
        "zh_cn": "zh-Hans",
        "zh-hans": "zh-Hans",
        "zh-sg": "zh-Hans",
        "zh-tw": "zh-Hant",
        "zh_tw": "zh-Hant",
        "zh-hant": "zh-Hant",
        "zh-hk": "zh-Hant",
    }
    return aliases.get(value.lower(), value)


def subtitle_media_type(format: str) -> str:
    return _MEDIA_TYPES.get(format, "text/plain; charset=utf-8")


def subtitle_extension(format: str) -> str:
    return format if format in _FORMATS else "srt"


def prepare_subtitle(
    raw: bytes,
    *,
    language: str | None = None,
    format_hint: str | None = None,
    filename: str | None = None,
) -> PreparedSubtitle:
    """校验大小、解码并判断格式；任何一步失败都抛 invalid_subtitle。"""
    if not raw:
        raise _invalid("字幕内容是空的")
    if len(raw) > MAX_SUBTITLE_BYTES:
        raise _invalid("字幕文件不能超过 5 MiB")
    hints = {value.lower() for value in (format_hint, _extension(filename)) if value}
    if hints & _BITMAP_EXTENSIONS:
        raise _invalid("不支持位图字幕，请选择文本字幕（srt、ass、ssa 或 vtt）")
    text, original_encoding = _decode(raw, language)
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_SUBTITLE_BYTES:
        raise _invalid("字幕解码后不能超过 5 MiB")
    detected = detect_format(text)
    if detected is None:
        raise _invalid("无法识别字幕格式，只支持 srt、ass、ssa 和 vtt")
    return PreparedSubtitle(
        content=encoded,
        format=detected,
        language=normalize_language_code(language),
        original_encoding=original_encoding,
        sha256=hashlib.sha256(encoded).hexdigest(),
        size=len(encoded),
    )


def _decode(raw: bytes, language: str | None) -> tuple[str, str]:
    if raw.startswith(codecs.BOM_UTF8):
        return _decode_strict(raw, "utf-8-sig"), "utf-8-sig"
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return _decode_strict(raw, "utf-16"), "utf-16"
    # 非 UTF-16 的文本里出现 NUL 基本就是二进制；GB18030 会把任意字节解成字符，必须先挡掉
    if b"\x00" in raw:
        raise _invalid("字幕内容不是文本")
    if raw.startswith(b"PG"):
        raise _invalid("不支持位图字幕（SUP）")
    # 繁体字幕先用 Big5，避免 GB18030 把 Big5 字节解成另一套汉字
    traditional = normalize_language_code(language) == "zh-Hant"
    encodings = ("utf-8", "big5", "gb18030") if traditional else ("utf-8", "gb18030", "big5")
    for encoding in encodings:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise _invalid("无法识别字幕文本编码，请转成 UTF-8 后再试")


def _decode_strict(raw: bytes, encoding: str) -> str:
    try:
        return raw.decode(encoding)
    except UnicodeDecodeError as exc:
        raise _invalid("字幕文本编码不正确") from exc


def detect_format(text: str) -> str | None:
    """按内容判断格式；无法识别（包括位图）时返回 None。"""
    head = text.lstrip("\ufeff \t\r\n")[:2000]
    if "[script info]" in head.lower() and _ASS_PLUS.search(text):
        return "ass"
    if "[script info]" in head.lower() and _SSA.search(text):
        return "ssa"
    if head.upper().startswith("WEBVTT"):
        return "vtt"
    if _SRT_TIMECODE.search(text):
        return "srt"
    return None


def compress(content: bytes) -> bytes:
    return zlib.compress(content, level=9)


def decompress(content: bytes) -> bytes:
    try:
        return zlib.decompress(content)
    except zlib.error as exc:
        raise AppError(500, "invalid_subtitle", "在线字幕内容已损坏") from exc


def _extension(filename: str | None) -> str:
    if not filename:
        return ""
    name = filename.rpartition("/")[2]
    suffix = name.rpartition(".")[2]
    return suffix.lower() if suffix and suffix != name else ""


def _invalid(message: str) -> AppError:
    return AppError(422, "invalid_subtitle", message)
