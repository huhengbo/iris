"""AI 找片：问句转筛选条件、可见范围、语义召回和降级（M56，ADR 0030）。"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.ai.types import AiError, Finish, StreamEvent
from app.container import AppContainer
from tests.conftest import bearer, create_user, login, save_tmdb_key
from tests.fake_ai import FakeAi
from tests.fake_cloud115 import FakeCloud115
from tests.fake_tmdb import FakeTmdb
from tests.test_ai_providers import add_provider, container
from tests.test_ai_semantic import build
from tests.test_ai_tools import call
from tests.test_catalog import movie_library
from tests.test_sync import create_library, fresh_admin, sync

ADMIN = "/api/admin"
URL = "/api/ai/ask-search"


@pytest.fixture
def library(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
    fake_ai: FakeAi,
) -> AppContainer:
    """三部电影，评分、年份、类型各不同；对话模型已配置，向量模型没有。"""
    save_tmdb_key(client, admin)
    provider = add_provider(client, {"access_token": admin["Authorization"].split()[1]})
    client.put(
        f"{ADMIN}/ai/roles",
        json={"chat": {"provider_id": provider["id"], "model": "c"}},
        headers=admin,
    )
    films = {
        "太空漫游": (1, 1999, 8.4, ["科幻"]),
        "爱情故事": (2, 2015, 6.1, ["爱情", "剧情"]),
        "战争之王": (3, 2021, 7.9, ["战争", "剧情"]),
    }
    for title, (tmdb_id, year, rating, genres) in films.items():
        details = tmdb.add_movie(tmdb_id, title, year)
        tmdb.titles[("movie", tmdb_id)].details = _with(details, rating, genres)
    movie_library(
        client, admin, cloud115, [f"{t} ({y})/{t}.{y}.mkv" for t, (_, y, _, _) in films.items()]
    )
    assert sync(client, admin)["stats"]["matched"] == 3
    return container(client)


def _with(details: Any, rating: float, genres: list[str]) -> Any:
    from dataclasses import replace

    return replace(details, rating=rating, genres=genres)


def filters_turn(**filters: Any) -> list[StreamEvent]:
    return [call("set_filters", filters, "f"), Finish("tool_calls")]


def ask(client: TestClient, headers: dict[str, str], query: str = "找片") -> Any:
    return client.post(URL, json={"query": query}, headers=headers)


def titles(response: Any) -> list[str]:
    return [i["title"] for i in response.json()["items"]]


def test_a_description_becomes_filters_and_results(
    client: TestClient, library: AppContainer, admin: dict[str, str], fake_ai: FakeAi
) -> None:
    fake_ai.script(filters_turn(genres=["剧情"], min_rating=7), [Finish("stop")])

    response = ask(client, admin, "评分高的剧情片")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["understood"]["genres"] == ["剧情"] and body["understood"]["min_rating"] == 7
    assert titles(response) == ["战争之王"]
    assert body["total"] == 1 and body["semantic_used"] is False
    # 模型拿到的提示里有可选类型、资料库和当前年份
    prompt = fake_ai.calls[0].messages[1].content
    assert "可选类型：战争、爱情、科幻、剧情" in prompt or "科幻" in prompt
    assert "当前年份：2026" in prompt and "电影" in prompt
    assert [t.name for t in fake_ai.calls[0].tools] == ["set_filters"]


def test_each_filter_narrows_the_results(
    client: TestClient, library: AppContainer, admin: dict[str, str]
) -> None:
    def query(**filters: Any) -> list[str]:
        response = client.post(
            f"{URL}/query", json={"filters": filters}, headers=fresh_admin(client)
        )
        assert response.status_code == 200, response.text
        return titles(response)

    assert query() == ["太空漫游", "战争之王", "爱情故事"]  # 评分高的在前
    assert query(kind="series") == []
    assert query(genres=["爱情", "科幻"]) == ["太空漫游", "爱情故事"]
    assert query(year_from=2000, year_to=2020) == ["爱情故事"]
    assert query(min_rating=8) == ["太空漫游"]
    assert query(max_runtime=1) == []
    assert query(watched=True) == []
    assert query(watched=False) == ["太空漫游", "战争之王", "爱情故事"]
    assert query(library_id=999) == []
    paged = client.post(
        f"{URL}/query", json={"filters": {}, "offset": 1, "limit": 1}, headers=fresh_admin(client)
    ).json()
    assert [i["title"] for i in paged["items"]] == ["战争之王"] and paged["total"] == 3


def test_unknown_genre_messages_read_naturally() -> None:
    from app.ai.ask import unknown_genres_message

    assert "可选：科幻、喜剧" in unknown_genres_message(["恐怖"], ["科幻", "喜剧"])
    empty = unknown_genres_message(["恐怖"], [])
    assert "库里还没有可用的类型" in empty and "可选：" not in empty


def test_model_mistakes_are_sent_back_and_corrected(
    client: TestClient, library: AppContainer, admin: dict[str, str], fake_ai: FakeAi
) -> None:
    fake_ai.script(
        filters_turn(genres=["恐怖"]),  # 库里没有这个类型
        filters_turn(genres=["科幻"], semantic_query="恐怖氛围"),
    )

    response = ask(client, admin)

    assert response.status_code == 200
    assert response.json()["understood"]["genres"] == ["科幻"]
    assert "不在库里；可选：" in fake_ai.calls[1].messages[-1].content
    assert titles(response) == ["太空漫游"]


def test_filters_cannot_reach_libraries_the_user_cannot_see(
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
    # alice 只能看到第一个资料库
    libs = client.get(f"{ADMIN}/libraries", headers=fresh_admin(client)).json()
    public = next(lib["id"] for lib in libs if lib["name"] == "电影")
    private = next(lib["id"] for lib in libs if lib["name"] == "私人")
    users = client.get(f"{ADMIN}/users", headers=fresh_admin(client)).json()
    alice_id = next(u["id"] for u in users if u["username"] == "alice")
    client.put(
        f"{ADMIN}/users/{alice_id}/libraries",
        json={"all_libraries": False, "library_ids": [public]},
        headers=fresh_admin(client),
    )
    alice = bearer(login(client, "alice", "alice-password"))

    # 管理员看得到，alice 看不到
    assert "秘密电影" in [
        i["title"]
        for i in client.post(
            f"{URL}/query", json={"filters": {}}, headers=fresh_admin(client)
        ).json()["items"]
    ]
    unfiltered = client.post(f"{URL}/query", json={"filters": {}}, headers=alice).json()
    assert "秘密电影" not in [i["title"] for i in unfiltered["items"]]
    forced = client.post(
        f"{URL}/query", json={"filters": {"library_id": private}}, headers=alice
    ).json()
    assert forced["items"] == [] and forced["total"] == 0

    # 模型编造范围外的资料库编号，会被工具拒绝
    fake_ai.script(filters_turn(library_id=private), filters_turn())
    response = ask(client, alice)
    assert response.status_code == 200
    assert "资料库编号不对" in fake_ai.calls[1].messages[-1].content
    assert "秘密电影" not in titles(response)
    # 提示里没有范围外的资料库
    assert "私人" not in fake_ai.calls[0].messages[1].content


def test_semantic_results_are_ranked_by_meaning_and_filtered(
    client: TestClient, library: AppContainer, admin: dict[str, str], fake_ai: FakeAi
) -> None:
    provider_id = library.ai.roles()["chat"].provider_id  # type: ignore[union-attr]
    client.put(
        f"{ADMIN}/ai/roles",
        json={"embedding": {"provider_id": provider_id, "model": "bge"}},
        headers=fresh_admin(client),
    )
    build(library.semantic)

    fake_ai.script(filters_turn(semantic_query="战争 战争 战争"))
    body = ask(client, fresh_admin(client)).json()
    assert body["semantic_used"] is True
    assert [i["title"] for i in body["items"]][0] == "战争之王"
    assert body["total"] == 3

    # 筛选条件和语义排序一起生效
    fake_ai.script(filters_turn(min_rating=8, semantic_query="战争"))
    body = ask(client, fresh_admin(client)).json()
    assert [i["title"] for i in body["items"]] == ["太空漫游"]

    # 语义召回失败（向量模型出错）时退回只用筛选条件
    fake_ai.embed_error = AiError("timeout", "超时")
    fake_ai.script(filters_turn(semantic_query="战争"))
    degraded = ask(client, fresh_admin(client)).json()
    assert degraded["semantic_used"] is False and len(degraded["items"]) == 3


def test_ai_failures_are_reported_so_the_client_can_fall_back(
    client: TestClient, library: AppContainer, admin: dict[str, str], fake_ai: FakeAi
) -> None:
    fake_ai.script(AiError("rate_limited", "太快了"))
    response = ask(client, admin)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "ai_unavailable"
    assert "太快了" in response.json()["error"]["message"]

    fake_ai.script([Finish("stop")])  # 模型没有给出筛选条件
    assert ask(client, fresh_admin(client)).status_code == 502


def test_unconfigured_ai_is_a_conflict(client: TestClient, admin: dict[str, str]) -> None:
    for path, body in ((URL, {"query": "x"}), (f"{URL}/query", {"filters": {}})):
        response = client.post(path, json=body, headers=admin)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "ai_not_configured"


def test_login_is_required(client: TestClient) -> None:
    assert client.post(URL, json={"query": "x"}).status_code == 401
    assert client.post(f"{URL}/query", json={"filters": {}}).status_code == 401


def test_filter_validation(
    client: TestClient, library: AppContainer, admin: dict[str, str]
) -> None:
    for bad in ({"min_rating": 11}, {"year_from": 1000}, {"kind": "anime"}, {"genres": ["a"] * 9}):
        response = client.post(f"{URL}/query", json={"filters": bad}, headers=fresh_admin(client))
        assert response.status_code == 422, bad
    assert client.post(URL, json={"query": ""}, headers=fresh_admin(client)).status_code == 422
