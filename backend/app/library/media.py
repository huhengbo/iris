from __future__ import annotations

from typing import Literal

MediaKind = Literal["video", "subtitle", "nfo"]

VIDEO_EXTENSIONS = frozenset(
    {
        "mkv",
        "mp4",
        "avi",
        "ts",
        "m2ts",
        "iso",
        "rmvb",
        "wmv",
        "mov",
        "flv",
        "webm",
        "mpg",
        "mpeg",
        "vob",
        "m4v",
    }
)
SUBTITLE_EXTENSIONS = frozenset({"srt", "ass", "ssa", "sup", "sub", "idx", "vtt", "smi"})


def media_kind(name: str) -> MediaKind | None:
    """按扩展名判断文件类型。同步关心视频、字幕和 NFO（只读取其中的 ID），其他文件返回 None。"""
    _, dot, extension = name.rpartition(".")
    if not dot:
        return None
    extension = extension.lower()
    if extension in VIDEO_EXTENSIONS:
        return "video"
    if extension in SUBTITLE_EXTENSIONS:
        return "subtitle"
    if extension == "nfo":
        return "nfo"
    return None
