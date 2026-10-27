"""服务器本地目录来源。

来源配置的 ``root`` 必填：相对路径相对宿主 ``media_dir`` 解析，绝对路径直接使用；解析
符号链接后必须等于 ``media_dir`` 或位于其内，校验通过后保存解析后的绝对路径。任何
locator 也都限制在来源根目录内。宿主没有下发 ``media_dir`` 时来源明确不可用。

符号链接一律不跟随：``scan`` / ``browse`` 跳过符号链接，``read`` / ``resolve`` 拒绝
定位符中任何一段是符号链接（即使它指向根目录内部），并再次确认解析后的路径仍在根目录
内。文件的媒体类型统一由 SDK 的 ``media_kind`` 判断，非媒体文件不进入浏览结果。

locator 是相对于来源根目录的路径，用 ``/`` 分隔；来源根目录统一用 ``None`` 请求，
返回的 ``ResourceListing.locator`` 是空字符串。本地来源只读，不声明 delete 能力。
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from iris_plugin_sdk import (
    InvalidSourceConfig,
    PlaybackTarget,
    PluginContext,
    ResourceConfigField,
    ResourceEntry,
    ResourceListing,
    ResourceNotFound,
    ResourceUnavailable,
    SourceStatus,
    media_kind,
)

_NO_MEDIA_DIR = "宿主没有配置本地媒体目录，本地来源不可用"


class LocalResourceProvider:
    """按来源配置打开本地目录；所有路径都限制在宿主下发的 media_dir 内。"""

    multiple = True
    capabilities = frozenset({"status"})

    def __init__(self, media_dir: Path | None, plugin_id: str) -> None:
        self.id = plugin_id
        self._media_dir = None if media_dir is None else media_dir.resolve()
        if self._media_dir is None:
            help_text = "宿主没有配置本地媒体目录，本地来源不可用。"
            default = ""
        else:
            help_text = f"容器内的绝对路径，必须位于 {self._media_dir} 之内"
            default = str(self._media_dir)
        self.config_fields: tuple[ResourceConfigField, ...] = (
            ResourceConfigField(
                "root",
                "服务器目录",
                help=help_text,
                required=True,
                default=default,
            ),
        )

    def validate_config(self, config: Mapping[str, Any]) -> dict[str, Any]:
        media = self._require_media_dir()
        root = config.get("root")
        if not isinstance(root, str) or not root.strip():
            raise InvalidSourceConfig("请填写服务器目录")
        path = Path(root.strip())
        if not path.is_absolute():
            path = media / path
        path = path.resolve()
        if path != media and media not in path.parents:
            raise InvalidSourceConfig(f"服务器目录必须位于 {media} 之内")
        if not path.is_dir():
            raise InvalidSourceConfig(f"服务器目录 {path} 不存在，确认已挂载到容器里")
        return {"root": str(path)}

    def check(self, config: Mapping[str, Any]) -> None:
        self._require_media_dir()

    def status(self, config: Mapping[str, Any]) -> SourceStatus:
        if self._media_dir is None:
            return SourceStatus("failed", message=_NO_MEDIA_DIR)
        root = config.get("root")
        if not isinstance(root, str) or not root:
            return SourceStatus("failed", message="还没有配置服务器目录")
        path = Path(root)
        if not path.is_dir():
            return SourceStatus("failed", message=f"服务器目录 {path} 不存在或没有挂载")
        return SourceStatus("ok")

    def open(self, source_id: int, config: Mapping[str, Any]) -> LocalResourceSource:
        media = self._require_media_dir()
        root = config.get("root")
        if not isinstance(root, str) or not root:
            raise ResourceUnavailable("本地来源还没有配置服务器目录")
        path = Path(root).resolve()
        if path != media and media not in path.parents:
            raise ResourceUnavailable(f"服务器目录不在 {media} 之内")
        return LocalResourceSource(path)

    def _require_media_dir(self) -> Path:
        if self._media_dir is None:
            raise ResourceUnavailable(_NO_MEDIA_DIR)
        return self._media_dir


class LocalResourceSource:
    """locator 是相对于来源根目录的路径；根目录的 locator 是空字符串。"""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def browse(self, locator: str | None = None) -> ResourceListing:
        directory = self._dir(locator or "")
        try:
            children = sorted(
                (child for child in directory.iterdir() if _visible(child)),
                key=lambda item: item.name.casefold(),
            )
        except OSError as exc:
            raise ResourceUnavailable(f"读取本地目录失败：{exc.strerror}") from exc
        entries: list[ResourceEntry] = []
        for child in children:
            if child.is_dir():
                # 目录照常返回；宿主只允许把目录添加为资料库目录。
                entries.append(
                    ResourceEntry(self._locator(child), child.name, "video", is_dir=True)
                )
                continue
            kind = media_kind(child.name)
            if kind is not None:
                entries.append(ResourceEntry(self._locator(child), child.name, kind))
        relative = directory.relative_to(self._root).as_posix()
        ancestors: list[tuple[str, str]] = []
        if relative != ".":
            current = Path()
            for name in Path(relative).parts:
                current /= name
                ancestors.append((current.as_posix(), name))
        return ResourceListing(
            locator or "", self._display(directory), tuple(entries), tuple(ancestors)
        )

    def scan(
        self,
        locator: str | None,
        known: Mapping[str, ResourceEntry] | None = None,
    ) -> list[ResourceEntry]:
        # 本地目录遍历很便宜，每次都完整扫描；没有声明 known_scan 能力。
        directory = self._dir(locator)
        found: list[ResourceEntry] = []

        def fail(error: OSError) -> None:
            raise ResourceUnavailable(f"读取本地目录失败：{error.strerror}") from error

        for current, dirs, files in os.walk(directory, onerror=fail):
            dirs[:] = sorted(
                name
                for name in dirs
                if not name.startswith(".") and not Path(current, name).is_symlink()
            )
            for name in sorted(files):
                path = Path(current, name)
                if name.startswith(".") or path.is_symlink():
                    continue
                kind = media_kind(name)
                if kind is None:
                    continue
                relative_path = path.relative_to(directory).as_posix()
                try:
                    stat = path.stat()
                except OSError as exc:
                    raise ResourceUnavailable(
                        f"读取本地文件失败：{relative_path}（{exc.strerror}）"
                    ) from exc
                found.append(
                    ResourceEntry(
                        locator=self._locator(path),
                        relative_path=relative_path,
                        kind=kind,
                        size=stat.st_size,
                        sha1=_fingerprint(stat),
                        is_dir=False,
                        playback_locator=None,
                        key=None,
                        identity=None,
                        root_path=self._display(directory),
                    )
                )
        return found

    def read(self, locator: str, max_bytes: int) -> bytes:
        path = self._file(locator)
        try:
            if path.stat().st_size > max_bytes:
                raise ResourceUnavailable("文件超过读取上限")
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise ResourceNotFound("文件不存在") from exc
        except OSError as exc:
            raise ResourceUnavailable(f"读取本地文件失败：{exc.strerror}") from exc

    def resolve(
        self, locator: str, user_agent: str, throttle_key: str | None = None
    ) -> PlaybackTarget:
        # 宿主播放层直接提供文件内容，并再次确认真实路径位于它自己的媒体目录内。
        return PlaybackTarget(path=self._file(locator))

    def delete(self, locator: str) -> None:
        raise ResourceUnavailable("本地来源不支持删除文件")

    def _dir(self, locator: str | None) -> Path:
        if not self._root.is_dir():
            raise ResourceUnavailable(f"服务器目录 {self._root} 不存在或没有挂载")
        directory = self._path(locator or "")
        if not directory.is_dir():
            raise ResourceNotFound(f"目录「{locator or '/'}」不存在")
        return directory

    def _file(self, locator: str) -> Path:
        if not self._root.is_dir():
            raise ResourceUnavailable(f"服务器目录 {self._root} 不存在或没有挂载")
        path = self._path(locator)
        if not path.is_file():
            raise ResourceNotFound("文件不存在")
        return path

    def _path(self, locator: str) -> Path:
        """把 locator 变成根目录内的真实路径；路径中任何一段是符号链接都拒绝。

        先在未解析的路径上逐段检查，避免 ``resolve()`` 把中间的符号链接隐藏掉；解析后
        再确认一次仍在根目录内，防止检查之后目录被换成指向根目录外的符号链接。
        """
        relative = PurePosixPath(locator)
        if relative.is_absolute():
            raise ResourceNotFound("路径越界")
        current = self._root
        for part in relative.parts:
            if part == "..":
                raise ResourceNotFound("路径越界")
            current /= part
            if current.is_symlink():
                raise ResourceNotFound("不跟随符号链接")
        resolved = current.resolve()
        if resolved != self._root and self._root not in resolved.parents:
            raise ResourceNotFound("路径越界")
        return resolved

    def _locator(self, path: Path) -> str:
        return path.relative_to(self._root).as_posix()

    def _display(self, directory: Path) -> str:
        relative = directory.relative_to(self._root).as_posix()
        return "/" if relative == "." else f"/{relative}"


class LocalPlugin:
    """插件入口：把本地来源注册为 ``resource.source``。"""

    def __init__(self) -> None:
        self._provider: LocalResourceProvider | None = None

    def start(self, context: PluginContext) -> None:
        self._provider = LocalResourceProvider(context.media_dir, context.plugin_id)
        context.logger.info("本地存储来源已准备")

    def stop(self) -> None:
        self._provider = None

    def providers(self) -> Mapping[str, object]:
        if self._provider is None:
            return {}
        return {"resource.source": self._provider}


def _visible(path: Path) -> bool:
    return not path.name.startswith(".") and not path.is_symlink()


def _fingerprint(stat: os.stat_result) -> str:
    """不读文件内容的指纹：每次同步都对大视频算 sha1 太慢。同一文件系统内改名或移动时
    inode 不变，用它认出挪动过的文件；内容改写时 mtime 会变，NFO 据此重新读取。"""
    raw = f"{stat.st_dev}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}"
    return hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()
