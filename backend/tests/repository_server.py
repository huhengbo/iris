"""测试用的本地插件仓库：一个真实的 127.0.0.1 HTTP 服务。

提供 index.json 和插件包，支持 ETag/304、模拟 500，并记录收到的请求头，供仓库拉取和
插件下载的端到端测试使用。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from tests.plugin_env import FAKE_PLUGIN, install_venv


class RepositoryHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int]) -> None:
        super().__init__(address, partial(_Handler, directory=self))
        self.files: dict[str, bytes] = {}
        self.redirects: dict[str, str] = {}
        self.requests: list[tuple[str, dict[str, str]]] = []
        self.fail = False

    @property
    def base_url(self) -> str:
        host = str(self.server_address[0])
        port = int(self.server_address[1])
        return f"http://{host}:{port}"

    def index_url(self) -> str:
        return f"{self.base_url}/index.json"

    def add_file(self, path: str, data: bytes) -> None:
        self.files[path] = data


class _Handler(BaseHTTPRequestHandler):
    def __init__(
        self,
        request: Any,
        client_address: Any,
        server: RepositoryHTTPServer,
        *,
        directory: RepositoryHTTPServer,
    ) -> None:
        self.repository = directory
        super().__init__(request, client_address, server)

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        self.repository.requests.append((path, dict(self.headers)))
        if self.repository.fail:
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        location = self.repository.redirects.get(path)
        if location is not None:
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data = self.repository.files.get(path)
        if data is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        etag = '"' + hashlib.sha256(data).hexdigest() + '"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", _content_type(path))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        return


def _content_type(path: str) -> str:
    if path.endswith(".json"):
        return "application/json"
    if path.endswith(".zip"):
        return "application/zip"
    return "application/octet-stream"


def make_plugin_zip(
    target: Path,
    *,
    version: str = "1.0.0",
    plugin_id: str = "org.example.fake",
    name: str = "测试假插件",
    entry: str = "fake_plugin",
    provides: tuple[str, ...] | None = None,
    capabilities: tuple[str, ...] | None = None,
) -> Path:
    """按仓库包规范（文件在根目录）打包 tests/fixtures/fake_plugin。"""
    source = target.parent / f".package-{target.stem}"
    shutil.rmtree(source, ignore_errors=True)
    shutil.copytree(
        FAKE_PLUGIN,
        source,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", "pyproject.toml", "uv.lock"),
    )
    manifest_path = source / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "id": plugin_id,
            "name": name,
            "version": version,
            "entry": entry,
            "sdk": ">=1.0,<2.0",
        }
    )
    if provides is not None:
        manifest["provides"] = list(provides)
    if capabilities is not None:
        manifest["capabilities"] = list(capabilities)
    if "admin.ui" not in manifest["provides"]:
        manifest.pop("ui", None)
        manifest.pop("pages", None)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (source / "pyproject.toml").write_text(
        f"""[project]
name = "iris-plugin-fake"
version = "{version}"
requires-python = ">=3.13"
dependencies = ["iris-plugin-sdk>=1.0,<2"]

[build-system]
requires = ["setuptools>=80"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
py-modules = ["fake_plugin", "fake_state"]
""",
        encoding="utf-8",
    )
    (source / "uv.lock").write_text(
        f"""version = 1
revision = 3

[[package]]
name = "iris-plugin-fake"
version = "{version}"
source = {{ virtual = "." }}
""",
        encoding="utf-8",
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            archive.write(path, path.relative_to(source).as_posix())
    shutil.rmtree(source, ignore_errors=True)
    return target


def index_entry(
    *,
    plugin_id: str = "org.example.fake",
    name: str = "测试假插件",
    version: str = "1.0.0",
    url: str,
    archive: Path,
    sdk: str = ">=1.0,<2.0",
    provides: tuple[str, ...] | None = None,
    capabilities: tuple[str, ...] | None = None,
    sha256: str | None = None,
    size: int | None = None,
) -> dict[str, Any]:
    data = archive.read_bytes()
    return {
        "id": plugin_id,
        "name": name,
        "description": "端到端测试用插件",
        "version": version,
        "sdk": sdk,
        "provides": list(
            provides
            if provides is not None
            else (
                "resource.source",
                "metadata.search",
                "metadata.details",
                "metadata.images",
                "metadata.people",
                "image.fetch",
                "subtitle.search",
                "subtitle.download",
                "admin.ui",
            )
        ),
        "capabilities": list(capabilities if capabilities is not None else ("logger", "http")),
        "url": url,
        "sha256": sha256 or hashlib.sha256(data).hexdigest(),
        "size": size if size is not None else len(data),
        "homepage": None,
        "signature": None,
    }


def publish_index(
    server: RepositoryHTTPServer,
    entries: list[dict[str, Any]],
    *,
    schema: int = 1,
    name: str = "测试插件仓库",
    path: str = "/index.json",
) -> dict[str, Any]:
    index = {
        "schema": schema,
        "name": name,
        "generated_at": "2026-01-01T00:00:00Z",
        "plugins": entries,
    }
    server.add_file(path, json.dumps(index, ensure_ascii=False).encode("utf-8"))
    return index


class FakeInstaller:
    """不联网的依赖安装器：用测试解释器建立 .venv，并记录收到的代理。"""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, str | None]] = []
        self.fail: Exception | None = None

    def install(self, directory: Path, *, proxy: str | None) -> None:
        self.calls.append((directory, proxy))
        if self.fail is not None:
            raise self.fail
        shutil.rmtree(directory / ".venv", ignore_errors=True)
        install_venv(directory)
