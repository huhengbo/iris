"""生活事件：双游标、去抖、专用目录过滤和 resource.changed 推送。

从 backend/tests/test_life.py 迁移。宿主不再直接提供 SyncEngine，插件只负责推目录，
所以断言从「任务记录」改为「发给宿主的 resource.changed」。
"""

from __future__ import annotations

import stat
import threading
import time
from typing import Any

import pytest
from iris_plugin_sdk import PluginRequestError

from iris_source_115.errors import Cloud115Unavailable
from iris_source_115.provider import EXPORT_DIR_NAME
from tests.conftest import Harness, save_cookie, update_settings
from tests.fake_gateway import VALID_COOKIE

UPLOAD, MOVE, DELETE = 2, 5, 22
SOURCE_ID = 7


def enable(harness: Harness, interval: int = 20) -> dict[str, Any]:
    response = update_settings(harness, {"life_enabled": True, "life_interval": interval})
    body = response.body
    assert isinstance(body, dict)
    return body


def status(harness: Harness) -> dict[str, Any]:
    body = harness.request("GET", "life").body
    assert isinstance(body, dict)
    return body


def open_source(harness: Harness, source_id: int = SOURCE_ID) -> Any:
    """宿主打开来源实例；插件据此知道 resource.changed 要带哪个 source_id。"""
    return harness.provider.open(source_id, {})


def settle(harness: Harness) -> None:
    """拉取事件，再等目录安静下来推送。"""
    harness.life.tick()
    harness.clock.advance(seconds=45)
    harness.life.tick()


def tick(harness: Harness) -> None:
    """跳过拉取间隔再拉一次。"""
    harness.life._next_poll = None
    harness.life.tick()


def authenticate(harness: Harness) -> None:
    assert save_cookie(harness).status == 200


def test_disabled_by_default_and_makes_no_calls(harness: Harness) -> None:
    assert status(harness) == {
        "enabled": False,
        "interval_seconds": 20,
        "api": "app",
        "last_polled_at": None,
        "last_error": None,
        "pending": 0,
    }
    harness.gateway.calls.clear()
    harness.life.tick()
    assert harness.gateway.calls == []
    assert harness.gateway.life_shown is False


def test_enabling_turns_on_life_log_and_skips_old_events(harness: Harness) -> None:
    authenticate(harness)
    # 开启之前的变动由宿主的定时同步处理
    old = harness.gateway.make_path("影视/电影/旧片 (2020)/旧片.2020.mkv")
    harness.gateway.event(UPLOAD, old)

    body = enable(harness)

    assert body["values"]["life_enabled"] is True
    settle(harness)
    assert harness.gateway.life_shown is True
    assert status(harness)["pending"] == 0
    assert status(harness)["last_polled_at"] is not None
    assert harness.notifier.changes == []


def test_upload_triggers_debounced_resource_changed(harness: Harness) -> None:
    authenticate(harness)
    enable(harness)
    open_source(harness)
    movie_dir = harness.gateway.make_path("影视/电影/新片 (2024)")
    first = harness.gateway.add_file(movie_dir, "新片.2024.1080p.mkv")
    harness.gateway.event(UPLOAD, first)
    harness.life.tick()
    assert status(harness)["pending"] == 1

    # 同一目录还在陆续上传，先不推送
    harness.clock.advance(seconds=30)
    second = harness.gateway.add_file(movie_dir, "新片.2024.1080p.chs.srt")
    harness.gateway.event(UPLOAD, second)
    harness.life.tick()
    assert harness.notifier.changes == []

    harness.clock.advance(seconds=45)
    harness.life.tick()

    assert harness.notifier.changes == [(SOURCE_ID, [movie_dir])]
    assert status(harness)["pending"] == 0


def test_busy_directory_is_pushed_after_max_wait(harness: Harness) -> None:
    authenticate(harness)
    enable(harness)
    open_source(harness)
    folder = harness.gateway.make_path("影视/电影/合集")
    for minute in range(5):
        node = harness.gateway.add_file(folder, f"片{minute}.mkv")
        harness.gateway.event(UPLOAD, node)
        harness.life.tick()
        assert harness.notifier.changes == []
        harness.clock.advance(seconds=30)
        harness.life.tick()
        harness.clock.advance(seconds=30)
    node = harness.gateway.add_file(folder, "片5.mkv")
    harness.gateway.event(UPLOAD, node)
    harness.life.tick()
    # 持续有变动时最多等 5 分钟，不再等安静 45 秒
    assert harness.notifier.changes == [(SOURCE_ID, [folder])]


def test_deleted_file_queues_its_parent_directory(harness: Harness) -> None:
    authenticate(harness)
    enable(harness)
    open_source(harness)
    folder = harness.gateway.make_path("影视/电影/星际穿越 (2014)")
    node = harness.gateway.add_file(folder, "Interstellar.2014.1080p.mp4")
    harness.gateway.event(DELETE, node)
    harness.gateway.remove(node)
    harness.gateway.calls.clear()

    settle(harness)

    # 插件看不到宿主的文件表，删除也交给宿主列上级目录判断
    assert harness.notifier.changes == [(SOURCE_ID, [folder])]
    assert "export_start" not in harness.gateway.calls


def test_deleted_dir_queues_its_parent(harness: Harness) -> None:
    authenticate(harness)
    enable(harness)
    open_source(harness)
    folder = harness.gateway.make_path("影视/电影/星际穿越 (2014)")
    parent = harness.gateway.nodes[folder].parent
    assert parent is not None
    harness.gateway.event(DELETE, folder)
    harness.gateway.remove(folder)

    settle(harness)

    assert harness.notifier.changes == [(SOURCE_ID, [parent])]


def test_file_moved_out_of_libraries_queues_the_new_parent(harness: Harness) -> None:
    authenticate(harness)
    enable(harness)
    open_source(harness)
    folder = harness.gateway.make_path("影视/电影/星际穿越 (2014)")
    node = harness.gateway.add_file(folder, "Interstellar.2014.1080p.mp4")
    target = harness.gateway.make_path("暂存")
    harness.gateway.move(node, target)
    harness.gateway.event(MOVE, node)

    settle(harness)

    assert harness.notifier.changes == [(SOURCE_ID, [target])]


def test_dir_moved_out_of_libraries_queues_the_new_parent(harness: Harness) -> None:
    authenticate(harness)
    enable(harness)
    open_source(harness)
    folder = harness.gateway.make_path("影视/电影/星际穿越 (2014)")
    target = harness.gateway.make_path("暂存")
    harness.gateway.move(folder, target)
    harness.gateway.event(MOVE, folder)

    settle(harness)

    # 事件只给出移到了哪里；旧位置靠宿主的定时同步发现
    assert harness.notifier.changes == [(SOURCE_ID, [target])]


def test_non_media_files_are_ignored(harness: Harness) -> None:
    authenticate(harness)
    enable(harness)
    open_source(harness)
    node = harness.gateway.make_path("影视/电影/星际穿越 (2014)/海报说明.txt")
    harness.gateway.event(UPLOAD, node)

    settle(harness)

    assert status(harness)["pending"] == 0
    assert harness.notifier.changes == []


def test_deleted_non_media_files_are_ignored(harness: Harness) -> None:
    authenticate(harness)
    enable(harness)
    open_source(harness)
    node = harness.gateway.make_path("影视/电影/星际穿越 (2014)/海报说明.txt")
    harness.gateway.event(DELETE, node)
    harness.gateway.remove(node)

    settle(harness)

    assert status(harness)["pending"] == 0
    assert harness.notifier.changes == []


def test_rejected_app_api_falls_back_to_web_for_a_day(harness: Harness) -> None:
    authenticate(harness)
    harness.gateway.life_app_rejected = True
    assert enable(harness)["values"]["life_enabled"] is True
    assert status(harness)["api"] == "web"
    harness.life.tick()
    assert harness.gateway.life_apis == ["web", "web"]

    harness.gateway.life_app_rejected = False
    harness.clock.advance(hours=23)
    harness.life.tick()
    assert harness.gateway.life_apis[-1] == "web"
    harness.clock.advance(hours=1)
    tick(harness)
    assert harness.gateway.life_apis[-1] == "app"
    assert status(harness)["api"] == "app"


def test_poll_failure_is_reported_and_retried(harness: Harness) -> None:
    authenticate(harness)
    enable(harness)
    node = harness.gateway.make_path("影视/电影/合集/片.mkv")
    harness.gateway.event(UPLOAD, node)
    harness.gateway.errors_once["life_events"] = Cloud115Unavailable()

    harness.life.tick()
    assert status(harness)["last_error"] is not None
    assert status(harness)["pending"] == 0

    # 下一个检查间隔再拉，事件没有丢
    harness.clock.advance(seconds=20)
    harness.life.tick()
    assert status(harness)["last_error"] is None
    assert status(harness)["pending"] == 1


def test_pending_changes_wait_until_a_source_is_opened(harness: Harness) -> None:
    authenticate(harness)
    enable(harness)
    node = harness.gateway.make_path("影视/电影/合集/片.mkv")
    harness.gateway.event(UPLOAD, node)

    settle(harness)
    assert harness.notifier.changes == []
    assert status(harness)["pending"] == 1

    open_source(harness, 9)
    harness.life.tick()
    assert harness.notifier.changes == [(9, [harness.gateway.nodes[node].parent])]


def test_disabling_clears_pending(harness: Harness) -> None:
    authenticate(harness)
    enable(harness)
    harness.gateway.event(UPLOAD, harness.gateway.make_path("影视/电影/合集/片.mkv"))
    harness.life.tick()
    assert status(harness)["pending"] == 1

    response = update_settings(harness, {"life_enabled": False, "life_interval": 60})
    body = response.body
    assert body["values"]["life_enabled"] is False
    assert body["values"]["life_interval"] == 60
    assert status(harness)["pending"] == 0
    harness.gateway.calls.clear()
    harness.clock.advance(minutes=10)
    harness.life.tick()
    assert harness.gateway.calls == []


def test_life_interval_is_validated(harness: Harness) -> None:
    for bad in (9, 601):
        with pytest.raises(PluginRequestError) as error:
            update_settings(harness, {"life_enabled": True, "life_interval": bad})
        assert error.value.status == 400
        assert error.value.code == "invalid_setting"


def test_events_older_than_the_time_cursor_are_skipped(harness: Harness) -> None:
    authenticate(harness)
    enable(harness)
    first = harness.gateway.make_path("影视/电影/新片 (2024)/新片.2024.mkv")
    harness.gateway.event(UPLOAD, first, time=1000)
    harness.life.tick()
    assert status(harness)["pending"] == 1

    # ID 更新但时间更早：按双游标视为已处理过的旧事件
    late = harness.gateway.make_path("影视/电影/迟到 (2023)/迟到.2023.mkv")
    harness.gateway.event(UPLOAD, late, time=900)
    tick(harness)
    assert status(harness)["pending"] == 1

    state = harness.state("life.json")
    # 旧事件没有推进游标
    assert state["cursor"] == 1
    assert state["cursor_time"] == 1000


def test_events_in_own_scratch_dirs_are_ignored(harness: Harness) -> None:
    authenticate(harness)
    # 建库时已经用过导出目录，插件知道这个专用目录的 ID
    directory = harness.provider.prepare_export_dir()
    name = harness.gateway.nodes[directory].name
    assert name == EXPORT_DIR_NAME
    enable(harness)
    open_source(harness)
    export = harness.gateway.make_path(f"{EXPORT_DIR_NAME}/流浪地球.mkv")
    harness.gateway.event(UPLOAD, export)

    tick(harness)

    assert status(harness)["pending"] == 0
    assert harness.notifier.changes == []


def test_life_state_is_stored_in_private_state_files(harness: Harness) -> None:
    authenticate(harness)
    enable(harness, interval=30)
    node = harness.gateway.make_path("影视/电影/合集/片.mkv")
    harness.gateway.event(UPLOAD, node, time=1234)
    harness.life.tick()

    life = harness.state("life.json")
    assert life["armed"] is True
    assert life["cursor"] >= 1
    assert life["cursor_time"] == 1234
    assert life["pending"] == {
        harness.gateway.nodes[node].parent: {
            "first": harness.clock.now().isoformat(timespec="seconds"),
            "last": harness.clock.now().isoformat(timespec="seconds"),
        }
    }
    settings = harness.state("settings.json")
    assert settings["life_enabled"] is True
    assert settings["life_interval"] == 30
    for name in ("life.json", "settings.json"):
        mode = stat.S_IMODE((harness.state_dir / name).stat().st_mode)
        assert mode == 0o600, name


def test_settings_expose_life_fields(harness: Harness) -> None:
    authenticate(harness)
    body = harness.request("GET", "settings").body
    fields = {field["name"]: field for field in body["fields"]}
    assert set(fields) == {"same_playback", "life_enabled", "life_interval"}
    assert fields["life_interval"]["min"] == 10
    assert fields["life_interval"]["max"] == 600
    assert body["values"] == {
        "same_playback": False,
        "life_enabled": False,
        "life_interval": 20,
    }
    assert VALID_COOKIE not in str(body)


def test_status_and_settings_are_not_blocked_by_a_running_poll(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    authenticate(harness)
    enable(harness)
    open_source(harness)
    harness.life.tick()
    assert status(harness)["enabled"] is True

    entered = threading.Event()
    release = threading.Event()
    original = harness.gateway.life_events

    def blocking_life_events(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(harness.gateway, "life_events", blocking_life_events)
    harness.life._next_poll = None
    poll = threading.Thread(target=harness.life.tick)
    poll.start()
    try:
        assert entered.wait(5)
        started = time.monotonic()
        assert status(harness)["enabled"] is True
        assert update_settings(harness, {"life_interval": 30}).status == 200
        assert time.monotonic() - started < 1
    finally:
        release.set()
        poll.join(5)
