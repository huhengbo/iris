"""插件自定义页面：会话令牌、插件接口分发和页面静态文件。

插件页面是不受信任的代码，静态文件带 CSP sandbox 响应头，运行在不透明源里，
读不到管理后台的登录令牌。页面只拿到一个绑定插件、短时效的令牌，调用 /api/plugins/{id}/。
"""

from __future__ import annotations

import json
import logging
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, FastAPI, Request
from iris_plugin_sdk import (
    AdminUiProvider,
    PluginRequest,
    PluginRequestError,
    PluginResponse,
    safe_ui_path,
)
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse, Response

from app.api.schemas import Out
from app.auth.sessions import Unauthorized
from app.auth.tokens import TokenSigner
from app.auth.users import UserRepository
from app.clock import to_iso
from app.deps import AdminUser, AppClock, Plugins, container_of
from app.errors import AppError, NotFound, error_body

PLUGIN_TOKEN_TTL = timedelta(minutes=15)
PLUGIN_BODY_MAX_BYTES = 1024 * 1024
PLUGIN_API_PREFIX = "/api/plugins/"
SDK_DIR = Path(__file__).resolve().parent.parent / "plugins" / "static"
SDK_FILES = {
    "iris-plugin.js": "text/javascript; charset=utf-8",
    "iris-plugin.css": "text/css; charset=utf-8",
}

logger = logging.getLogger(__name__)

admin_router = APIRouter(prefix="/admin/plugins", tags=["admin"])
# 插件接口的请求和响应由各插件自己定义，不进入 OpenAPI 契约
api_router = APIRouter(prefix="/plugins", include_in_schema=False)
page_router = APIRouter(prefix="/plugins", include_in_schema=False)


class PluginDisabled(AppError):
    def __init__(self) -> None:
        super().__init__(409, "plugin_disabled", "插件已停用或未能启动")


class PluginPageLinkOut(Out):
    id: str
    title: str
    url: str


class PluginSessionOut(Out):
    token: str
    expires_at: str
    pages: list[PluginPageLinkOut]


def _signer(request: Request) -> TokenSigner:
    config = container_of(request).config
    return TokenSigner(config.secret_key, "plugin-ui-token")


@admin_router.post("/{plugin_id}/session")
def create_plugin_session(
    plugin_id: str, request: Request, user: AdminUser, plugins: Plugins, clock: AppClock
) -> PluginSessionOut:
    """为管理员打开插件页面签发令牌；令牌只能调用这一个插件的接口。"""
    pages = plugins.pages(plugin_id)
    if not pages:
        raise NotFound("插件不存在或没有页面")
    if plugins.admin_ui(plugin_id) is None:
        raise PluginDisabled()
    expires_at = clock.now() + PLUGIN_TOKEN_TTL
    token = _signer(request).sign(
        {
            "pid": plugin_id,
            "sub": user.id,
            "tv": user.token_version,
            "exp": int(expires_at.timestamp()),
        }
    )
    return PluginSessionOut(
        token=token,
        expires_at=to_iso(expires_at),
        pages=[
            PluginPageLinkOut(
                id=page.id, title=page.title, url=f"/plugins/{plugin_id}/ui/{page.entry}"
            )
            for page in pages
        ],
    )


@api_router.api_route("/{plugin_id}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def call_plugin(plugin_id: str, path: str, request: Request) -> Response:
    """把插件页面的请求交给插件的 admin.ui provider；请求体只接受 JSON。"""
    # 先鉴权再读请求体：令牌只看请求头，未认证的请求（包括分块传输）不占内存
    user_id = await run_in_threadpool(_authenticate, request, plugin_id)
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > PLUGIN_BODY_MAX_BYTES:
        raise AppError(413, "payload_too_large", "请求体不能超过 1 MB")
    chunks = bytearray()
    async for chunk in request.stream():
        if len(chunks) + len(chunk) > PLUGIN_BODY_MAX_BYTES:
            raise AppError(413, "payload_too_large", "请求体不能超过 1 MB")
        chunks.extend(chunk)
    try:
        body = json.loads(chunks) if chunks else None
    except ValueError as exc:
        raise AppError(400, "invalid_json", "请求体必须是 JSON") from exc
    return await run_in_threadpool(_dispatch, request, plugin_id, path, body, user_id)


def _dispatch(request: Request, plugin_id: str, path: str, body: Any, user_id: int) -> Response:
    started = time.perf_counter()
    try:
        response = _handle(request, plugin_id, path, body, user_id)
    except AppError as exc:
        _audit(plugin_id, user_id, request, path, exc.status_code, started)
        raise
    except Exception:
        _audit(plugin_id, user_id, request, path, 500, started)
        raise
    _audit(plugin_id, user_id, request, path, response.status_code, started)
    return response


def _handle(request: Request, plugin_id: str, path: str, body: Any, user_id: int) -> Response:
    plugins = container_of(request).plugins
    provider = plugins.admin_ui(plugin_id)
    if provider is None:
        raise PluginDisabled()
    plugin_request = PluginRequest(
        method=request.method,
        path=path,
        # 同名查询参数只保留最后一个；需要多值参数的插件目前拿不到完整列表
        query=dict(request.query_params),
        body=body,
        user_id=user_id,
    )
    try:
        result = provider.handle(plugin_request)
    except PluginRequestError as exc:
        status = exc.status if 400 <= exc.status < 500 else 400
        return JSONResponse(error_body(exc.code, exc.message), status_code=status)
    except Exception:
        logger.exception("插件 %s 处理 %s /%s 失败", plugin_id, request.method, path)
        return JSONResponse(
            error_body("plugin_failed", "插件处理请求时出错，请查看日志"), status_code=500
        )
    if not isinstance(result, PluginResponse) or not 200 <= result.status < 300:
        logger.error("插件 %s 返回了无效的响应", plugin_id)
        return JSONResponse(error_body("plugin_failed", "插件返回了无效的响应"), status_code=500)
    if result.status == 204:
        return Response(status_code=204)
    try:
        return JSONResponse(result.body, status_code=result.status)
    except (TypeError, ValueError):
        logger.exception("插件 %s 返回的响应体无法序列化为 JSON", plugin_id)
        return JSONResponse(error_body("plugin_failed", "插件返回了无效的响应"), status_code=500)


def _audit(
    plugin_id: str,
    user_id: int,
    request: Request,
    path: str,
    status: int,
    started: float,
) -> None:
    # 审计只记方法、路径和状态，不带查询参数和请求体
    logger.info(
        "插件接口调用 plugin=%s user=%s method=%s path=%s status=%s elapsed_ms=%.1f",
        plugin_id,
        user_id,
        request.method,
        path,
        status,
        (time.perf_counter() - started) * 1000,
    )


def _authenticate(request: Request, plugin_id: str) -> int:
    """校验插件令牌：必须签给这个插件，且签发对象仍是未停用、令牌版本未变的管理员。"""
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Unauthorized("缺少插件令牌")
    payload = _signer(request).verify(token)
    container = container_of(request)
    if (
        payload is None
        or payload.get("pid") != plugin_id
        or payload.get("exp", 0) <= container.clock.now().timestamp()
    ):
        raise Unauthorized("插件令牌无效或已过期")
    with container.database.read_session() as connection:
        user = UserRepository(connection, container.clock).get(int(payload.get("sub", 0)))
    if (
        user is None
        or user.disabled
        or not user.is_admin
        or payload.get("tv") != user.token_version
    ):
        raise Unauthorized("插件令牌无效或已过期")
    return user.id


@page_router.get("/_sdk/{name}")
def plugin_sdk(name: str) -> Response:
    media_type = SDK_FILES.get(name)
    if media_type is None:
        raise NotFound()
    response = FileResponse(SDK_DIR / name, media_type=media_type)
    _static_headers(response)
    return response


@page_router.get("/{plugin_id}/ui/{path:path}")
def plugin_page(plugin_id: str, path: str, request: Request, plugins: Plugins) -> Response:
    """插件页面文件。只托管运行中插件 ui_root 内的文件，并强制 CSP 沙箱。"""
    if not safe_ui_path(path):
        raise NotFound()
    provider = cast(AdminUiProvider | None, plugins.running_provider(plugin_id, "admin.ui"))
    if provider is None:
        raise NotFound()
    root = provider.ui_root.resolve()
    candidate = (root / path).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise NotFound()
    response = FileResponse(candidate)
    _static_headers(response)
    config = container_of(request).config
    ancestors = " ".join(("'self'", *config.cors_origins))
    response.headers["Content-Security-Policy"] = (
        "sandbox allow-scripts allow-forms; "
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
        "base-uri 'none'; form-action 'none'; "
        f"frame-ancestors {ancestors}"
    )
    return response


def _static_headers(response: Response) -> None:
    # 沙箱页面是不透明源，字体等跨源加载的资源需要 CORS；这些文件只是代码，不含数据
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"


def install_plugin_cors(app: FastAPI) -> None:
    """插件接口的跨源规则：沙箱页面的源是 null，只能允许任意源，并且只认 Bearer 令牌。

    必须在 CORSMiddleware 之后安装（成为更外层），否则 null 源的预检会被它拒绝。
    """

    @app.middleware("http")
    async def plugin_cors(request: Request, call_next: Any) -> Any:
        if not request.url.path.startswith(PLUGIN_API_PREFIX):
            return await call_next(request)
        if request.method == "OPTIONS":
            return Response(
                status_code=204,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE",
                    "Access-Control-Allow-Headers": "Authorization, Content-Type",
                    "Access-Control-Max-Age": "600",
                },
            )
        response = await call_next(request)
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response
