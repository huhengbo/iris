from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.deps import Images
from app.errors import NotFound

router = APIRouter(prefix="/images", tags=["images"])

# 文件名包含内容哈希，内容变了地址也变，可以永久缓存
IMAGE_CACHE_CONTROL = "public, max-age=31536000, immutable"


@router.get("/{name}")
def get_image(name: str, store: Images) -> FileResponse:
    """不需要登录：<img> 不能带 Bearer 令牌，图片都来自 TMDB 的公开内容，文件名也无法枚举。"""
    path = store.path(name)
    if path is None:
        raise NotFound("图片不存在")
    media_type = "image/png" if path.suffix == ".png" else "image/jpeg"
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": IMAGE_CACHE_CONTROL})
