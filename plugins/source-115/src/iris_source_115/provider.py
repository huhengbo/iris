"""115 资源来源：浏览、扫描、读取、播放解析和删除。

目录定位符是 115 的目录 ID（根目录用 None，115 自己的根 ID 是 "0"），文件定位符是
插件内部使用的 pickcode；导出、扫描、读取、删除和播放解析全部在插件内完成。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from functools import partial
from hashlib import sha256
from typing import Any

from iris_plugin_sdk import (
    MediaKind,
    PlaybackTarget,
    ResourceConfigField,
    ResourceEntry,
    ResourceListing,
    ResourceNotFound,
    ResourceRateLimited,
    ResourceUnavailable,
    SourceStatus,
    WaitReason,
    media_kind,
)

from iris_source_115.clock import Clock
from iris_source_115.copies import PlaybackCopies
from iris_source_115.errors import (
    RATE_LIMIT_BACKOFF,
    Cloud115Error,
    Cloud115Unavailable,
    CredentialInvalid,
    CredentialMissing,
    DirNotFound,
    ExportBusy,
    FileGone,
    LifeApiRejected,
    RateLimited,
    to_sdk_error,
)
from iris_source_115.gateway import DirectLink, DirRef, LifeEvent
from iris_source_115.links import DirectLinkCache
from iris_source_115.scheduler import Priority
from iris_source_115.service import Cloud115Service
from iris_source_115.throttle import PlayThrottle

logger = logging.getLogger(__name__)

EXPORT_DIR_NAME = "光圈临时目录"
# 多端同时播放的副本放在专用目录下的子目录里（ADR 0027）
PLAYBACK_COPY_DIR = f"{EXPORT_DIR_NAME}/多端播放"
EXPORT_FILE_SUFFIX = "_目录树.txt"
EXPORT_BUSY_RETRY = timedelta(seconds=60)
EXPORT_POLL_INTERVAL = timedelta(seconds=3)
# 等待其他导出任务结束和等待本次导出完成共用这个上限
EXPORT_TIMEOUT = timedelta(minutes=30)
# 115 自己的根目录 ID；协议规定来源根目录用 None，定位符 "0" 不能作为资料库目录
ROOT_CID = "0"


class Cloud115ResourceProvider:
    """115 网盘。它绑定全局的 115 账号，所以只有一个来源实例。"""

    multiple = False
    capabilities = frozenset({"known_scan", "delete", "status", "changes"})
    config_fields: tuple[ResourceConfigField, ...] = ()

    def __init__(
        self,
        service: Cloud115Service,
        clock: Clock,
        plugin_id: str,
        sleeper: Callable[[float], None] | None = None,
        *,
        progress: Callable[[datetime, WaitReason], None] | None = None,
        heartbeat: Callable[[], None] | None = None,
    ) -> None:
        self.id = plugin_id
        self._service = service
        self._clock = clock
        self._sleeper = sleeper or time.sleep
        self._progress = progress
        self._heartbeat_cb = heartbeat
        self._links = DirectLinkCache(clock)
        self._throttle = PlayThrottle(clock)
        self._file_ids: dict[str, str] = {}
        self._copies = PlaybackCopies(service, PLAYBACK_COPY_DIR, sleeper)
        self._same_playback: Callable[[], bool] = lambda: False
        self._on_open: Callable[[int], None] = lambda source_id: None
        self._export_dir: str | None = None

    def use_same_playback(self, enabled: Callable[[], bool]) -> None:
        """插件启动时接上设置页的「多端同时播放」开关。"""
        self._same_playback = enabled

    def set_open_hook(self, hook: Callable[[int], None]) -> None:
        """来源实例打开时通知生活事件监听，插件据此把 source_id 带进 resource.changed。"""
        self._on_open = hook

    def is_scratch_dir(self, cid: str) -> bool:
        """是不是本插件自己写入的专用目录。这里的变动不是用户的，生活事件要忽略。"""
        return cid in (self._export_dir, self._copies.dir_id)

    def validate_config(self, config: Mapping[str, Any]) -> dict[str, Any]:
        return {}

    def status(self, config: Mapping[str, Any]) -> SourceStatus:
        status = self._service.status()
        credential = status.credential
        if credential is None:
            return SourceStatus("failed", message="还没有接入 115 账号", page="account")
        if credential.invalid_at is not None:
            return SourceStatus(
                "failed", message="115 登录已失效，请重新扫码或填写 Cookie", page="account"
            )
        if status.rate_limited:
            resume_at = status.breaker.resume_at
            return SourceStatus(
                "limited",
                message="115 接口暂时受限",
                resume_at=None if resume_at is None else resume_at.isoformat(),
                page="account",
            )
        return SourceStatus("ok")

    def check(self, config: Mapping[str, Any]) -> None:
        credential = self._service.status().credential
        if credential is None:
            raise to_sdk_error(CredentialMissing(), now=self._clock.now())
        if credential.invalid_at is not None:
            raise to_sdk_error(CredentialInvalid(), now=self._clock.now())

    def open(self, source_id: int, config: Mapping[str, Any]) -> Cloud115ResourceSource:
        self._on_open(source_id)
        return Cloud115ResourceSource(
            service=self._service,
            clock=self._clock,
            links=self._links,
            throttle=self._throttle,
            scanner=self.scan,
            file_ids=self._file_ids,
            copies=self._copies,
            # 每次播放时再读开关：插件启动、改设置都晚于来源实例的创建
            same_playback=lambda: self._same_playback(),
            progress=self._progress,
            heartbeat=self._heartbeat_cb,
        )

    def scan(
        self,
        locator: str | None,
        known: Mapping[str, ResourceEntry] | None = None,
    ) -> list[ResourceEntry]:
        """导出目录树拿到全部路径，再列目录取文件的 ID、pickcode 等。

        列目录每个目录一次请求，库大了很贵：库里已有记录的路径（known）不再去列，只列含
        新路径的目录和它的上级目录。一个目录下的媒体文件都已知时整个目录直接复用。
        """
        cid = _directory_id(locator)
        export_dir = self.prepare_export_dir()
        self.clean_exports(export_dir)
        paths = set(self.export_paths(cid, export_dir))
        media_paths = {path for path in paths if media_kind(path.rpartition("/")[2]) is not None}
        # 已不在目录树里的已知路径不能返回，宿主据此把它们标为失效
        reusable = {path: entry for path, entry in (known or {}).items() if path in media_paths}
        unknown = media_paths - reusable.keys()
        entries: list[ResourceEntry] = []
        root_path: str | None = None

        def reuse(prefix: str) -> None:
            for path in sorted(media_paths):
                if prefix and not path.startswith(prefix):
                    continue
                entry = reusable[path]
                if entry.identity is not None:
                    self._file_ids[entry.locator] = entry.identity
                entries.append(replace(entry, root_path=root_path))

        if not unknown:
            # 没有新路径：不用列任何目录，只取一次目录路径来刷新根目录的显示名
            root_path = "/" + "/".join(ref.name for ref in self.folder_path(cid))
            reuse("")
            return entries

        def walk(directory: str, prefix: str) -> None:
            nonlocal root_path
            self._heartbeat()
            listing = self._call(lambda: self._service.list_dir(directory, Priority.BACKGROUND))
            if root_path is None:
                root_path = "/" + "/".join(ref.name for ref in listing.path)
            for entry in listing.entries:
                relative = f"{prefix}/{entry.name}" if prefix else entry.name
                if entry.is_dir:
                    # 导出目录和多端播放副本是本插件自己的，不参与资料库扫描
                    if not prefix and entry.name == EXPORT_DIR_NAME:
                        continue
                    directory_prefix = relative.rstrip("/") + "/"
                    if not any(path.startswith(directory_prefix) for path in media_paths):
                        continue
                    if not any(path.startswith(directory_prefix) for path in unknown):
                        reuse(directory_prefix)
                        continue
                    walk(entry.id, relative)
                    continue
                if relative not in media_paths:
                    continue
                kind = media_kind(entry.name)
                if kind is not None:
                    self._file_ids[entry.pickcode or entry.id] = entry.id
                    entries.append(
                        ResourceEntry(
                            locator=entry.pickcode or entry.id,
                            relative_path=relative,
                            kind=kind,
                            size=entry.size,
                            sha1=entry.sha1 or None,
                            playback_locator=entry.pickcode or entry.id,
                            key=entry.id,
                            identity=entry.id,
                            root_path=root_path,
                        )
                    )

        walk(cid, "")
        return entries

    def folder_path(self, locator: str) -> list[DirRef]:
        return self._call(lambda: self._service.dir_path(locator, Priority.BACKGROUND))

    def prepare_export_dir(self) -> str:
        self._export_dir = self._call(
            lambda: self._service.make_dir(EXPORT_DIR_NAME, Priority.BACKGROUND)
        )
        return self._export_dir

    def clean_exports(self, export_dir: str) -> None:
        try:
            listing = self._call(lambda: self._service.list_dir(export_dir, Priority.BACKGROUND))
            for entry in listing.entries:
                if not entry.is_dir and entry.name.endswith(EXPORT_FILE_SUFFIX):
                    self._call(partial(self._service.delete_file, entry.id, Priority.BACKGROUND))
        except ResourceUnavailable as exc:
            logger.warning("failed to clean 115 export dir: %s", exc.message)

    def export_paths(self, locator: str, export_dir: str) -> list[str]:
        give_up = self._clock.now() + EXPORT_TIMEOUT
        while True:
            try:
                export_id = self._call(
                    lambda: self._service.export_start(locator, export_dir, Priority.BACKGROUND)
                )
                break
            except ExportBusy:
                if self._clock.now() >= give_up:
                    raise ResourceUnavailable("115 上的其他导出任务 30 分钟内没有结束") from None
                if self._progress is None:
                    self._sleeper(EXPORT_BUSY_RETRY.total_seconds())
                else:
                    self._progress(self._clock.now() + EXPORT_BUSY_RETRY, "source_busy")
        give_up = self._clock.now() + EXPORT_TIMEOUT
        while True:
            # 导出可能持续很久；发心跳让宿主重置扫描的空闲超时
            self._heartbeat()
            self._sleeper(EXPORT_POLL_INTERVAL.total_seconds())
            file_id = self._call(
                lambda: self._service.export_status(export_id, Priority.BACKGROUND)
            )
            if file_id is not None:
                break
            if self._clock.now() >= give_up:
                raise ResourceUnavailable("导出目录树 30 分钟内没有完成")
        try:
            return self._call(lambda: self._service.export_read(file_id, Priority.BACKGROUND))
        finally:
            try:
                self._call(lambda: self._service.delete_file(file_id, Priority.BACKGROUND))
            except ResourceUnavailable as exc:
                logger.warning("failed to delete 115 export file: %s", exc.message)

    def _heartbeat(self) -> None:
        if self._heartbeat_cb is not None:
            self._heartbeat_cb()

    def _call[T](self, call: Callable[[], T]) -> T:
        try:
            return call()
        except RateLimited as exc:
            if self._progress is None:
                raise to_sdk_error(exc, now=self._clock.now()) from exc
            self._progress(exc.resume_at or self._clock.now() + RATE_LIMIT_BACKOFF, "rate_limited")
            return self._call(call)
        except ExportBusy:
            # 导出任务忙碌由 export_paths 自己退避重试，不能在这里翻译掉
            raise
        except (
            DirNotFound,
            FileGone,
            CredentialMissing,
            CredentialInvalid,
            Cloud115Unavailable,
        ) as exc:
            raise to_sdk_error(exc, now=self._clock.now()) from exc

    def life_show(self) -> None:
        self._service.life_show(Priority.INTERACTIVE)

    def life_events(
        self, after_id: int | None, after_time: int | None, web: bool, priority: Priority
    ) -> list[LifeEvent]:
        try:
            return self._service.life_events(after_id, after_time, web, priority)
        except LifeApiRejected as exc:
            raise to_sdk_error(exc, now=self._clock.now()) from exc


class Cloud115ResourceSource:
    def __init__(
        self,
        *,
        service: Cloud115Service,
        clock: Clock,
        links: DirectLinkCache,
        throttle: PlayThrottle,
        scanner: Callable[
            [str | None, Mapping[str, ResourceEntry] | None],
            list[ResourceEntry],
        ],
        file_ids: dict[str, str],
        copies: PlaybackCopies,
        same_playback: Callable[[], bool],
        progress: Callable[[datetime, WaitReason], None] | None,
        heartbeat: Callable[[], None] | None,
    ) -> None:
        self._service = service
        self._clock = clock
        self._links = links
        self._throttle = throttle
        self._scanner = scanner
        self._file_ids = file_ids
        self._copies = copies
        self._same_playback = same_playback
        self._progress = progress
        self._heartbeat_cb = heartbeat

    def browse(self, locator: str | None = None) -> ResourceListing:
        cid = _directory_id(locator)
        listing = self._call(lambda: self._service.list_dir(cid))
        entries: list[ResourceEntry] = []
        for entry in listing.entries:
            # 同步导出目录在根目录下，不能选作资料库目录
            if cid == ROOT_CID and entry.is_dir and entry.name == EXPORT_DIR_NAME:
                continue
            kind: MediaKind
            if entry.is_dir:
                locator_id = entry.id
                kind = "video"
            else:
                detected = media_kind(entry.name)
                if detected is None:
                    # 浏览只列出同步认识的媒体文件，其他文件不返回
                    continue
                kind = detected
                locator_id = entry.pickcode or entry.id
                self._file_ids[locator_id] = entry.id
            entries.append(
                # 115 的目录也有 pickcode，但浏览目录要用 cid，所以目录的 locator 固定是 cid
                ResourceEntry(
                    locator_id,
                    entry.name,
                    kind,
                    entry.size,
                    entry.sha1 or None,
                    entry.is_dir,
                    locator_id,
                    entry.id,
                    entry.id,
                )
            )
        path = "/" + "/".join(ref.name for ref in listing.path)
        return ResourceListing(
            cid,
            path,
            tuple(entries),
            tuple((ref.id, ref.name) for ref in listing.path),
            selectable=_selectable(cid, listing.path),
        )

    def scan(
        self,
        locator: str | None,
        known: Mapping[str, ResourceEntry] | None = None,
    ) -> list[ResourceEntry]:
        return self._scanner(locator, known)

    def read(self, locator: str, max_bytes: int) -> bytes:
        return self._call(
            lambda: self._service.read_file(self._file_ids.get(locator, locator), max_bytes)
        )

    def resolve(
        self, locator: str, user_agent: str, throttle_key: str | None = None
    ) -> PlaybackTarget:
        key = _throttle_key(throttle_key) if throttle_key else None
        if key is not None:
            self._raise_if_limited(self._throttle.request(key))
        # 多端同时播放按播放会话分开缓存直链；令牌摘要就是会话
        owner = (key or "") if self._same_playback() else ""

        def fetch(shared: bool) -> DirectLink:
            if key is not None:
                self._raise_if_limited(self._throttle.fetch(key))
            if shared:
                try:
                    return self._copies.link(locator, user_agent)
                except Cloud115Error as exc:
                    # 复制不成就退回原文件的直链，和不开多端播放时一样
                    logger.warning("115 playback copy failed, using the original: %s", exc.code)
            try:
                return self._service.direct_link(locator, user_agent, Priority.INTERACTIVE)
            except (
                RateLimited,
                DirNotFound,
                FileGone,
                CredentialMissing,
                CredentialInvalid,
                Cloud115Unavailable,
            ) as exc:
                raise to_sdk_error(exc, now=self._clock.now()) from exc

        url = self._links.get_or_fetch(locator, user_agent, fetch, owner)
        return PlaybackTarget(url=url.strip())

    @staticmethod
    def _raise_if_limited(wait: int) -> None:
        if wait:
            raise ResourceRateLimited("请求太频繁，请稍后再试", wait)

    def delete(self, locator: str) -> None:
        file_id = self._file_ids.get(locator, locator)
        self._call(lambda: self._service.delete_file(file_id))

    def _heartbeat(self) -> None:
        if self._heartbeat_cb is not None:
            self._heartbeat_cb()

    def _call[T](self, call: Callable[[], T]) -> T:
        # 只在管理员操作时调用，按交互优先级排队
        try:
            return call()
        except RateLimited as exc:
            if self._progress is None:
                raise to_sdk_error(exc, now=self._clock.now()) from exc
            self._progress(exc.resume_at or self._clock.now() + RATE_LIMIT_BACKOFF, "rate_limited")
            return self._call(call)
        except (
            DirNotFound,
            FileGone,
            CredentialMissing,
            CredentialInvalid,
            Cloud115Unavailable,
        ) as exc:
            raise to_sdk_error(exc, now=self._clock.now()) from exc


def _directory_id(locator: str | None) -> str:
    """来源根统一用 None（115 内部是 "0"）；其他定位符必须是数字目录 ID。"""
    if locator is None:
        return ROOT_CID
    if not locator.isdigit():
        raise ResourceNotFound("115 目录 ID 不合法")
    return locator


def _selectable(cid: str, path: list[DirRef]) -> bool:
    """来源根和插件自己的专用目录不能作为资料库目录，但仍照常返回列表。"""
    if cid == ROOT_CID:
        return False
    return not (path and path[0].name == EXPORT_DIR_NAME)


def _throttle_key(value: str) -> str:
    return sha256(value.encode()).hexdigest()
