"""宿主端的 JSON-RPC 2.0 NDJSON 客户端。

一个客户端绑定一个插件子进程的标准输入和输出：请求按 ID 多路复用，超时按方法配置，
插件反向调用宿主时走同一连接。传输层错误和进程退出不向调用方泄露插件堆栈，而是按方法
还原成对应的 SDK 不可用异常。
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, BinaryIO

import httpx2
from iris_plugin_sdk import (
    CodecError,
    HostCallError,
    MetadataUnavailable,
    PluginHttpResponse,
    PluginRequestError,
    ResourceUnavailable,
    SubtitleUnavailable,
    decode,
    decode_exception,
    encode,
    encode_exception,
)
from iris_plugin_sdk.protocol import (
    MAX_MESSAGE_BYTES,
    WAIT_REASONS,
    ErrorCode,
    MessageTooLarge,
    RpcError,
    WaitReason,
    decode_frame,
    encode_frame,
)

from app.plugins.host import PluginHostError, PluginHttp

logger = logging.getLogger(__name__)

# 宿主代插件发起请求的并发上限；插件发来的 resource.changed 由单线程按顺序处理。
HOST_CALL_THREADS = 8
# 协议第 13 节的超时；scan 是空闲超时，由 progress 通知续期。
SCAN_IDLE_TIMEOUT = 120.0
METHOD_TIMEOUTS: dict[str, float] = {
    "initialize": 30.0,
    "resource.browse": 60.0,
    "resource.scan": SCAN_IDLE_TIMEOUT,
    "resource.read": 60.0,
    "resource.resolve": 15.0,
    "resource.describe": 5.0,
    "resource.validate_config": 10.0,
    "resource.delete": 60.0,
    "resource.check": 15.0,
    "resource.status": 15.0,
    "provider.unavailable_reason": 5.0,
    "metadata.search": 15.0,
    "metadata.find": 15.0,
    "metadata.details": 15.0,
    "metadata.season": 15.0,
    "metadata.page_url": 5.0,
    "metadata.person": 15.0,
    "image.images": 15.0,
    "image.fetch": 60.0,
    "metadata.image": 60.0,
    "subtitle.search": 15.0,
    "subtitle.download": 60.0,
    "admin.request": 15.0,
    "plugin.flush": 10.0,
    "plugin.shutdown": 10.0,
}

_LOG_METHODS = {
    "debug": logging.Logger.debug,
    "info": logging.Logger.info,
    "warning": logging.Logger.warning,
    "error": logging.Logger.error,
}


class RpcMethodNotFound(RpcError):
    """插件没有实现这个方法（-32601）。"""

    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.METHOD_NOT_FOUND, message)


class RpcTimeoutError(TimeoutError):
    """宿主等待插件响应超时。"""

    def __init__(self, plugin_id: str, method: str, timeout: float) -> None:
        super().__init__(f"插件 {plugin_id} 调用 {method} 超过 {timeout:g} 秒")
        self.plugin_id = plugin_id
        self.method = method
        self.timeout = timeout


class PluginUnavailable(Exception):
    """插件进程不可用，且该方法没有更具体的 SDK 异常。"""


def unavailable_exception(method: str, message: str) -> Exception:
    """按调用方法给出对应的不可用异常；进程退出和连接中断都走这里。"""
    if method.startswith("resource."):
        return ResourceUnavailable(message)
    if method.startswith(("metadata.", "image.")):
        return MetadataUnavailable(message)
    if method.startswith("subtitle."):
        return SubtitleUnavailable(message)
    if method == "admin.request":
        return PluginRequestError(message, status=503, code="plugin_unavailable")
    return PluginUnavailable(message)


@dataclass
class _Pending:
    method: str
    future: threading.Event = field(default_factory=threading.Event)
    deadline: float = 0.0
    result: Any = None
    error: BaseException | None = None
    waiter: Callable[[datetime, WaitReason], None] | None = None
    progress: list[tuple[datetime, WaitReason]] = field(default_factory=list)
    progress_event: threading.Event = field(default_factory=threading.Event)


class PluginRpcClient:
    """一个插件连接的客户端；线程安全，可在多个宿主线程里并发调用。"""

    def __init__(
        self,
        plugin_id: str,
        stdin: BinaryIO,
        stdout: BinaryIO,
        *,
        http: PluginHttp,
        on_resource_changed: Callable[[int, list[str]], None],
        timeouts: Mapping[str, float] | None = None,
        log: logging.Logger = logger,
        on_fatal: Callable[[str], None] | None = None,
    ) -> None:
        self.plugin_id = plugin_id
        self._stdin = stdin
        self._stdout = stdout
        self._http = http
        self._on_resource_changed = on_resource_changed
        self._timeouts = dict(METHOD_TIMEOUTS)
        if timeouts is not None:
            self._timeouts.update(timeouts)
        self._log = log
        self._on_fatal = on_fatal
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[int | str, _Pending] = {}
        self._next_id = 0
        self._closed = threading.Event()
        self._close_reason = "插件连接已关闭"
        self._host_calls = ThreadPoolExecutor(
            max_workers=HOST_CALL_THREADS, thread_name_prefix=f"plugin-host-call-{plugin_id}"
        )
        self._notifications = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"plugin-host-notify-{plugin_id}"
        )
        self._reader = threading.Thread(
            target=self._read_loop, name=f"plugin-rpc-{plugin_id}", daemon=True
        )
        self._reader.start()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def call(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        timeout: float | None = None,
        waiter: Callable[[datetime, WaitReason], None] | None = None,
    ) -> Any:
        """调用插件并等待响应；waiter 接收插件发来的 progress 暂停通知。"""
        if not isinstance(method, str) or not method:
            raise ValueError("RPC 方法名必须是非空字符串")
        if timeout is None:
            timeout = self._timeouts.get(method)
        if timeout is None:
            raise ValueError(f"没有为 RPC 方法配置超时：{method}")
        pending = _Pending(method=method, deadline=time.monotonic() + timeout, waiter=waiter)
        try:
            raw = encode_frame(
                {
                    "jsonrpc": "2.0",
                    "id": self._register(pending),
                    "method": method,
                    "params": encode(dict(params)),
                }
            )
            self._write(raw)
        except BaseException:
            self._forget(pending)
            raise
        return self._wait(pending, timeout)

    def notify(self, method: str, params: Mapping[str, Any]) -> None:
        raw = encode_frame({"jsonrpc": "2.0", "method": method, "params": encode(dict(params))})
        self._write(raw)

    def close(self) -> None:
        """主动关闭客户端；进程由 supervisor 负责终止。"""
        if self._closed.is_set():
            # 读线程可能已经因为进程退出把连接标记为关闭；HTTP 连接池仍由这里负责释放
            self._http.close()
            return
        self._close_reason = "插件连接已关闭"
        self._closed.set()
        self._http.close()
        self._fail_pending()
        self._host_calls.shutdown(wait=False, cancel_futures=True)
        self._notifications.shutdown(wait=False, cancel_futures=True)

    # ---- 请求 ID 和等待 ----

    def _register(self, pending: _Pending) -> int:
        with self._pending_lock:
            if self._closed.is_set():
                raise unavailable_exception(pending.method, self._close_reason)
            self._next_id += 1
            request_id = self._next_id
            self._pending[request_id] = pending
            return request_id

    def _wait(self, pending: _Pending, timeout: float) -> Any:
        while True:
            if pending.progress_event.is_set():
                pending.progress_event.clear()
                with self._pending_lock:
                    events, pending.progress = pending.progress, []
                for when, reason in events:
                    assert pending.waiter is not None
                    pending.waiter(when, reason)
                # 暂停发生在同步线程里，期间插件不会发响应；给 scan 的空闲超时重新计时
                pending.deadline = max(pending.deadline, time.monotonic() + timeout)
                continue
            remaining = pending.deadline - time.monotonic()
            if remaining <= 0:
                self._forget(pending)
                raise RpcTimeoutError(self.plugin_id, pending.method, timeout)
            if pending.future.wait(min(remaining, 0.1)):
                if pending.progress_event.is_set():
                    continue
                if pending.error is not None:
                    raise pending.error
                return pending.result

    def _forget(self, pending: _Pending) -> None:
        with self._pending_lock:
            for request_id, item in list(self._pending.items()):
                if item is pending:
                    del self._pending[request_id]

    def _fail_pending(self) -> None:
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for item in pending:
            item.error = unavailable_exception(item.method, self._close_reason)
            item.future.set()

    def _touch_scan(self) -> None:
        """progress 是 scan 的心跳：续期所有等待中的 scan 调用。"""
        timeout = self._timeouts.get("resource.scan", SCAN_IDLE_TIMEOUT)
        deadline = time.monotonic() + timeout
        with self._pending_lock:
            for item in self._pending.values():
                if item.method == "resource.scan":
                    item.deadline = deadline

    # ---- 写 ----

    def _write(self, raw: bytes) -> None:
        if self._closed.is_set():
            raise PluginUnavailable(self._close_reason)
        try:
            with self._write_lock:
                self._stdin.write(raw)
                self._stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            self._fatal(f"写入插件失败：{exc}")
            raise PluginUnavailable("插件连接已断开") from exc

    def _send(self, message: Mapping[str, Any]) -> None:
        try:
            raw = encode_frame(message)
        except (CodecError, MessageTooLarge) as exc:
            self._log.error("插件 %s 的响应无法编码：%s", self.plugin_id, exc)
            if "id" in message and message["id"] is not None:
                self._send_error(message["id"], ErrorCode.INTERNAL_ERROR, "宿主响应编码失败")
            return
        try:
            with self._write_lock:
                self._stdin.write(raw)
                self._stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            self._fatal(f"写入插件失败：{exc}")
            raise PluginUnavailable("插件连接已断开") from exc

    # ---- 读 ----

    def _read_loop(self) -> None:
        try:
            while not self._closed.is_set():
                try:
                    raw = self._stdout.readline(MAX_MESSAGE_BYTES + 1)
                    if not raw:
                        break
                    if len(raw) > MAX_MESSAGE_BYTES:
                        self._fatal("插件发送的消息超过 64 MB")
                        return
                    try:
                        message = decode_frame(raw)
                    except ValueError as exc:
                        self._fatal(f"插件消息不合法：{exc}")
                        return
                    if message.get("jsonrpc") != "2.0":
                        self._fatal("插件消息不是 JSON-RPC 2.0")
                        return
                    if "method" in message:
                        self._handle_request(message)
                    else:
                        self._handle_response(message)
                except Exception as exc:
                    # 单条消息处理之外的问题不能只结束读线程：通知 supervisor 杀掉进程并重启
                    self._fatal(f"处理插件消息时发生意外错误：{exc}")
                    return
        finally:
            if not self._closed.is_set():
                self._close_reason = "插件进程已退出"
                self._closed.set()
                self._fail_pending()

    def _handle_response(self, message: Mapping[str, Any]) -> None:
        request_id = message.get("id")
        if not isinstance(request_id, (int, str)) or isinstance(request_id, bool):
            self._fatal("插件响应缺少合法的 ID")
            return
        with self._pending_lock:
            pending = self._pending.pop(request_id, None)
        if pending is None:
            self._log.debug("插件 %s 返回了未知请求 %s 的响应", self.plugin_id, request_id)
            return
        if "error" in message:
            error = message["error"]
            if not isinstance(error, dict):
                pending.error = unavailable_exception(pending.method, "插件返回了无效的错误对象")
            else:
                code = error.get("code")
                text = error.get("message")
                if not isinstance(code, int) or isinstance(code, bool):
                    pending.error = unavailable_exception(pending.method, "插件返回了无效的错误码")
                elif code == ErrorCode.METHOD_NOT_FOUND:
                    pending.error = RpcMethodNotFound(
                        text if isinstance(text, str) else "方法不存在"
                    )
                elif "data" in error:
                    pending.error = decode_exception(error["data"])
                else:
                    pending.error = unavailable_exception(
                        pending.method,
                        text if isinstance(text, str) and text else "插件内部错误",
                    )
            pending.future.set()
            return
        if "result" not in message:
            pending.error = unavailable_exception(pending.method, "插件响应缺少 result")
            pending.future.set()
            return
        try:
            pending.result = decode(message["result"])
        except CodecError as exc:
            pending.error = unavailable_exception(
                pending.method, f"插件返回了无法识别的结果：{exc}"
            )
        pending.future.set()

    def _handle_request(self, message: Mapping[str, Any]) -> None:
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params", {})
        if not isinstance(method, str) or not method:
            self._reject(request_id, ErrorCode.INVALID_REQUEST, "方法必须是字符串")
            return
        if not isinstance(params, dict):
            self._reject(request_id, ErrorCode.INVALID_PARAMS, "params 必须是对象")
            return
        try:
            decoded = decode(params)
        except CodecError as exc:
            self._reject(request_id, ErrorCode.INVALID_PARAMS, str(exc))
            return
        if request_id is None:
            if method in ("log", "progress"):
                # 日志和心跳必须按顺序、立即处理，不能占住读线程太久。
                try:
                    self._dispatch_host(method, decoded)
                except (ValueError, TypeError) as exc:
                    self._drop_notification(method, exc)
                return
            # resource.changed 会启动同步等耗时工作；单线程按到达顺序处理，避免占住读线程。
            self._notifications.submit(self._run_notification, method, decoded)
            return
        if not isinstance(request_id, (int, str)) or isinstance(request_id, bool):
            self._send_error(None, ErrorCode.INVALID_REQUEST, "请求 ID 必须是整数或字符串")
            return
        if method == "progress":
            self._reject(request_id, ErrorCode.INVALID_REQUEST, "progress 必须是通知")
            return
        self._host_calls.submit(self._run_host_request, request_id, method, decoded)

    def _reject(self, request_id: Any, code: int, message: str) -> None:
        """通知没有响应可回，只记日志丢弃；请求按 JSON-RPC 返回错误。"""
        if request_id is None:
            self._log.error("插件 %s 的通知不合法，已丢弃：%s", self.plugin_id, message)
        else:
            self._send_error(request_id, code, message)

    def _drop_notification(self, method: str, error: BaseException) -> None:
        self._log.error("插件 %s 的 %s 通知参数不合法，已丢弃：%s", self.plugin_id, method, error)

    # ---- 处理插件 → 宿主 ----

    def _run_host_request(self, request_id: int | str, method: str, params: Any) -> None:
        try:
            result = self._dispatch_host(method, params)
        except RpcError as exc:
            self._send_error(request_id, exc.code, exc.message)
        except PluginHostError as exc:
            # 宿主拒绝请求（参数、资产名、URL、重定向等）时，插件要拿到真实原因。
            self._send_exception(request_id, HostCallError(str(exc), code="host_rejected"))
        except httpx2.TransportError as exc:
            self._send_exception(
                request_id,
                HostCallError(f"宿主请求远端失败：{exc}", code="host_http_failed"),
            )
        except (ValueError, TypeError) as exc:
            self._send_exception(request_id, HostCallError(str(exc), code="host_rejected"))
        except Exception:
            self._log.exception("插件 %s 调用宿主 %s 失败", self.plugin_id, method)
            self._send_error(request_id, ErrorCode.INTERNAL_ERROR, "宿主处理请求失败")
        else:
            self._send({"jsonrpc": "2.0", "id": request_id, "result": encode(result)})

    def _run_notification(self, method: str, params: Any) -> None:
        try:
            self._dispatch_host(method, params)
        except (ValueError, TypeError) as exc:
            self._drop_notification(method, exc)
        except Exception:
            self._log.exception("插件 %s 的 %s 通知处理失败", self.plugin_id, method)

    def _dispatch_host(self, method: str, params: Any) -> Any:
        if method == "log":
            self._log_message(params)
            return None
        if method == "progress":
            self._progress(params)
            return None
        if method == "resource.changed":
            self._resource_changed(params)
            return None
        if method == "http.request":
            return self._http_request(params)
        raise RpcMethodNotFound(f"宿主不支持方法：{method}")

    def _log_message(self, params: Any) -> None:
        level = _string(params, "level")
        message = _string(params, "message")
        writer = _LOG_METHODS.get(level)
        if writer is None:
            raise ValueError(f"未知日志级别：{level}")
        writer(self._log, "[%s] %s", self.plugin_id, message)

    def _progress(self, params: Any) -> None:
        if not isinstance(params, dict):
            raise ValueError("progress 参数必须是对象")
        until = params.get("until")
        if until is None:
            if "reason" in params:
                raise ValueError("progress 心跳不能带 reason")
            # 纯心跳：只续期扫描的空闲超时，不能让任务等待。
            self._touch_scan()
            self._log.debug("插件 %s 心跳", self.plugin_id)
            return
        if not isinstance(until, str):
            raise ValueError("until 必须是带时区的 ISO 8601 字符串")
        reason_text = params.get("reason")
        if not isinstance(reason_text, str) or reason_text not in WAIT_REASONS:
            raise ValueError(f"未知的等待原因：{reason_text!r}")
        reason: WaitReason = reason_text
        when = datetime.fromisoformat(until)
        if when.tzinfo is None or when.utcoffset() is None:
            raise ValueError("until 必须带时区")
        # 校验通过后才续期；非法通知既不让任务等待，也不延长扫描超时。
        self._touch_scan()
        self._log.debug("插件 %s 等待中：%s（直到 %s）", self.plugin_id, reason, until)
        with self._pending_lock:
            for pending in self._pending.values():
                if pending.waiter is not None:
                    pending.progress.append((when, reason))
                    pending.progress_event.set()

    def _resource_changed(self, params: Any) -> None:
        source_id = params.get("source_id") if isinstance(params, dict) else None
        locators = params.get("locators") if isinstance(params, dict) else None
        if not isinstance(source_id, int) or isinstance(source_id, bool):
            raise ValueError("source_id 必须是整数")
        if not isinstance(locators, list) or any(
            not isinstance(item, str) or not item for item in locators
        ):
            raise ValueError("locators 必须是非空字符串数组")
        self._on_resource_changed(source_id, locators)

    def _http_request(self, params: Any) -> PluginHttpResponse:
        method = _string(params, "method")
        url = _string(params, "url")
        kwargs = params.get("kwargs") if isinstance(params, dict) else None
        if not isinstance(kwargs, dict):
            raise ValueError("kwargs 必须是对象")
        response = self._http.request(method, url, **kwargs)
        return _http_response(response)

    def _send_exception(self, request_id: int | str, error: BaseException) -> None:
        payload = encode_exception(error)
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": ErrorCode.BUSINESS_ERROR,
                    "message": payload["message"],
                    "data": payload,
                },
            }
        )

    def _send_error(self, request_id: Any, code: int, message: str) -> None:
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": code, "message": message},
            }
        )

    def _fatal(self, reason: str) -> None:
        if self._closed.is_set():
            return
        self._log.error("插件 %s 的连接已断开：%s", self.plugin_id, reason)
        self._close_reason = reason
        self._closed.set()
        self._fail_pending()
        if self._on_fatal is not None:
            self._on_fatal(reason)


def _string(params: Any, key: str) -> str:
    if not isinstance(params, dict):
        raise ValueError("params 必须是对象")
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} 必须是非空字符串")
    return value


def _http_response(response: Any) -> PluginHttpResponse:
    return PluginHttpResponse(
        status_code=response.status_code,
        headers=dict(response.headers),
        content=response.content,
        url=str(response.url),
        history=tuple(_http_response(item) for item in response.history),
    )
