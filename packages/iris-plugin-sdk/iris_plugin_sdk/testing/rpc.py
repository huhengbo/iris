"""测试用的宿主端 JSON-RPC 客户端：拉起真实插件子进程并处理反向通知。

`iris_plugin_sdk.testing.RpcClient` 同时支持 SDK 自己的假插件和插件包的冒烟测试。宿主能力
（http）在内存里实现，测试可以检查 `http_requests` 和 `http_response`。
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, cast

from iris_plugin_sdk import codec
from iris_plugin_sdk.types import PluginHttpResponse


class RpcClient:
    """按 PROTOCOL.md 的 NDJSON 帧格式与插件子进程通信。"""

    def __init__(
        self,
        plugin_dir: Path,
        *,
        module: str,
        http_response: PluginHttpResponse | None = None,
    ) -> None:
        plugin_dir = plugin_dir.resolve()
        env = dict(os.environ)
        paths = [str(plugin_dir / "src"), str(plugin_dir)]
        if env.get("PYTHONPATH"):
            paths.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(paths)
        self.process = subprocess.Popen(
            [sys.executable, "-m", module],
            cwd=plugin_dir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        assert self.process.stderr is not None
        self._stdin = self.process.stdin
        self._messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self._next_id = 0
        self.logs: list[dict[str, Any]] = []
        self.progress: list[dict[str, Any]] = []
        self.changes: list[dict[str, Any]] = []
        self.http_requests: list[dict[str, Any]] = []
        self.http_response = http_response or PluginHttpResponse(
            status_code=200,
            headers={},
            content=b"",
            url="https://example.test/",
        )
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for raw in self.process.stdout:
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if "method" in message:
                self._handle_host_call(message)
            else:
                self._messages.put(message)

    def _drain_stderr(self) -> None:
        assert self.process.stderr is not None
        for _ in self.process.stderr:
            pass

    def _handle_host_call(self, message: dict[str, Any]) -> None:
        method = message["method"]
        params = codec.decode(message.get("params", {}))
        if "id" not in message:
            if method == "log":
                self.logs.append(params)
            elif method == "progress":
                self.progress.append(params)
            elif method == "resource.changed":
                self.changes.append(params)
            return
        try:
            result = self._host_call(method, params)
        except Exception as exc:  # noqa: BLE001 - 测试客户端把错误回给插件
            self._write(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "error": {
                        "code": -32000,
                        "message": str(exc),
                        "data": codec.encode_exception(exc),
                    },
                }
            )
            return
        self._write({"jsonrpc": "2.0", "id": message["id"], "result": codec.encode(result)})

    def _host_call(self, method: str, params: Any) -> Any:
        if not isinstance(params, dict):
            raise ValueError("params 必须是对象")
        if method == "http.request":
            self.http_requests.append(dict(params))
            return self.http_response
        raise ValueError(f"测试客户端没有实现宿主方法：{method}")

    def request(self, method: str, params: dict[str, Any], timeout: float = 15.0) -> Any:
        self._next_id += 1
        request_id = self._next_id
        self._write(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": codec.encode(params)}
        )
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"等待 {method} 超时")
            message = self._messages.get(timeout=remaining)
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise codec.decode_exception(message["error"].get("data"))
            return codec.decode(message.get("result"))

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": codec.encode(params)})

    def initialize(
        self, data_dir: Path, proxy: str | None = None, media_dir: Path | None = None
    ) -> dict[str, Any]:
        # 宿主总是下发媒体目录；测试默认造一个，需要真实目录的用例显式传入。
        media = media_dir or data_dir.parent / "media"
        return cast(
            dict[str, Any],
            self.request(
                "initialize",
                {
                    "sdk_version": "1.0.0",
                    "data_dir": str(data_dir),
                    "media_dir": str(media),
                    "proxy": proxy,
                    "host": {"version": "test", "url": "http://host.test"},
                },
            ),
        )

    def _write(self, message: dict[str, Any]) -> None:
        raw = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
        self._stdin.write(raw)
        self._stdin.flush()

    def close(self) -> None:
        if self.process.poll() is None:
            with contextlib.suppress(TimeoutError, BrokenPipeError, ValueError, queue.Empty):
                self.request("plugin.shutdown", {}, timeout=5)
        with contextlib.suppress(OSError):
            self._stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
