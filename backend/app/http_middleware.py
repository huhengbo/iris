"""HTTP 中间件：接口耗时、慢请求日志和可缓存的 GET 响应的 ETag。"""

from __future__ import annotations

import logging
import time
from hashlib import sha256
from typing import Any

from fastapi import FastAPI
from starlette.concurrency import iterate_in_threadpool
from starlette.responses import Response


def install_api_timing(app: FastAPI) -> None:
    @app.middleware("http")
    async def api_timing(request: Any, call_next: Any) -> Any:
        started = time.perf_counter()
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            elapsed_ms = (time.perf_counter() - started) * 1000
            response.headers["Server-Timing"] = f"app;dur={elapsed_ms:.1f}"
            # 错误响应不打 ETag，否则带着它再请求会拿到 304 而不是错误
            if (
                request.method == "GET"
                and response.status_code == 200
                and _etag_path(request.url.path)
            ):
                body = b"".join([chunk async for chunk in response.body_iterator])
                etag = f'"{sha256(body).hexdigest()[:16]}"'
                response.headers["ETag"] = etag
                if request.headers.get("if-none-match") == etag:
                    return Response(
                        status_code=304,
                        headers={
                            "ETag": etag,
                            "Server-Timing": response.headers["Server-Timing"],
                        },
                    )
                response.body_iterator = iterate_in_threadpool(iter([body]))
            if elapsed_ms >= 250:
                logging.getLogger("app.http").warning(
                    "slow request %s %s status=%s duration_ms=%.1f",
                    request.method,
                    request.url.path,
                    response.status_code,
                    elapsed_ms,
                )
        return response


def _etag_path(path: str) -> bool:
    return (
        path == "/api/home"
        or path == "/api/libraries"
        or path.startswith(
            (
                "/api/libraries/",
                "/api/items/",
                "/api/seasons/",
                "/api/people/",
            )
        )
        or path == "/api/search"
    )
