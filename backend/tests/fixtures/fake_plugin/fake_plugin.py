"""测试用进程外插件：用 iris_plugin_sdk.runtime 实现协议和测试控制方法。

这个模块只存在于 backend/tests/fixtures，不进入生产代码。模式由插件目录里的 mode.txt 控制，
测试用它覆盖启动崩溃、忽略关闭等场景；状态通过 ``test.control`` RPC 由宿主侧测试操纵。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from fake_state import (  # type: ignore[import-not-found]
    FakeImageFetcher,
    FakeMetadataImages,
    FakeMetadataPeople,
    FakeMetadataProvider,
    FakeMetadataState,
    FakeResourceProvider,
    FakeSourceState,
    FakeSubtitleProvider,
    FakeSubtitleState,
)
from iris_plugin_sdk import (
    HostCallError,
    InvalidSourceConfig,
    MetadataNotFound,
    PluginContext,
    PluginRequest,
    PluginRequestError,
    PluginResponse,
    ResourceNotFound,
    ResourceUnavailable,
    SubtitleUnavailable,
    codec,
    load_manifest,
)
from iris_plugin_sdk.protocol import RpcError
from iris_plugin_sdk.runtime import PluginRuntime


def _mode() -> str:
    path = Path(__file__).with_name("mode.txt")
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


MODE = _mode()
if MODE == "slow_start":
    time.sleep(0.8)
if MODE == "crash_on_start":
    os._exit(23)
if MODE == "crash_on_start_with_stderr":
    import sys

    sys.stderr.write("Traceback (most recent call last):\nImportError: 假插件缺少依赖\n")
    sys.stderr.flush()
    os._exit(23)
if MODE == "stderr":
    import sys

    sys.stderr.write("假插件 stderr 第一行\n假插件 stderr 第二行\n")
    sys.stderr.flush()


PLUGIN_DIR = Path(__file__).resolve().parent
# 测试控制需要知道本次安装的 plugin.json 声明了哪些 provider、id 是什么。
MANIFEST = load_manifest(PLUGIN_DIR)


class FakeAdmin:
    def __init__(self, directory: Path) -> None:
        self._root = directory / "ui"

    @property
    def ui_root(self) -> Path:
        return self._root

    def handle(self, request: PluginRequest) -> PluginResponse | None:
        if request.path == "missing":
            return None
        if request.path == "rejected":
            raise PluginRequestError("不允许", status=403, code="forbidden")
        if request.path == "reject":
            raise PluginRequestError("插件拒绝了请求", status=422, code="rejected")
        if request.path == "reject-server":
            raise PluginRequestError("插件暂时不可用", status=503, code="unavailable")
        if request.path == "boom":
            raise RuntimeError("SECRET-PLUGIN-DETAIL")
        if request.path == "invalid":
            return {"not": "a PluginResponse"}  # type: ignore[return-value]
        if request.path == "invalid-status":
            return PluginResponse({"ok": False}, status=302)
        if request.path == "unserializable":
            return PluginResponse({"bad": {1, 2}})
        if request.path == "empty":
            return PluginResponse(status=204)
        return PluginResponse(
            {
                "method": request.method,
                "path": request.path,
                "query": dict(request.query),
                "body": request.body,
                "user_id": request.user_id,
            }
        )


class FakePlugin:
    def __init__(self) -> None:
        self.source = FakeSourceState(MANIFEST.id)
        if MODE == "no_changes":
            self.source.capabilities = frozenset({"status", "delete", "known_scan"})
        self.metadata = FakeMetadataState(MANIFEST.id)
        self.subtitles = FakeSubtitleState(MANIFEST.id)
        self.context: PluginContext | None = None
        self.config_changes: list[str | None] = []
        self.host_url: str | None = None
        self.raw_progress: Callable[[dict[str, Any]], None] = lambda params: None
        self._providers: dict[str, object] = {}
        self._started = False

    def start(self, context: PluginContext) -> None:
        self.context = context
        self._started = True
        metadata = FakeMetadataProvider(self.metadata)
        subtitles = FakeSubtitleProvider(self.subtitles)
        candidates: dict[str, object] = {
            "resource.source": FakeResourceProvider(self.source),
            "metadata.search": metadata,
            "metadata.details": metadata,
            "metadata.images": FakeMetadataImages(self.metadata),
            "metadata.people": FakeMetadataPeople(self.metadata),
            "image.fetch": FakeImageFetcher(self.metadata),
            "subtitle.search": subtitles,
            "subtitle.download": subtitles,
            "admin.ui": FakeAdmin(PLUGIN_DIR),
        }
        self._providers = {
            capability: provider
            for capability, provider in candidates.items()
            if capability in MANIFEST.provides
        }
        context.logger.info("测试插件已启动")
        if MODE == "crash_after_start":
            threading.Thread(target=self._crash_soon, daemon=True).start()

    def _crash_soon(self) -> None:
        time.sleep(0.05)
        os._exit(24)

    def stop(self) -> None:
        self._started = False
        self._providers = {}

    def providers(self) -> Mapping[str, object]:
        return self._providers

    def flush(self) -> None:
        return None

    def config_changed(self, *, proxy: str | None, host_url: str | None) -> None:
        self.config_changes.append(proxy)
        self.host_url = host_url

    @property
    def context_or_fail(self) -> PluginContext:
        if self.context is None:
            raise RuntimeError("插件还没有启动")
        return self.context

    # ---- 测试控制 ----

    def control(self, domain: str, op: str, params: dict[str, Any]) -> Any:
        if domain == "source":
            return self._source_control(op, params)
        if domain == "metadata":
            return self._metadata_control(op, params)
        if domain == "subtitle":
            return self._subtitle_control(op, params)
        raise ValueError(f"未知的控制域：{domain}")

    def _source_control(self, op: str, params: dict[str, Any]) -> Any:
        state = self.source
        if op == "add_dir":
            return state.add_dir(params.get("parent"), str(params["name"]))
        if op == "add_file":
            return state.add_file(
                params.get("parent"),
                str(params["name"]),
                size=params.get("size"),
                sha1=params.get("sha1"),
                content=bytes(params.get("content") or b""),
            )
        if op == "make_path":
            return state.make_path(str(params["path"]))
        if op == "make_dir":
            return state.make_path(str(params["name"]))
        if op == "child":
            return state.child(params.get("parent"), str(params["name"]))
        if op == "remove":
            state.remove(str(params["node_id"]))
            return None
        if op == "move":
            state.move(
                str(params["node_id"]),
                params.get("new_parent"),
                params.get("new_name"),
            )
            return None
        if op == "files_in":
            return state.files_in(params.get("node_id"))
        if op == "node":
            return state.node(str(params["node_id"]))
        if op == "set_node":
            state.set_node(str(params["node_id"]), **dict(params["fields"]))
            return None
        if op == "calls":
            return list(state.calls)
        if op == "clear_calls":
            state.calls.clear()
            return None
        if op == "link_requests":
            return [list(item) for item in state.link_requests]
        if op == "set_error":
            state.error = params.get("error")
            return None
        if op == "set_resolve_path":
            state.resolve_path = params.get("path")
            return None
        if op == "fail_next":
            state.fail_next[str(params["method"])] = int(params["count"])
            return None
        if op == "set_wait":
            state.wait_once = (str(params["until"]), str(params["reason"]))
            return None
        if op == "set_fatal_scan":
            state.fatal_scan = bool(params["value"])
            return None
        if op == "progress":
            # 绕过 SDK 的 progress 校验，专门测试宿主遇到非法通知时的处理。
            self.raw_progress(dict(params))
            return None
        if op == "set_resolve_error":
            state.resolve_error = str(params["error"])
            return None
        if op == "set_selectable":
            state.selectable = bool(params["selectable"])
            return None
        if op == "set_multiple":
            state.multiple = bool(params["multiple"])
            return None
        if op == "set_status":
            state.status_state = str(params.get("state", "ok"))
            state.status_message = params.get("message")
            state.status_resume_at = params.get("resume_at")
            state.status_page = params.get("page")
            return None
        if op == "changed":
            notify = self.context_or_fail.notify
            assert notify is not None
            notify.resource_changed(int(params["source_id"]), list(params["locators"]))
            return None
        raise ValueError(f"未知的来源控制操作：{op}")

    def _metadata_control(self, op: str, params: dict[str, Any]) -> Any:
        state = self.metadata
        if op == "add_movie":
            return state.add_movie(
                int(params["tmdb_id"]),
                str(params["title"]),
                params.get("year"),
                original_title=params.get("original_title"),
                imdb_id=params.get("imdb_id"),
                cast=params.get("cast"),
            )
        if op == "add_series":
            return state.add_series(
                int(params["tmdb_id"]),
                str(params["title"]),
                params.get("year"),
                {int(key): int(value) for key, value in (params.get("seasons") or {}).items()},
                original_title=params.get("original_title"),
                cast=params.get("cast"),
                imdb_id=params.get("imdb_id"),
                tvdb_id=params.get("tvdb_id"),
            )
        if op == "clear_titles":
            state.clear_titles()
            return None
        if op == "rename":
            state.rename(str(params["kind"]), int(params["tmdb_id"]), str(params["title"]))
            return None
        if op == "set_details":
            state.set_details(str(params["kind"]), int(params["tmdb_id"]), dict(params["fields"]))
            return None
        if op == "add_person":
            state.add_person(dict(params["person"]))
            return None
        if op == "set_record":
            state.set_record(params["record"])
            return None
        if op == "delete_record":
            state.delete_record(str(params["remote_id"]))
            return None
        if op == "set_find":
            state.set_find(str(params["scheme"]), str(params["value"]), params.get("remote_id"))
            return None
        if op == "set_search_results":
            state.set_search_results(
                str(params["kind"]), str(params["query"]), list(params["results"])
            )
            return None
        if op == "set_reason":
            state.reason = params.get("reason")
            return None
        if op == "set_error":
            state.error = params.get("error")
            return None
        if op == "set_image_delay":
            state.image_delay = float(params["seconds"])
            return None
        if op == "set_page_url_template":
            state.page_url_template = str(params["template"])
            return None
        if op == "set_images":
            state.images_available = list(params["images"])
            return None
        if op == "get_title":
            return state.title(str(params["kind"]), int(params["tmdb_id"]))
        if op == "config_changes":
            return {
                "proxy": self.context.proxy if self.context is not None else None,
                "count": len(self.config_changes),
            }
        if op == "calls":
            return list(state.calls)
        if op == "find_calls":
            return [list(item) for item in state.find_calls]
        if op == "clear_calls":
            state.calls.clear()
            state.find_calls.clear()
            return None
        raise ValueError(f"未知的元数据控制操作：{op}")

    def _subtitle_control(self, op: str, params: dict[str, Any]) -> Any:
        state = self.subtitles
        if op == "set_reason":
            state.reason = params.get("reason")
            return None
        if op == "set_error":
            state.error = params.get("error")
            state.error_message = str(params.get("message") or "字幕站不可用")
            state.error_retry_after = params.get("retry_after")
            return None
        if op == "set_results":
            state.results = {
                str(key): list(value) for key, value in dict(params.get("results") or {}).items()
            }
            state.results_configured = True
            return None
        if op == "search_languages":
            return list(state.search_languages)
        if op == "download_candidates":
            return list(state.download_candidates)
        if op == "set_candidates":
            state.candidates = list(params["candidates"])
            return None
        if op == "set_same_content":
            state.same_content = bool(params["value"])
            return None
        if op == "media_requests":
            return list(state.media_requests)
        if op == "calls":
            return list(state.calls)
        if op == "clear_calls":
            state.calls.clear()
            state.search_languages.clear()
            state.download_candidates.clear()
            state.media_requests.clear()
            return None
        raise ValueError(f"未知的字幕控制操作：{op}")


class FakeRuntime(PluginRuntime):
    def __init__(self, plugin: FakePlugin) -> None:
        super().__init__(plugin)
        self._fake = plugin
        # 这两个模式修改运行时实际使用的 manifest，模拟插件返回不一致的数据。
        if MODE == "mismatch_id":
            object.__setattr__(self._manifest, "id", "org.example.other")
        if MODE == "bad_sdk_runtime":
            object.__setattr__(self._manifest, "sdk", ">=2.0")
        # 测试来源直接发原始 progress，宿主端校验才不会被 SDK 提前拦掉。
        plugin.source.progress = lambda until, reason: self.notify(
            "progress", {"until": until, "reason": reason}
        )
        plugin.source.heartbeat = lambda: self.notify("progress", {"until": None})
        plugin.raw_progress = lambda params: self.notify("progress", params)
        self._initialize_params: dict[str, Any] | None = None
        self._last_priority: str | None = None

    def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "initialize":
            self._initialize_params = dict(params)
        if method.startswith("test."):
            params = codec.decode(params)
        if method == "test.control":
            return self._fake.control(
                str(params["domain"]), str(params["op"]), dict(params.get("params") or {})
            )
        if method == "test.initialize_params":
            return self._initialize_params
        if method == "test.last_priority":
            return self._last_priority
        if method.startswith(("metadata.", "image.")):
            priority = params.get("priority")
            self._last_priority = priority if isinstance(priority, str) else None
        if method == "plugin.flush" and MODE == "ignore_flush":
            time.sleep(60)
        if method == "plugin.shutdown" and MODE == "ignore_shutdown":
            time.sleep(60)
        if method == "test.echo":
            return params["value"]
        if method == "test.sleep":
            time.sleep(float(params["seconds"]))
            return params["value"]
        if method == "test.raise":
            return self._raise_for(params["kind"])
        if method == "test.context":
            context = self._fake.context_or_fail
            return {
                "plugin_id": context.plugin_id,
                "data_dir": str(context.data_dir),
                "media_dir": str(context.media_dir),
                "proxy": context.proxy,
                "host_version": context.host.version,
                "host_url": context.host.url,
            }
        if method == "test.config_changes":
            return {
                "proxy": self._fake.context_or_fail.proxy,
                "count": len(self._fake.config_changes),
            }
        if method == "test.changed":
            notify = self._fake.context_or_fail.notify
            assert notify is not None
            notify.resource_changed(int(params["source_id"]), list(params["locators"]))
            return None
        if method == "test.http":
            http = self._fake.context_or_fail.http
            if http is None:
                raise RuntimeError("插件没有 http 能力")
            response = http.get(str(params["url"]))
            return {"status": response.status_code, "text": response.text, "url": response.url}
        if method == "test.host_rejected":
            http = self._fake.context_or_fail.http
            if http is None:
                raise RuntimeError("插件没有 http 能力")
            try:
                http.request("TRACE", "https://example.com/")
            except HostCallError as exc:
                return {"code": exc.code, "message": exc.message}
            raise AssertionError("宿主应该拒绝 TRACE 请求")
        if method == "test.log":
            self._fake.context_or_fail.logger.info("运行时日志测试")
            return None
        if method == "test.std_log":
            logger = logging.getLogger("fake.standard")
            logger.info("标准 logging 日志")
            try:
                raise RuntimeError("标准日志异常")
            except RuntimeError:
                logger.exception("标准 logs 异常")
            return None
        if method == "test.progress":
            notify = self._fake.context_or_fail.notify
            assert notify is not None
            notify.heartbeat()
            return None
        if method == "test.progress_request":
            try:
                self.call_host("progress", {"until": None})
            except HostCallError as exc:
                return {"code": exc.code, "message": exc.message}
            raise AssertionError("宿主应该拒绝把 progress 当请求")
        if method == "test.standard_error":
            raise RpcError(-32603, "运行时内部错误")
        if method == "test.crash":
            os._exit(17)
        return super()._dispatch(method, params)

    @staticmethod
    def _raise_for(kind: str) -> Any:
        if kind == "resource_unavailable":
            raise ResourceUnavailable(
                "来源限流",
                fatal=True,
                status_code=429,
                code="resource_rate_limited",
            )
        if kind == "resource_not_found":
            raise ResourceNotFound("找不到")
        if kind == "metadata_not_found":
            raise MetadataNotFound("条目不存在")
        if kind == "subtitle_unavailable":
            raise SubtitleUnavailable("字幕站不可用")
        if kind == "invalid_source_config":
            raise InvalidSourceConfig("配置不合法")
        if kind == "value_error":
            raise ValueError("未登记的内部细节")
        raise RuntimeError("插件内部堆栈不能泄露")


if __name__ == "__main__":
    FakeRuntime(FakePlugin()).serve()
