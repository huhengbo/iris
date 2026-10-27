"""115 插件的管理页面接口：账号状态、扫码登录、凭据维护和设置页。

页面只调用 `/api/plugins/source.115/` 下的路径；宿主负责鉴权、静态文件、CSP 和审计。
错误码 `cloud115_*` 只出现在插件内部和插件页面上。
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from iris_plugin_sdk import (
    JsonSettingsStore,
    PluginRequest,
    PluginRequestError,
    PluginResponse,
    SettingField,
    SettingsAdminUi,
    SettingsEndpoint,
)

from iris_source_115.clock import Clock, to_iso
from iris_source_115.errors import Cloud115Error, RateLimited
from iris_source_115.gateway import AccountInfo
from iris_source_115.life import LifeMonitor
from iris_source_115.scheduler import ApiGroup
from iris_source_115.service import DEVICES, Cloud115Service, QrProgress

LIFE_DEFAULT_INTERVAL_SECONDS = 20
LIFE_MIN_INTERVAL_SECONDS = 10
LIFE_MAX_INTERVAL_SECONDS = 600
_DEVICE_LABELS = {device.id: device.label for device in DEVICES}
_COOKIE_MAX_LENGTH = 8192
_DEVICE_MAX_LENGTH = 32
_API_LABELS = {ApiGroup.WEB: "网页版接口", ApiGroup.APP: "App 版接口"}


def cloud115_settings(data_dir: Path) -> SettingsEndpoint:
    """设置页的公共逻辑；设置值存在插件自己的 state/settings.json。"""
    return SettingsEndpoint(
        (
            SettingField(
                "same_playback",
                "多端同时播放",
                "bool",
                default=False,
                help=(
                    "同一个文件已经在别的设备上播放时，复制一份到「光圈临时目录/多端播放」，"
                    "用副本的直链播放，几秒后删除副本。会写入 115。"
                ),
            ),
            SettingField(
                "life_enabled",
                "监听生活事件",
                "bool",
                default=False,
                help=(
                    "开启后会打开 115 账号的「最近记录」开关，按下面的间隔拉取变动并触发定向同步。"
                ),
            ),
            SettingField(
                "life_interval",
                "生活事件检查间隔（秒）",
                "number",
                default=LIFE_DEFAULT_INTERVAL_SECONDS,
                min=LIFE_MIN_INTERVAL_SECONDS,
                max=LIFE_MAX_INTERVAL_SECONDS,
                help=(
                    f"{LIFE_MIN_INTERVAL_SECONDS} 到 {LIFE_MAX_INTERVAL_SECONDS} 秒之间；"
                    "漏掉的变动由定时同步兜底。"
                ),
            ),
        ),
        store=JsonSettingsStore(data_dir),
    )


def _account(account: AccountInfo) -> dict[str, Any]:
    return {
        "user_id": account.user_id,
        "user_name": account.user_name,
        "is_vip": account.is_vip,
        "vip_expires": account.vip_expires,
        "space_total": account.space_total,
        "space_used": account.space_used,
    }


def _qr_progress(progress: QrProgress) -> dict[str, Any]:
    return {
        "status": progress.status,
        "message": progress.message,
        "account": _account(progress.account) if progress.account is not None else None,
    }


def _status(service: Cloud115Service, clock: Clock) -> dict[str, Any]:
    status = service.status()
    meta = status.credential
    if meta is None:
        credential = {
            "state": "missing",
            "device": None,
            "device_label": None,
            "user_name": None,
            "saved_at": None,
            "invalid_at": None,
        }
    else:
        credential = {
            "state": "invalid" if meta.invalid_at else "valid",
            "device": meta.device,
            "device_label": _DEVICE_LABELS.get(meta.device or "", meta.device),
            "user_name": meta.user_name,
            "saved_at": to_iso(meta.saved_at),
            "invalid_at": to_iso(meta.invalid_at) if meta.invalid_at else None,
        }
    now = clock.now()
    apis = []
    for group in (ApiGroup.WEB, ApiGroup.APP):
        state = status.states[group]
        limited = state.resume_at is not None and now < state.resume_at
        apis.append(
            {
                "id": str(group),
                "label": _API_LABELS[group],
                "limited": limited,
                "resume_at": to_iso(state.resume_at) if limited and state.resume_at else None,
            }
        )
    resume_at = status.breaker.resume_at if status.rate_limited else None
    return {
        "credential": credential,
        "rate_limit": {
            "limited": status.rate_limited,
            "resume_at": to_iso(resume_at) if resume_at else None,
            # 按接口分组的熔断：网页版受限时同步暂停，App 版受限时播放取不到直链
            "apis": apis,
        },
    }


def _body(request: PluginRequest) -> dict[str, Any]:
    body = request.body
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise PluginRequestError("请求体必须是 JSON 对象", status=400, code="invalid_request")
    return body


def _text(body: dict[str, Any], key: str, *, max_length: int) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PluginRequestError("请填写必填项", status=400, code="invalid_request")
    if len(value) > max_length:
        raise PluginRequestError("输入内容过长", status=400, code="invalid_request")
    return value


def _plugin_error(error: Cloud115Error) -> PluginRequestError:
    """把业务错误转成插件页能收到的 4xx；5xx 只保留安全的中文说明。"""
    status = error.status_code
    if isinstance(error, RateLimited):
        status = 429
    elif not 400 <= status < 500:
        status = 400
    return PluginRequestError(error.message, status=status, code=error.code)


class Cloud115AdminUi(SettingsAdminUi):
    """source.115 的 admin.ui provider：通用设置交给 SDK，其余是 115 专有接口。"""

    def __init__(
        self,
        service: Cloud115Service,
        settings: SettingsEndpoint,
        life: LifeMonitor,
        clock: Clock,
        *,
        ui_root: Path,
    ) -> None:
        super().__init__(settings, ui_root, status=lambda: _status(service, clock))
        self._service = service
        self._life = life

    @property
    def life(self) -> LifeMonitor:
        return self._life

    def handle(self, request: PluginRequest) -> PluginResponse:
        try:
            return self._handle(request)
        except Cloud115Error as exc:
            raise _plugin_error(exc) from None

    def _handle(self, request: PluginRequest) -> PluginResponse:
        # status、settings、settings/test 由 SDK 的通用骨架处理
        if request.path in {"status", "settings", "settings/test"}:
            return super().handle(request)
        path = request.path.rstrip("/")
        if path == "account" and request.method == "GET":
            return PluginResponse(_account(self._service.account()))
        if path == "devices" and request.method == "GET":
            return PluginResponse(
                [
                    {
                        "id": device.id,
                        "label": device.label,
                        "recommended": device.recommended,
                        "note": device.note,
                    }
                    for device in DEVICES
                ]
            )
        if path == "life" and request.method == "GET":
            status = self._life.status()
            return PluginResponse(
                {
                    "enabled": status.enabled,
                    "interval_seconds": status.interval_seconds,
                    "api": status.api,
                    "last_polled_at": status.last_polled_at,
                    "last_error": status.last_error,
                    "pending": status.pending,
                }
            )
        if path == "qr-login" and request.method == "POST":
            body = _body(request)
            login = self._service.start_qr(
                _text(body, "device", max_length=_DEVICE_MAX_LENGTH).strip()
            )
            return PluginResponse(
                {
                    "session_id": login.session_id,
                    "image_base64": base64.b64encode(login.image_png).decode(),
                    "expires_at": to_iso(login.expires_at),
                },
                status=201,
            )
        if path.startswith("qr-login/"):
            session_id = path[len("qr-login/") :]
            if not session_id:
                raise PluginRequestError("扫码会话不存在", status=404, code="not_found")
            if request.method == "GET":
                return PluginResponse(_qr_progress(self._service.poll_qr(session_id)))
            if request.method == "DELETE":
                self._service.cancel_qr(session_id)
                return PluginResponse(status=204)
            raise PluginRequestError("不支持的请求方法", status=405, code="method_not_allowed")
        if path == "cookie" and request.method == "PUT":
            body = _body(request)
            cookie = _text(body, "cookie", max_length=_COOKIE_MAX_LENGTH)
            return PluginResponse(_account(self._service.save_cookie(cookie)))
        if path == "credential" and request.method == "DELETE":
            self._service.clear_credential()
            return PluginResponse(status=204)
        if path in {"status", "account", "devices", "life", "qr-login", "cookie", "credential"}:
            raise PluginRequestError("不支持的请求方法", status=405, code="method_not_allowed")
        raise PluginRequestError("接口不存在", status=404, code="not_found")

    def settings_updated(self, before: Mapping[str, Any]) -> None:
        """设置保存成功后立即应用到生活事件监听。"""
        self._life.apply_settings()
