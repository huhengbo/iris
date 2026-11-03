from __future__ import annotations

from importlib.metadata import version

from fastapi import APIRouter
from pydantic import BaseModel

from app.deps import Ai, Settings

router = APIRouter(prefix="/system", tags=["system"])

PRODUCT = "iris"
# 接口有不兼容的变更时加一，客户端据此提示升级（ADR 0017）
API_VERSION = 1


class AiAvailabilityOut(BaseModel):
    """AI 助手哪些入口可用；没有配置对话模型时全部为 false，客户端不显示任何 AI 入口。"""

    chat: bool
    ask_search: bool
    semantic: bool


class SystemInfoOut(BaseModel):
    product: str
    server_id: str
    server_name: str
    version: str
    api_version: int
    ai: AiAvailabilityOut


@router.get("/info")
def system_info(settings: Settings, ai: Ai) -> SystemInfoOut:
    """公开接口：客户端添加服务器时用它确认地址指向的是本服务，并取得服务器 ID。"""
    server = settings.server()
    available = ai.availability()
    return SystemInfoOut(
        product=PRODUCT,
        server_id=server.id,
        server_name=server.name,
        version=version("iris-backend"),
        api_version=API_VERSION,
        ai=AiAvailabilityOut(
            chat=available.chat, ask_search=available.ask_search, semantic=available.semantic
        ),
    )
