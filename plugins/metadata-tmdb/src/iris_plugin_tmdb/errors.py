"""TMDB 插件抛给宿主的错误；全部是 SDK 的元数据异常。"""

from __future__ import annotations

from iris_plugin_sdk import MetadataNotFound, MetadataUnavailable


class TmdbNotConfigured(MetadataUnavailable):
    def __init__(self) -> None:
        super().__init__("还没有配置 TMDB Key")


class TmdbAuthFailed(MetadataUnavailable):
    """TMDB 拒绝了 Key（401）。Key 填错或已被吊销，要管理员重新填写。"""

    def __init__(self) -> None:
        super().__init__("TMDB Key 无效，请检查后重新填写")


class TmdbNotFound(MetadataNotFound):
    def __init__(self) -> None:
        super().__init__("TMDB 上没有这个条目")


class TmdbUnavailable(MetadataUnavailable):
    """网络错误、超时、代理不可用或 TMDB 返回的其他错误，可以稍后重试。"""

    def __init__(self, message: str = "暂时连不上 TMDB，请检查网络或代理后重试") -> None:
        super().__init__(message)
