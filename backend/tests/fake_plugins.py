"""测试用的假插件控制：元数据、图片和资源来源。

插件本身运行在子进程里；这里把状态操作封装成和原进程内假插件相同的接口。
"""

from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from pathlib import Path

from iris_plugin_sdk import (
    ImageLookupRequest,
    MetadataNotFound,
    MetadataRecord,
    MetadataUnavailable,
    RemoteImage,
)

from tests.plugin_env import FakePluginSpec, PluginControl, install_fake_plugin
from tests.plugin_env import control_for as plugin_control


def install_metadata_plugin(
    plugins_dir: Path,
    plugin_id: str = "test.main",
    name: str = "假元数据",
    *,
    search: bool = True,
    images: bool = True,
    fetcher: bool = False,
) -> FakeMetadataPlugin:
    provides = ["metadata.details"]
    if search:
        provides.append("metadata.search")
    if images:
        provides.append("metadata.images")
    if fetcher:
        provides.append("image.fetch")
    install_fake_plugin(
        plugins_dir,
        FakePluginSpec(plugin_id, name, tuple(provides), capabilities=("logger",)),
    )
    return FakeMetadataPlugin(plugin_control(plugins_dir, plugin_id), plugin_id, name)


def install_image_plugin(
    plugins_dir: Path, plugin_id: str = "test.images", name: str = "假图片"
) -> FakeImagePlugin:
    install_fake_plugin(
        plugins_dir,
        FakePluginSpec(plugin_id, name, ("image.fetch",), capabilities=("logger",)),
    )
    return FakeImagePlugin(plugin_control(plugins_dir, plugin_id), plugin_id, name)


def install_resource_plugin(
    plugins_dir: Path, plugin_id: str = "test.source", name: str = "假来源"
) -> FakeResourcePlugin:
    install_fake_plugin(
        plugins_dir,
        FakePluginSpec(plugin_id, name, ("resource.source",), capabilities=("logger",)),
    )
    return FakeResourcePlugin(plugin_control(plugins_dir, plugin_id))


class _RecordMap(MutableMapping[str, MetadataRecord]):
    def __init__(self, control: PluginControl) -> None:
        self._control = control
        self._known: dict[str, MetadataRecord] = {}

    def __getitem__(self, key: str) -> MetadataRecord:
        return self._known[key]

    def __setitem__(self, key: str, value: MetadataRecord) -> None:
        self._known[key] = value
        self._control.call("metadata", "set_record", {"record": value})

    def __delitem__(self, key: str) -> None:
        del self._known[key]
        self._control.call("metadata", "delete_record", {"remote_id": key})

    def __iter__(self) -> Iterator[str]:
        return iter(self._known)

    def __len__(self) -> int:
        return len(self._known)


class _FindMap(MutableMapping[tuple[str, str], str | None]):
    def __init__(self, control: PluginControl) -> None:
        self._control = control
        self._values: dict[tuple[str, str], str | None] = {}

    def __getitem__(self, key: tuple[str, str]) -> str | None:
        return self._values[key]

    def __setitem__(self, key: tuple[str, str], value: str | None) -> None:
        self._values[key] = value
        self._control.call(
            "metadata", "set_find", {"scheme": key[0], "value": key[1], "remote_id": value}
        )

    def __delitem__(self, key: tuple[str, str]) -> None:
        del self._values[key]

    def __iter__(self) -> Iterator[tuple[str, str]]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


class MetadataControl:
    def __init__(self, control: PluginControl) -> None:
        self._control = control
        self.records = _RecordMap(control)
        self.find_results = _FindMap(control)

    @property
    def reason(self) -> str | None:
        return None

    @reason.setter
    def reason(self, value: str | None) -> None:
        self._control.call("metadata", "set_reason", {"reason": value})

    @property
    def error(self) -> Exception | None:
        return None

    @error.setter
    def error(self, value: Exception | None) -> None:
        if value is None:
            mode = None
        elif isinstance(value, MetadataUnavailable):
            mode = "unavailable"
        elif isinstance(value, MetadataNotFound):
            mode = "not_found"
        else:
            raise TypeError(f"不支持的假元数据错误：{value!r}")
        self._control.call("metadata", "set_error", {"error": mode})

    def _calls(self) -> list[str]:
        return [str(item) for item in self._control.call("metadata", "calls")]

    @property
    def search_calls(self) -> list[str]:
        return [call for call in self._calls() if call.startswith("search:")]

    @property
    def find_calls(self) -> list[tuple[str, str, str]]:
        return [
            (str(item[0]), str(item[1]), str(item[2]))
            for item in self._control.call("metadata", "find_calls")
        ]

    @property
    def details_calls(self) -> list[str]:
        return [call.rsplit(":", 1)[1] for call in self._calls() if call.startswith("details:")]

    @property
    def image_requests(self) -> list[ImageLookupRequest]:
        requests: list[ImageLookupRequest] = []
        for call in self._calls():
            if not call.startswith("image-list:"):
                continue
            _, kind, language, external = call.split(":", 3)
            ids = dict(item.split("=", 1) for item in external.split(",") if item)
            requests.append(
                ImageLookupRequest(cast_kind(kind), ids, language)  # type: ignore[arg-type]
            )
        return requests

    @property
    def downloads(self) -> list[tuple[str, int]]:
        found: list[tuple[str, int]] = []
        for call in self._calls():
            if call.startswith("image:"):
                _, source, width = call.split(":", 2)
                found.append((source, int(width)))
        return found

    def set_page_url_template(self, template: str) -> None:
        self._control.call("metadata", "set_page_url_template", {"template": template})

    @property
    def available(self) -> list[RemoteImage]:
        return []

    @available.setter
    def available(self, images: list[RemoteImage]) -> None:
        self._control.call("metadata", "set_images", {"images": images})


def cast_kind(value: str) -> str:
    return value


class FakeMetadataPlugin:
    def __init__(self, control: PluginControl, plugin_id: str, name: str) -> None:
        self.control = control
        self.plugin_id = plugin_id
        self.name = name
        self.provider = MetadataControl(control)


class FakeImagePlugin:
    def __init__(
        self, control: PluginControl, plugin_id: str = "test.images", name: str = "假图片"
    ):
        self.control = control
        self.plugin_id = plugin_id
        self.name = name
        self.provider = MetadataControl(control)


class ResourceControl:
    def __init__(self, control: PluginControl) -> None:
        self._control = control

    @property
    def selectable(self) -> bool:
        return bool(self._control.call("source", "node", {"node_id": "0"}) and True)

    @selectable.setter
    def selectable(self, value: bool) -> None:
        self._control.call("source", "set_selectable", {"selectable": value})


class FakeResourcePlugin:
    def __init__(self, control: PluginControl) -> None:
        self.control = control
        self.provider = ResourceControl(control)
