"""Emby 兼容接口：路由、鉴权、ID 编码和 DTO（ADR 0024）。"""

from __future__ import annotations

from app.emby.middleware import install_emby_rewrite

__all__ = ["install_emby_rewrite"]
