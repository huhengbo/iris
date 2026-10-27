from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from iris_plugin_sdk import (
    PlaybackTarget,
    ResourceConfigField,
    ResourceEntry,
    ResourceListing,
    ResourceNotFound,
    ResourceProvider,
    ResourceUnavailable,
    SourceStatus,
)
from iris_plugin_sdk.testing import ResourceSourceContract

FILES = {
    "readme.nfo": b"<movie/>",
    "movies/a.mkv": b"a" * 100,
    "movies/2024/b.srt": b"subtitle" * 4,
    "temp/delete.mkv": b"temporary",
}
DIRS = {"movies", "movies/2024", "temp", "scratch"}


ResourceKind = Literal["video", "subtitle", "nfo"]


def _kind(path: str) -> ResourceKind:
    if path.endswith(".mkv"):
        return "video"
    if path.endswith(".nfo"):
        return "nfo"
    return "subtitle"


class MemorySource:
    def __init__(self) -> None:
        self.files = dict(FILES)

    def _check_dir(self, locator: str | None) -> str:
        current = locator or ""
        if current not in ("", *DIRS):
            raise ResourceNotFound(f"目录不存在：{current}")
        return current

    def browse(self, locator: str | None = None) -> ResourceListing:
        current = self._check_dir(locator)
        prefix = f"{current}/" if current else ""
        children: list[ResourceEntry] = []
        for directory in sorted(DIRS):
            if not directory.startswith(prefix):
                continue
            rest = directory[len(prefix) :]
            if not rest or "/" in rest:
                continue
            children.append(ResourceEntry(directory, rest, "video", is_dir=True))
        for path in sorted(self.files):
            if not path.startswith(prefix):
                continue
            rest = path[len(prefix) :]
            if not rest or "/" in rest:
                continue
            children.append(
                ResourceEntry(
                    path,
                    rest,
                    _kind(path),
                    len(self.files[path]),
                )
            )
        ancestors: list[tuple[str, str]] = []
        if current:
            parts = current.split("/")
            for index in range(len(parts)):
                locator_part = "/".join(parts[: index + 1])
                ancestors.append((locator_part, parts[index]))
        return ResourceListing(
            current,
            "/" if not current else f"/{current}",
            tuple(children),
            tuple(ancestors),
            selectable=bool(current) and current != "scratch",
        )

    def scan(
        self,
        locator: str | None,
        known: Mapping[str, ResourceEntry] | None = None,
    ) -> list[ResourceEntry]:
        current = self._check_dir(locator)
        prefix = f"{current}/" if current else ""
        if known is not None:
            assert known
        return [
            ResourceEntry(
                path,
                path[len(prefix) :],
                _kind(path),
                len(content),
            )
            for path, content in sorted(self.files.items())
            if path.startswith(prefix)
        ]

    def read(self, locator: str, max_bytes: int) -> bytes:
        content = self.files.get(locator)
        if content is None:
            raise ResourceNotFound("文件不存在")
        if len(content) > max_bytes:
            raise ResourceUnavailable("文件超过读取上限")
        return content

    def resolve(
        self, locator: str, user_agent: str, throttle_key: str | None = None
    ) -> PlaybackTarget:
        if locator not in self.files:
            raise ResourceNotFound("文件不存在")
        return PlaybackTarget(url=f"https://cdn.test/{locator}")

    def delete(self, locator: str) -> None:
        if self.files.pop(locator, None) is None:
            raise ResourceNotFound("文件不存在")


class MemoryProvider:
    id = "test.memory"
    multiple = True
    capabilities = frozenset({"status", "changes", "delete", "known_scan"})
    config_fields: tuple[ResourceConfigField, ...] = ()

    def validate_config(self, config: Mapping[str, Any]) -> dict[str, Any]:
        return dict(config)

    def check(self, config: Mapping[str, Any]) -> None:
        return None

    def open(self, source_id: int, config: Mapping[str, Any]) -> MemorySource:
        return MemorySource()

    def status(self, config: Mapping[str, Any]) -> SourceStatus:
        return SourceStatus("ok")


class TestMemorySourceContract(ResourceSourceContract):
    def make_source(self) -> MemorySource:
        return self._source

    def make_provider(self) -> ResourceProvider:
        return MemoryProvider()

    @property
    def _source(self) -> MemorySource:
        source = getattr(self, "_memory_source", None)
        if source is None:
            source = MemorySource()
            self._memory_source = source
        return source

    def expected_paths(self) -> set[str]:
        return set(FILES)

    def delete_target(self) -> str:
        return "temp/delete.mkv"

    def unselectable_locator(self) -> str:
        return "scratch"
