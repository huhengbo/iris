"""测试用的最小资源插件；由测试复制到临时目录并以子进程运行。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from iris_plugin_sdk import (
    AdminUiProvider,
    InvalidSourceConfig,
    MetadataKind,
    MetadataRecord,
    PlaybackTarget,
    PluginContext,
    PluginRequest,
    PluginResponse,
    ResourceEntry,
    ResourceListing,
    ResourceNotFound,
    ResourceProvider,
    ResourceUnavailable,
    SourceStatus,
    current_priority,
    run,
)

FILES: dict[str, bytes] = {
    "movie.mkv": b"0123456789" * 10,
    "notes.nfo": b"<movie><title>Fake</title></movie>",
    "subs/movie.srt": b"1\n00:00:01,000 --> 00:00:02,000\nhi\n",
}
DIRS = {"subs"}


ResourceKind = Literal["video", "subtitle", "nfo"]


def _kind(path: str) -> ResourceKind:
    if path.endswith(".mkv"):
        return "video"
    if path.endswith(".nfo"):
        return "nfo"
    return "subtitle"


class FakeSource:
    def __init__(self, source_id: int) -> None:
        self.source_id = source_id
        self._files = dict(FILES)
        self._dirs = set(DIRS)

    def browse(self, locator: str | None = None) -> ResourceListing:
        current = locator or ""
        if current == "boom":
            raise RuntimeError("source exploded")
        if current not in ("", *self._dirs):
            raise ResourceNotFound(f"目录不存在：{current}")
        prefix = f"{current}/" if current else ""
        entries: list[ResourceEntry] = []
        if not current:
            for name in sorted(self._dirs):
                entries.append(ResourceEntry(name, name, "video", is_dir=True))
        for path in sorted(self._files):
            if not path.startswith(prefix):
                continue
            rest = path[len(prefix) :]
            if "/" in rest:
                continue
            entries.append(
                ResourceEntry(
                    path,
                    rest,
                    _kind(rest),
                    len(self._files[path]),
                )
            )
        ancestors: tuple[tuple[str, str], ...] = ()
        if current:
            ancestors = ((current, current.rsplit("/", 1)[-1]),)
        return ResourceListing(
            current, "/" if not current else f"/{current}", tuple(entries), ancestors
        )

    def scan(
        self,
        locator: str | None,
        known: Mapping[str, ResourceEntry] | None = None,
    ) -> list[ResourceEntry]:
        locator = locator or ""
        if locator not in ("", *self._dirs):
            raise ResourceNotFound(f"目录不存在：{locator}")
        if locator == "boom":
            raise RuntimeError("source exploded")
        prefix = f"{locator}/" if locator else ""
        return [
            ResourceEntry(
                path,
                path[len(prefix) :],
                _kind(path),
                len(content),
            )
            for path, content in sorted(self._files.items())
            if path.startswith(prefix)
        ]

    def read(self, locator: str, max_bytes: int) -> bytes:
        if locator == "boom":
            raise RuntimeError("source exploded")
        content = self._files.get(locator)
        if content is None:
            raise ResourceNotFound("文件不存在")
        if len(content) > max_bytes:
            raise ResourceUnavailable("文件超过读取上限")
        return content

    def resolve(
        self, locator: str, user_agent: str, throttle_key: str | None = None
    ) -> PlaybackTarget:
        if locator not in self._files:
            raise ResourceNotFound("文件不存在")
        return PlaybackTarget(url=f"https://cdn.test/{locator}")

    def delete(self, locator: str) -> None:
        if locator not in self._files:
            raise ResourceNotFound("文件不存在")
        del self._files[locator]


class FakeProvider(ResourceProvider):
    id = "fake.source"
    multiple = True
    capabilities = frozenset({"status", "changes", "delete", "known_scan"})
    config_fields = ()

    def __init__(self) -> None:
        self.opens = 0
        self.source_id: int | None = None
        self.source = FakeSource(0)

    def validate_config(self, config: Mapping[str, Any]) -> dict[str, Any]:
        if config.get("bad"):
            raise InvalidSourceConfig("配置不合法")
        return dict(config)

    def check(self, config: Mapping[str, Any]) -> None:
        return None

    def open(self, source_id: int, config: Mapping[str, Any]) -> FakeSource:
        self.opens += 1
        self.source_id = source_id
        return self.source

    def status(self, config: Mapping[str, Any]) -> SourceStatus:
        return SourceStatus("ok", page="settings")


class FakeMetadata:
    """metadata.details 探针：把运行时设置的优先级写进标题。"""

    id = "test.fake"

    def unavailable_reason(self) -> str | None:
        return None

    def details(self, remote_id: str, *, kind: MetadataKind) -> MetadataRecord:
        return MetadataRecord(
            provider_id=self.id,
            remote_id=remote_id,
            title=current_priority(),
            kind=kind,
        )


class FakeUi(AdminUiProvider):
    ui_root = Path(__file__).resolve().parent / "ui"

    def __init__(self) -> None:
        self.plugin: FakePlugin | None = None

    def handle(self, request: PluginRequest) -> PluginResponse:
        if request.path == "changed" and self.plugin is not None:
            assert self.plugin.context is not None
            assert self.plugin.context.notify is not None
            self.plugin.context.notify.resource_changed(7, ["12", "34"])
        if request.path == "opens" and self.plugin is not None:
            return PluginResponse(
                {"opens": self.plugin.provider.opens, "source_id": self.plugin.provider.source_id}
            )
        if request.path == "progress" and self.plugin is not None:
            assert self.plugin.context is not None
            assert self.plugin.context.notify is not None
            self.plugin.context.notify.progress(
                datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC), "rate_limited"
            )
        body: dict[str, Any] = {"path": request.path, "method": request.method}
        if self.plugin is not None:
            body["proxy"] = self.plugin.proxy
            if self.plugin.context is not None:
                body["host_url"] = self.plugin.context.host.url
            if self.plugin.context is not None:
                body["media_dir"] = str(self.plugin.context.media_dir)
        return PluginResponse(body)


class FakePlugin:
    def __init__(self) -> None:
        self.context: PluginContext | None = None
        self.proxy: str | None = None
        self.host_url: str | None = None
        self.flushed = False
        self.provider = FakeProvider()
        self.metadata = FakeMetadata()
        self.ui = FakeUi()
        self.ui.plugin = self

    def start(self, context: PluginContext) -> None:
        self.context = context
        self.proxy = context.proxy
        context.logger.info("fake started data_dir=%s", context.data_dir)
        if context.http is not None:
            response = context.http.get("https://example.test/ping")
            assert response.status_code == 200

    def stop(self) -> None:
        self.context = None

    def flush(self) -> None:
        self.flushed = True
        if self.context is not None:
            (self.context.data_dir / "state" / "flushed").write_text("yes", encoding="utf-8")

    def config_changed(self, *, proxy: str | None, host_url: str | None) -> None:
        self.proxy = proxy
        self.host_url = host_url

    def providers(self) -> Mapping[str, object]:
        return {
            "resource.source": self.provider,
            "metadata.details": self.metadata,
            "admin.ui": self.ui,
        }


run(FakePlugin())
