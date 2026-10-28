from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

from app.emby.middleware import is_emby_request
from app.errors import NotFound

# 这些前缀属于后端，不能回退到前端页面，否则错误的 API 路径会得到 200 的 HTML
BACKEND_PREFIXES = ("api/", "play/", "plugins/", "emby/")
# Flutter Web 的产物（包括裁剪过的图标字体和资源清单）文件名都不带哈希，
# 缓存任何一个都会让新代码配上旧资源，例如图标缺失。统一按 ETag 每次校验，未变化时只回 304。
WEB_CACHE_CONTROL = "no-cache"
WEB_FONT_CACHE_CONTROL = "public, max-age=31536000, immutable"
WEB_FONT_NAME = "NotoSansCJKsc-Regular.v2.otf"


def install_web_client(app: FastAPI, static_dir: Path) -> None:
    """托管 Flutter Web 构建产物；未知路径回退到 index.html，交给前端路由处理。"""
    root = static_dir.resolve()
    index = root / "index.html"
    # 只借用它的条件请求处理（If-None-Match / If-Modified-Since → 304），路径解析仍由下面负责
    files = StaticFiles(directory=root, check_dir=False)

    @app.get("/{path:path}", include_in_schema=False)
    def web_client(path: str, request: Request) -> Response:
        if (
            path in ("api", "emby")
            or path.startswith(BACKEND_PREFIXES)
            or is_emby_request(request.headers)
        ):
            raise NotFound()
        if not index.is_file():
            raise NotFound("前端未构建")
        candidate = (root / path).resolve()
        if not (path and candidate.is_file() and candidate.is_relative_to(root)):
            candidate = index
        response = files.file_response(candidate, candidate.stat(), request.scope)
        response.headers["Cache-Control"] = (
            WEB_FONT_CACHE_CONTROL if candidate.name == WEB_FONT_NAME else WEB_CACHE_CONTROL
        )
        if candidate.name.endswith(".otf"):
            response.headers["Content-Type"] = "font/otf"
        return response
