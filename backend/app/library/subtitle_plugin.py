"""宿主内置的本地外挂字幕匹配：把资料库里和视频同目录、同名前缀的字幕文件匹配成候选。

只看路径，不读文件也不访问网络；字幕文件本身作为资源来源里的文件保存，播放时直接引用。
每个视频都要匹配，所以它是同步时直接调用的宿主逻辑，不是插件。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from iris_plugin_sdk import MediaContext

from app.library.recognize import subtitle_info, subtitle_match_length

PROVIDER_ID = "local"


@dataclass(frozen=True)
class LocalSubtitleRequest:
    """宿主本地字幕匹配的输入；不属于插件协议，media.filename 是视频的相对路径。"""

    media: MediaContext
    files: tuple[str, ...]


@dataclass(frozen=True)
class LocalSubtitleCandidate:
    remote_id: str
    label: str
    language: str
    format: str
    forced: bool
    score: float
    # 文件名标了 default，播放时优先选用
    default: bool


class SubtitleMatcher(Protocol):
    """同步时把资料库里的字幕文件匹配给视频。"""

    def search(self, request: LocalSubtitleRequest) -> list[LocalSubtitleCandidate]: ...


class LocalSubtitles:
    """候选的 remote_id 是字幕的相对路径，score 是匹配的视频名长度（越长越具体）。"""

    id = PROVIDER_ID

    def search(self, request: LocalSubtitleRequest) -> list[LocalSubtitleCandidate]:
        video = request.media.filename
        if not video:
            return []
        candidates = []
        for path in request.files:
            length = subtitle_match_length(path, video)
            if length is None:
                continue
            info = subtitle_info(path, video)
            candidates.append(
                LocalSubtitleCandidate(
                    remote_id=path,
                    label=info.label,
                    language=info.language or "",
                    format=path.rpartition(".")[2].lower(),
                    forced=info.forced,
                    score=float(length),
                    default=info.default,
                )
            )
        return candidates


LOCAL_SUBTITLES = LocalSubtitles()
