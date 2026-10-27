"""全局限速、按接口分组的熔断和真实网关的错误分类。"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import timedelta
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

import pytest

from iris_source_115.errors import (
    Cloud115Unavailable,
    CopyNotReady,
    CredentialInvalid,
    FileGone,
    RateLimited,
)
from iris_source_115.p115 import P115Gateway
from iris_source_115.scheduler import (
    ApiGroup,
    CircuitBreaker,
    Priority,
    RateLimiter,
    RequestScheduler,
)
from tests.conftest import FakeClock

INTERVAL = 0.05


class FakeMonotonic:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def wait_until(condition: Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition not reached"
        time.sleep(0.005)


def start(
    limiter: RateLimiter, priority: Priority, name: str, order: list[str]
) -> threading.Thread:
    def work() -> None:
        limiter.acquire(priority)
        order.append(name)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    return thread


def test_requests_are_spaced_by_the_interval() -> None:
    now = FakeMonotonic()
    limiter = RateLimiter(INTERVAL, monotonic=now)
    order: list[str] = []
    limiter.acquire(Priority.BACKGROUND)

    second = start(limiter, Priority.BACKGROUND, "second", order)
    wait_until(lambda: limiter.waiting == 1)
    time.sleep(INTERVAL * 3)
    # 时间没到下一个时间片，只能继续等
    assert order == []

    now.value = INTERVAL
    second.join(timeout=2)
    assert order == ["second"]


def test_playback_requests_run_before_queued_sync_requests() -> None:
    now = FakeMonotonic()
    limiter = RateLimiter(INTERVAL, monotonic=now)
    order: list[str] = []
    limiter.acquire(Priority.BACKGROUND)

    sync = start(limiter, Priority.BACKGROUND, "sync", order)
    wait_until(lambda: limiter.waiting == 1)
    play = start(limiter, Priority.INTERACTIVE, "play", order)
    wait_until(lambda: limiter.waiting == 2)

    now.value = INTERVAL
    play.join(timeout=2)
    assert order == ["play"]
    assert limiter.waiting == 1

    now.value = INTERVAL * 2
    sync.join(timeout=2)
    assert order == ["play", "sync"]


def breaker(tmp_path: Path, clock: FakeClock, group: ApiGroup = ApiGroup.WEB) -> CircuitBreaker:
    return CircuitBreaker(clock, tmp_path / "state" / f"breaker-{group}.json", group)


def test_backoff_doubles_up_to_six_hours(tmp_path: Path) -> None:
    clock = FakeClock()
    web = breaker(tmp_path, clock)

    backoffs = []
    for _ in range(6):
        resume_at = web.trip()
        backoffs.append(resume_at - clock.now())
        clock.current = resume_at
    assert backoffs == [timedelta(minutes=m) for m in (30, 60, 120, 240, 360, 360)]

    web.record_success()
    assert web.trip() - clock.now() == timedelta(minutes=30)


def test_concurrent_risk_control_hits_count_once(tmp_path: Path) -> None:
    clock = FakeClock()
    web = breaker(tmp_path, clock)

    first = web.trip()
    assert web.trip() == first
    with pytest.raises(RateLimited):
        web.check()


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (lambda: _http_error(405), RateLimited),
        (lambda: _http_error(502), Cloud115Unavailable),
        (lambda: _p115_auth_error(), CredentialInvalid),
        (lambda: TimeoutError(), Cloud115Unavailable),
    ],
)
def test_real_gateway_classifies_errors(
    raised: Callable[[], Exception], expected: type[Exception]
) -> None:
    from iris_source_115.p115 import _translate_errors

    with pytest.raises(expected), _translate_errors():
        raise raised()


def _p115_auth_error() -> Exception:
    import errno

    from p115client.exception import P115AuthenticationError

    # 网关按异常类型分类，errno 取值无关；errno.EAUTH 只有 macOS 才有
    return P115AuthenticationError(
        errno.EPERM, {"state": False, "errno": 99, "error": "请重新登录"}
    )


def _http_error(code: int) -> Exception:
    # p115client 默认的 HTTP 层遇到 4xx/5xx 时抛 urllib 的 HTTPError
    return HTTPError("https://webapi.115.com/files", code, "error", Message(), None)


def test_breakers_are_separate_per_api_group(tmp_path: Path) -> None:
    clock = FakeClock()
    breakers = {group: breaker(tmp_path, clock, group) for group in ApiGroup}
    scheduler = RequestScheduler(RateLimiter(0), breakers)

    def risk_control() -> None:
        raise RateLimited()

    with pytest.raises(RateLimited):
        scheduler.run(Priority.BACKGROUND, risk_control, ApiGroup.WEB)
    # 网页版接口熔断时 App 版接口照常可用，反之亦然
    with pytest.raises(RateLimited):
        scheduler.run(Priority.INTERACTIVE, lambda: "listed", ApiGroup.WEB)
    assert scheduler.run(Priority.INTERACTIVE, lambda: "link", ApiGroup.APP) == "link"

    # 熔断状态各自保存，重启后不会互相覆盖
    reloaded = {group: breaker(tmp_path, clock, group) for group in ApiGroup}
    assert reloaded[ApiGroup.WEB].state.resume_at == clock.now() + timedelta(minutes=30)
    assert reloaded[ApiGroup.APP].state.resume_at is None


class _StubClient:
    """代替 P115Client，只回放给定的响应或异常。"""

    def __init__(self, pages: list[dict[str, object]] | None = None) -> None:
        self.pages = list(pages or [])
        self.error: Exception | None = None

    def life_behavior_detail_app(self, payload: object, **_: object) -> dict[str, object]:
        return self.pages.pop(0)

    def download_url(self, pickcode: str, **_: object) -> str:
        assert self.error is not None
        raise self.error


def _life_page(*events: tuple[int, int]) -> dict[str, object]:
    items = [
        {"id": id_, "type": 2, "file_id": str(id_), "file_name": "a.mkv", "update_time": t}
        for id_, t in events
    ]
    return {"state": True, "data": {"list": items, "count": len(items)}}


def test_real_gateway_stops_life_events_at_the_time_cursor(
    monkeypatch: pytest.MonkeyPatch, real_gateway: Callable[..., P115Gateway]
) -> None:
    from iris_source_115 import p115

    # ID 比游标新、时间却比游标早的事件不再返回（参考 p115strmhelper 的双游标）
    stub = _StubClient([_life_page((12, 1000), (11, 900), (10, 1000))])
    monkeypatch.setattr(p115, "_client", lambda cookie: stub)
    events = real_gateway().life_events("cookie", 9, 950, web=False)
    assert [(event.id, event.time) for event in events] == [(12, 1000)]


def test_real_gateway_reports_copy_not_ready(
    monkeypatch: pytest.MonkeyPatch, real_gateway: Callable[..., P115Gateway]
) -> None:
    import errno

    from p115client.exception import P115OSError

    from iris_source_115 import p115

    stub = _StubClient()
    monkeypatch.setattr(p115, "_client", lambda cookie: stub)
    stub.error = P115OSError(errno.EIO, {"state": False, "error": "文件上传不完整"})
    with pytest.raises(CopyNotReady):
        real_gateway().direct_link("cookie", "pickcode", "UA")
    stub.error = FileNotFoundError(errno.ENOENT, {"state": False, "error": "文件不存在"})
    with pytest.raises(FileGone):
        real_gateway().direct_link("cookie", "pickcode", "UA")
