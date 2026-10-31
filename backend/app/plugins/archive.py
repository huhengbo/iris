"""插件 zip 的校验和安全解压。

不信任 zip 头里的大小和权限：解压时按实际写入字节计算总大小，拒绝路径穿越、符号链接、
设备文件和重复路径，文件统一写为 0644、目录 0755。包内容必须是自包含的 uv 项目，
依赖只能来自 registry 或 https 包，不能出现需要 git 或本地路径的源。
"""

from __future__ import annotations

import shutil
import stat
import tomllib
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any

from iris_plugin_sdk import (
    PluginManifest,
    PluginManifestError,
    PluginSdkIncompatibleError,
    load_manifest,
)

from app.plugins.index_schema import (
    MAX_ARCHIVE_ENTRIES,
    MAX_PACKAGE_BYTES,
    MAX_UNCOMPRESSED_BYTES,
    PLUGIN_ID,
    VERSION,
)

REQUIRED_FILES = ("plugin.json", "pyproject.toml", "uv.lock")
FORBIDDEN_DIRECTORIES = frozenset({".venv", "tests", "__pycache__"})
FORBIDDEN_FILES = frozenset({"uv.toml"})
_UV_SOURCE_KEYS = frozenset({"registry", "url", "editable", "virtual", "directory"})
_PATH_SOURCE_KEYS = frozenset({"path", "editable", "git"})


class ArchiveError(ValueError):
    """压缩包或包内容不合法。"""


class ArchiveTooLarge(ArchiveError):
    pass


class PackageError(ArchiveError):
    """解压后的插件包内容不合法。"""


class PackageIncompatible(PackageError):
    """插件包声明的 SDK 范围不包含宿主的 SDK；上传预览映射为 409 plugin_incompatible。"""


def check_archive(path: Path, *, max_bytes: int = MAX_PACKAGE_BYTES) -> None:
    """不落盘地检查 zip 的结构、条目数和声明大小。"""
    size = path.stat().st_size
    if size > max_bytes:
        raise ArchiveTooLarge(f"压缩包不能超过 {max_bytes // (1024 * 1024)} MiB")
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            _validate_entries(infos)
            declared = sum(info.file_size for info in infos)
            if declared > MAX_UNCOMPRESSED_BYTES:
                raise ArchiveTooLarge("解压后的内容超过 200 MiB")
            names = {_normalized_name(info) for info in infos}
            missing = [name for name in REQUIRED_FILES if name not in names]
            if missing:
                raise PackageError(f"压缩包缺少文件：{', '.join(missing)}")
    except zipfile.BadZipFile as exc:
        raise ArchiveError("压缩包不是有效的 zip") from exc


def extract_archive(
    path: Path,
    destination: Path,
    *,
    max_bytes: int = MAX_PACKAGE_BYTES,
) -> None:
    """校验并解压到新目录；目录必须不存在或为空。"""
    check_archive(path, max_bytes=max_bytes)
    written = 0
    try:
        destination.mkdir(parents=True, exist_ok=True)
        destination.chmod(0o755)
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            _validate_entries(infos)
            for info in infos:
                name = _normalized_name(info)
                target = destination.joinpath(*PurePosixPath(name).parts)
                if not target.resolve().is_relative_to(destination.resolve()):
                    raise ArchiveError(f"压缩包包含不安全的路径：{info.filename}")
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    target.chmod(0o755)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.parent.chmod(0o755)
                with archive.open(info) as source, target.open("wb") as output:
                    while True:
                        chunk = source.read(64 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > MAX_UNCOMPRESSED_BYTES:
                            raise ArchiveTooLarge("解压后的内容超过 200 MiB")
                        output.write(chunk)
                target.chmod(0o644)
    except OSError as exc:
        shutil.rmtree(destination, ignore_errors=True)
        raise ArchiveError(f"解压失败：{exc.strerror or exc}") from exc
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def check_package(directory: Path) -> PluginManifest:
    """检查解压后的插件包：manifest、依赖声明和锁文件。"""
    for name in REQUIRED_FILES:
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise PackageError(f"插件包缺少文件：{name}")
    try:
        manifest = load_manifest(directory)
    except PluginSdkIncompatibleError as exc:
        raise PackageIncompatible(str(exc)) from exc
    except PluginManifestError as exc:
        raise PackageError(str(exc)) from exc
    if PLUGIN_ID.fullmatch(manifest.id) is None:
        raise PackageError("插件 id 不合法")
    if VERSION.fullmatch(manifest.version) is None:
        raise PackageError("插件版本号不合法")
    _check_pyproject(directory / "pyproject.toml")
    _check_lockfile(directory / "uv.lock")
    return manifest


def _check_pyproject(path: Path) -> None:
    data = _load_toml(path, "pyproject.toml")
    tool = data.get("tool")
    if not isinstance(tool, dict):
        return
    uv = tool.get("uv")
    if not isinstance(uv, dict):
        return
    sources = uv.get("sources")
    if sources is None:
        return
    if not isinstance(sources, dict):
        raise PackageError("pyproject.toml 的 [tool.uv.sources] 必须是表")
    for name, source in sources.items():
        if isinstance(source, dict):
            bad = sorted(set(source) & _PATH_SOURCE_KEYS)
            if bad:
                raise PackageError(f"插件包不能使用 {', '.join(bad)} 依赖源：{name}")
        elif isinstance(source, list):
            for item in source:
                if isinstance(item, dict):
                    bad = sorted(set(item) & _PATH_SOURCE_KEYS)
                    if bad:
                        raise PackageError(f"插件包不能使用 {', '.join(bad)} 依赖源：{name}")


def _check_lockfile(path: Path) -> None:
    data = _load_toml(path, "uv.lock")
    packages = data.get("package")
    if not isinstance(packages, list):
        raise PackageError("uv.lock 缺少 [[package]]")
    for package in packages:
        if not isinstance(package, dict):
            raise PackageError("uv.lock 的 package 必须是表")
        source = package.get("source")
        name = str(package.get("name", "?"))
        if not isinstance(source, dict):
            raise PackageError(f"uv.lock 里的 {name} 缺少 source")
        if "git" in source:
            raise PackageError(f"插件包依赖 git 源，但运行时镜像没有 git：{name}")
        unknown = set(source) - _UV_SOURCE_KEYS
        if unknown:
            raise PackageError(
                f"uv.lock 里的 {name} 使用了不支持的源：{', '.join(sorted(unknown))}"
            )
        if "url" in source:
            url = source["url"]
            if not isinstance(url, str) or not url.startswith("https://"):
                raise PackageError(f"uv.lock 里的 {name} 只能使用 HTTPS 下载地址")
        if "registry" in source and not isinstance(source["registry"], str):
            raise PackageError(f"uv.lock 里的 {name} registry 源不合法")
        for key in ("editable", "virtual", "directory"):
            if key in source and source[key] != ".":
                raise PackageError(f'uv.lock 里的 {name} 只能引用项目自身（{key} = "."）')


def _load_toml(path: Path, label: str) -> dict[str, Any]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise PackageError(f"{label} 不是合法的 TOML：{exc}") from exc
    if not isinstance(data, dict):
        raise PackageError(f"{label} 必须是 TOML 表")
    return data


def _validate_entries(infos: Iterable[zipfile.ZipInfo]) -> None:
    infos = list(infos)
    if len(infos) > MAX_ARCHIVE_ENTRIES:
        raise ArchiveTooLarge(f"压缩包条目不能超过 {MAX_ARCHIVE_ENTRIES} 个")
    names: set[str] = set()
    file_names: set[str] = set()
    for info in infos:
        name = _normalized_name(info)
        if name in names:
            raise ArchiveError(f"压缩包包含重复路径：{name}")
        names.add(name)
        if not info.is_dir():
            file_names.add(name)
        _check_mode(info)
        parts = PurePosixPath(name).parts
        if not parts:
            raise ArchiveError("压缩包包含空路径")
        if any(part in FORBIDDEN_DIRECTORIES for part in parts[:-1]) or (
            parts[-1] in FORBIDDEN_DIRECTORIES and info.is_dir()
        ):
            raise ArchiveError(f"压缩包不能包含目录：{name}")
        if parts[-1] in FORBIDDEN_FILES:
            raise ArchiveError(f"压缩包不能包含文件：{name}")
    # 文件和目录同名（a 与 a/b）无法同时解压
    for name in names:
        parts = PurePosixPath(name).parts
        for end in range(1, len(parts)):
            if "/".join(parts[:end]) in file_names:
                raise ArchiveError(f"压缩包里的文件和目录同名：{'/'.join(parts[:end])}")


def _normalized_name(info: zipfile.ZipInfo) -> str:
    raw = info.filename
    if "\\" in raw:
        raise ArchiveError("压缩包路径必须使用 / 分隔")
    if raw.startswith("/") or raw.startswith("~") or (len(raw) > 1 and raw[1] == ":"):
        raise ArchiveError(f"压缩包包含绝对路径：{raw}")
    if "//" in raw:
        raise ArchiveError(f"压缩包包含不安全的路径：{raw}")
    parts = PurePosixPath(raw.rstrip("/")).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise ArchiveError(f"压缩包包含不安全的路径：{raw}")
    return PurePosixPath(*parts).as_posix()


def _check_mode(info: zipfile.ZipInfo) -> None:
    mode = (info.external_attr >> 16) & 0xFFFF
    if not mode:
        return
    if stat.S_ISLNK(mode):
        raise ArchiveError(f"压缩包不能包含符号链接：{info.filename}")
    if stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode):
        raise ArchiveError(f"压缩包不能包含设备文件：{info.filename}")
