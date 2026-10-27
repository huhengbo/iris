"""监听 115 生活事件，把有变动的目录攒起来推给宿主做定向同步。

状态存在插件自己的 `state/life.json`：启用状态之外的双游标（事件 ID + 时间）、
App 版接口被拒后临时改用网页版接口的截止时间，以及等待推送的目录。
事件只在插件进程里拉取；宿主只收到 `resource.changed` 通知。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from iris_plugin_sdk import (
    PluginLogger,
    PluginNotifier,
    SettingsEndpoint,
    media_kind,
    read_json,
    write_json,
)

from iris_source_115.clock import Clock, from_iso, to_iso
from iris_source_115.errors import Cloud115Error, ResourceApiRejected
from iris_source_115.gateway import LIFE_EVENT_LIMIT, LifeEvent
from iris_source_115.provider import Cloud115ResourceProvider
from iris_source_115.scheduler import Priority

LIFE_ENABLED = "life_enabled"
LIFE_INTERVAL = "life_interval"
TICK_SECONDS = 5
# 同一个目录的事件攒一攒再同步：安静 45 秒后同步，持续有变动时最多等 5 分钟
QUIET = timedelta(seconds=45)
MAX_WAIT = timedelta(minutes=5)
# App 版接口返回 405 后改用网页版接口这么久
WEB_FALLBACK = timedelta(hours=24)

# 生活事件类型，见 p115client 的 BEHAVIOR_TYPE_TO_NAME
_ADDED = {1, 2, 14, 18, 23}  # 上传、接收、复制
_MOVED = {5, 6}
_RENAMED = {20, 24}
_DELETED = 22


@dataclass(frozen=True)
class _Pending:
    first: str
    last: str


@dataclass
class _State:
    # 已经开始监听（打开过最近记录并确定起始游标）
    armed: bool = False
    # 处理到的最新事件 ID 和时间（Unix 秒）
    cursor: int | None = None
    cursor_time: int | None = None
    # 等待推送给宿主的目录；键是 115 目录 ID
    pending: dict[str, _Pending] = field(default_factory=dict)
    # App 版接口被拒后，改用网页版接口到这个时间
    web_until: str | None = None
    last_polled_at: str | None = None
    last_error: str | None = None

    @classmethod
    def load(cls, data: dict[str, Any] | None) -> _State:
        if data is None:
            return cls()
        pending = {
            str(key): _Pending(first=str(item["first"]), last=str(item["last"]))
            for key, item in (data.get("pending") or {}).items()
        }
        cursor = data.get("cursor")
        cursor_time = data.get("cursor_time")
        return cls(
            armed=bool(data.get("armed", False)),
            cursor=int(cursor) if cursor is not None else None,
            cursor_time=int(cursor_time) if cursor_time is not None else None,
            pending=pending,
            web_until=_optional_text(data.get("web_until")),
            last_polled_at=_optional_text(data.get("last_polled_at")),
            last_error=_optional_text(data.get("last_error")),
        )

    def snapshot(self) -> _State:
        return replace(self, pending=dict(self.pending))

    def dump(self) -> dict[str, Any]:
        return {
            "armed": self.armed,
            "cursor": self.cursor,
            "cursor_time": self.cursor_time,
            "pending": {
                key: {"first": pending.first, "last": pending.last}
                for key, pending in self.pending.items()
            },
            "web_until": self.web_until,
            "last_polled_at": self.last_polled_at,
            "last_error": self.last_error,
        }


@dataclass(frozen=True)
class LifeStatus:
    enabled: bool
    interval_seconds: int
    # 当前用的接口：App 版，或者 App 版被拒后临时改用的网页版
    api: Literal["app", "web"]
    last_polled_at: str | None
    last_error: str | None
    # 等待推送给宿主的目录数
    pending: int


class LifeMonitor:
    """在插件自己的后台线程里监听 115 生活事件。

    默认关闭：开启时要打开账号的「最近记录」开关，会修改 115 账号设置。
    只处理开启之后的事件；事件漏掉或太多时由宿主的定时同步兜底。
    """

    def __init__(
        self,
        source: Cloud115ResourceProvider,
        state_path: Path,
        settings: SettingsEndpoint,
        clock: Clock,
        logger: PluginLogger,
        tick_seconds: float = TICK_SECONDS,
    ) -> None:
        self._source = source
        self._path = state_path
        self._settings = settings
        self._clock = clock
        self._logger = logger
        self._tick_seconds = tick_seconds
        self._lock = threading.Lock()
        self._state = _State()
        # 设置变化时递增，丢弃还在网络请求中的旧一轮结果
        self._generation = 0
        self._notify: PluginNotifier | None = None
        self._source_id: int | None = None
        # 下次拉取的时间，只在内存里：插件重启后马上拉一次
        self._next_poll: datetime | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- 生命周期 ----

    def start(self, notify: PluginNotifier | None) -> None:
        raw = read_json(self._path)
        if raw is not None and not isinstance(raw, dict):
            raise ValueError(f"生活事件状态文件必须是 JSON 对象：{self._path}")
        with self._lock:
            self._state = _State.load(raw)
            self._notify = notify
            self._next_poll = None
        self._thread = threading.Thread(target=self._run, name="life-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            self._generation += 1
        thread = self._thread
        if thread is not None:
            # 宿主给 stop/shutdown 10 秒；后台线程是 daemon，网络请求没结束也不阻塞进程退出
            thread.join(timeout=5)
        self._thread = None

    def flush(self) -> None:
        # 备份前宿主只等 10 秒；后台线程正在拉事件时跳过这次写入，下一轮会再存
        if not self._lock.acquire(timeout=5):
            self._logger.warning("115 生活事件状态正忙，跳过这次 flush")
            return
        try:
            self._save()
        finally:
            self._lock.release()

    def set_source_id(self, source_id: int) -> None:
        """来源实例打开时由 provider 调用；resource.changed 需要这个 ID。

        不做加锁：整数赋值本身是原子的，来源打开不应该被正在拉取事件的后台线程挡住。
        """
        self._source_id = source_id

    def _run(self) -> None:
        while not self._stop.wait(self._tick_seconds):
            try:
                self.tick()
            except Exception:
                self._logger.exception("115 生活事件检查失败")

    # ---- 设置 ----

    def apply_settings(self) -> None:
        """设置页保存后立即生效；启动监听需要联网，放在锁外避免挡住状态页。"""
        with self._lock:
            self._generation += 1
            enabled = self._enabled()
            needs_start = enabled and not self._state.armed
            if not enabled and (
                self._state.armed or self._state.pending or self._state.cursor is not None
            ):
                self._state = _State()
            self._next_poll = None
            self._save()
            generation = self._generation
        if needs_start:
            self._start(generation)

    def status(self) -> LifeStatus:
        with self._lock:
            return self._status()

    # ---- 定时检查 ----

    def tick(self) -> None:
        with self._lock:
            enabled = self._enabled()
            if not enabled:
                if self._state.armed or self._state.pending or self._state.cursor is not None:
                    self._state = _State()
                    self._save()
                return
            generation = self._generation
            start = not self._state.armed
        if start:
            self._start(generation)
        with self._lock:
            if generation != self._generation or not self._state.armed:
                return
            now = self._clock.now()
            if self._next_poll is not None and now < self._next_poll:
                state = None
            else:
                self._next_poll = now + timedelta(seconds=self._interval())
                state = self._state.snapshot()
        if state is not None:
            self._poll(state, generation)
        with self._lock:
            if generation == self._generation and self._state.armed:
                self._flush(self._clock.now())
                self._save()

    def _start(self, generation: int) -> None:
        """打开最近记录开关，从当前最新的事件开始监听。由管理员操作触发，用交互优先级。"""
        state = _State()
        try:
            self._source.life_show()
            latest = self._fetch(state, None, Priority.INTERACTIVE)
        except Cloud115Error as exc:
            with self._lock:
                if generation == self._generation:
                    self._state.last_error = exc.message
                    self._save()
            return
        with self._lock:
            if generation != self._generation:
                return
            _advance(state, latest)
            state.armed = True
            self._state = state
            self._next_poll = None
            self._save()

    def _poll(self, state: _State, generation: int) -> None:
        now = self._clock.now()
        try:
            if state.cursor is None:
                _advance(state, self._fetch(state, None, Priority.BACKGROUND))
                events: list[LifeEvent] = []
            else:
                events = self._fetch(state, state.cursor, Priority.BACKGROUND)
        except Cloud115Error as exc:
            # 风控、网络错误或凭据失效：记下来，下次再试
            with self._lock:
                if generation == self._generation:
                    self._state.last_error = exc.message
            return
        with self._lock:
            if generation != self._generation:
                return
            self._state.cursor = state.cursor
            self._state.cursor_time = state.cursor_time
            self._state.web_until = state.web_until
            self._state.last_polled_at = to_iso(now)
            self._state.last_error = None
            if len(events) >= LIFE_EVENT_LIMIT:
                self._logger.warning("115 生活事件太多，较早的变动等定时同步处理")
                self._state.last_error = "115 上的变动太多，较早的变动等定时同步处理"
            for event in reversed(events):
                self._handle(event, to_iso(now))
            _advance(self._state, events)

    def _fetch(self, state: _State, after_id: int | None, priority: Priority) -> list[LifeEvent]:
        now = self._clock.now()
        web = _uses_web(state, now)
        after_time = state.cursor_time if after_id is not None else None
        try:
            return self._source.life_events(after_id, after_time, web, priority)
        except ResourceApiRejected:
            self._logger.warning("115 App 版生活事件接口被拒，改用网页版接口")
            state.web_until = to_iso(now + WEB_FALLBACK)
            return self._source.life_events(after_id, after_time, True, priority)

    def _handle(self, event: LifeEvent, now: str) -> None:
        if self._source.is_scratch_dir(event.parent_id):
            # 导出目录树、多端播放副本是本插件自己写的，不是资料库的变动
            return
        if event.type == _DELETED:
            if not event.is_dir and media_kind(event.name) is None:
                return
            # 插件看不到宿主的文件表，删除交给宿主列一次上级目录来判断
            self._queue(event.parent_id, now)
            return
        if not event.is_dir and media_kind(event.name) is None:
            return
        if event.type in _ADDED or event.type in _RENAMED or event.type in _MOVED:
            # 115 的移动事件只给移动后的 parent_id，没有原父目录字段；旧位置由宿主定时同步发现
            self._queue(event.parent_id, now)

    def _queue(self, directory: str, now: str) -> None:
        pending = self._state.pending.get(directory)
        if pending is None:
            self._state.pending[directory] = _Pending(first=now, last=now)
        else:
            self._state.pending[directory] = _Pending(first=pending.first, last=now)

    def _flush(self, now: datetime) -> None:
        due = sorted(key for key, pending in self._state.pending.items() if _settled(pending, now))
        if not due:
            return
        if self._notify is None or self._source_id is None:
            # 宿主还没有打开来源实例，先留着，等有了 source_id 再推
            return
        try:
            self._notify.resource_changed(self._source_id, due)
        except Exception:
            self._logger.exception("推送 115 目录变动失败")
            return
        for key in due:
            del self._state.pending[key]

    # ---- 内部 ----

    def _enabled(self) -> bool:
        return bool(self._settings.values()[LIFE_ENABLED])

    def _interval(self) -> int:
        return int(self._settings.values()[LIFE_INTERVAL])

    def _status(self) -> LifeStatus:
        web = _uses_web(self._state, self._clock.now())
        return LifeStatus(
            enabled=self._enabled(),
            interval_seconds=self._interval(),
            api="web" if web else "app",
            last_polled_at=self._state.last_polled_at,
            last_error=self._state.last_error,
            pending=len(self._state.pending),
        )

    def _save(self) -> None:
        write_json(self._path, self._state.dump())


def _advance(state: _State, events: list[LifeEvent]) -> None:
    """把游标移到这批事件里最新的一条；没有事件时，新开始监听的游标从 0 开始。"""
    if events:
        state.cursor = max(event.id for event in events)
        times = [event.time for event in events if event.time]
        if times:
            state.cursor_time = max(times)
    elif state.cursor is None:
        state.cursor = 0


def _uses_web(state: _State, now: datetime) -> bool:
    return state.web_until is not None and now < from_iso(state.web_until)


def _settled(pending: _Pending, now: datetime) -> bool:
    quiet = now - from_iso(pending.last) >= QUIET
    return quiet or now - from_iso(pending.first) >= MAX_WAIT


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) else None
