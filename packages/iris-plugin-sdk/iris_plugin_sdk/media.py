"""按扩展名判断资源来源里的文件类型。

来源插件扫描、浏览和判断目录变动时使用同一张表，避免各插件各写一份。扩展名不区分大小写，
传入文件名或 POSIX 路径都可以。
"""

from __future__ import annotations

from typing import Literal

MediaKind = Literal["video", "subtitle", "nfo"]

VIDEO_EXTENSIONS = frozenset(
    {
        "avi",
        "flv",
        "iso",
        "m2ts",
        "m4v",
        "mkv",
        "mov",
        "mp4",
        "mpeg",
        "mpg",
        "rmvb",
        "ts",
        "vob",
        "webm",
        "wmv",
    }
)
SUBTITLE_EXTENSIONS = frozenset({"ass", "idx", "smi", "srt", "ssa", "sub", "sup", "vtt"})


def media_kind(name: str) -> MediaKind | None:
    """返回文件名对应的媒体类型；不认识的扩展名返回 None。"""
    base = name.rsplit("/", 1)[-1]
    _, dot, extension = base.rpartition(".")
    if not dot:
        return None
    extension = extension.casefold()
    if extension in VIDEO_EXTENSIONS:
        return "video"
    if extension in SUBTITLE_EXTENSIONS:
        return "subtitle"
    if extension == "nfo":
        return "nfo"
    return None
