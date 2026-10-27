"""各接口共用的响应结构。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ImageOut(Out):
    """一档图片：宽度和相对服务器根的地址（ADR 0017）。"""

    width: int
    url: str
