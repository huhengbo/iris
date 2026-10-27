from __future__ import annotations

from importlib.metadata import version

from fastapi import APIRouter
from pydantic import BaseModel

from app.deps import Settings

router = APIRouter(prefix="/system", tags=["system"])

PRODUCT = "iris"
# 接口有不兼容的变更时加一，客户端据此提示升级（ADR 0017）
API_VERSION = 1


class SystemInfoOut(BaseModel):
    product: str
    server_id: str
    server_name: str
    version: str
    api_version: int


@router.get("/info")
def system_info(settings: Settings) -> SystemInfoOut:
    """公开接口：客户端添加服务器时用它确认地址指向的是本服务，并取得服务器 ID。"""
    server = settings.server()
    return SystemInfoOut(
        product=PRODUCT,
        server_id=server.id,
        server_name=server.name,
        version=version("iris-backend"),
        api_version=API_VERSION,
    )
