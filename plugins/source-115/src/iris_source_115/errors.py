"""115 插件内部的错误，以及通往宿主接口的唯一翻译层。

错误码只用于插件内部和插件页面；宿主接口只认 SDK 的 `Resource*` 异常，由
`to_sdk_error` 统一翻译。
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from iris_plugin_sdk import ResourceNotFound, ResourceRateLimited, ResourceUnavailable

# 风控没有给出恢复时间时的退避，和熔断器首次触发的时长一致
RATE_LIMIT_BACKOFF = timedelta(minutes=30)


class ResourceApiRejected(Exception):
    """来源的增量事件接口拒绝当前 API，需要切换到备用接口。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class Cloud115Error(Exception):
    """插件内部错误的基类；message 可以展示给管理员。"""

    code = "cloud115_error"
    status_code = 502

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class CredentialMissing(Cloud115Error):
    code = "cloud115_not_configured"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("还没有接入 115 账号")


class CredentialInvalid(Cloud115Error):
    """115 明确返回未登录、被踢下线或需要验证账号。凭据本身已不可用，要重新登录。"""

    code = "cloud115_credential_invalid"
    status_code = 409

    def __init__(self, message: str = "115 登录已失效，请重新扫码或填写 Cookie") -> None:
        super().__init__(message)


class RateLimited(Cloud115Error):
    """115 风控：HTTP 405 加 HTML 拦截页。和凭据失效无关，只能等待退避结束。

    预计恢复时间放在 resume_at 里，由状态接口返回，客户端按本地时区展示。
    """

    code = "resource_rate_limited"
    status_code = 503

    def __init__(self, resume_at: datetime | None = None) -> None:
        super().__init__("115 暂时受限，已暂停访问，请稍后再试")
        self.resume_at = resume_at


class DirNotFound(Cloud115Error):
    code = "cloud115_dir_not_found"
    status_code = 404

    def __init__(self) -> None:
        super().__init__("115 目录不存在或已被删除")


class FileGone(Cloud115Error):
    code = "resource_file_gone"
    status_code = 404

    def __init__(self, message: str = "115 上找不到这个文件，可能已被删除或移走") -> None:
        super().__init__(message)


class ExportBusy(Cloud115Error):
    """115 同一时间只允许一个导出目录树任务，可能被其他工具占用，等一会再提交。"""

    code = "cloud115_export_busy"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("115 上有其他导出目录树任务正在运行")


class Cloud115Unavailable(Cloud115Error):
    """网络错误、超时或 115 返回的其他错误，可以稍后重试。"""

    code = "resource_unavailable"
    status_code = 502

    def __init__(self, message: str = "暂时连不上 115，请稍后重试") -> None:
        super().__init__(message)


class CopyNotReady(Cloud115Unavailable):
    """刚复制出来的文件 115 还没处理完，取直链时返回「文件上传不完整」，稍等再取。"""

    def __init__(self) -> None:
        super().__init__("115 上的副本还没有准备好")


class LifeApiRejected(Cloud115Unavailable):
    """App 版生活事件接口返回 405。

    这个接口风控严，但只影响它自己：不触发全局熔断，改用网页版接口。
    """

    def __init__(self) -> None:
        super().__init__("115 拒绝了 App 版生活事件接口的请求")


class InvalidCookie(Cloud115Error):
    code = "invalid_cookie"
    status_code = 422

    def __init__(self) -> None:
        super().__init__("Cookie 格式不对，至少要包含 UID、CID、SEID")


class UnsupportedDevice(Cloud115Error):
    code = "unsupported_device"
    status_code = 422

    def __init__(self) -> None:
        super().__init__("不支持这个设备身份")


class QrSessionMissing(Cloud115Error):
    code = "not_found"
    status_code = 404

    def __init__(self) -> None:
        super().__init__("扫码会话不存在或已结束，请重新获取二维码")


def to_sdk_error(error: Cloud115Error, *, now: datetime) -> Exception:
    """把 115 内部异常翻译成宿主认识的 SDK 异常。

    资源接口的所有出口都只抛这里的结果，文案、错误码和 fatal 只在这里定义一次。
    RateLimited 的恢复时间换算成 retry_after 秒数；没有 resume_at 时按熔断器首次
    退避计算，保证宿主管不到原始异常也能拿到恢复时间。
    """
    if isinstance(error, RateLimited):
        resume_at = error.resume_at or now + RATE_LIMIT_BACKOFF
        retry_after = max(1, math.ceil((resume_at - now).total_seconds()))
        return ResourceRateLimited(error.message, retry_after, 503)
    if isinstance(error, LifeApiRejected):
        return ResourceApiRejected(error.message)
    if isinstance(error, (DirNotFound, FileGone)):
        return ResourceNotFound(error.message)
    if isinstance(error, CredentialMissing):
        return ResourceUnavailable(
            error.message,
            fatal=True,
            status_code=error.status_code,
            code="resource_not_configured",
        )
    if isinstance(error, CredentialInvalid):
        return ResourceUnavailable(
            error.message,
            fatal=True,
            status_code=error.status_code,
            code="resource_credential_invalid",
        )
    if isinstance(error, Cloud115Unavailable):
        return ResourceUnavailable(
            error.message, status_code=error.status_code, code="resource_unavailable"
        )
    raise TypeError(f"不是资源接口错误：{type(error).__name__}")
