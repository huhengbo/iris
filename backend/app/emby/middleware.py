"""把根路径下的 Emby 大写前缀改写到 /emby/ 下的统一小写路由（ADR 0024）。"""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send

# 根路径只接管这些 Emby 首段；Flutter 的 /item、/libraries、/person 等页面路由不在此列
EMBY_ROOT_PREFIXES = frozenset(
    {
        "users",
        "items",
        "videos",
        "system",
        "sessions",
        "shows",
        "persons",
        "genres",
        "library",
        "displaypreferences",
        "branding",
        "userviews",
        "playback",
        "plugins",
        "packages",
        "studios",
        "years",
        "artists",
        "musicgenres",
        "playlists",
        "collections",
        "livetv",
        "localization",
        "notifications",
        "devices",
        "scheduledtasks",
        "startup",
        "auth",
    }
)


class EmbyPathMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path = scope.get("path", "")
            rewritten = rewrite_path(path)
            if rewritten != path:
                scope = dict(scope)
                scope["path"] = rewritten
                scope["raw_path"] = rewritten.encode("utf-8")
        await self.app(scope, receive, send)


def rewrite_path(path: str) -> str:
    lowered = path.lower()
    # 播放地址不带 /emby 前缀；有的客户端仍会再拼一次，变成 /emby/emby/...
    while lowered.startswith("/emby/emby/"):
        lowered = lowered[5:]
    if lowered == "/emby" or lowered.startswith("/emby/"):
        return lowered
    segments = path.strip("/").split("/", 1)
    first = segments[0].lower()
    if first in EMBY_ROOT_PREFIXES:
        # /library/3 是 Flutter 的详情页，只有 /Library/MediaFolders 这类 Emby 路径才改写
        if first == "library" and len(segments) == 2 and segments[1].split("/", 1)[0].isdigit():
            return path
        # /plugins/{id}/ui/... 是 Iris 的插件页面，只有 Emby 的 /Plugins 列表才改写
        if first == "plugins" and len(segments) == 2 and segments[1]:
            return path
        return "/emby" + lowered
    return path


def is_emby_request(headers: Mapping[str, str]) -> bool:
    """带 Emby 客户端标识的请求：没有对应路由时应得到 JSON 404，不能落到 Web 页面的 HTML。"""
    if any(name.lower().startswith("x-emby-") for name in headers):
        return True
    return headers.get("authorization", "").lower().startswith(("mediabrowser", "emby"))


def install_emby_rewrite(app: FastAPI) -> None:
    app.add_middleware(EmbyPathMiddleware)
