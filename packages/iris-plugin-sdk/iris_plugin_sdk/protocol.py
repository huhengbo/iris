"""宿主与插件共享的传输协议常量。

这里是协议的唯一来源：错误码、帧上限和分帧读写、等待原因、宿主 HTTP 能力白名单。
插件运行时（``iris_plugin_sdk.runtime``）和宿主实现都从这里导入，两边不再各存一份。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import IntEnum
from typing import Any, Literal

# 一行一条 JSON-RPC 消息，单条上限（含换行）；协议第 1 节。
MAX_MESSAGE_BYTES = 64 * 1024 * 1024

# progress 带 until 时的等待原因；宿主同步任务按它记录 waiting_reason。
WaitReason = Literal["rate_limited", "source_busy"]
WAIT_REASONS: frozenset[WaitReason] = frozenset({"rate_limited", "source_busy"})

# 插件通过宿主 http 能力发请求时允许的参数和方法；timeout、重定向等由宿主控制。
HTTP_ALLOWED_KWARGS = frozenset({"params", "headers", "json", "data", "content"})
HTTP_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"})


class ErrorCode(IntEnum):
    """JSON-RPC 标准错误码和本协议的扩展错误码。"""

    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    # 两个方向的业务错误共用 -32000：插件业务异常在插件 → 宿主，宿主拒绝在宿主 → 插件。
    BUSINESS_ERROR = -32000
    NOT_INITIALIZED = -32002


class MessageTooLarge(ValueError):
    """一条 JSON-RPC 消息超过协议上限。"""


class RpcError(Exception):
    """JSON-RPC 层的错误，不经过 codec 的异常还原。"""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = int(code)
        self.message = message


def encode_frame(message: Mapping[str, Any]) -> bytes:
    """编码一帧，并在写入前检查单条消息上限。"""
    raw = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
    if len(raw) > MAX_MESSAGE_BYTES:
        raise MessageTooLarge(f"单条消息超过 {MAX_MESSAGE_BYTES} 字节")
    return raw


def decode_frame(raw: bytes) -> dict[str, Any]:
    """解析一帧；调用方负责按上限截断读取。"""
    if len(raw) > MAX_MESSAGE_BYTES:
        raise MessageTooLarge(f"单条消息超过 {MAX_MESSAGE_BYTES} 字节")
    if not raw.endswith(b"\n"):
        raise MessageTooLarge("消息没有以换行结束")
    try:
        message = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("消息不是合法的 JSON") from exc
    if not isinstance(message, dict):
        raise ValueError("消息必须是 JSON 对象")
    return message
