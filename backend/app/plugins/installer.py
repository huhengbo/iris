"""插件依赖安装。

默认实现调用运行时的 ``uv``：先导出带哈希的依赖清单，再装进插件自己的 venv，最后用
``--no-deps`` 安装插件本身。不使用 ``uv sync --frozen``，因为它直接使用 ``uv.lock`` 里的
下载地址，不认 ``UV_DEFAULT_INDEX`` 等镜像配置（astral-sh/uv#19625）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Protocol

INSTALL_TIMEOUT_SECONDS = 15 * 60
PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
PASSTHROUGH_ENV_KEYS = (
    "UV_DEFAULT_INDEX",
    "UV_INDEX_URL",
    "UV_INDEX",
    "UV_EXTRA_INDEX_URL",
    "SSL_CERT_FILE",
    "UV_NATIVE_TLS",
)
BASE_ENV_KEYS = ("PATH", "HOME", "LANG")
STDERR_TAIL_LINES = 50


class DependencyInstallError(Exception):
    """依赖安装失败；message 里带 uv 输出的最后几十行。"""


class DependencyInstaller(Protocol):
    """安装器接口；测试注入假实现，生产用 :class:`UvInstaller`。"""

    def install(self, directory: Path, *, proxy: str | None) -> None: ...


class UvInstaller:
    def __init__(
        self,
        cache_dir: Path,
        *,
        uv: str | None = None,
        timeout: float = INSTALL_TIMEOUT_SECONDS,
    ) -> None:
        self._uv = uv or shutil.which("uv") or "uv"
        self._cache_dir = cache_dir
        self._timeout = timeout

    def install(self, directory: Path, *, proxy: str | None) -> None:
        directory = directory.resolve()
        python = getattr(sys, "_base_executable", sys.executable)
        venv = directory / ".venv"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        env = self._environment(proxy)
        with tempfile.TemporaryDirectory(dir=directory, prefix=".requirements-") as temporary:
            requirements = Path(temporary) / "requirements.txt"
            self._run(
                directory,
                env,
                [
                    self._uv,
                    "venv",
                    str(venv),
                    "--python",
                    python,
                    "--clear",
                ],
            )
            self._run(
                directory,
                env,
                [
                    self._uv,
                    "export",
                    "--frozen",
                    "--no-dev",
                    "--no-emit-project",
                    "--project",
                    str(directory),
                    "--output-file",
                    str(requirements),
                ],
            )
            self._run(
                directory,
                env,
                [
                    self._uv,
                    "pip",
                    "install",
                    "--python",
                    str(venv / "bin" / "python"),
                    "--require-hashes",
                    "--no-progress",
                    "--requirement",
                    str(requirements),
                ],
            )
            self._run(
                directory,
                env,
                [
                    self._uv,
                    "pip",
                    "install",
                    "--python",
                    str(venv / "bin" / "python"),
                    "--no-deps",
                    "--no-editable",
                    str(directory),
                ],
            )

    def _environment(self, proxy: str | None) -> dict[str, str]:
        env = {key: os.environ[key] for key in BASE_ENV_KEYS if key in os.environ}
        env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
        env.update(
            {
                "UV_CACHE_DIR": str(self._cache_dir),
                "UV_PYTHON_DOWNLOADS": "never",
                "UV_LINK_MODE": "copy",
                "UV_COMPILE_BYTECODE": "1",
                "UV_NO_CONFIG": "1",
            }
        )
        for key in PASSTHROUGH_ENV_KEYS:
            value = os.environ.get(key)
            if value:
                env[key] = value
        if proxy:
            for key in PROXY_ENV_KEYS:
                env[key] = proxy
        return env

    def _run(self, cwd: Path, env: dict[str, str], command: list[str]) -> None:
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise DependencyInstallError(f"找不到 uv：{self._uv}") from exc
        except subprocess.TimeoutExpired as exc:
            raise DependencyInstallError("安装依赖超时") from exc
        if result.returncode == 0:
            return
        output = (result.stdout or "") + (result.stderr or "")
        tail = "\n".join(output.strip().splitlines()[-STDERR_TAIL_LINES:])
        command_name = " ".join(command[:3])
        raise DependencyInstallError(f"{command_name} 失败：\n{tail}")
