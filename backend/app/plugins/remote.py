"""进程外插件的宿主端 provider 代理。

每一类 provider 都把调用编码成 JSON-RPC 发给插件进程，不把插件的实现细节带回宿主。
业务异常由 ``PluginRpcClient`` 还原成 SDK 异常；这里只把“连接层不可用”补成对应能力
应该抛出的 Unavailable，核心不需要知道插件是不是子进程。
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from iris_plugin_sdk import (
    RESOURCE_CAPABILITIES,
    ImageLookupRequest,
    MetadataCandidate,
    MetadataKind,
    MetadataPerson,
    MetadataRecord,
    MetadataSearchRequest,
    MetadataSeason,
    MetadataUnavailable,
    PlaybackTarget,
    PluginManifest,
    PluginManifestError,
    PluginRequest,
    PluginRequestError,
    PluginResponse,
    RemoteImage,
    ResourceConfigField,
    ResourceEntry,
    ResourceListing,
    ResourceSource,
    ResourceUnavailable,
    SourceStatus,
    SubtitleCandidate,
    SubtitleDownload,
    SubtitleSearchRequest,
    SubtitleUnavailable,
    WaitReason,
)

from app.plugins.rpc import (
    PluginRpcClient,
    PluginUnavailable,
    RpcMethodNotFound,
    RpcTimeoutError,
    unavailable_exception,
)
from app.plugins.supervisor import PluginRuntimeError
from app.priority import Priority, current_priority

# 插件进程崩溃后 supervisor 会自动重启；代理每次调用都取当前连接，不缓存旧的连接。
ClientProvider = Callable[[], PluginRpcClient]
WaitCallback = Callable[[datetime, WaitReason], None]


class RemoteResourceProvider:
    """resource.source 的进程外代理；能力和配置表单来自插件自己的 describe。"""

    def __init__(self, client: ClientProvider) -> None:
        self._client = client
        self._lock = threading.Lock()
        self._description: dict[str, Any] | None = None

    @property
    def id(self) -> str:
        return str(self._describe()["id"])

    @property
    def multiple(self) -> bool:
        return bool(self._describe()["multiple"])

    @property
    def capabilities(self) -> frozenset[str]:
        return frozenset(self._describe()["capabilities"])

    @property
    def config_fields(self) -> tuple[ResourceConfigField, ...]:
        return tuple(self._describe()["config_fields"])

    def validate_config(self, config: Mapping[str, Any]) -> dict[str, Any]:
        result = self._call("resource.validate_config", {"config": dict(config)})
        if not isinstance(result, dict):
            raise ValueError("插件返回的配置格式不正确")
        return result

    def check(self, config: Mapping[str, Any]) -> None:
        self._call("resource.check", {"config": dict(config)})

    def status(self, config: Mapping[str, Any]) -> SourceStatus:
        result = self._call("resource.status", {"config": dict(config)})
        if not isinstance(result, SourceStatus):
            raise ResourceUnavailable("插件返回的来源状态格式不正确")
        return result

    def open(
        self,
        source_id: int,
        config: Mapping[str, Any],
        *,
        waiter: WaitCallback | None = None,
    ) -> ResourceSource:
        return RemoteResourceSource(
            self._client, source_id, dict(config), self.capabilities, waiter=waiter
        )

    def _describe(self) -> dict[str, Any]:
        with self._lock:
            if self._description is None:
                result = self._call("resource.describe", {})
                self._description = _description(result)
            return self._description

    def _call(self, method: str, params: Mapping[str, Any]) -> Any:
        return _call(self._client, method, params)


class RemoteResourceSource:
    def __init__(
        self,
        client: ClientProvider,
        source_id: int,
        config: Mapping[str, Any],
        capabilities: frozenset[str],
        *,
        waiter: WaitCallback | None = None,
    ) -> None:
        self._client = client
        self._source_id = source_id
        self._config = dict(config)
        self._capabilities = capabilities
        self._waiter = waiter

    def browse(
        self,
        locator: str | None = None,
        *,
        waiter: WaitCallback | None = None,
    ) -> ResourceListing:
        result = self._call("resource.browse", {"locator": locator}, waiter=waiter or self._waiter)
        if not isinstance(result, ResourceListing):
            raise ResourceUnavailable("插件返回的目录格式不正确")
        return result

    def scan(
        self,
        locator: str | None,
        known: Mapping[str, ResourceEntry] | None = None,
        *,
        waiter: WaitCallback | None = None,
    ) -> list[ResourceEntry]:
        params: dict[str, Any] = {"locator": locator}
        if "known_scan" in self._capabilities:
            params["known"] = None if known is None else dict(known)
        result = self._call("resource.scan", params, waiter=waiter or self._waiter)
        if not isinstance(result, list) or any(
            not isinstance(item, ResourceEntry) for item in result
        ):
            raise ResourceUnavailable("插件返回的扫描结果格式不正确")
        return result

    def read(self, locator: str, max_bytes: int) -> bytes:
        result = self._call("resource.read", {"locator": locator, "max_bytes": max_bytes})
        if not isinstance(result, bytes):
            raise ResourceUnavailable("插件返回的文件内容格式不正确")
        return result

    def resolve(
        self, locator: str, user_agent: str, throttle_key: str | None = None
    ) -> PlaybackTarget:
        result = self._call(
            "resource.resolve",
            {"locator": locator, "user_agent": user_agent, "throttle_key": throttle_key},
        )
        if not isinstance(result, PlaybackTarget):
            raise ResourceUnavailable("插件返回的播放地址格式不正确")
        return result

    def delete(self, locator: str) -> None:
        if "delete" not in self._capabilities:
            raise ResourceUnavailable("来源不支持删除文件")
        self._call("resource.delete", {"locator": locator})

    def _call(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        waiter: WaitCallback | None = None,
    ) -> Any:
        return _call(
            self._client,
            method,
            {"source_id": self._source_id, "config": self._config, **params},
            waiter=waiter,
        )


class RemoteMetadataProvider:
    """metadata.search / metadata.details / metadata.find / metadata.season。"""

    def __init__(self, client: ClientProvider, plugin_id: str) -> None:
        self._client = client
        self.id = plugin_id

    def unavailable_reason(self) -> str | None:
        return _unavailable_reason(self._client, "metadata.details")

    def search(self, request: MetadataSearchRequest) -> list[MetadataCandidate]:
        result = self._call("metadata.search", {"request": request})
        if not isinstance(result, list) or any(
            not isinstance(item, MetadataCandidate) for item in result
        ):
            raise MetadataUnavailable("插件返回的搜索结果格式不正确")
        return result

    def find(self, kind: MetadataKind, scheme: str, value: str) -> str | None:
        result = self._call("metadata.find", {"kind": kind, "scheme": scheme, "value": value})
        if result is not None and not isinstance(result, str):
            raise MetadataUnavailable("插件返回的条目 ID 格式不正确")
        return result

    def details(self, remote_id: str, *, kind: MetadataKind) -> MetadataRecord:
        result = self._call("metadata.details", {"remote_id": remote_id, "kind": kind})
        if not isinstance(result, MetadataRecord):
            raise MetadataUnavailable("插件返回的条目格式不正确")
        return result

    def season(self, remote_id: str, number: int) -> MetadataSeason:
        result = self._call("metadata.season", {"remote_id": remote_id, "number": number})
        if not isinstance(result, MetadataSeason):
            raise MetadataUnavailable("插件返回的季信息格式不正确")
        return result

    def page_url(self, kind: MetadataKind, remote_id: str) -> str | None:
        result = self._call("metadata.page_url", {"kind": kind, "remote_id": remote_id})
        if result is not None and not isinstance(result, str):
            raise MetadataUnavailable("插件返回的页面地址格式不正确")
        return result

    def _call(self, method: str, params: Mapping[str, Any]) -> Any:
        return _call(self._client, method, {**params, "priority": _priority()})


class RemoteMetadataImageProvider:
    """metadata.images：只下载本插件在 MetadataRecord 里给出的图片来源。"""

    def __init__(self, client: ClientProvider) -> None:
        self._client = client

    def image(self, source: str, width: int) -> bytes:
        result = _call(
            self._client,
            "metadata.image",
            {"source": source, "width": width, "priority": _priority()},
        )
        if not isinstance(result, bytes):
            raise MetadataUnavailable("插件返回的图片格式不正确")
        return result


class RemoteMetadataPeopleProvider:
    def __init__(self, client: ClientProvider) -> None:
        self._client = client

    def person(self, remote_id: str) -> MetadataPerson:
        result = _call(
            self._client,
            "metadata.person",
            {"remote_id": remote_id, "priority": _priority()},
        )
        if not isinstance(result, MetadataPerson):
            raise MetadataUnavailable("插件返回的人物资料格式不正确")
        return result


class RemoteImageFetcher:
    """image.fetch：按外部 ID 列图、下载图片。"""

    def __init__(self, client: ClientProvider, plugin_id: str) -> None:
        self._client = client
        self.id = plugin_id

    def unavailable_reason(self) -> str | None:
        return _unavailable_reason(self._client, "image.fetch")

    def images(self, request: ImageLookupRequest) -> list[RemoteImage]:
        result = _call(self._client, "image.images", {"request": request, "priority": _priority()})
        if not isinstance(result, list) or any(
            not isinstance(item, RemoteImage) for item in result
        ):
            raise MetadataUnavailable("插件返回的图片列表格式不正确")
        return result

    def image(self, source: str, width: int) -> bytes:
        result = _call(
            self._client,
            "image.fetch",
            {"source": source, "width": width, "priority": _priority()},
        )
        if not isinstance(result, bytes):
            raise MetadataUnavailable("插件返回的图片格式不正确")
        return result


class RemoteSubtitleProvider:
    def __init__(self, client: ClientProvider, plugin_id: str) -> None:
        self._client = client
        self.id = plugin_id

    def unavailable_reason(self) -> str | None:
        return _unavailable_reason(self._client, "subtitle.search")

    def search(self, request: SubtitleSearchRequest) -> list[SubtitleCandidate]:
        # 插件自己的 SubtitleUnavailable 会原样还原；未知异常按协议是 ResourceUnavailable，
        # 不能当成“字幕站不可用”，留给调用方归为失败。
        result = _call(self._client, "subtitle.search", {"request": request})
        if not isinstance(result, list) or any(
            not isinstance(item, SubtitleCandidate) for item in result
        ):
            raise SubtitleUnavailable("插件返回的字幕列表格式不正确")
        return result

    def download(self, candidate: SubtitleCandidate) -> SubtitleDownload:
        result = _call(self._client, "subtitle.download", {"candidate": candidate})
        if not isinstance(result, SubtitleDownload):
            raise SubtitleUnavailable("插件返回的字幕格式不正确")
        return result


class RemoteAdminUiProvider:
    """admin.ui：静态文件由宿主托管，业务请求转成 admin.request。"""

    def __init__(self, client: ClientProvider, manifest: PluginManifest, plugin_dir: Path) -> None:
        if manifest.ui is None:
            raise PluginManifestError("admin.ui 插件没有声明 ui 目录")
        self._client = client
        self._ui_root = plugin_dir / manifest.ui

    @property
    def ui_root(self) -> Path:
        return self._ui_root

    def handle(self, request: PluginRequest) -> PluginResponse:
        result = _call(self._client, "admin.request", {"request": request})
        if not isinstance(result, PluginResponse):
            raise PluginRequestError("插件返回了无效的响应", status=500, code="plugin_failed")
        return result


def providers_for(
    client: ClientProvider, manifest: PluginManifest, plugin_dir: Path
) -> dict[str, object]:
    """按 manifest.provides 构造一组代理；键和进程内注册表的 provider 键一致。"""
    providers: dict[str, object] = {}
    provides = set(manifest.provides)
    if "resource.source" in provides:
        providers["resource.source"] = RemoteResourceProvider(client)
    if provides & {"metadata.search", "metadata.details"}:
        metadata = RemoteMetadataProvider(client, manifest.id)
        for capability in ("metadata.search", "metadata.details"):
            if capability in provides:
                providers[capability] = metadata
    if "metadata.images" in provides:
        providers["metadata.images"] = RemoteMetadataImageProvider(client)
    if "metadata.people" in provides:
        providers["metadata.people"] = RemoteMetadataPeopleProvider(client)
    if "image.fetch" in provides:
        providers["image.fetch"] = RemoteImageFetcher(client, manifest.id)
    if provides & {"subtitle.search", "subtitle.download"}:
        subtitles = RemoteSubtitleProvider(client, manifest.id)
        for capability in ("subtitle.search", "subtitle.download"):
            if capability in provides:
                providers[capability] = subtitles
    if "admin.ui" in provides:
        providers["admin.ui"] = RemoteAdminUiProvider(client, manifest, plugin_dir)
    return providers


def _priority() -> str:
    return "background" if current_priority() == Priority.BACKGROUND else "interactive"


def _call(
    client: ClientProvider,
    method: str,
    params: Mapping[str, Any],
    *,
    waiter: WaitCallback | None = None,
) -> Any:
    try:
        return client().call(method, params, waiter=waiter)
    except RpcMethodNotFound as exc:
        raise unavailable_exception(method, str(exc)) from exc
    except RpcTimeoutError as exc:
        raise unavailable_exception(method, f"插件响应超时：{method}") from exc
    except (PluginRuntimeError, PluginUnavailable) as exc:
        raise unavailable_exception(method, str(exc)) from exc
    except ResourceUnavailable as exc:
        # 未知的插件异常统一还原成 ResourceUnavailable；元数据和图片能力族改抛自己的异常。
        if method.startswith(("metadata.", "image.")):
            raise MetadataUnavailable(exc.message) from exc
        raise


def _unavailable_reason(client: ClientProvider, capability: str) -> str | None:
    try:
        result = client().call("provider.unavailable_reason", {"capability": capability})
    except RpcMethodNotFound:
        return None
    except RpcTimeoutError as exc:
        raise unavailable_exception(
            "metadata.details" if capability.startswith("metadata.") else capability,
            "插件响应超时：provider.unavailable_reason",
        ) from exc
    if result is not None and not isinstance(result, str):
        raise ValueError("插件返回的不可用原因格式不正确")
    return result


def _description(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise ResourceUnavailable("插件返回的资源描述格式不正确")
    identifier = result.get("id")
    multiple = result.get("multiple")
    capabilities = result.get("capabilities")
    fields = result.get("config_fields")
    if not isinstance(identifier, str) or not identifier:
        raise ResourceUnavailable("插件返回的资源 id 不合法")
    if not isinstance(multiple, bool):
        raise ResourceUnavailable("插件返回的 multiple 不合法")
    if not isinstance(capabilities, list) or any(
        not isinstance(item, str) or not item for item in capabilities
    ):
        raise ResourceUnavailable("插件返回的能力列表不合法")
    unknown = set(capabilities) - RESOURCE_CAPABILITIES
    if unknown:
        raise ResourceUnavailable(f"插件返回了未知的资源能力：{', '.join(sorted(unknown))}")
    if not isinstance(fields, list) or any(
        not isinstance(item, ResourceConfigField) for item in fields
    ):
        raise ResourceUnavailable("插件返回的配置字段不合法")
    return {
        "id": identifier,
        "multiple": multiple,
        "capabilities": capabilities,
        "config_fields": fields,
    }
