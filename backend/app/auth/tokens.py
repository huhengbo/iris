from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from typing import Any


class TokenSigner:
    """HMAC-SHA256 签名的紧凑令牌：base64url(JSON 载荷).base64url(签名)。

    每种用途（登录、播放）从应用密钥派生独立的子密钥，一种令牌不能冒充另一种。
    过期时间由调用方结合时钟校验。
    """

    def __init__(self, secret_key: str, purpose: str) -> None:
        self._key = hmac.new(secret_key.encode(), purpose.encode(), hashlib.sha256).digest()

    def sign(self, payload: dict[str, Any]) -> str:
        body = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        return f"{body}.{self._signature(body)}"

    def verify(self, token: str) -> dict[str, Any] | None:
        body, sep, signature = token.partition(".")
        if not sep or not hmac.compare_digest(signature, self._signature(body)):
            return None
        try:
            payload = json.loads(_b64decode(body))
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None

    def _signature(self, body: str) -> str:
        return _b64encode(hmac.new(self._key, body.encode(), hashlib.sha256).digest())


def new_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def hash_opaque_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
