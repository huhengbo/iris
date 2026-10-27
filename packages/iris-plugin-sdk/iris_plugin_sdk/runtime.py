"""插件端的 stdio JSON-RPC 2.0 运行时。

一行一条消息（NDJSON），请求 ID 双向多路复用：宿主可以并发调用插件，插件也可以在处理
一个请求时反过来调用宿主（log、http、progress）。插件作者只实现 Plugin 接口，
并在模块入口调用 run(plugin) 即可。
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
import traceback
from collections.abc import Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, cast

from iris_plugin_sdk import codec
from iris_plugin_sdk.manifest import load_manifest, manifest_to_dict
from iris_plugin_sdk.priority import priority_scope
from iris_plugin_sdk.protocol import (
    HTTP_ALLOWED_KWARGS,
    MAX_MESSAGE_BYTES,
    WAIT_REASONS,
    ErrorCode,
    MessageTooLarge,
    RpcError,
    WaitReason,
    decode_frame,
    encode_frame,
)
from iris_plugin_sdk.types import (
    RESOURCE_CAPABILITIES,
    SDK_VERSION,
    ConfigAware,
    Flushable,
    HostCallError,
    HostInfo,
    MetadataPriority,
    Plugin,
    PluginContext,
    PluginHttpClient,
    PluginHttpResponse,
    PluginLogger,
    PluginManifest,
    PluginManifestError,
    PluginNotifier,
    PluginRequestError,
    PluginResponse,
    PluginSdkIncompatibleError,
    ResourceEntry,
    ResourceProvider,
    ResourceSource,
    sdk_compatible,
)

WORKER_THREADS = 8
# 插件调用宿主的默认超时；宿主自己的 HTTP 超时是 15 秒，留出排队余量。
HOST_CALL_TIMEOUT = 30.0
_EOF = object()

# 这些方法的 params 都带 priority，运行时在调用 provider 前设置 contextvar。
_PRIORITY_METHODS = frozenset(
    {
        "metadata.search",
        "metadata.find",
        "metadata.details",
        "metadata.season",
        "metadata.page_url",
        "metadata.person",
        "image.images",
        "image.fetch",
        "metadata.image",
    }
)


_LOG_LEVELS = (
    (logging.ERROR, "error"),
    (logging.WARNING, "warning"),
    (logging.INFO, "info"),
)


class _LogForwardHandler(logging.Handler):
    """把标准 logging 记录转成 ``log`` 通知；不走 logging 自身，避免递归。"""

    def __init__(self, runtime: PluginRuntime) -> None:
        super().__init__(logging.DEBUG)
        self._runtime = runtime
        self._local = threading.local()
        self.setFormatter(logging.Formatter())

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(self._local, "emitting", False):
            return
        self._local.emitting = True
        try:
            level = "debug"
            for threshold, name in _LOG_LEVELS:
                if record.levelno >= threshold:
                    level = name
                    break
            try:
                message = self.format(record)
            except Exception:
                message = record.getMessage()
            self._runtime.notify("log", {"level": level, "message": message})
        except Exception:
            # 日志转发失败时不能再用 logging 报错，否则会自己递归自己。
            pass
        finally:
            self._local.emitting = False


class _RpcLogger(PluginLogger):
    def __init__(self, runtime: PluginRuntime) -> None:
        self._runtime = runtime

    def debug(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._log("debug", message, args)

    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._log("info", message, args)

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._log("warning", message, args)

    def error(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._log("error", message, args)

    def exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._log("error", message, args, traceback.format_exc().rstrip())

    def _log(
        self, level: str, message: str, args: tuple[Any, ...], details: str | None = None
    ) -> None:
        if args:
            try:
                text = message % args
            except (TypeError, ValueError):
                text = " ".join([message, *(str(item) for item in args)])
        else:
            text = message
        if details:
            text = f"{text}\n{details}"
        self._runtime.notify("log", {"level": level, "message": text})


class _RpcNotifier(PluginNotifier):
    def __init__(self, runtime: PluginRuntime) -> None:
        self._runtime = runtime

    def resource_changed(self, source_id: int, locators: Sequence[str]) -> None:
        if not isinstance(source_id, int) or isinstance(source_id, bool):
            raise ValueError("source_id 必须是整数")
        values = list(locators)
        if any(not isinstance(item, str) or not item for item in values):
            raise ValueError("locators 必须是非空字符串数组")
        self._runtime.notify("resource.changed", {"source_id": source_id, "locators": values})

    def progress(self, until: datetime, reason: WaitReason) -> None:
        self._runtime._progress(until, reason)

    def heartbeat(self) -> None:
        self._runtime._heartbeat()


class _RpcHttpClient(PluginHttpClient):
    def __init__(self, runtime: PluginRuntime) -> None:
        self._runtime = runtime

    def request(self, method: str, url: str, **kwargs: Any) -> PluginHttpResponse:
        unknown = sorted(set(kwargs) - HTTP_ALLOWED_KWARGS)
        if unknown:
            raise HostCallError(f"插件不能设置参数：{', '.join(unknown)}")
        result = self._runtime.call_host(
            "http.request", {"method": method, "url": url, "kwargs": kwargs}
        )
        if not isinstance(result, PluginHttpResponse):
            raise HostCallError("宿主返回的 HTTP 响应格式不正确")
        return result

    def get(self, url: str, **kwargs: Any) -> PluginHttpResponse:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> PluginHttpResponse:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> PluginHttpResponse:
        return self.request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> PluginHttpResponse:
        return self.request("DELETE", url, **kwargs)


class PluginRuntime:
    """一个插件进程的运行时；serve() 返回即进程退出。"""

    def __init__(self, plugin: Plugin) -> None:
        self._plugin = plugin
        # 协议规定工作目录就是插件目录；manifest 只从 plugin.json 读取。
        self._manifest: PluginManifest = load_manifest(Path.cwd())
        self._context: PluginContext | None = None
        self._logger = _RpcLogger(self)
        self._providers: dict[str, object] = {}
        # source_id → (打开时的 config, 来源实例)；config 变化时重新打开。
        self._sources: dict[int, tuple[dict[str, Any], ResourceSource]] = {}
        self._sources_lock = threading.Lock()
        self._proxy: str | None = None
        self._initialized = threading.Event()
        self._closed = threading.Event()
        self._stdout: BinaryIO | None = None
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[int | str, Future[Any]] = {}
        self._next_id = 0
        self._queue: queue.Queue[Any] = queue.Queue()
        self._log_handler: _LogForwardHandler | None = None
        self._pool = ThreadPoolExecutor(max_workers=WORKER_THREADS, thread_name_prefix="plugin-rpc")

    def serve(self, stdin: BinaryIO | None = None, stdout: BinaryIO | None = None) -> None:
        self._stdout = stdout or sys.stdout.buffer
        self._install_log_handler()
        reader = threading.Thread(
            target=self._read_loop, args=(stdin or sys.stdin.buffer,), daemon=True
        )
        reader.start()
        try:
            while not self._closed.is_set():
                try:
                    message = self._queue.get(timeout=0.2)
                except queue.Empty:
                    continue
                if message is _EOF:
                    break
                self._handle_message(message)
        finally:
            self._closed.set()
            self._pool.shutdown(wait=True, cancel_futures=True)
            self._stop_plugin()

    def _install_log_handler(self) -> None:
        """标准 logging 和 context.logger 走同一条 log 通知。"""
        root = logging.getLogger()
        handler = _LogForwardHandler(self)
        root.addHandler(handler)
        if root.level > logging.INFO:
            root.setLevel(logging.INFO)
        self._log_handler = handler

    # ---- 读循环 ----

    def _read_loop(self, stream: BinaryIO) -> None:
        while not self._closed.is_set():
            try:
                raw = stream.readline(MAX_MESSAGE_BYTES + 1)
            except (OSError, ValueError):
                break
            if not raw:
                break
            try:
                message = decode_frame(raw)
            except MessageTooLarge:
                self._queue.put(_Oversized())
                break
            except ValueError:
                self._queue.put(_Invalid())
                continue
            self._queue.put(message)
        self._queue.put(_EOF)

    def _handle_message(self, message: Any) -> None:
        if isinstance(message, _Oversized):
            self._write_notification_error(ErrorCode.PARSE_ERROR, "单条消息超过 64 MB")
            self._closed.set()
            return
        if isinstance(message, _Invalid):
            self._write_notification_error(ErrorCode.PARSE_ERROR, "消息不是合法的 JSON")
            return
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            self._write_notification_error(ErrorCode.INVALID_REQUEST, "不是 JSON-RPC 2.0 消息")
            return
        method = message.get("method")
        request_id = message.get("id")
        if method is None:
            self._handle_response(message)
            return
        if not isinstance(method, str):
            self._write_notification_error(ErrorCode.INVALID_REQUEST, "方法必须是字符串")
            return
        params = message.get("params", {})
        if not isinstance(params, dict):
            if isinstance(request_id, (int, str)):
                self._send_error(request_id, ErrorCode.INVALID_PARAMS, "params 必须是对象")
            else:
                self._write_notification_error(ErrorCode.INVALID_PARAMS, "params 必须是对象")
            return
        if request_id is None:
            # 宿主通知没有 ID，不回复。
            if method == "host.config_changed" and self._initialized.is_set():
                self._pool.submit(self._config_changed, params)
            return
        if not isinstance(request_id, (int, str)):
            self._write_notification_error(ErrorCode.INVALID_REQUEST, "请求 ID 必须是整数或字符串")
            return
        lifecycle = {"initialize", "plugin.flush", "plugin.shutdown"}
        if method not in lifecycle and not self._initialized.is_set():
            self._send_error(request_id, ErrorCode.NOT_INITIALIZED, "插件还没有完成 initialize")
            return
        self._pool.submit(self._run_request, request_id, method, params)

    def _handle_response(self, message: Mapping[str, Any]) -> None:
        request_id = message.get("id")
        if not isinstance(request_id, (int, str)):
            return
        with self._pending_lock:
            future = self._pending.pop(request_id, None)
        if future is None:
            return
        if "error" in message:
            error = message["error"]
            if not isinstance(error, dict):
                future.set_exception(HostCallError("宿主返回了无效的错误对象"))
            else:
                data = error.get("data")
                if isinstance(data, dict):
                    # 宿主把拒绝原因编码成 HostCallError，按 type/code/message 还原。
                    future.set_exception(codec.decode_exception(data))
                else:
                    code = error.get("code")
                    text = error.get("message")
                    future.set_exception(
                        HostCallError(
                            text if isinstance(text, str) and text else "宿主调用失败",
                            code=(
                                "host_method_not_found"
                                if code == ErrorCode.METHOD_NOT_FOUND
                                else "host_call_failed"
                            ),
                        )
                    )
        else:
            future.set_result(codec.decode(message.get("result")))

    # ---- 处理宿主请求 ----

    def _run_request(self, request_id: int | str, method: str, params: dict[str, Any]) -> None:
        try:
            result = self._dispatch(method, params)
        except RpcError as exc:
            self._send_error(request_id, exc.code, exc.message)
            return
        except Exception as exc:
            if not codec.is_protocol_exception(exc):
                # 宿主只能看到「插件内部错误」，堆栈留在宿主日志里供排查
                self._logger.exception("处理 %s 时出现未预期的异常", method)
            self._send_exception(request_id, exc)
            if method == "plugin.shutdown":
                self._closed.set()
            return
        self._send_result(request_id, result)
        if method == "plugin.shutdown":
            self._closed.set()

    def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "initialize":
            return self._initialize(params)
        if method == "plugin.flush":
            self._flush_plugin()
            return None
        if method == "plugin.shutdown":
            self._flush_plugin()
            self._stop_plugin()
            return None
        if method == "provider.unavailable_reason":
            return self._unavailable_reason(_string(params, "capability"))
        if method == "resource.describe":
            return self._resource_describe()
        if method == "resource.validate_config":
            return self._resource_provider().validate_config(_mapping(params, "config"))
        if method == "resource.check":
            self._resource_provider().check(_config(params))
            return None
        if method == "resource.status":
            return self._resource_provider().status(_config(params))
        if method == "resource.browse":
            return self._source(params).browse(_optional_string(params, "locator"))
        if method == "resource.scan":
            known = _known(params.get("known"))
            return self._source(params).scan(_optional_string(params, "locator"), known=known)
        if method == "resource.read":
            return self._source(params).read(_string(params, "locator"), _int(params, "max_bytes"))
        if method == "resource.resolve":
            return self._source(params).resolve(
                _string(params, "locator"),
                _string(params, "user_agent"),
                _optional_string(params, "throttle_key"),
            )
        if method == "resource.delete":
            self._source(params).delete(_string(params, "locator"))
            return None
        if method in _PRIORITY_METHODS:
            with priority_scope(_priority(params)):
                return self._dispatch_priority(method, params)
        if method == "subtitle.search":
            return self._provider("subtitle.search").search(
                _dataclass(params, "request", "SubtitleSearchRequest")
            )
        if method == "subtitle.download":
            return self._provider("subtitle.download").download(
                _dataclass(params, "candidate", "SubtitleCandidate")
            )
        if method == "admin.request":
            provider = self._provider("admin.ui")
            response = provider.handle(_dataclass(params, "request", "PluginRequest"))
            if response is None:
                raise PluginRequestError("接口不存在", status=404, code="not_found")
            if not isinstance(response, PluginResponse):
                raise TypeError("admin.ui 必须返回 PluginResponse")
            return response
        raise RpcError(ErrorCode.METHOD_NOT_FOUND, f"未知的 RPC 方法：{method}")

    def _dispatch_priority(self, method: str, params: dict[str, Any]) -> Any:
        if method == "metadata.search":
            return self._provider("metadata.search").search(
                _dataclass(params, "request", "MetadataSearchRequest")
            )
        if method == "metadata.find":
            return self._provider("metadata.details").find(
                _string(params, "kind"), _string(params, "scheme"), _string(params, "value")
            )
        if method == "metadata.details":
            return self._provider("metadata.details").details(
                _string(params, "remote_id"), kind=_string(params, "kind")
            )
        if method == "metadata.season":
            return self._provider("metadata.details").season(
                _string(params, "remote_id"), _int(params, "number")
            )
        if method == "metadata.page_url":
            return self._provider("metadata.details").page_url(
                _string(params, "kind"), _string(params, "remote_id")
            )
        if method == "metadata.person":
            return self._provider("metadata.people").person(_string(params, "remote_id"))
        if method == "image.images":
            return self._provider("image.fetch").images(
                _dataclass(params, "request", "ImageLookupRequest")
            )
        if method == "image.fetch":
            return self._provider("image.fetch").image(
                _string(params, "source"), _int(params, "width")
            )
        if method == "metadata.image":
            return self._provider("metadata.images").image(
                _string(params, "source"), _int(params, "width")
            )
        raise RpcError(ErrorCode.METHOD_NOT_FOUND, f"未知的 RPC 方法：{method}")

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._initialized.is_set():
            raise RpcError(ErrorCode.INVALID_REQUEST, "插件已经初始化")
        sdk_version = _string(params, "sdk_version")
        if not sdk_compatible(self._manifest.sdk, sdk_version):
            raise PluginSdkIncompatibleError(
                f"插件要求 SDK {self._manifest.sdk}，宿主提供 {sdk_version}"
            )
        data_dir = Path(_string(params, "data_dir"))
        if not data_dir.is_absolute():
            raise RpcError(ErrorCode.INVALID_PARAMS, "data_dir 必须是绝对路径")
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "state").mkdir(parents=True, exist_ok=True)
        proxy = _optional_string(params, "proxy")
        media_dir = _path(params, "media_dir")
        if not media_dir.is_absolute():
            raise RpcError(ErrorCode.INVALID_PARAMS, "media_dir 必须是绝对路径")
        raw_host = params.get("host")
        if not isinstance(raw_host, dict):
            raise RpcError(ErrorCode.INVALID_PARAMS, "host 必须是对象")
        self._proxy = proxy
        context = PluginContext(
            plugin_id=self._manifest.id,
            logger=self._logger,
            data_dir=data_dir,
            host=HostInfo(
                version=_string(raw_host, "version"),
                url=_optional_string(raw_host, "url"),
            ),
            media_dir=media_dir,
            proxy=proxy,
            http=_RpcHttpClient(self) if "http" in self._manifest.capabilities else None,
            notify=_RpcNotifier(self),
        )
        self._plugin.start(context)
        self._context = context
        providers = dict(self._plugin.providers())
        declared = set(self._manifest.provides)
        actual = set(providers)
        if actual != declared:
            self._plugin.stop()
            missing = ", ".join(sorted(declared - actual)) or "无"
            extra = ", ".join(sorted(actual - declared)) or "无"
            raise PluginManifestError(
                f"providers 与 manifest.provides 不一致：缺少 {missing}，多出 {extra}"
            )
        self._providers = providers
        self._initialized.set()
        return {
            "manifest": manifest_to_dict(self._manifest),
            "sdk_version": SDK_VERSION,
        }

    def _stop_plugin(self) -> None:
        self._clear_sources()
        if not self._initialized.is_set():
            self._providers = {}
            self._context = None
            return
        self._initialized.clear()
        try:
            self._plugin.stop()
        finally:
            self._providers = {}
            self._context = None

    def _flush_plugin(self) -> None:
        if isinstance(self._plugin, Flushable):
            self._plugin.flush()

    # ---- 通知和回调 ----

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": codec.encode(params)})

    def call_host(self, method: str, params: dict[str, Any]) -> Any:
        if self._closed.is_set():
            raise HostCallError("宿主连接已关闭")
        with self._pending_lock:
            self._next_id += 1
            request_id = self._next_id
            future: Future[Any] = Future()
            self._pending[request_id] = future
        self._write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": codec.encode(params),
            }
        )
        try:
            return future.result(timeout=HOST_CALL_TIMEOUT)
        except TimeoutError:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise HostCallError(f"调用宿主超时：{method}") from None

    def _config_changed(self, params: dict[str, Any]) -> None:
        proxy = _optional_string(params, "proxy")
        host_url = _optional_string(params, "host_url")
        self._proxy = proxy
        # 宿主配置可能影响来源实例（例如代理），下一次调用按新配置重新打开。
        self._clear_sources()
        if self._context is not None:
            # PluginContext / HostInfo 是冻结的，实时值通过 hook 通知；插件也可以自己保存。
            object.__setattr__(self._context, "proxy", proxy)
            object.__setattr__(self._context.host, "url", host_url)
        if isinstance(self._plugin, ConfigAware):
            self._plugin.config_changed(proxy=proxy, host_url=host_url)

    def _progress(self, until: datetime, reason: WaitReason) -> None:
        if reason not in WAIT_REASONS:
            raise ValueError(f"未知的等待原因：{reason}")
        if until.tzinfo is None or until.utcoffset() is None:
            raise ValueError("progress 的 until 必须带时区")
        self.notify("progress", {"reason": reason, "until": until.isoformat()})

    def _heartbeat(self) -> None:
        self.notify("progress", {"until": None})

    def _resource_describe(self) -> dict[str, Any]:
        provider = self._resource_provider()
        capabilities = set(provider.capabilities)
        unknown = capabilities - RESOURCE_CAPABILITIES
        if unknown:
            raise ValueError(f"未知的资源能力：{', '.join(sorted(unknown))}")
        return {
            "id": provider.id,
            "multiple": provider.multiple,
            "capabilities": sorted(capabilities),
            "config_fields": [codec.encode(field) for field in provider.config_fields],
        }

    # ---- provider 查找 ----

    def _provider(self, capability: str) -> Any:
        provider = self._providers.get(capability)
        if provider is None:
            raise RpcError(ErrorCode.METHOD_NOT_FOUND, f"插件没有提供能力：{capability}")
        return provider

    def _resource_provider(self) -> ResourceProvider:
        return cast(ResourceProvider, self._provider("resource.source"))

    def _source(self, params: dict[str, Any]) -> ResourceSource:
        source_id = _source_id(params)
        config = _config(params)
        with self._sources_lock:
            cached = self._sources.get(source_id)
            if cached is not None and cached[0] == config:
                return cached[1]
            source = self._resource_provider().open(source_id, config)
            self._sources[source_id] = (config, source)
            return source

    def _clear_sources(self) -> None:
        with self._sources_lock:
            self._sources.clear()

    def _unavailable_reason(self, capability: str) -> str | None:
        provider = self._provider(capability)
        method = getattr(provider, "unavailable_reason", None)
        if method is None:
            raise RpcError(ErrorCode.METHOD_NOT_FOUND, f"能力 {capability} 没有 unavailable_reason")
        return cast(str | None, method())

    # ---- 写消息 ----

    def _send_result(self, request_id: int | str, result: Any) -> None:
        self._write({"jsonrpc": "2.0", "id": request_id, "result": codec.encode(result)})

    def _send_error(self, request_id: int | str, code: int, message: str) -> None:
        self._write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": code, "message": message},
            }
        )

    def _send_exception(self, request_id: int | str, error: BaseException) -> None:
        payload = codec.encode_exception(error)
        self._write(
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

    def _write_notification_error(self, code: int, message: str) -> None:
        self._write(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": code, "message": message},
            }
        )

    def _write(self, message: dict[str, Any]) -> None:
        if self._stdout is None or self._closed.is_set():
            return
        try:
            raw = encode_frame(message)
        except MessageTooLarge:
            self._logger.error("插件要发送的消息超过 %d 字节，连接已断开", MAX_MESSAGE_BYTES)
            self._closed.set()
            return
        try:
            with self._write_lock:
                self._stdout.write(raw)
                self._stdout.flush()
        except (BrokenPipeError, OSError, ValueError):
            self._closed.set()


class _Oversized:
    pass


class _Invalid:
    pass


def _string(params: Mapping[str, Any], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str):
        raise RpcError(ErrorCode.INVALID_PARAMS, f"{key} 必须是字符串")
    return value


def _path(params: Mapping[str, Any], key: str) -> Path:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise RpcError(ErrorCode.INVALID_PARAMS, f"{key} 必须是绝对路径")
    return Path(value)


def _priority(params: Mapping[str, Any]) -> MetadataPriority:
    value = params.get("priority", "interactive")
    if value not in ("interactive", "background"):
        raise RpcError(ErrorCode.INVALID_PARAMS, "priority 必须是 interactive 或 background")
    return cast(MetadataPriority, value)


def _optional_string(params: Mapping[str, Any], key: str) -> str | None:
    value = params.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise RpcError(ErrorCode.INVALID_PARAMS, f"{key} 必须是字符串或 null")
    return value


def _int(params: Mapping[str, Any], key: str) -> int:
    value = params.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise RpcError(ErrorCode.INVALID_PARAMS, f"{key} 必须是整数")
    return value


def _mapping(params: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = params.get(key)
    if not isinstance(value, dict):
        raise RpcError(ErrorCode.INVALID_PARAMS, f"{key} 必须是对象")
    return value


def _dataclass(params: Mapping[str, Any], key: str, expected: str) -> Any:
    value = params.get(key)
    if value is None:
        raise RpcError(ErrorCode.INVALID_PARAMS, f"缺少 {key}")
    if not isinstance(value, dict) or value.get("$type") != expected:
        raise RpcError(ErrorCode.INVALID_PARAMS, f"{key} 必须是 {expected}")
    return codec.decode(value)


def _config(params: Mapping[str, Any]) -> dict[str, Any]:
    return dict(_mapping(params, "config"))


def _source_id(params: Mapping[str, Any]) -> int:
    value = params.get("source_id")
    if not isinstance(value, int) or isinstance(value, bool):
        raise RpcError(ErrorCode.INVALID_PARAMS, "source_id 必须是整数")
    return value


def _known(value: Any) -> dict[str, ResourceEntry] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise RpcError(ErrorCode.INVALID_PARAMS, "known 必须是对象")
    known: dict[str, ResourceEntry] = {}
    for path, entry in value.items():
        if not isinstance(path, str):
            raise RpcError(ErrorCode.INVALID_PARAMS, "known 的键必须是字符串")
        decoded = codec.decode(entry)
        if not isinstance(decoded, ResourceEntry):
            raise RpcError(ErrorCode.INVALID_PARAMS, "known 的值必须是 ResourceEntry")
        known[path] = decoded
    return known


def run(plugin: Plugin) -> None:
    """插件进程入口：插件作者在模块里调用它，进程在 shutdown 或 stdin 关闭后退出。"""
    PluginRuntime(plugin).serve()
