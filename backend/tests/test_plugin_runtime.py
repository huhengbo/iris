"""宿主端进程外插件运行时：握手、RPC、看护和 provider 代理。"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

import httpx2
import pytest
from iris_plugin_sdk import (
    InvalidSourceConfig,
    MediaContext,
    MetadataNotFound,
    MetadataSearchRequest,
    MetadataUnavailable,
    PluginRequest,
    PluginRequestError,
    PluginResponse,
    ResourceNotFound,
    ResourceUnavailable,
    SourceStatus,
    SubtitleSearchRequest,
    testing,
)
from iris_plugin_sdk.protocol import (
    MAX_MESSAGE_BYTES,
    MessageTooLarge,
    decode_frame,
    encode_frame,
)

from app.plugins.remote import (
    RemoteAdminUiProvider,
    RemoteMetadataProvider,
    RemoteResourceProvider,
    RemoteSubtitleProvider,
    providers_for,
)
from app.plugins.rpc import (
    PluginRpcClient,
    PluginUnavailable,
    RpcTimeoutError,
)
from app.plugins.supervisor import (
    PluginManifestMismatch,
    PluginSdkIncompatible,
    PluginStartFailed,
)
from tests.plugin_runtime_support import Harness, make_harness, wait_state, wait_until


@pytest.fixture
def harness(tmp_path: Path) -> Any:
    runtime = make_harness(tmp_path)
    runtime.supervisor.start("org.example.fake")
    try:
        yield runtime
    finally:
        runtime.supervisor.shutdown()


def test_handshake_and_host_context(harness: Harness) -> None:
    supervisor = harness.supervisor
    discovered = supervisor.discovered()
    assert [item.plugin_id for item in discovered] == ["org.example.fake"]
    assert discovered[0].manifest is not None
    assert discovered[0].manifest.entry == "fake_plugin"
    assert supervisor.is_running("org.example.fake")

    context = harness.call("test.context")
    assert context["plugin_id"] == "org.example.fake"
    assert context["data_dir"] == str(harness.data_dir / "plugins" / "org.example.fake")
    assert context["media_dir"] == str(harness.data_dir.parent / "media")
    assert context["proxy"] is None
    assert context["host_version"] == "0.1.0"
    assert context["host_url"] == "http://127.0.0.1:8080"
    state_dir = harness.data_dir / "plugins" / "org.example.fake" / "state"
    assert state_dir.is_dir()


def test_initialize_sends_media_dir(tmp_path: Path) -> None:
    media_dir = tmp_path / "media"
    runtime = make_harness(tmp_path, media_dir=media_dir)
    runtime.supervisor.start("org.example.fake")
    try:
        params = runtime.call("test.initialize_params")
        assert params["media_dir"] == str(media_dir)
        assert runtime.call("test.context")["media_dir"] == str(media_dir)
    finally:
        runtime.supervisor.shutdown()


def test_initialize_always_sends_media_dir(harness: Harness) -> None:
    assert harness.call("test.initialize_params")["media_dir"] == str(
        harness.data_dir.parent / "media"
    )


def test_sdk_range_in_manifest_is_incompatible(tmp_path: Path) -> None:
    runtime = make_harness(tmp_path, sdk=">=2.0")
    discovered = runtime.supervisor.discovered()
    assert discovered[0].manifest is None
    assert discovered[0].error is not None
    assert discovered[0].error.code == "plugin_sdk_incompatible"

    with pytest.raises(PluginSdkIncompatible) as error:
        runtime.supervisor.start("org.example.fake")
    assert error.value.code == "plugin_sdk_incompatible"


def test_runtime_sdk_is_incompatible(tmp_path: Path) -> None:
    runtime = make_harness(tmp_path, modes={"org.example.fake": "bad_sdk_runtime"})
    with pytest.raises(PluginSdkIncompatible):
        runtime.supervisor.start("org.example.fake")
    runtime.supervisor.shutdown()


def test_manifest_id_mismatch(tmp_path: Path) -> None:
    runtime = make_harness(tmp_path, modes={"org.example.fake": "mismatch_id"})
    with pytest.raises(PluginManifestMismatch):
        runtime.supervisor.start("org.example.fake")
    runtime.supervisor.shutdown()


def test_stderr_is_logged_with_plugin_id(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    runtime = make_harness(tmp_path, modes={"org.example.fake": "stderr"})
    caplog.set_level("DEBUG")
    runtime.supervisor.start("org.example.fake")
    wait_until(lambda: "假插件 stderr 第一行" in caplog.text)
    assert "org.example.fake" in caplog.text
    runtime.supervisor.shutdown()


def test_crash_logs_stderr_tail_as_one_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    runtime = make_harness(tmp_path, modes={"org.example.fake": "crash_on_start_with_stderr"})
    caplog.set_level("WARNING")
    with pytest.raises(PluginStartFailed):
        runtime.supervisor.start("org.example.fake")
    wait_until(lambda: "异常退出" in caplog.text)
    record = next(r for r in caplog.records if "异常退出" in r.getMessage())
    assert record.levelname == "WARNING"
    assert "Traceback" in record.getMessage()
    assert "ImportError: 假插件缺少依赖" in record.getMessage()
    runtime.supervisor.shutdown()


def test_concurrent_calls_are_multiplexed(harness: Harness) -> None:
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(
                harness.call,
                "test.sleep",
                {"seconds": 0.2, "value": index},
                timeout=5.0,
            )
            for index in range(8)
        ]
        assert [future.result() for future in futures] == list(range(8))
    assert time.monotonic() - started < 1.2


def test_timeout_uses_method_limit(tmp_path: Path) -> None:
    runtime = make_harness(tmp_path, timeouts={"test.sleep": 0.1})
    runtime.supervisor.start("org.example.fake")
    try:
        with pytest.raises(RpcTimeoutError):
            runtime.call("test.sleep", {"seconds": 0.5, "value": 1})
        assert runtime.supervisor.is_running("org.example.fake")
    finally:
        runtime.supervisor.shutdown()


def test_message_limit_is_exactly_64mb() -> None:
    base = {"jsonrpc": "2.0", "id": 1, "result": ""}
    overhead = len(encode_frame(base))
    payload = "x" * (MAX_MESSAGE_BYTES - overhead)
    raw = encode_frame({"jsonrpc": "2.0", "id": 1, "result": payload})
    assert len(raw) == MAX_MESSAGE_BYTES
    assert decode_frame(raw)["id"] == 1

    with pytest.raises(MessageTooLarge):
        encode_frame({"jsonrpc": "2.0", "id": 1, "result": payload + "x"})
    with pytest.raises(MessageTooLarge):
        decode_frame(b"{" + b"x" * MAX_MESSAGE_BYTES)


def test_unknown_exception_does_not_leak_details(harness: Harness) -> None:
    with pytest.raises(ResourceUnavailable) as error:
        harness.call("test.raise", {"kind": "unknown"})
    assert error.value.message == "插件内部错误"
    assert "堆栈" not in error.value.message


def test_unknown_value_error_is_internal_error(harness: Harness) -> None:
    with pytest.raises(ResourceUnavailable) as error:
        harness.call("test.raise", {"kind": "value_error"})
    assert error.value.message == "插件内部错误"
    assert "内部细节" not in error.value.message


def test_standard_jsonrpc_error_is_unavailable(harness: Harness) -> None:
    with pytest.raises(PluginUnavailable) as error:
        harness.call("test.standard_error")
    assert "运行时内部错误" in str(error.value)


def test_sdk_exceptions_are_restored(harness: Harness) -> None:
    with pytest.raises(ResourceUnavailable) as error:
        harness.call("test.raise", {"kind": "resource_unavailable"})
    assert error.value.code == "resource_rate_limited"
    assert error.value.fatal is True
    assert error.value.status_code == 429

    with pytest.raises(MetadataNotFound):
        harness.call("test.raise", {"kind": "metadata_not_found"})
    with pytest.raises(InvalidSourceConfig):
        harness.call("test.raise", {"kind": "invalid_source_config"})


def test_proxy_change_sends_config_changed(harness: Harness) -> None:
    before = harness.call("test.config_changes")
    assert before == {"proxy": None, "count": 0}

    harness.proxy["value"] = "http://127.0.0.1:7890"
    harness.supervisor.config_changed("org.example.fake")
    wait_until(lambda: harness.call("test.config_changes")["count"] == 1)
    assert harness.call("test.context")["proxy"] == "http://127.0.0.1:7890"


def test_crash_restarts_plugin_without_affecting_others(tmp_path: Path) -> None:
    runtime = make_harness(
        tmp_path,
        modes={"org.example.fake": "", "org.example.fake2": ""},
    )
    runtime.supervisor.start("org.example.fake")
    runtime.supervisor.start("org.example.fake2")
    try:
        with pytest.raises(PluginUnavailable):
            runtime.call("test.crash", plugin_id="org.example.fake")
        # 另一个插件在第一个插件崩溃和重启期间一直可用
        assert (
            runtime.call("test.echo", {"value": "other"}, plugin_id="org.example.fake2") == "other"
        )
        wait_until(
            lambda: (
                runtime.supervisor.state("org.example.fake") == "running"
                and _can_call(runtime, "org.example.fake")
            )
        )
        assert runtime.call("test.echo", {"value": "ok"}) == "ok"
        assert runtime.supervisor.is_running("org.example.fake2")
    finally:
        runtime.supervisor.shutdown()


def test_consecutive_failures_enter_failed(tmp_path: Path) -> None:
    runtime = make_harness(
        tmp_path,
        modes={"org.example.fake": "crash_after_start", "org.example.fake2": ""},
        max_failures=5,
    )
    runtime.supervisor.start("org.example.fake")
    runtime.supervisor.start("org.example.fake2")
    try:
        wait_state(runtime.supervisor, "org.example.fake", "failed", timeout=15.0)
        assert runtime.supervisor.failure_count("org.example.fake") == 5
        assert runtime.call("test.echo", {"value": "ok"}, plugin_id="org.example.fake2") == "ok"
        assert runtime.supervisor.is_running("org.example.fake2")
    finally:
        runtime.supervisor.shutdown()


def test_shutdown_timeout_kills_process(tmp_path: Path) -> None:
    runtime = make_harness(
        tmp_path,
        modes={"org.example.fake": "ignore_shutdown"},
        timeouts={"plugin.shutdown": 0.1},
    )
    runtime.supervisor.start("org.example.fake")
    runtime.supervisor.stop("org.example.fake")
    assert runtime.supervisor.state("org.example.fake") == "stopped"
    with pytest.raises(PluginStartFailed):
        runtime.client()
    runtime.supervisor.shutdown()


def test_flush_timeout_kills_and_restarts(tmp_path: Path) -> None:
    runtime = make_harness(
        tmp_path,
        modes={"org.example.fake": "ignore_flush"},
        timeouts={"plugin.flush": 0.1},
    )
    runtime.supervisor.start("org.example.fake")
    try:
        assert runtime.supervisor.flush("org.example.fake") is False
        wait_until(lambda: _can_call(runtime, "org.example.fake"))
        assert runtime.call("test.echo", {"value": "after-restart"}) == "after-restart"
    finally:
        runtime.supervisor.shutdown()


def test_admin_request(harness: Harness) -> None:
    manifest = harness.supervisor.manifest("org.example.fake")
    assert manifest is not None
    providers = providers_for(
        lambda: harness.client(), manifest, harness.plugins_dir / "org.example.fake"
    )
    admin = providers["admin.ui"]
    assert isinstance(admin, RemoteAdminUiProvider)
    response = admin.handle(
        PluginRequest(
            method="POST",
            path="echo",
            query={"a": "2"},
            body={"hello": "世界"},
            user_id=7,
        )
    )
    assert isinstance(response, PluginResponse)
    assert response.body == {
        "method": "POST",
        "path": "echo",
        "query": {"a": "2"},
        "body": {"hello": "世界"},
        "user_id": 7,
    }
    with pytest.raises(PluginRequestError) as error:
        admin.handle(PluginRequest("GET", "missing", {}, None, 1))
    assert error.value.code == "not_found"
    with pytest.raises(PluginRequestError) as error:
        admin.handle(PluginRequest("GET", "rejected", {}, None, 1))
    assert error.value.status == 403


def test_resource_changed_callback(harness: Harness) -> None:
    harness.call("test.changed", {"source_id": 12, "locators": ["a", "b"]})
    wait_until(lambda: harness.changed == [("org.example.fake", 12, ["a", "b"])])


def test_http_host_calls(tmp_path: Path) -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, text="ok")

    runtime = make_harness(tmp_path, http_transport=httpx2.MockTransport(handler))
    runtime.supervisor.start("org.example.fake")
    try:
        # 测试里用公网 IP 字面量通过外部插件的 SSRF 检查，实际连接由 MockTransport 接管
        http = runtime.call("test.http", {"url": "https://93.184.216.34/api"})
        assert http == {"status": 200, "text": "ok", "url": "https://93.184.216.34/api"}
        assert requests[0].headers["host"] == "93.184.216.34"
    finally:
        runtime.supervisor.shutdown()


def test_host_rejection_reaches_plugin(harness: Harness) -> None:
    result = harness.call("test.host_rejected")

    assert result["code"] == "host_rejected"
    assert "TRACE" in result["message"]


def test_log_notification_is_logged(harness: Harness, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    harness.call("test.log")
    wait_until(lambda: "运行时日志测试" in caplog.text)
    assert "org.example.fake" in caplog.text
    assert "org.example.fake" in caplog.text


def test_standard_logging_is_forwarded(harness: Harness, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")

    harness.call("test.std_log")

    wait_until(lambda: "标准 logging 日志" in caplog.text)
    assert "org.example.fake" in caplog.text
    assert "标准 logs 异常" in caplog.text
    assert "Traceback" in caplog.text


def test_remote_resource_description_rejects_unknown_capabilities() -> None:
    class _Client:
        def call(self, method: str, params: Any, *, waiter: Any = None) -> Any:
            assert method == "resource.describe"
            return {
                "id": "fake.source",
                "multiple": True,
                "capabilities": ["status", "unknown"],
                "config_fields": [],
            }

    provider = RemoteResourceProvider(lambda: cast(Any, _Client()))

    with pytest.raises(ResourceUnavailable, match="未知的资源能力"):
        _ = provider.capabilities


def test_remote_metadata_and_subtitle_providers(harness: Harness) -> None:
    manifest = harness.supervisor.manifest("org.example.fake")
    assert manifest is not None
    providers = providers_for(
        lambda: harness.client(), manifest, harness.plugins_dir / "org.example.fake"
    )
    metadata = providers["metadata.details"]
    assert isinstance(metadata, RemoteMetadataProvider)
    assert metadata.id == "org.example.fake"
    assert metadata.unavailable_reason() is None
    candidates = metadata.search(MetadataSearchRequest(title="电影", year=2024, kind="movie"))
    assert candidates[0].remote_id == "remote-1"
    assert metadata.find("movie", "tmdb", "1") == "remote-1"
    record = metadata.details("remote-1", kind="movie")
    assert record.title == "条目 remote-1"
    assert metadata.season("remote-1", 2).number == 2
    assert metadata.page_url("movie", "remote-1") == "https://example.test/movie/remote-1"
    with pytest.raises(MetadataNotFound):
        metadata.details("missing", kind="movie")
    with pytest.raises(MetadataUnavailable):
        metadata.details("standard-error", kind="movie")

    subtitles = providers["subtitle.download"]
    assert isinstance(subtitles, RemoteSubtitleProvider)
    found = subtitles.search(SubtitleSearchRequest(media=_media()))
    assert found[0].remote_id == "sub-1"
    downloaded = subtitles.download(found[0])
    assert downloaded.content.startswith(b"1\n")


def test_scan_progress_heartbeat_renews_timeout(tmp_path: Path) -> None:
    runtime = make_harness(tmp_path, timeouts={"resource.scan": 0.1})
    runtime.supervisor.start("org.example.fake")
    try:
        provider = RemoteResourceProvider(lambda: runtime.client())
        source = provider.open(1, {"root": "/media", "scan_progress": 10})
        # 每次等待前发 progress，宿主按心跳把 120 秒空闲超时重新计算
        assert source.scan(None)
    finally:
        runtime.supervisor.shutdown()


def test_progress_as_request_is_rejected(harness: Harness) -> None:
    result = harness.call("test.progress_request")

    assert result["code"] == "host_call_failed"
    assert "progress 必须是通知" in result["message"]


@pytest.mark.parametrize(
    "params",
    [
        {"until": None, "reason": ""},
        {"until": None, "reason": "source_busy_wait"},
        {"until": "2026-01-02T03:04:05+00:00"},
        {"until": "not-an-iso-date", "reason": "rate_limited"},
        {"until": "2026-01-02T03:04:05", "reason": "rate_limited"},
    ],
)
def test_invalid_progress_notification_is_dropped(
    harness: Harness,
    caplog: pytest.LogCaptureFixture,
    params: dict[str, Any],
) -> None:
    caplog.set_level("ERROR", logger="app.plugins.rpc")

    harness.call("test.control", {"domain": "source", "op": "progress", "params": params})

    assert harness.call("test.echo", {"value": "alive"}) == "alive"
    assert harness.supervisor.state("org.example.fake") == "running"
    assert harness.supervisor.failure_count("org.example.fake") == 0
    assert "progress 通知参数不合法" in caplog.text


def test_reader_unexpected_exception_restarts_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_harness(tmp_path)
    original = PluginRpcClient._handle_request
    armed = threading.Event()

    def fail_once(client: PluginRpcClient, message: dict[str, Any]) -> None:
        if armed.is_set():
            armed.clear()
            raise RuntimeError("通知处理意外失败")
        original(client, message)

    monkeypatch.setattr(PluginRpcClient, "_handle_request", fail_once)
    runtime.supervisor.start("org.example.fake")
    try:
        armed.set()
        with pytest.raises(PluginUnavailable):
            runtime.call("test.log")
        wait_until(lambda: runtime.supervisor.failure_count("org.example.fake") >= 1)
        wait_until(lambda: _can_call(runtime, "org.example.fake"))
        assert runtime.supervisor.state("org.example.fake") == "running"
        assert runtime.call("test.echo", {"value": "ok"}) == "ok"
    finally:
        runtime.supervisor.shutdown()


def test_client_rejects_closed_connection(harness: Harness) -> None:
    harness.client().close()

    with pytest.raises(PluginStartFailed):
        harness.supervisor.client("org.example.fake")


def test_flush_all_collects_timeouts(tmp_path: Path) -> None:
    runtime = make_harness(
        tmp_path,
        modes={"org.example.fake": "ignore_flush"},
        timeouts={"plugin.flush": 0.1},
    )
    runtime.supervisor.start("org.example.fake")
    try:
        assert runtime.supervisor.flush_all() == ["org.example.fake"]
    finally:
        runtime.supervisor.shutdown()


def test_remote_resource_provider_describe_and_status(harness: Harness) -> None:
    provider = RemoteResourceProvider(lambda: harness.client())
    assert provider.id == "fake.source"
    assert provider.multiple is True
    assert provider.capabilities == frozenset({"status", "changes", "delete", "known_scan"})
    assert provider.config_fields[0].key == "root"
    assert provider.validate_config({"root": "/media"}) == {"root": "/media"}
    with pytest.raises(InvalidSourceConfig):
        provider.validate_config({"root": "invalid"})
    provider.check({"root": "/media"})
    assert provider.status({"root": "/media"}) == SourceStatus("ok")
    assert provider.status({"root": "bad"}).state == "failed"
    source = provider.open(1, {"root": "/media"})
    listing = source.browse()
    assert listing.locator == "root"
    assert listing.entries
    with pytest.raises(ResourceNotFound):
        source.browse("missing")


def test_remote_resource_provider_surfaces_unavailable(tmp_path: Path) -> None:
    runtime = make_harness(tmp_path, modes={"org.example.fake": "ignore_shutdown"})
    runtime.supervisor.start("org.example.fake")
    provider = RemoteResourceProvider(lambda: runtime.client())
    runtime.supervisor.client("org.example.fake").close()
    try:
        with pytest.raises(ResourceUnavailable):
            provider.status({"root": "/media"})
    finally:
        runtime.supervisor.shutdown()


class TestFakeRemoteResourceContract(testing.ResourceSourceContract):
    """用真实子进程 RPC 跑 SDK 的资源契约测试。"""

    @pytest.fixture(autouse=True)
    def _contract_runtime(self, harness: Harness) -> None:
        self._provider = RemoteResourceProvider(lambda: harness.client())
        self._contract_source_instance = self._provider.open(1, {"root": "/media"})

    def make_source(self) -> Any:
        return self._contract_source_instance

    def make_provider(self) -> Any:
        return self._provider

    def expected_paths(self) -> set[str]:
        return {"电影/a.mkv", "电影/b.srt", "其他/c.nfo"}

    def delete_target(self) -> str | None:
        return "f1"

    def unselectable_locator(self) -> str | None:
        return "rejected"


def _media() -> MediaContext:
    return MediaContext(title="电影", kind="movie")


def _can_call(runtime: Harness, plugin_id: str) -> bool:
    try:
        runtime.call("test.echo", {"value": "probe"}, plugin_id=plugin_id)
    except Exception:
        return False
    return True
