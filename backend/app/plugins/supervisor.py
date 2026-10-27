"""进程外插件的发现、拉起、握手和看护。

宿主扫描插件根目录下的 ``plugin.json`` 发现插件，用插件自己的虚拟环境拉起入口模块，通过
stdin/stdout 上的 JSON-RPC 与它通信。安装目录名可以和 manifest.id 不同，插件身份以
manifest.id 为准。同一插件的启停串行，不同插件各有自己的锁和看护线程，互不阻塞。
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Literal, cast

import httpx2
from iris_plugin_sdk import (
    SDK_RANGE,
    SDK_VERSION,
    PluginManifest,
    PluginManifestError,
    PluginSdkIncompatibleError,
    load_manifest,
    sdk_compatible,
)

from app.plugins.host import HOST_VERSION, PluginHttp
from app.plugins.rpc import (
    METHOD_TIMEOUTS,
    PluginRpcClient,
    PluginUnavailable,
)

logger = logging.getLogger(__name__)

PluginState = Literal["stopped", "starting", "running", "restarting", "failed"]
# 首次崩溃等 1 秒，之后翻倍直到 60 秒；连续失败 5 次进入 failed。
DEFAULT_BACKOFF_INITIAL = 1.0
DEFAULT_BACKOFF_MAX = 60.0
DEFAULT_MAX_FAILURES = 5
# 进程稳定运行超过这个时间后，下一次崩溃重新从 1 秒退避开始。
DEFAULT_STABLE_RUNTIME = 60.0
# 插件异常退出时随警告一起记下的 stderr 行数
STDERR_TAIL_LINES = 50


class PluginRuntimeError(Exception):
    """插件运行时错误；code 是宿主对外使用的错误码。"""

    code = "plugin_runtime_error"

    def __init__(self, plugin_id: str, message: str) -> None:
        super().__init__(message)
        self.plugin_id = plugin_id
        self.message = message


class PluginSdkIncompatible(PluginRuntimeError):
    code = "plugin_sdk_incompatible"


class PluginManifestMismatch(PluginRuntimeError):
    code = "plugin_manifest_mismatch"


class PluginManifestInvalid(PluginRuntimeError):
    code = "plugin_manifest_invalid"


class PluginNotInstalled(PluginRuntimeError):
    code = "plugin_not_installed"


class PluginStartFailed(PluginRuntimeError):
    code = "plugin_start_failed"


@dataclass(frozen=True)
class DiscoveredPlugin:
    plugin_id: str
    directory: Path
    manifest: PluginManifest | None
    error: PluginRuntimeError | None
    built_in: bool


@dataclass
class _Plugin:
    plugin_id: str
    directory: Path
    manifest: PluginManifest | None
    discovery_error: PluginRuntimeError | None
    built_in: bool
    lock: threading.RLock
    stop_event: threading.Event
    state: PluginState
    process: subprocess.Popen[bytes] | None = None
    client: PluginRpcClient | None = None
    failure_count: int = 0
    generation: int = 0
    started_at: float = 0.0
    suspended: bool = False
    last_error: PluginRuntimeError | None = None


class PluginSupervisor:
    """插件进程管理器。所有运行状态只存在内存里，启用状态由调用方（数据库）决定。"""

    def __init__(
        self,
        plugins_dir: Path,
        data_dir: Path,
        *,
        media_dir: Path,
        host_version: str = HOST_VERSION,
        host_url: Callable[[], str | None] = lambda: None,
        proxy: Callable[[str], str | None] = lambda _: None,
        on_resource_changed: Callable[[str, int, list[str]], None] | None = None,
        http_transport: httpx2.BaseTransport | None = None,
        timeouts: Mapping[str, float] | None = None,
        backoff_initial: float = DEFAULT_BACKOFF_INITIAL,
        backoff_max: float = DEFAULT_BACKOFF_MAX,
        max_failures: int = DEFAULT_MAX_FAILURES,
        stable_runtime: float = DEFAULT_STABLE_RUNTIME,
        log: logging.Logger = logger,
    ) -> None:
        self._plugins_dir = plugins_dir.resolve()
        self._data_dir = data_dir.resolve()
        self._host_version = host_version
        self._host_url = host_url
        self._media_dir = media_dir.resolve()
        self._proxy = proxy
        self._on_resource_changed = on_resource_changed
        self._http_transport = http_transport
        self._timeouts = dict(timeouts or {})
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        self._max_failures = max_failures
        self._stable_runtime = stable_runtime
        self._log = log
        self._lock = threading.RLock()
        self._plugins: dict[str, _Plugin] = {}
        self._discovered = False

    def set_resource_changed_handler(self, handler: Callable[[str, int, list[str]], None]) -> None:
        """来源插件推送目录变动时的回调；在 compose 根装配完成后接入定向同步。"""
        self._on_resource_changed = handler

    # ---- 发现 ----

    def discover(self) -> list[DiscoveredPlugin]:
        """扫描插件根目录；已经发现过时不再重复扫描。"""
        with self._lock:
            if self._discovered:
                return self.discovered()
            self._discovered = True
            if not self._plugins_dir.is_dir():
                self._log.info("插件目录不存在，没有可加载的插件：%s", self._plugins_dir)
                return []
            for directory in sorted(self._plugins_dir.iterdir()):
                if not directory.is_dir() or not (directory / "plugin.json").is_file():
                    continue
                record = self._load(directory)
                if record.plugin_id in self._plugins:
                    self._log.error("插件 id 重复，忽略：%s", directory)
                    continue
                self._plugins[record.plugin_id] = record
            return self.discovered()

    def discovered(self) -> list[DiscoveredPlugin]:
        with self._lock:
            return [
                DiscoveredPlugin(
                    plugin_id=record.plugin_id,
                    directory=record.directory,
                    manifest=record.manifest,
                    error=record.discovery_error,
                    built_in=record.built_in,
                )
                for record in sorted(self._plugins.values(), key=lambda item: item.plugin_id)
            ]

    def manifests(self) -> list[PluginManifest]:
        return [item.manifest for item in self.discovered() if item.manifest is not None]

    def manifest(self, plugin_id: str) -> PluginManifest | None:
        with self._lock:
            record = self._plugins.get(plugin_id)
        return None if record is None else record.manifest

    def _load(self, directory: Path) -> _Plugin:
        try:
            manifest = load_manifest(directory)
        except PluginSdkIncompatibleError as exc:
            plugin_id = directory.name
            return _Plugin(
                plugin_id=plugin_id,
                directory=directory,
                manifest=None,
                discovery_error=PluginSdkIncompatible(plugin_id, str(exc)),
                built_in=True,
                lock=threading.RLock(),
                stop_event=threading.Event(),
                state="failed",
            )
        except PluginManifestError as exc:
            return self._invalid(directory.name, directory, str(exc))
        plugin_id = manifest.id
        # 当前只从宿主自带的插件目录加载，全部按内置插件对待；第三方安装流程不在这一波范围。
        return _Plugin(
            plugin_id=plugin_id,
            directory=directory,
            manifest=manifest,
            discovery_error=None,
            built_in=True,
            lock=threading.RLock(),
            stop_event=threading.Event(),
            state="stopped",
        )

    def _invalid(self, plugin_id: str, directory: Path, message: str) -> _Plugin:
        self._log.error("插件 %s 的 manifest 不合法：%s", plugin_id, message)
        return _Plugin(
            plugin_id=plugin_id,
            directory=directory,
            manifest=None,
            discovery_error=PluginManifestInvalid(plugin_id, message),
            built_in=True,
            lock=threading.RLock(),
            stop_event=threading.Event(),
            state="failed",
        )

    # ---- 状态 ----

    def state(self, plugin_id: str) -> PluginState:
        with self._lock:
            record = self._require(plugin_id)
            with record.lock:
                return record.state

    def error(self, plugin_id: str) -> PluginRuntimeError | None:
        """最近一次发现或启动失败的原因；正常运行时为 None。"""
        with self._lock:
            record = self._require(plugin_id)
            with record.lock:
                return record.discovery_error or record.last_error

    def failure_count(self, plugin_id: str) -> int:
        with self._lock:
            record = self._require(plugin_id)
            with record.lock:
                return record.failure_count

    def is_running(self, plugin_id: str) -> bool:
        return self.state(plugin_id) == "running"

    def pid(self, plugin_id: str) -> int | None:
        """运行中插件进程的 PID；没有运行时返回 None。"""
        with self._lock:
            record = self._require(plugin_id)
            with record.lock:
                if record.process is None or record.process.poll() is not None:
                    return None
                return record.process.pid

    def client(self, plugin_id: str) -> PluginRpcClient:
        """取运行中的连接；没有运行时抛出 PluginStartFailed。"""
        with self._lock:
            record = self._require(plugin_id)
            with record.lock:
                if record.client is None or record.client.closed or record.state != "running":
                    raise PluginStartFailed(plugin_id, f"插件 {plugin_id} 没有运行")
                return record.client

    # ---- 启停 ----

    def start(self, plugin_id: str) -> None:
        """手动启动（或重新启用）；清除失败计数。"""
        with self._lock:
            record = self._require(plugin_id)
        with record.lock:
            if record.process is not None and record.process.poll() is None:
                return
            record.generation += 1
            record.stop_event.clear()
            record.failure_count = 0
            record.suspended = False
            if record.discovery_error is not None:
                record.state = "failed"
                record.last_error = record.discovery_error
                raise record.discovery_error
            record.state = "starting"
            try:
                self._spawn(record)
            except PluginRuntimeError as exc:
                record.failure_count += 1
                record.last_error = exc
                # 手动启动（含宿主启动时拉起）失败后不自动重试，等管理员重新启用。
                record.state = "failed"
                raise
            except Exception as exc:
                error = PluginStartFailed(record.plugin_id, str(exc))
                record.failure_count += 1
                record.last_error = error
                record.state = "failed"
                raise error from exc
            record.last_error = None

    def stop(self, plugin_id: str) -> None:
        """优雅停止插件；shutdown 超时后杀进程。"""
        with self._lock:
            record = self._require(plugin_id)
        with record.lock:
            record.generation += 1
            record.stop_event.set()
            process = record.process
            client = record.client
            if process is None:
                self._kill(record)
                record.state = "stopped"
                return
            try:
                if client is not None and not client.closed:
                    client.call("plugin.shutdown", {}, timeout=self._timeout("plugin.shutdown"))
            except Exception as exc:
                # shutdown 失败不能阻止宿主停掉进程；进程随后会被杀掉。
                self._log.warning("插件 %s 停止失败，杀掉进程：%s", plugin_id, exc)
            finally:
                self._kill(record, process)
                record.state = "stopped"

    def stop_all(self) -> None:
        """停止全部插件，并记住哪些原本在运行，供恢复后重启。"""
        with self._lock:
            records = list(self._plugins.values())
        with ThreadPoolExecutor(max_workers=max(1, len(records))) as pool:
            for record in records:
                with record.lock:
                    record.suspended = record.state == "running"
            futures = [pool.submit(self.stop, record.plugin_id) for record in records]
            for future in futures:
                future.result()

    def start_all(self) -> None:
        """重启由 stop_all 停下的插件；单个插件启动失败只记录日志。"""
        with self._lock:
            records = [record for record in self._plugins.values() if record.suspended]
            for record in records:
                record.suspended = False
        for record in records:
            try:
                self.start(record.plugin_id)
            except Exception:
                self._log.exception("插件 %s 重启失败", record.plugin_id)

    def shutdown(self) -> None:
        """进程退出前停掉全部插件，不再保留重启意图。"""
        with self._lock:
            records = list(self._plugins.values())
            for record in records:
                record.suspended = False
        with ThreadPoolExecutor(max_workers=max(1, len(records))) as pool:
            futures = [pool.submit(self.stop, record.plugin_id) for record in records]
            for future in futures:
                future.result()

    # ---- 生命周期操作 ----

    def flush(self, plugin_id: str) -> bool:
        """备份前 flush 一个插件；失败只记日志并杀掉卡住的进程，不影响备份。"""
        with self._lock:
            record = self._require(plugin_id)
        with record.lock:
            client = record.client
            if client is None or record.state != "running":
                return True
            try:
                client.call("plugin.flush", {}, timeout=self._timeout("plugin.flush"))
            except Exception as exc:
                self._log.warning("插件 %s flush 失败，继续备份：%s", plugin_id, exc)
                self._kill(record)
                return False
            return True

    def flush_all(self) -> list[str]:
        """备份前 flush 全部运行中的插件；返回失败的插件 id，由调用方记录警告。"""
        with self._lock:
            plugin_ids = [
                record.plugin_id for record in self._plugins.values() if record.state == "running"
            ]
        failed: list[str] = []
        if not plugin_ids:
            return failed
        with ThreadPoolExecutor(max_workers=len(plugin_ids)) as pool:
            futures = {plugin_id: pool.submit(self.flush, plugin_id) for plugin_id in plugin_ids}
            for plugin_id, future in futures.items():
                try:
                    if not future.result():
                        failed.append(plugin_id)
                except Exception:
                    # 兜底：flush 自身的日志之外，任何意外都不能中断 flush_all。
                    self._log.exception("插件 %s flush 失败，继续备份", plugin_id)
                    failed.append(plugin_id)
        return failed

    def config_changed(self, plugin_id: str) -> None:
        """代理或启用配置变化时通知插件。"""
        with self._lock:
            record = self._require(plugin_id)
        with record.lock:
            client = record.client
            if client is None or record.state != "running":
                return
            try:
                client.notify(
                    "host.config_changed",
                    {"proxy": self._proxy(plugin_id), "host_url": self._host_url()},
                )
            except PluginUnavailable as exc:
                self._log.warning("插件 %s 已不可用，配置变更未送达：%s", plugin_id, exc)

    def config_changed_all(self) -> None:
        with self._lock:
            plugin_ids = list(self._plugins)
        for plugin_id in plugin_ids:
            self.config_changed(plugin_id)

    # ---- 进程和握手 ----

    def _spawn(self, record: _Plugin) -> None:
        manifest = record.manifest
        assert manifest is not None
        python = record.directory / ".venv" / "bin" / "python"
        if not python.is_file():
            raise PluginStartFailed(record.plugin_id, f"插件缺少虚拟环境解释器：{python}")
        self._log.info("启动插件 %s：%s -m %s", record.plugin_id, python, manifest.entry)
        process = subprocess.Popen(
            [str(python), "-m", manifest.entry],
            cwd=record.directory,
            env=self._environment(record.directory),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        client: PluginRpcClient | None = None
        stderr_reader = threading.Thread(
            target=self._pump_stderr,
            args=(record, process),
            name=f"plugin-stderr-{record.plugin_id}",
            daemon=True,
        )
        stderr_reader.start()
        try:
            client = PluginRpcClient(
                record.plugin_id,
                cast(BinaryIO, process.stdin),
                cast(BinaryIO, process.stdout),
                http=PluginHttp(
                    record.plugin_id,
                    built_in=record.built_in,
                    transport=self._http_transport,
                    proxy=lambda: self._proxy(record.plugin_id),
                ),
                on_resource_changed=lambda source_id, locators: self._resource_changed(
                    record.plugin_id, source_id, locators
                ),
                timeouts=self._timeouts,
                log=self._log,
                on_fatal=lambda _: self._kill(record, process),
            )
            try:
                response = client.call(
                    "initialize",
                    {
                        "sdk_version": SDK_VERSION,
                        "data_dir": str(self._data_dir / "plugins" / record.plugin_id),
                        "proxy": self._proxy(record.plugin_id),
                        "media_dir": str(self._media_dir),
                        "host": {"version": self._host_version, "url": self._host_url()},
                    },
                    timeout=self._timeout("initialize"),
                )
            except PluginSdkIncompatibleError as exc:
                raise PluginSdkIncompatible(record.plugin_id, str(exc)) from exc
            self._validate_handshake(record, response)
        except Exception:
            if client is not None:
                client.close()
            self._kill(record, process)
            raise
        record.process = process
        record.client = client
        record.started_at = time.monotonic()
        record.last_error = None
        record.state = "running"
        generation = record.generation
        threading.Thread(
            target=self._watch,
            args=(record, process, client, generation),
            name=f"plugin-watch-{record.plugin_id}",
            daemon=True,
        ).start()
        self._log.info("插件 %s 已启动", record.plugin_id)

    def _validate_handshake(self, record: _Plugin, response: Any) -> None:
        if not isinstance(response, dict):
            raise PluginStartFailed(record.plugin_id, "initialize 返回的不是对象")
        sdk_version = response.get("sdk_version")
        if not isinstance(sdk_version, str) or not sdk_compatible(SDK_RANGE, sdk_version):
            raise PluginSdkIncompatible(
                record.plugin_id,
                f"插件运行时的 SDK 版本 {sdk_version!r} 不在宿主范围 {SDK_RANGE} 内",
            )
        raw = response.get("manifest")
        if not isinstance(raw, dict):
            raise PluginManifestInvalid(record.plugin_id, "initialize 没有返回 manifest")
        returned_id = raw.get("id")
        if returned_id != record.plugin_id:
            raise PluginManifestMismatch(
                record.plugin_id,
                f"插件返回的 manifest.id {returned_id!r} 和 plugin.json 不一致",
            )
        raw_sdk = raw.get("sdk")
        if not isinstance(raw_sdk, str):
            raise PluginManifestInvalid(record.plugin_id, "initialize 返回的 manifest 缺少 sdk")
        try:
            compatible = sdk_compatible(raw_sdk, SDK_VERSION)
        except PluginManifestError as exc:
            raise PluginManifestInvalid(record.plugin_id, str(exc)) from exc
        if not compatible:
            raise PluginSdkIncompatible(
                record.plugin_id,
                f"插件要求 SDK {raw_sdk}，宿主提供 {SDK_VERSION}",
            )

    def _watch(
        self,
        record: _Plugin,
        process: subprocess.Popen[bytes],
        client: PluginRpcClient,
        generation: int,
    ) -> None:
        process.wait()
        with record.lock:
            if generation != record.generation:
                client.close()
                return
            client.close()
            record.process = None
            record.client = None
            if record.stop_event.is_set():
                return
            if time.monotonic() - record.started_at >= self._stable_runtime:
                record.failure_count = 0
            record.failure_count += 1
            record.last_error = PluginStartFailed(record.plugin_id, "插件进程已退出")
            if record.failure_count >= self._max_failures:
                record.state = "failed"
                self._log.error(
                    "插件 %s 连续失败 %d 次，进入 failed",
                    record.plugin_id,
                    record.failure_count,
                )
                return
            record.state = "restarting"
            self._log.warning(
                "插件 %s 已退出，%.2f 秒后第 %d 次重启",
                record.plugin_id,
                self._backoff(record.failure_count),
                record.failure_count,
            )
        while True:
            with record.lock:
                if record.stop_event.is_set() or generation != record.generation:
                    return
                delay = self._backoff(record.failure_count)
            if record.stop_event.wait(delay):
                return
            with record.lock:
                if record.stop_event.is_set() or generation != record.generation:
                    return
                record.state = "starting"
                try:
                    self._spawn(record)
                    return
                except Exception as exc:
                    record.failure_count += 1
                    record.last_error = (
                        exc
                        if isinstance(exc, PluginRuntimeError)
                        else PluginStartFailed(record.plugin_id, str(exc))
                    )
                    self._log.exception(
                        "插件 %s 第 %d 次重启失败",
                        record.plugin_id,
                        record.failure_count,
                    )
                    if record.failure_count >= self._max_failures:
                        record.state = "failed"
                        return
                    record.state = "restarting"

    def _environment(self, directory: Path) -> dict[str, str]:
        env = {
            key: os.environ[key]
            for key in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
            if key in os.environ
        }
        env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
        env.update(
            {
                "PYTHONUNBUFFERED": "1",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONDONTWRITEBYTECODE": "1",
                "VIRTUAL_ENV": str(directory / ".venv"),
            }
        )
        return env

    def _pump_stderr(self, record: _Plugin, process: subprocess.Popen[bytes]) -> None:
        """插件日志走 log 通知；stderr 只用于诊断进程崩溃。

        逐行只记 debug；进程非正常退出时，把最后几行作为一整块 warning 记下，
        启动阶段的导入错误等堆栈才不会丢。
        """
        assert process.stderr is not None
        tail: deque[str] = deque(maxlen=STDERR_TAIL_LINES)
        for raw in iter(process.stderr.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                tail.append(line)
                self._log.debug("插件 %s stderr: %s", record.plugin_id, line)
        code = process.wait()
        if code != 0 and tail and not record.stop_event.is_set():
            self._log.warning(
                "插件 %s 异常退出（退出码 %s），stderr 最后 %d 行：\n%s",
                record.plugin_id,
                code,
                len(tail),
                "\n".join(tail),
            )

    def _kill(self, record: _Plugin, process: subprocess.Popen[bytes] | None = None) -> None:
        """关闭连接并杀掉进程；启动失败时 process 还没有登记到 record 上。"""
        target = process if process is not None else record.process
        client = record.client
        if record.process is target:
            record.process = None
            record.client = None
        if client is not None:
            client.close()
        if target is None:
            return
        if target.poll() is None:
            target.kill()
        try:
            target.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._log.error("插件进程在杀掉后 5 秒内没有退出：pid=%s", target.pid)

    def _resource_changed(self, plugin_id: str, source_id: int, locators: list[str]) -> None:
        if self._on_resource_changed is not None:
            self._on_resource_changed(plugin_id, source_id, locators)

    def _backoff(self, failures: int) -> float:
        delay: float = self._backoff_initial * (2.0 ** max(0, failures - 1))
        return float(min(delay, self._backoff_max))

    def _timeout(self, method: str) -> float:
        if method in self._timeouts:
            return self._timeouts[method]
        return METHOD_TIMEOUTS[method]

    def _require(self, plugin_id: str) -> _Plugin:
        record = self._plugins.get(plugin_id)
        if record is None:
            raise PluginNotInstalled(plugin_id, f"插件 {plugin_id} 没有安装")
        return record
