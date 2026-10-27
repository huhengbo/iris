"""服务日志查看和下载接口。"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.api.schemas import Out
from app.deps import (
    AdminUser,
)
from app.ops.log_buffer import current as current_log_buffer

router = APIRouter(prefix="/admin", tags=["admin"])


class LogRowOut(Out):
    # 记录产生的时间戳，也用作 after 参数的游标
    id: float
    at: str
    level: str
    module: str
    message: str


@router.get("/logs")
def logs(
    _: AdminUser,
    level: str | None = None,
    after: float | None = None,
) -> list[LogRowOut]:
    return [LogRowOut.model_validate(row) for row in current_log_buffer().rows(level, after)]


@router.get("/logs/download")
def download_logs(_: AdminUser, level: str | None = None) -> PlainTextResponse:
    return PlainTextResponse(
        current_log_buffer().text(level),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="iris.log"'},
    )
