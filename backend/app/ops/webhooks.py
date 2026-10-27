"""Webhook 事件投递：业务请求只写入待投递记录，网络 I/O 在守护线程执行。"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Any

from app.clock import Clock, to_iso
from app.db import Database
from app.errors import NotFound

MAX_ATTEMPTS = 3
SIGNATURE_HEADER = "X-Iris-Signature"


@dataclass(frozen=True)
class Webhook:
    id: int
    url: str
    events: list[str]
    enabled: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class WebhookDelivery:
    id: int
    event: str
    status: str
    attempts: int
    response: str | None
    created_at: str
    delivered_at: str | None


@dataclass(frozen=True)
class PendingDelivery:
    """已登记、等待发出的投递；signature 是完整的请求头值（sha256=...）。"""

    delivery_id: int
    url: str
    payload: str
    signature: str


def sign(secret: str, payload: str) -> str:
    digest = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _parse_events(text: str | None) -> list[str]:
    try:
        events = json.loads(text or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return events if isinstance(events, list) else []


def _post(url: str, payload: str, signature: str) -> str:
    request = urllib.request.Request(
        url,
        data=payload.encode(),
        headers={"Content-Type": "application/json", SIGNATURE_HEADER: signature},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - 管理员配置的地址
        return f"HTTP {response.status}"


class WebhookRepository:
    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        self._db = connection
        self._clock = clock

    def all(self) -> list[Webhook]:
        rows = self._db.execute(f"{_SELECT} ORDER BY id").fetchall()
        return [_to_webhook(row) for row in rows]

    def require(self, webhook_id: int) -> Webhook:
        row = self._db.execute(f"{_SELECT} WHERE id = ?", (webhook_id,)).fetchone()
        if row is None:
            raise NotFound("Webhook 不存在")
        return _to_webhook(row)

    def create(self, url: str, events: list[str], secret: str | None, enabled: bool) -> int:
        """没有给密钥时生成一个，签名密钥只在创建时可以指定。"""
        now = to_iso(self._clock.now())
        cursor = self._db.execute(
            "INSERT INTO webhook(url,events,secret,enabled,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (url, _compact(events), secret or secrets.token_urlsafe(24), int(enabled), now, now),
        )
        assert cursor.lastrowid is not None
        return int(cursor.lastrowid)

    def update(self, webhook_id: int, url: str, events: list[str], enabled: bool) -> Webhook:
        self.require(webhook_id)
        self._db.execute(
            "UPDATE webhook SET url=?, events=?, enabled=?, updated_at=? WHERE id=?",
            (url, _compact(events), int(enabled), to_iso(self._clock.now()), webhook_id),
        )
        return self.require(webhook_id)

    def delete(self, webhook_id: int) -> None:
        if self._db.execute("DELETE FROM webhook WHERE id = ?", (webhook_id,)).rowcount == 0:
            raise NotFound("Webhook 不存在")

    def deliveries(self, webhook_id: int, limit: int = 50) -> list[WebhookDelivery]:
        self.require(webhook_id)
        rows = self._db.execute(
            "SELECT id,event,status,attempts,response,created_at,delivered_at "
            "FROM webhook_delivery WHERE webhook_id = ? ORDER BY id DESC LIMIT ?",
            (webhook_id, limit),
        ).fetchall()
        return [WebhookDelivery(*row) for row in rows]

    def prepare_retry(self, webhook_id: int, delivery_id: int) -> PendingDelivery:
        """把旧投递重新置为待发送；次数在这里加一，发送结果只更新状态。"""
        delivery = self._db.execute(
            "SELECT payload FROM webhook_delivery WHERE id=? AND webhook_id=?",
            (delivery_id, webhook_id),
        ).fetchone()
        hook = self._db.execute(
            "SELECT url,secret FROM webhook WHERE id=?", (webhook_id,)
        ).fetchone()
        if delivery is None or hook is None:
            raise NotFound("投递记录不存在")
        payload = str(delivery["payload"])
        self._db.execute(
            "UPDATE webhook_delivery SET status='pending', attempts=attempts+1, response=NULL "
            "WHERE id=?",
            (delivery_id,),
        )
        return PendingDelivery(
            delivery_id, str(hook["url"]), payload, sign(str(hook["secret"]), payload)
        )

    def prepare_test(self, webhook_id: int, actor_id: int) -> PendingDelivery:
        """为已启用的 Webhook 登记一条测试投递；次数在发送结束后才计入。"""
        hook = self._db.execute(
            "SELECT url,secret FROM webhook WHERE id = ? AND enabled = 1", (webhook_id,)
        ).fetchone()
        if hook is None:
            raise NotFound("Webhook 不存在或已停用")
        payload = _compact({"event": "test", "server": "iris", "actor_id": actor_id})
        cursor = self._db.execute(
            "INSERT INTO webhook_delivery(webhook_id,event,payload,status,created_at) "
            "VALUES (?,?,?,'pending',?)",
            (webhook_id, "test", payload, to_iso(self._clock.now())),
        )
        assert cursor.lastrowid is not None
        return PendingDelivery(
            int(cursor.lastrowid), str(hook["url"]), payload, sign(str(hook["secret"]), payload)
        )

    def record_result(
        self, delivery_id: int, status: str, response: str | None, *, count_attempt: bool
    ) -> None:
        attempts = "attempts=attempts+1, " if count_attempt else ""
        self._db.execute(
            f"UPDATE webhook_delivery SET status=?, {attempts}response=?, delivered_at=? "
            "WHERE id=?",
            (status, response, to_iso(self._clock.now()), delivery_id),
        )


def deliver_in_background(
    database: Database,
    clock: Clock,
    pending: PendingDelivery,
    *,
    name: str,
    count_attempt: bool,
) -> None:
    """在守护线程里发出一次投递并回写结果；网络失败只记录状态，不影响请求。"""

    def deliver() -> None:
        status, response = "succeeded", None
        try:
            response = _post(pending.url, pending.payload, pending.signature)
        except Exception as error:
            status, response = "failed", str(error)[:500]
        with database.session() as connection:
            WebhookRepository(connection, clock).record_result(
                pending.delivery_id, status, response, count_attempt=count_attempt
            )

    threading.Thread(target=deliver, name=name, daemon=True).start()


_SELECT = "SELECT id,url,events,enabled,created_at,updated_at FROM webhook"


def _to_webhook(row: sqlite3.Row) -> Webhook:
    return Webhook(
        id=row["id"],
        url=row["url"],
        events=_parse_events(row["events"]),
        enabled=bool(row["enabled"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def emit(
    connection: Any,
    database: Database,
    clock: Clock,
    event: str,
    payload: dict[str, Any],
) -> None:
    """在当前事务登记事件并异步投递；Webhook 故障不会影响业务事务。"""
    body = _compact({"event": event, **payload})
    hooks = connection.execute("SELECT id,url,secret FROM webhook WHERE enabled = 1").fetchall()
    delivery_ids: list[int] = []
    for hook in hooks:
        events = _parse_events(hook["events"])
        if event not in events and "*" not in events:
            continue
        cursor = connection.execute(
            "INSERT INTO webhook_delivery(webhook_id,event,payload,status,created_at) "
            "VALUES (?,?,?,?,?)",
            (hook["id"], event, body, "pending", to_iso(clock.now())),
        )
        if cursor.lastrowid is not None:
            delivery_ids.append(int(cursor.lastrowid))
    if delivery_ids:
        threading.Thread(
            target=_deliver_many,
            args=(database, clock, delivery_ids),
            name="webhook-delivery",
            daemon=True,
        ).start()


def _deliver_many(database: Database, clock: Clock, delivery_ids: list[int]) -> None:
    # 等待业务事务提交，避免独立连接在 SQLite 上与请求事务竞争写锁。
    time.sleep(0.05)
    for delivery_id in delivery_ids:
        _deliver_one(database, clock, delivery_id)


def _deliver_one(database: Database, clock: Clock, delivery_id: int) -> None:
    for attempt in range(MAX_ATTEMPTS):
        with database.session() as connection:
            row = connection.execute(
                "SELECT d.payload,d.attempts,w.url,w.secret FROM webhook_delivery d "
                "JOIN webhook w ON w.id=d.webhook_id WHERE d.id=?",
                (delivery_id,),
            ).fetchone()
            if row is None:
                return
            payload = str(row["payload"])
            signature = sign(str(row["secret"]), payload)
            connection.execute(
                "UPDATE webhook_delivery SET attempts=attempts+1 WHERE id=?",
                (delivery_id,),
            )
        try:
            detail = _post(str(row["url"]), payload, signature)
            with database.session() as connection:
                connection.execute(
                    "UPDATE webhook_delivery SET status='succeeded',response=?,"
                    "delivered_at=? WHERE id=?",
                    (detail, to_iso(clock.now()), delivery_id),
                )
            return
        except Exception as error:
            if attempt + 1 == MAX_ATTEMPTS:
                with database.session() as connection:
                    connection.execute(
                        "UPDATE webhook_delivery SET status='failed',response=?,"
                        "delivered_at=? WHERE id=?",
                        (str(error)[:500], to_iso(clock.now()), delivery_id),
                    )
                return
            time.sleep(0.5 * (2**attempt))
