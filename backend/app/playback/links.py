from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol

from app.clock import Clock

# 缓存比直链早这么久过期，不给出快过期的地址。和 p115strmhelper、p115dav 一样取 5 分钟：
# 播放器拖动、断线重连时会重新请求跳转地址，拿到的是新的直链，不需要留出整部片的时长
LINK_SAFETY_MARGIN = timedelta(minutes=5)
# 115 没给过期时间时只缓存这么久
UNKNOWN_EXPIRY_TTL = timedelta(minutes=10)
MAX_CACHED_LINKS = 1024


class ExpiringLink(Protocol):
    """带过期时间的直链，来源插件自己实现。"""

    @property
    def url(self) -> str: ...

    @property
    def expires_at(self) -> datetime | None: ...


class DirectLinkCache:
    """直链缓存，按 (playback_locator, User-Agent, 播放会话) 区分（ADR 0003）。

    播放会话来自 115 插件的「多端同时播放」（该插件的 ADR 0027）。

    不开多端同时播放时播放会话（owner）一律为空，同一文件同一 UA 共用一个直链。
    播放器起播时常常并发发起好几个请求，同一个文件的并发未命中只取一次链。
    单进程部署，放在内存里；重启后清空可以接受。
    """

    def __init__(self, clock: Clock, max_entries: int = MAX_CACHED_LINKS) -> None:
        self._clock = clock
        self._max_entries = max_entries
        self._lock = threading.Lock()
        self._entries: OrderedDict[tuple[str, str, str], tuple[str, datetime]] = OrderedDict()
        self._fetching: dict[str, tuple[threading.Lock, int]] = {}

    def get_or_fetch(
        self,
        playback_locator: str,
        user_agent: str,
        fetch: Callable[[bool], ExpiringLink],
        owner: str = "",
    ) -> str:
        """fetch 的参数表示别的播放会话此刻是否还拿着这个文件的有效直链。"""
        key = (playback_locator, user_agent, owner)
        cached = self._get(key)
        if cached is not None:
            return cached
        # 按文件加锁：判断「别的会话是否在用」和取链之间不能插进别的会话。
        # 记下等锁的人数，最后一个人走时才移除锁，保证同一文件始终只有一把锁
        with self._lock:
            file_lock, users = self._fetching.get(playback_locator, (threading.Lock(), 0))
            self._fetching[playback_locator] = (file_lock, users + 1)
        try:
            with file_lock:
                # 等锁期间别的请求可能已经取到了
                cached = self._get(key)
                if cached is not None:
                    return cached
                link = fetch(self._held_by_others(playback_locator, owner))
                self._put(key, link)
                return link.url
        finally:
            with self._lock:
                file_lock, users = self._fetching[playback_locator]
                if users == 1:
                    del self._fetching[playback_locator]
                else:
                    self._fetching[playback_locator] = (file_lock, users - 1)

    def _held_by_others(self, playback_locator: str, owner: str) -> bool:
        now = self._clock.now()
        with self._lock:
            return any(
                key[0] == playback_locator and key[2] != owner and now < valid_until
                for key, (_, valid_until) in self._entries.items()
            )

    def _get(self, key: tuple[str, str, str]) -> str | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            url, valid_until = entry
            if self._clock.now() >= valid_until:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return url

    def _put(self, key: tuple[str, str, str], link: ExpiringLink) -> None:
        now = self._clock.now()
        if link.expires_at is None:
            valid_until = now + UNKNOWN_EXPIRY_TTL
        else:
            valid_until = link.expires_at - LINK_SAFETY_MARGIN
        if valid_until <= now:
            return
        with self._lock:
            self._entries[key] = (link.url, valid_until)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
