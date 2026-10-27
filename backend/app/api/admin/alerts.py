"""管理员警报接口。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.api.schemas import Out
from app.deps import (
    AdminUser,
    AppClock,
    AppDatabase,
    Db,
    ReadDb,
)
from app.ops.alerts import Alert, AlertRepository
from app.ops.webhooks import emit as emit_webhook

router = APIRouter(prefix="/admin", tags=["admin"])


class AlertPatch(BaseModel):
    read: bool | None = None
    resolved: bool | None = None


class AlertOut(Out):
    id: int
    kind: str
    level: str
    message: str
    fingerprint: str | None
    created_at: str
    read_at: str | None
    resolved_at: str | None

    @classmethod
    def of(cls, alert: Alert) -> AlertOut:
        return cls.model_validate(alert)


class AlertCreate(BaseModel):
    kind: str = Field(min_length=1, max_length=80)
    level: str = Field(default="warning", pattern="^(info|warning|error)$")
    message: str = Field(min_length=1, max_length=1000)
    fingerprint: str | None = Field(default=None, max_length=200)


@router.post("/alerts", status_code=201)
def create_alert(
    body: AlertCreate,
    _: AdminUser,
    db: Db,
    database: AppDatabase,
    clock: AppClock,
) -> AlertOut:
    alert = AlertRepository(db, clock).create(body.kind, body.level, body.message, body.fingerprint)
    emit_webhook(
        db,
        database,
        clock,
        "alert",
        {"kind": body.kind, "level": body.level, "message": body.message},
    )
    return AlertOut.of(alert)


@router.get("/alerts")
def list_alerts(
    _: AdminUser,
    db: ReadDb,
    clock: AppClock,
    active: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[AlertOut]:
    return [
        AlertOut.of(a) for a in AlertRepository(db, clock).recent(active_only=active, limit=limit)
    ]


@router.post("/alerts/{alert_id}")
def patch_alert(alert_id: int, body: AlertPatch, _: AdminUser, db: Db, clock: AppClock) -> AlertOut:
    alert = AlertRepository(db, clock).update(alert_id, read=body.read, resolved=body.resolved)
    return AlertOut.of(alert)
