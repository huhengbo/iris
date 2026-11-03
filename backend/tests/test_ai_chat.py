"""AI 对话：会话、SSE 事件序列、工具的可见范围、中断和清理（M57，ADR 0030）。"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.ai.types import (
    AiError,
    Finish,
    StreamEvent,
    TextDelta,
    ThinkingDelta,
    UsageEvent,
)
from app.container import AppContainer
from tests.conftest import FakeClock, bearer, create_user, login, save_tmdb_key
from tests.fake_ai import FakeAi
from tests.fake_cloud115 import FakeCloud115
from tests.fake_tmdb import FakeTmdb
from tests.test_ai_providers import add_provider, container
from tests.test_ai_tools import call
from tests.test_catalog import movie_library
from tests.test_sync import create_library, fresh_admin, sync

ADMIN = "/api/admin"
CHATS = "/api/ai/chats"


@pytest.fixture
def library(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
    fake_ai: FakeAi,
) -> AppContainer:
    save_tmdb_key(client, admin)
    provider = add_provider(client, {"access_token": admin["Authorization"].split()[1]})
    client.put(
        f"{ADMIN}/ai/roles",
        json={"chat": {"provider_id": provider["id"], "model": "c"}},
        headers=admin,
    )
    films = {"太空漫游": (1, 1999, 8.4, ["科幻"]), "爱情故事": (2, 2015, 6.1, ["爱情"])}
    for title, (tmdb_id, year, rating, genres) in films.items():
        details = tmdb.add_movie(tmdb_id, title, year)
        tmdb.titles[("movie", tmdb_id)].details = replace(details, rating=rating, genres=genres)
    movie_library(
        client, admin, cloud115, [f"{t} ({y})/{t}.{y}.mkv" for t, (_, y, _, _) in films.items()]
    )
    assert sync(client, admin)["stats"]["matched"] == 2
    return container(client)


def new_chat(client: TestClient, headers: dict[str, str]) -> int:
    response = client.post(CHATS, headers=headers)
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def send(
    client: TestClient, headers: dict[str, str], chat_id: int, content: str = "推荐一部科幻片"
) -> list[tuple[str, Any]]:
    """发送消息并解析 SSE。"""
    with client.stream(
        "POST", f"{CHATS}/{chat_id}/messages", json={"content": content}, headers=headers
    ) as response:
        assert response.status_code == 200, response.read()
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"
        body = "".join(response.iter_text())
    events = []
    for block in body.strip().split("\n\n"):
        lines = block.split("\n")
        assert lines[0].startswith("event: ") and lines[1].startswith("data: "), block
        events.append((lines[0][7:], json.loads(lines[1][6:])))
    return events


def names(events: list[tuple[str, Any]]) -> list[str]:
    return [name for name, _ in events]


def search_turn(**filters: Any) -> list[StreamEvent]:
    return [
        ThinkingDelta("用户想看科幻"),
        TextDelta("我来查一下"),
        call("search_library", filters, "s1"),
        UsageEvent(100, 10),
        Finish("tool_calls"),
    ]


def answer_turn(text: str = "推荐《太空漫游》，评分很高。") -> list[StreamEvent]:
    return [
        TextDelta(text[:6]),
        TextDelta(text[6:]),
        call("suggest_followups", {"questions": ["还有别的吗", "有没有短一点的"]}, "f1"),
        UsageEvent(200, 30),
        Finish("tool_calls"),
    ]


def test_a_full_turn_streams_thinking_tools_cards_text_and_suggestions(
    client: TestClient, library: AppContainer, admin: dict[str, str], fake_ai: FakeAi
) -> None:
    headers = fresh_admin(client)
    chat_id = new_chat(client, headers)
    fake_ai.script(search_turn(genres=["科幻"]), answer_turn())

    events = send(client, headers, chat_id)

    assert names(events) == [
        "start",
        "thinking",
        "thinking",
        "text",  # 过渡语，客户端在 tool_start 时丢弃
        "tool_start",
        "tool_end",
        "text",
        "text",
        "suggestions",
        "done",
    ]
    by_name = {n: d for n, d in events}
    assert events[1][1] == {"delta": "用户想看科幻", "seconds": None}
    assert events[2][1]["delta"] == "" and events[2][1]["seconds"] >= 0
    assert by_name["tool_start"]["label"] == "正在搜索资料库…"
    end = by_name["tool_end"]
    assert (end["name"], end["summary"], end["ok"]) == ("search_library", "找到 1 部", True)
    assert [i["title"] for i in end["items"]] == ["太空漫游"]
    assert by_name["suggestions"] == {"items": ["还有别的吗", "有没有短一点的"]}
    assert (by_name["done"]["input_tokens"], by_name["done"]["output_tokens"]) == (300, 40)
    # 追问不会作为一步工具调用出现
    assert [n for n in names(events) if n.startswith("tool_")] == ["tool_start", "tool_end"]

    detail = client.get(f"{CHATS}/{chat_id}", headers=headers).json()
    assert detail["title"] == "推荐一部科幻片"
    user_message, reply = detail["messages"]
    assert user_message["role"] == "user" and user_message["content"] == "推荐一部科幻片"
    assert reply["content"] == "推荐《太空漫游》，评分很高。"  # 过渡语不保存
    assert reply["status"] == "done" and reply["error"] is None
    assert reply["suggestions"] == ["还有别的吗", "有没有短一点的"]
    assert [(s["name"], s["label"], s["summary"]) for s in reply["steps"]] == [
        ("search_library", "正在搜索资料库…", "找到 1 部")
    ]
    assert [i["title"] for i in reply["items"]] == ["太空漫游"]
    assert reply["thinking_seconds"] is not None
    # 模型拿到了系统提示和用户的话，工具只有对话用的几个
    first = fake_ai.calls[0]
    assert first.messages[0].role == "system" and first.messages[-1].content == "推荐一部科幻片"
    assert {t.name for t in first.tools} == {
        "search_library",
        "item_details",
        "person_works",
        "my_history",
        "suggest_followups",
    }


def test_follow_up_questions_carry_the_conversation_history(
    client: TestClient, library: AppContainer, admin: dict[str, str], fake_ai: FakeAi
) -> None:
    headers = fresh_admin(client)
    chat_id = new_chat(client, headers)
    fake_ai.script(answer_turn("先看这部。先看这部。"), [TextDelta("好的"), Finish("stop")])
    send(client, headers, chat_id, "第一个问题")
    send(client, headers, chat_id, "第二个问题")

    sent = [(m.role, m.content) for m in fake_ai.calls[1].messages[1:]]
    assert sent == [
        ("user", "第一个问题"),
        ("assistant", "先看这部。先看这部。"),
        ("user", "第二个问题"),
    ]
    assert client.get(CHATS, headers=headers).json()["chats"][0]["title"] == "第一个问题"


def test_tools_only_see_the_users_visible_libraries(
    client: TestClient,
    library: AppContainer,
    admin: dict[str, str],
    admin_tokens: dict[str, str],
    fake_ai: FakeAi,
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
) -> None:
    tmdb.add_movie(9, "秘密电影", 2020)
    cloud115.make_path("影视/私人/秘密电影 (2020)/秘密电影.2020.mkv")
    create_library(client, admin, "私人", "movie", cloud115.make_path("影视/私人"))
    sync(client, admin)
    create_user(client, admin_tokens, "alice", "alice-password")
    libs = client.get(f"{ADMIN}/libraries", headers=fresh_admin(client)).json()
    public = next(lib["id"] for lib in libs if lib["name"] == "电影")
    users = client.get(f"{ADMIN}/users", headers=fresh_admin(client)).json()
    alice_id = next(u["id"] for u in users if u["username"] == "alice")
    client.put(
        f"{ADMIN}/users/{alice_id}/libraries",
        json={"all_libraries": False, "library_ids": [public]},
        headers=fresh_admin(client),
    )
    alice = bearer(login(client, "alice", "alice-password"))
    with client.app.state.container.database.read_session() as connection:  # type: ignore[attr-defined]
        secret_id = connection.execute(
            "SELECT id FROM media_item WHERE title = '秘密电影'"
        ).fetchone()[0]
    chat_id = new_chat(client, alice)
    fake_ai.script(
        [
            call("search_library", {}, "a"),
            call("item_details", {"item_id": secret_id}, "b"),
            call("person_works", {"name": "导演9"}, "c"),
            Finish("tool_calls"),
        ],
        [TextDelta("没有找到"), Finish("stop")],
    )

    events = send(client, alice, chat_id)

    ends = {d["name"]: d for n, d in events if n == "tool_end"}
    assert sorted(i["title"] for i in ends["search_library"]["items"]) == ["太空漫游", "爱情故事"]
    assert (ends["item_details"]["ok"], ends["item_details"]["summary"]) == (False, "条目不存在")
    assert ends["person_works"]["items"] == []
    # 范围外的作品没有出现在回给模型的任何工具结果里
    tool_results = " ".join(m.content for m in fake_ai.calls[1].messages if m.role == "tool")
    assert "秘密电影" not in tool_results


def test_my_history_reports_the_users_own_viewing(
    client: TestClient, library: AppContainer, admin: dict[str, str], fake_ai: FakeAi
) -> None:
    headers = fresh_admin(client)
    items = client.get("/api/libraries", headers=headers).json()
    library_id = items[0]["id"]
    cards = client.get(f"/api/libraries/{library_id}/items", headers=headers).json()["items"]
    watched_id = next(c["id"] for c in cards if c["title"] == "太空漫游")
    assert client.put(f"/api/items/{watched_id}/watched", headers=headers).status_code == 204
    chat_id = new_chat(client, headers)
    fake_ai.script(
        [call("my_history", {"kind": "watched"}, "w"), Finish("tool_calls")],
        [TextDelta("你看过太空漫游"), Finish("stop")],
    )

    events = send(client, headers, chat_id, "我看过什么")

    end = next(d for n, d in events if n == "tool_end")
    assert end["summary"] == "已看完的有 1 部"
    assert [i["title"] for i in end["items"]] == ["太空漫游"]


def test_model_failure_is_reported_in_the_stream_and_saved(
    client: TestClient, library: AppContainer, admin: dict[str, str], fake_ai: FakeAi
) -> None:
    headers = fresh_admin(client)
    chat_id = new_chat(client, headers)
    fake_ai.script(AiError("rate_limited", "太快了"))

    events = send(client, headers, chat_id)

    assert names(events) == ["start", "error"]
    assert events[1][1] == {"code": "ai_unavailable", "message": "AI 暂不可用：太快了"}
    reply = client.get(f"{CHATS}/{chat_id}", headers=headers).json()["messages"][1]
    assert reply["status"] == "error" and "太快了" in reply["error"]


def test_closing_the_stream_saves_what_was_generated_as_stopped(
    client: TestClient, library: AppContainer, admin: dict[str, str], fake_ai: FakeAi
) -> None:
    user = library.database  # 触发 container 属性检查
    assert user is not None
    headers = fresh_admin(client)
    chat_id = new_chat(client, headers)
    fake_ai.script([TextDelta("写到一半"), TextDelta("还没写完"), Finish("stop")])
    from app.auth.users import UserRepository

    with library.database.session() as connection:
        admin_user = UserRepository(connection, library.clock).get(1)
    assert admin_user is not None
    turn = library.chat.start(admin_user, chat_id, "讲个长故事")

    stream = turn.events(library)
    assert next(stream)[0] == "start"
    assert next(stream) == ("text", {"delta": "写到一半"})
    stream.close()  # 客户端断开

    reply = client.get(f"{CHATS}/{chat_id}", headers=headers).json()["messages"][1]
    assert (reply["status"], reply["content"]) == ("stopped", "写到一半")
    # 停止后还能继续这个对话，被中断的回答也在上下文里
    fake_ai.script([TextDelta("好"), Finish("stop")])
    send(client, headers, chat_id, "继续")
    assert ("assistant", "写到一半") in [(m.role, m.content) for m in fake_ai.calls[-1].messages]


def test_step_limit_ends_the_answer_with_a_readable_note(
    client: TestClient, library: AppContainer, admin: dict[str, str], fake_ai: FakeAi
) -> None:
    headers = fresh_admin(client)
    chat_id = new_chat(client, headers)
    loop_turn: list[StreamEvent] = [call("search_library", {}, "x"), Finish("tool_calls")]
    fake_ai.script(*[loop_turn] * 10)

    events = send(client, headers, chat_id)

    texts = "".join(d["delta"] for n, d in events if n == "text")
    assert "次数达到上限" in texts
    assert names(events)[-1] == "done"


def test_chats_belong_to_their_owner(
    client: TestClient,
    library: AppContainer,
    admin: dict[str, str],
    admin_tokens: dict[str, str],
    fake_ai: FakeAi,
) -> None:
    create_user(client, admin_tokens, "alice", "alice-password")
    alice = bearer(login(client, "alice", "alice-password"))
    chat_id = new_chat(client, fresh_admin(client))
    assert client.get(CHATS, headers=alice).json() == {"chats": []}
    assert client.get(f"{CHATS}/{chat_id}", headers=alice).status_code == 404
    assert client.delete(f"{CHATS}/{chat_id}", headers=alice).status_code == 404
    response = client.post(f"{CHATS}/{chat_id}/messages", json={"content": "hi"}, headers=alice)
    assert response.status_code == 404
    assert client.get(CHATS).status_code == 401


def test_deleting_a_chat_removes_its_messages(
    client: TestClient, library: AppContainer, admin: dict[str, str], fake_ai: FakeAi
) -> None:
    headers = fresh_admin(client)
    chat_id = new_chat(client, headers)
    fake_ai.script(answer_turn())
    send(client, headers, chat_id)
    assert client.delete(f"{CHATS}/{chat_id}", headers=headers).status_code == 204
    assert client.get(f"{CHATS}/{chat_id}", headers=headers).status_code == 404
    with library.database.read_session() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ai_chat_message").fetchone()[0] == 0


def test_message_validation_and_availability(
    client: TestClient, library: AppContainer, admin: dict[str, str], fake_ai: FakeAi
) -> None:
    headers = fresh_admin(client)
    chat_id = new_chat(client, headers)
    for bad in ("", "x" * 2001):
        response = client.post(
            f"{CHATS}/{chat_id}/messages", json={"content": bad}, headers=headers
        )
        assert response.status_code == 422
    blank = client.post(f"{CHATS}/{chat_id}/messages", json={"content": "   "}, headers=headers)
    assert blank.status_code == 400
    assert blank.json()["error"]["code"] == "invalid_chat_message"

    client.put(f"{ADMIN}/ai/roles", json={"chat": None}, headers=headers)
    not_configured = client.post(CHATS, headers=fresh_admin(client))
    assert not_configured.status_code == 409
    assert not_configured.json()["error"]["code"] == "ai_not_configured"


def test_old_chats_are_cleaned_up_after_thirty_days(
    client: TestClient, library: AppContainer, admin: dict[str, str], clock: FakeClock
) -> None:
    with library.database.read_session() as connection:
        enabled = connection.execute(
            "SELECT enabled FROM scheduled_task WHERE key = 'cleanup_ai_chats'"
        ).fetchone()[0]
    assert enabled == 1  # 对话保留 30 天是承诺，默认就要清理
    headers = fresh_admin(client)
    old = new_chat(client, headers)
    clock.advance(days=20)
    recent = new_chat(client, fresh_admin(client))
    clock.advance(days=15)

    response = client.post(
        f"{ADMIN}/scheduled-tasks/cleanup_ai_chats/run", headers=fresh_admin(client)
    )
    assert response.status_code in (200, 201, 202), response.text

    remaining = [c["id"] for c in client.get(CHATS, headers=fresh_admin(client)).json()["chats"]]
    assert remaining == [recent] and old not in remaining
