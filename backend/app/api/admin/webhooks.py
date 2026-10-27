"""Webhook 配置、投递记录与重试接口。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field, HttpUrl

from app.api.schemas import Out
from app.deps import (
    AdminUser,
    AppClock,
    AppDatabase,
    Db,
    ReadDb,
)
from app.ops.webhooks import Webhook, WebhookRepository, deliver_in_background

router = APIRouter(prefix="/admin", tags=["admin"])


class WebhookIn(BaseModel):
    url: HttpUrl
    events: list[str] = Field(default_factory=list, max_length=32)
    secret: str | None = Field(default=None, min_length=16, max_length=256)
    enabled: bool = True


class WebhookOut(Out):
    id: int
    url: str
    # 一直以 JSON 文本返回，客户端按这个格式解析
    events: str
    # 一直以 0/1 返回
    enabled: int
    created_at: str
    updated_at: str

    @classmethod
    def of(cls, hook: Webhook) -> WebhookOut:
        return cls(
            id=hook.id,
            url=hook.url,
            events=json.dumps(hook.events, ensure_ascii=False, separators=(",", ":")),
            enabled=int(hook.enabled),
            created_at=hook.created_at,
            updated_at=hook.updated_at,
        )


class WebhookCreatedOut(Out):
    id: int
    url: str
    events: list[str]
    enabled: bool


class WebhookDeliveryOut(Out):
    id: int
    event: str
    status: str
    attempts: int
    response: str | None
    created_at: str
    delivered_at: str | None


class WebhookRetryOut(Out):
    delivery_id: int


class WebhookTestOut(Out):
    delivery_id: int
    signature: str


@router.patch("/webhooks/{webhook_id}")
def update_webhook(
    webhook_id: int, body: WebhookIn, _: AdminUser, db: Db, clock: AppClock
) -> WebhookOut:
    hook = WebhookRepository(db, clock).update(webhook_id, str(body.url), body.events, body.enabled)
    return WebhookOut.of(hook)


@router.get("/webhooks")
def list_webhooks(_: AdminUser, db: ReadDb, clock: AppClock) -> list[WebhookOut]:
    return [WebhookOut.of(hook) for hook in WebhookRepository(db, clock).all()]


@router.post("/webhooks", status_code=201)
def create_webhook(body: WebhookIn, _: AdminUser, db: Db, clock: AppClock) -> WebhookCreatedOut:
    webhook_id = WebhookRepository(db, clock).create(
        str(body.url), body.events, body.secret, body.enabled
    )
    return WebhookCreatedOut(
        id=webhook_id, url=str(body.url), events=body.events, enabled=body.enabled
    )


@router.delete("/webhooks/{webhook_id}", status_code=204)
def delete_webhook(webhook_id: int, _: AdminUser, db: Db, clock: AppClock) -> Response:
    WebhookRepository(db, clock).delete(webhook_id)
    return Response(status_code=204)


@router.get("/webhooks/{webhook_id}/deliveries")
def webhook_deliveries(
    webhook_id: int, _: AdminUser, db: ReadDb, clock: AppClock
) -> list[WebhookDeliveryOut]:
    return [
        WebhookDeliveryOut.model_validate(item)
        for item in WebhookRepository(db, clock).deliveries(webhook_id)
    ]


@router.post("/webhooks/{webhook_id}/deliveries/{delivery_id}/retry", status_code=202)
def retry_webhook_delivery(
    webhook_id: int,
    delivery_id: int,
    _: AdminUser,
    database: AppDatabase,
    clock: AppClock,
) -> WebhookRetryOut:
    with database.session() as db:
        pending = WebhookRepository(db, clock).prepare_retry(webhook_id, delivery_id)
    deliver_in_background(database, clock, pending, name="webhook-retry", count_attempt=False)
    return WebhookRetryOut(delivery_id=delivery_id)


@router.post("/webhooks/{webhook_id}/test", status_code=202)
def test_webhook(
    webhook_id: int,
    admin_user: AdminUser,
    database: AppDatabase,
    clock: AppClock,
) -> WebhookTestOut:
    with database.session() as db:
        pending = WebhookRepository(db, clock).prepare_test(webhook_id, admin_user.id)
    deliver_in_background(database, clock, pending, name="webhook-delivery", count_attempt=True)
    return WebhookTestOut(delivery_id=pending.delivery_id, signature=pending.signature)
