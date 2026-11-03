from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    """业务错误。所有接口错误统一返回 {"error": {"code", "message"}}。"""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class NotFound(AppError):
    def __init__(self, message: str = "资源不存在") -> None:
        super().__init__(404, "not_found", message)


class ErrorBody(BaseModel):
    """错误内容；details 仅校验错误（422）携带，为 [{loc, type}] 列表。"""

    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    """所有 4xx/5xx 的统一响应体，仅用于生成 OpenAPI 文档。"""

    error: ErrorBody


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"error": error}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(error_body(exc.code, exc.message), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # 只返回字段位置和类型，不回显输入值（可能含密码）
        details = [
            {"loc": list(error.get("loc", ())), "type": error.get("type")} for error in exc.errors()
        ]
        return JSONResponse(
            error_body("validation_error", "请求参数不合法", details), status_code=422
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "not_found" if exc.status_code == 404 else f"http_{exc.status_code}"
        return JSONResponse(
            error_body(code, str(exc.detail)),
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
        )


_ERROR_REF = {"$ref": "#/components/schemas/ErrorResponse"}
_HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def install_openapi_errors(app: FastAPI) -> None:
    """让 OpenAPI 的错误响应与运行时一致：422 改用统一结构，其余 4xx/5xx 用范围声明。

    只改文档，不改运行时行为。
    """

    def build() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            openapi_version=app.openapi_version,
            summary=app.summary,
            description=app.description,
            routes=app.routes,
            tags=app.openapi_tags,
            servers=app.servers,
        )
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        for name in ("HTTPValidationError", "ValidationError"):
            components.pop(name, None)
        error_schemas = ErrorResponse.model_json_schema(ref_template="#/components/schemas/{model}")
        components.update(error_schemas.pop("$defs", {}))
        components["ErrorResponse"] = error_schemas
        content = {"application/json": {"schema": _ERROR_REF}}
        for item in schema.get("paths", {}).values():
            for method, operation in item.items():
                if method not in _HTTP_METHODS:
                    continue
                responses = operation.setdefault("responses", {})
                if operation.pop("x-sse", False):
                    # 流式接口：FastAPI 把 responses 里的模型登记成 JSON，这里改回 SSE 的媒体类型
                    content_ok = responses.get("200", {}).get("content", {})
                    if "application/json" in content_ok:
                        content_ok["text/event-stream"] = content_ok.pop("application/json")
                if "422" in responses:
                    responses["422"] = {"description": "请求参数不合法", "content": content}
                responses.setdefault("4XX", {"description": "客户端错误", "content": content})
                responses.setdefault("5XX", {"description": "服务端错误", "content": content})
        app.openapi_schema = schema
        return schema

    app.openapi = build  # type: ignore[method-assign]
