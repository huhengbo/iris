"""RPC 载荷的编解码：dataclass ↔ JSON 对象，bytes ↔ base64，异常 ↔ 错误对象。

JSON 本身没有类型信息，所以 dataclass、tuple、frozenset、Path、datetime 和 bytes 都用
`$type` / `$bytes` 这类带标记的对象表示。只认 SDK 里登记过的类型，未知类型解码时报错；
异常里不包含堆栈。
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from iris_plugin_sdk import types

_DATACLASSES: dict[str, type[Any]] = {
    cls.__name__: cls
    for cls in (
        types.PluginPage,
        types.PluginManifest,
        types.PluginHttpResponse,
        types.HostInfo,
        types.PluginRequest,
        types.PluginResponse,
        types.MediaContext,
        types.MetadataSearchRequest,
        types.MetadataCandidate,
        types.MetadataCredit,
        types.MetadataRecord,
        types.MetadataEpisode,
        types.MetadataSeason,
        types.MetadataPerson,
        types.RemoteImage,
        types.ImageLookupRequest,
        types.SubtitleSearchRequest,
        types.SubtitleCandidate,
        types.SubtitleDownload,
        types.ResourceEntry,
        types.ResourceListing,
        types.PlaybackTarget,
        types.SourceStatus,
        types.ResourceConfigField,
    )
}
_EXCEPTIONS: dict[str, type[Exception]] = {
    cls.__name__: cls
    for cls in (
        types.PluginManifestError,
        types.PluginSdkIncompatibleError,
        types.InvalidSourceConfig,
        types.HostCallError,
        types.PluginHttpError,
        types.PluginRequestError,
        types.MetadataError,
        types.MetadataNotFound,
        types.MetadataUnavailable,
        types.SubtitleUnavailable,
        types.ResourceUnavailable,
        types.ResourceNotFound,
        types.ResourceRateLimited,
    )
}


class CodecError(ValueError):
    """载荷不符合 SDK 的编解码契约。"""


def encode(value: Any) -> Any:
    """把 SDK 值转成可以 json.dumps 的对象。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return {"$bytes": base64.b64encode(value).decode("ascii")}
    if isinstance(value, Path):
        return {"$path": value.as_posix()}
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if is_dataclass(value) and not isinstance(value, type):
        name = type(value).__name__
        if name not in _DATACLASSES:
            raise CodecError(f"没有登记的 dataclass：{name}")
        return {
            "$type": name,
            "fields": {item.name: encode(getattr(value, item.name)) for item in fields(value)},
        }
    if isinstance(value, tuple):
        return {"$tuple": [encode(item) for item in value]}
    if isinstance(value, frozenset):
        return {"$frozenset": [encode(item) for item in value]}
    if isinstance(value, set):
        return {"$set": [encode(item) for item in value]}
    if isinstance(value, Mapping):
        encoded: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CodecError("JSON 对象的键必须是字符串")
            encoded[key] = encode(item)
        return encoded
    if isinstance(value, list):
        return [encode(item) for item in value]
    raise CodecError(f"不能编码的类型：{type(value).__name__}")


def decode(value: Any) -> Any:
    """把 encode() 的结果还原成 SDK 值。"""
    if isinstance(value, list):
        return [decode(item) for item in value]
    if not isinstance(value, dict):
        return value
    marker = value.get("$type")
    if marker is not None:
        raw = value.get("fields")
        if not isinstance(raw, dict):
            raise CodecError(f"{marker} 缺少 fields")
        cls = _DATACLASSES.get(marker)
        if cls is None:
            raise CodecError(f"没有登记的 dataclass：{marker}")
        decoded = {key: decode(item) for key, item in raw.items()}
        try:
            return cls(**decoded)
        except CodecError:
            raise
        except (TypeError, ValueError) as exc:
            raise CodecError(f"{marker} 字段不合法：{exc}") from exc
    if "$bytes" in value:
        try:
            return base64.b64decode(_string(value, "$bytes"), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise CodecError("bytes 不是合法的 base64") from exc
    if "$path" in value:
        return Path(_string(value, "$path"))
    if "$datetime" in value:
        try:
            return datetime.fromisoformat(_string(value, "$datetime"))
        except ValueError as exc:
            raise CodecError("datetime 格式不合法") from exc
    if "$date" in value:
        try:
            return date.fromisoformat(_string(value, "$date"))
        except ValueError as exc:
            raise CodecError("date 格式不合法") from exc
    if "$tuple" in value:
        return tuple(decode(item) for item in _list(value, "$tuple"))
    if "$frozenset" in value:
        return frozenset(decode(item) for item in _list(value, "$frozenset"))
    if "$set" in value:
        return set(decode(item) for item in _list(value, "$set"))
    return {str(key): decode(item) for key, item in value.items()}


def encode_exception(error: BaseException) -> dict[str, Any]:
    """把异常转成宿主可以还原的错误对象；不包含堆栈。"""
    payload: dict[str, Any] = {
        "type": type(error).__name__,
        "message": getattr(error, "message", str(error)),
    }
    for name in ("code", "status", "status_code", "retry_after", "fatal"):
        value = getattr(error, name, None)
        if value is not None:
            payload[name] = value
    return payload


def is_protocol_exception(error: BaseException) -> bool:
    """宿主能按类型还原的异常；其他异常到宿主只剩「插件内部错误」。"""
    return type(error).__name__ in _EXCEPTIONS


def decode_exception(payload: Any) -> Exception:
    """把错误对象还原成 SDK 异常；未知类型一律当成通用不可用异常。"""
    if not isinstance(payload, dict):
        return types.ResourceUnavailable("插件返回了无法识别的错误")
    name = payload.get("type")
    message = payload.get("message")
    if not isinstance(message, str) or not message:
        message = "插件内部错误"
    cls = _EXCEPTIONS.get(name) if isinstance(name, str) else None
    if cls is None:
        # 未知异常不向调用方暴露细节，只保留一句通用说明。
        return types.ResourceUnavailable("插件内部错误")
    try:
        if cls is types.PluginRequestError:
            return cls(
                message,
                status=_int(payload.get("status"), 400),
                code=_str(payload.get("code"), "plugin_error"),
            )
        if cls is types.PluginHttpError:
            return cls(message, status_code=_int(payload.get("status_code"), 502))
        if cls is types.HostCallError:
            return cls(message, code=_str(payload.get("code"), "host_call_failed"))
        if cls is types.ResourceUnavailable:
            return cls(
                message,
                fatal=bool(payload.get("fatal")),
                status_code=_int(payload.get("status_code"), 503),
                code=_str(payload.get("code"), "resource_unavailable"),
            )
        if cls is types.ResourceRateLimited:
            return cls(
                message,
                _optional_int(payload.get("retry_after")),
                _int(payload.get("status_code"), 429),
            )
        if cls is types.SubtitleUnavailable:
            return cls(message, _optional_int(payload.get("retry_after")))
        return cls(message)
    except (TypeError, ValueError):
        return types.ResourceUnavailable("插件返回了无法识别的错误")


def dumps(value: Any) -> str:
    return json.dumps(encode(value), ensure_ascii=False, separators=(",", ":"))


def loads(raw: str | bytes) -> Any:
    return decode(json.loads(raw))


def _string(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise CodecError(f"{key} 必须是字符串")
    return item


def _list(value: dict[str, Any], key: str) -> list[Any]:
    item = value.get(key)
    if not isinstance(item, list):
        raise CodecError(f"{key} 必须是数组")
    return item


def _int(value: Any, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _str(value: Any, default: str) -> str:
    return value if isinstance(value, str) and value else default
