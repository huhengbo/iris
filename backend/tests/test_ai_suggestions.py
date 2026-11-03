"""匹配建议和 AI 自动匹配（M53、M54，ADR 0030）。"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.ai.match_tools import SeenCandidate
from app.ai.suggestions import (
    NO_CONCLUSION,
    RETRY_AFTER,
    SuggestionService,
    WorkFacts,
)
from app.ai.types import AiError, Finish, TextDelta
from app.config import AppConfig
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


@pytest.fixture(autouse=True)
def tmdb_key(client: TestClient, admin: dict[str, str]) -> None:
    save_tmdb_key(client, admin)


@pytest.fixture
def app(client: TestClient, admin: dict[str, str]) -> AppContainer:
    provider = add_provider(client, {"access_token": admin["Authorization"].split()[1]})
    response = client.put(
        f"{ADMIN}/ai/roles",
        json={"task": {"provider_id": provider["id"], "model": "t"}},
        headers=admin,
    )
    assert response.status_code == 200, response.text
    return container(client)


def get(client: TestClient, path: str, **params: Any) -> Any:
    response = client.get(f"{ADMIN}{path}", params=params, headers=fresh_admin(client))
    assert response.status_code == 200, response.text
    return response.json()


def post(client: TestClient, path: str, body: Any = None, status: int = 200) -> Any:
    response = client.post(f"{ADMIN}{path}", json=body, headers=fresh_admin(client))
    assert response.status_code == status, response.text
    return response.json() if response.content else None


def put_settings(client: TestClient, **body: Any) -> None:
    response = client.put(f"{ADMIN}/ai/settings", json=body, headers=fresh_admin(client))
    assert response.status_code == 200, response.text


def ambiguous_movie(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115, tmdb: FakeTmdb
) -> int:
    """规则搜不到的电影：中文片名搜不出结果，用原名「Strange Title」能搜到两个候选。"""
    movie_library(client, admin, cloud115, ["奇怪的片名 (2021)/奇怪的片名.2021.mkv"])
    tmdb.add_movie(1, "奇怪的片名", 2021, original_title="Strange Title")
    tmdb.add_movie(2, "奇怪的片名", 2021, original_title="Strange Title")
    tmdb.search_results[("movie", "奇怪的片名")] = []
    assert sync(client, admin)["stats"]["unmatched"] == 1
    return int(get(client, "/works")["items"][0]["id"])


def pick(remote_id: str | None, confidence: float, reason: str = "年份和片名都对得上") -> list[Any]:
    """一轮对话：核对详情后提交。"""
    return [
        call("metadata_details", {"remote_id": remote_id or "1"}, "d1"),
        call(
            "submit_match",
            {"remote_id": remote_id, "confidence": confidence, "reason": reason},
            "s1",
        ),
        Finish("tool_calls"),
    ]


def work(client: TestClient, work_id: int, state: str = "unmatched") -> dict[str, Any]:
    items = get(client, "/works", state=state)["items"]
    return next(w for w in items if w["id"] == work_id)


def test_new_unmatched_work_gets_a_suggestion_with_its_steps(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
    fake_ai: FakeAi,
    app: AppContainer,
) -> None:
    work_id = ambiguous_movie(client, admin, cloud115, tmdb)
    assert work(client, work_id)["suggestion"] is None
    fake_ai.script(
        [
            call("metadata_search", {"query": "Strange Title"}, "a"),
            Finish("tool_calls"),
        ],
        pick("2", 0.8),
    )

    assert app.suggestions.run_pending(app) == 1

    suggestion = work(client, work_id)["suggestion"]
    assert (suggestion["remote_id"], suggestion["title"], suggestion["year"]) == (
        "2",
        "奇怪的片名",
        2021,
    )
    assert suggestion["confidence"] == 0.8 and suggestion["status"] == "pending"
    assert suggestion["reason"] == "年份和片名都对得上"
    assert suggestion["url"] == "https://www.themoviedb.org/movie/2"
    assert suggestion["model"] == "t"
    assert [(s["tool"], s["label"], s["summary"], s["ok"]) for s in suggestion["steps"]] == [
        ("metadata_search", "正在搜索「Strange Title」…", "搜索「Strange Title」：2 个结果", True),
        ("metadata_details", "正在查看条目详情…", "查看「奇怪的片名」的详情", True),
    ]
    # 模型拿到的是目录、文件名和规则候选
    prompt = fake_ai.calls[0].messages[1].content
    assert "奇怪的片名.2021.mkv" in prompt and "搜不到" in prompt
    assert {t.name for t in fake_ai.calls[0].tools} == {
        "metadata_search",
        "metadata_details",
        "metadata_season",
        "submit_match",
    }
    # 已有建议的作品不会重复分析
    assert app.suggestions.run_pending(app) == 0
    assert len(fake_ai.calls) == 2


def test_analysis_is_off_without_a_task_model_or_when_switched_off(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
    fake_ai: FakeAi,
    app: AppContainer,
) -> None:
    ambiguous_movie(client, admin, cloud115, tmdb)
    put_settings(client, suggest_matches=False)
    assert app.suggestions.run_pending(app) == 0
    put_settings(client, suggest_matches=True)
    client.put(f"{ADMIN}/ai/roles", json={"task": None}, headers=fresh_admin(client))
    assert app.suggestions.run_pending(app) == 0
    assert fake_ai.calls == []


def test_model_cannot_submit_a_candidate_it_never_saw(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
    fake_ai: FakeAi,
    app: AppContainer,
) -> None:
    work_id = ambiguous_movie(client, admin, cloud115, tmdb)
    submit = call("submit_match", {"remote_id": "999", "confidence": 0.99, "reason": "猜的"}, "s")
    fake_ai.script(
        [submit, Finish("tool_calls")],
        [
            call("submit_match", {"remote_id": None, "confidence": 0, "reason": "查不到"}, "s2"),
            Finish("tool_calls"),
        ],
    )
    app.suggestions.run_pending(app)

    suggestion = work(client, work_id)["suggestion"]
    assert suggestion["remote_id"] is None and suggestion["confidence"] == 0
    assert suggestion["reason"] == "查不到"
    # 第一次提交被拒绝的原因回给了模型
    assert "没有在搜索或详情结果中出现过" in fake_ai.calls[1].messages[-1].content
    # 没有候选的建议不能采用
    response = client.post(f"{ADMIN}/works/{work_id}/suggestion/adopt", headers=fresh_admin(client))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "suggestion_empty"


def test_a_model_that_never_submits_leaves_an_empty_suggestion(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
    fake_ai: FakeAi,
    app: AppContainer,
) -> None:
    work_id = ambiguous_movie(client, admin, cloud115, tmdb)
    fake_ai.script([TextDelta("我不确定"), Finish("stop")])
    app.suggestions.run_pending(app)
    suggestion = work(client, work_id)["suggestion"]
    assert suggestion["remote_id"] is None
    assert suggestion["reason"] == NO_CONCLUSION and suggestion["confidence"] == 0


def test_tool_calls_are_capped_at_six(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
    fake_ai: FakeAi,
    app: AppContainer,
) -> None:
    work_id = ambiguous_movie(client, admin, cloud115, tmdb)
    searches = [call("metadata_search", {"query": f"q{i}"}, f"c{i}") for i in range(8)]
    fake_ai.script([*searches, Finish("tool_calls")])
    app.suggestions.run_pending(app)
    suggestion = work(client, work_id)["suggestion"]
    assert len(suggestion["steps"]) == 6
    assert suggestion["reason"] == NO_CONCLUSION


def test_metadata_outage_saves_nothing_and_retries_later(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
    fake_ai: FakeAi,
    app: AppContainer,
) -> None:
    from tests.fake_tmdb import TmdbUnavailable

    work_id = ambiguous_movie(client, admin, cloud115, tmdb)
    tmdb.error = TmdbUnavailable()
    fake_ai.script(
        [call("metadata_search", {"query": "x"}, "a"), Finish("tool_calls")],
        [Finish("stop")],
    )
    assert app.suggestions.run_pending(app) == 0
    assert work(client, work_id)["suggestion"] is None

    tmdb.error = None
    fake_ai.turns.clear()
    fake_ai.script(pick("1", 0.7))
    assert app.suggestions.run_pending(app) == 1
    assert work(client, work_id)["suggestion"]["remote_id"] == "1"


def test_model_failures_back_off_then_alert_and_recover(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
    fake_ai: FakeAi,
    app: AppContainer,
    clock: FakeClock,
) -> None:
    work_id = ambiguous_movie(client, admin, cloud115, tmdb)
    service = app.suggestions
    fake_ai.script(AiError("timeout", "超时"))
    assert service.run_pending(app) == 0
    calls = len(fake_ai.calls)
    # 退避期内不再调用模型
    assert service.run_pending(app) == 0 and len(fake_ai.calls) == calls

    for _ in range(2):
        clock.advance(seconds=RETRY_AFTER.total_seconds() + 1)
        fake_ai.script(AiError("server", "崩了"))
        service.run_pending(app)
    alerts = client.get(f"{ADMIN}/alerts", headers=fresh_admin(client)).json()
    assert [a["kind"] for a in alerts] == ["ai"]
    assert "连续失败" in alerts[0]["message"]

    clock.advance(seconds=RETRY_AFTER.total_seconds() + 1)
    fake_ai.script(pick("1", 0.5))
    assert service.run_pending(app) == 1
    assert work(client, work_id)["suggestion"] is not None
    alerts = client.get(f"{ADMIN}/alerts", headers=fresh_admin(client)).json()
    assert alerts[0]["resolved_at"] is not None


def test_invalid_key_alerts_immediately(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
    fake_ai: FakeAi,
    app: AppContainer,
) -> None:
    ambiguous_movie(client, admin, cloud115, tmdb)
    fake_ai.script(AiError("auth", "Key 无效"))
    app.suggestions.run_pending(app)
    alerts = client.get(f"{ADMIN}/alerts", headers=fresh_admin(client)).json()
    assert len(alerts) == 1 and "Key 无效" in alerts[0]["message"]


def test_adopting_is_a_manual_assignment(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
    fake_ai: FakeAi,
    app: AppContainer,
) -> None:
    work_id = ambiguous_movie(client, admin, cloud115, tmdb)
    fake_ai.script(pick("2", 0.8))
    app.suggestions.run_pending(app)

    detail = post(client, f"/works/{work_id}/suggestion/adopt")

    assert detail["remote_id"] == "2" and detail["works"][0]["manual"] is True
    assert work(client, work_id, "matched")["suggestion"]["status"] == "adopted"
    again = client.post(f"{ADMIN}/works/{work_id}/suggestion/adopt", headers=fresh_admin(client))
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "suggestion_not_pending"
    # 同步不会改动手动指定
    assert sync(client, admin)["stats"]["unmatched"] == 0


def test_batch_adopt_uses_each_works_own_suggestion(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
    fake_ai: FakeAi,
    app: AppContainer,
) -> None:
    movie_library(
        client,
        admin,
        cloud115,
        ["奇怪的片名 (2021)/奇怪的片名.2021.mkv", "另一部 (2020)/另一部.2020.mkv"],
    )
    tmdb.add_movie(1, "奇怪的片名", 2021, original_title="Strange Title")
    tmdb.add_movie(2, "另一部", 2020, original_title="Another One")
    tmdb.search_results[("movie", "奇怪的片名")] = []
    tmdb.search_results[("movie", "另一部")] = []
    assert sync(client, admin)["stats"]["unmatched"] == 2
    works = get(client, "/works")["items"]
    by_title = {w["title"]: w["id"] for w in works}
    # 后台任务按作品编号顺序分析
    turns = [pick("1", 0.9), pick(None, 0.0, "不确定")]
    fake_ai.script(*(turns if by_title["奇怪的片名"] < by_title["另一部"] else turns[::-1]))
    assert app.suggestions.run_pending(app) == 2

    report = post(
        client,
        "/works/batch-adopt-suggestions",
        {"work_ids": [by_title["奇怪的片名"], by_title["另一部"], 999]},
    )

    assert (report["succeeded"], report["failed"]) == (1, 2)
    errors = {r["id"]: r["error"]["code"] for r in report["results"] if r["error"]}
    assert errors == {by_title["另一部"]: "suggestion_empty", 999: "not_found"}
    matched = get(client, "/works", state="matched")["items"]
    assert [(w["id"], w["manual"], w["item"]["remote_id"]) for w in matched] == [
        (by_title["奇怪的片名"], True, "1")
    ]


def test_ignored_suggestion_is_not_reanalyzed_until_asked(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
    fake_ai: FakeAi,
    app: AppContainer,
) -> None:
    work_id = ambiguous_movie(client, admin, cloud115, tmdb)
    fake_ai.script(pick("2", 0.4))
    app.suggestions.run_pending(app)
    post(client, f"/works/{work_id}/suggestion/ignore", status=204)
    assert work(client, work_id)["suggestion"]["status"] == "ignored"
    assert app.suggestions.run_pending(app) == 0

    post(client, f"/works/{work_id}/suggestion/reanalyze", status=204)
    assert work(client, work_id)["suggestion"] is None
    fake_ai.script(pick("1", 0.6))
    assert app.suggestions.run_pending(app) == 1
    assert work(client, work_id)["suggestion"]["remote_id"] == "1"


def test_auto_match_is_off_by_default(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
    fake_ai: FakeAi,
    app: AppContainer,
) -> None:
    work_id = ambiguous_movie(client, admin, cloud115, tmdb)
    fake_ai.script(pick("2", 0.99))
    app.suggestions.run_pending(app)
    assert work(client, work_id)["suggestion"]["status"] == "pending"
    assert get(client, "/ai/settings")["auto_match"] is False


def test_auto_match_writes_a_normal_match_and_can_be_reverted(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
    fake_ai: FakeAi,
    app: AppContainer,
    config: AppConfig,
) -> None:
    work_id = ambiguous_movie(client, admin, cloud115, tmdb)
    put_settings(client, auto_match=True)
    fake_ai.script(pick("2", 0.95))
    app.suggestions.run_pending(app)

    matched = work(client, work_id, "matched")
    assert matched["manual"] is False
    assert matched["item"]["remote_id"] == "2"
    assert matched["suggestion"]["status"] == "auto_matched"
    item_id = matched["item"]["id"]
    detail = get(client, f"/items/{item_id}")
    assert [(w["id"], w["manual"], w["ai_matched"]) for w in detail["works"]] == [
        (work_id, False, True)
    ]

    post(client, f"/works/{work_id}/suggestion/revert", status=204)

    assert work(client, work_id)["suggestion"]["status"] == "reverted"
    with sqlite3.connect(config.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM media_item").fetchone()[0] == 0
    # 回到待处理后同步和后台任务都不会再自动匹配
    sync(client, admin)
    assert app.suggestions.run_pending(app) == 0
    assert work(client, work_id)["suggestion"]["status"] == "reverted"

    # 管理员要求重新分析：同一个候选被禁止再提交
    post(client, f"/works/{work_id}/suggestion/reanalyze", status=204)
    fake_ai.script(pick("2", 0.99), pick("1", 0.99))
    app.suggestions.run_pending(app)
    suggestion = work(client, work_id, "matched")["suggestion"]
    assert suggestion["remote_id"] == "1" and suggestion["status"] == "auto_matched"


def test_banned_candidate_is_rejected_by_the_tool(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
    fake_ai: FakeAi,
    app: AppContainer,
) -> None:
    work_id = ambiguous_movie(client, admin, cloud115, tmdb)
    put_settings(client, auto_match=True)
    fake_ai.script(pick("2", 0.95))
    app.suggestions.run_pending(app)
    post(client, f"/works/{work_id}/suggestion/revert", status=204)
    post(client, f"/works/{work_id}/suggestion/reanalyze", status=204)

    fake_ai.script(pick("2", 0.99), [Finish("stop")])
    app.suggestions.run_pending(app)

    assert "被管理员撤销过" in fake_ai.calls[-1].messages[-1].content
    suggestion = work(client, work_id)["suggestion"]
    assert suggestion["remote_id"] is None and suggestion["status"] == "pending"


def test_revert_only_applies_to_auto_matched_works(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
    fake_ai: FakeAi,
    app: AppContainer,
) -> None:
    work_id = ambiguous_movie(client, admin, cloud115, tmdb)
    fake_ai.script(pick("2", 0.99))
    app.suggestions.run_pending(app)
    response = client.post(
        f"{ADMIN}/works/{work_id}/suggestion/revert", headers=fresh_admin(client)
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "work_not_auto_matched"
    assert (
        client.post(f"{ADMIN}/works/999/suggestion/revert", headers=fresh_admin(client)).status_code
        == 404
    )


def facts(**changes: Any) -> WorkFacts:
    base: dict[str, Any] = {
        "id": 1,
        "library_id": 1,
        "kind": "movie",
        "library_name": "电影",
        "path": "x",
        "title": "奇怪的片名",
        "alt_title": None,
        "year": 2021,
        "reason": None,
        "rule_candidates": (),
        "files": (),
        "file_count": 0,
        "folder_id": 1,
        "root_name": "电影",
    }
    return WorkFacts(**{**base, **changes})


def candidate(**changes: Any) -> SeenCandidate:
    base: dict[str, Any] = {
        "provider_id": "builtin.tmdb",
        "remote_id": "1",
        "title": "奇怪的片名",
        "original_title": None,
        "year": 2021,
    }
    return SeenCandidate(**{**base, **changes})


@pytest.mark.parametrize(
    ("work_changes", "candidate_changes", "confidence", "banned", "expected"),
    [
        ({}, {}, 0.9, frozenset(), None),
        ({}, {}, 0.89, frozenset(), "低于阈值"),
        ({}, {}, 0.99, frozenset({"1"}), "撤销过"),
        ({"year": None}, {}, 0.99, frozenset(), "缺少年份"),
        ({}, {"year": None}, 0.99, frozenset(), "缺少年份"),
        ({}, {"year": 2023}, 0.99, frozenset(), "年份相差超过 1"),
        ({}, {"year": 2022}, 0.99, frozenset(), None),
        ({}, {"title": "别的片"}, 0.99, frozenset(), "片名与候选"),
        ({}, {"title": "别的片", "original_title": "奇怪的 片名!"}, 0.99, frozenset(), None),
        ({}, {"title": "别的片", "en_title": "奇怪的片名"}, 0.99, frozenset(), None),
        ({"title": "别的名字", "alt_title": "奇怪的片名"}, {}, 0.99, frozenset(), None),
    ],
)
def test_auto_match_hard_checks(
    app: AppContainer,
    work_changes: dict[str, Any],
    candidate_changes: dict[str, Any],
    confidence: float,
    banned: frozenset[str],
    expected: str | None,
) -> None:
    service: SuggestionService = app.suggestions
    problem = service._check(
        facts(**work_changes), confidence, candidate(**candidate_changes), banned, 0.9
    )
    if expected is None:
        assert problem is None
    else:
        assert problem is not None and expected in problem


def test_series_episodes_must_exist_in_the_candidate(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
    fake_ai: FakeAi,
    app: AppContainer,
) -> None:
    tmdb.add_series(77, "某个剧", 2020, {1: 10}, original_title="Odd Show")
    tmdb.add_series(78, "某个剧", 2020, {1: 10}, original_title="Odd Show")
    tmdb.search_results[("series", "某个剧")] = []
    for path in (
        "某个剧 (2020)/Season 1/某个剧.S01E03.mkv",
        "某个剧 (2020)/Season 1/某个剧.S01E15.mkv",
    ):
        cloud115.make_path(f"影视/剧集/{path}")
    create_library(client, admin, "剧集", "series", cloud115.make_path("影视/剧集"))
    assert sync(client, admin)["stats"]["unmatched"] == 1
    put_settings(client, auto_match=True)
    work_id = get(client, "/works")["items"][0]["id"]
    fake_ai.script(pick("77", 0.99))

    app.suggestions.run_pending(app)

    suggestion = work(client, work_id)["suggestion"]
    assert suggestion["remote_id"] == "77" and suggestion["status"] == "pending"
    seen = SeenCandidate("builtin.tmdb", "77", "某个剧", None, 2020)
    problem = app.suggestions._check_episodes(
        facts(id=work_id, kind="series", library_id=1, root_name="剧集"), seen
    )
    assert problem == "第 1 季没有第 15 集"


def test_settings_validation_and_defaults(client: TestClient, admin: dict[str, str]) -> None:
    assert get(client, "/ai/settings") == {
        "reversible_write": "confirm",
        "suggest_matches": True,
        "auto_match": False,
        "auto_match_threshold": 0.9,
    }
    for bad in (0.3, 1.5):
        response = client.put(
            f"{ADMIN}/ai/settings", json={"auto_match_threshold": bad}, headers=fresh_admin(client)
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_ai_setting"
    put_settings(client, auto_match_threshold=0.95, auto_match=True)
    assert get(client, "/ai/settings")["auto_match_threshold"] == 0.95
    put_settings(client, suggest_matches=False)
    saved = get(client, "/ai/settings")
    assert saved["auto_match"] is True and saved["suggest_matches"] is False


def test_suggestion_endpoints_are_admin_only(
    client: TestClient, admin_tokens: dict[str, str]
) -> None:
    create_user(client, admin_tokens)
    user = bearer(login(client, "alice", "alice-password"))
    for action in ("adopt", "ignore", "reanalyze", "revert"):
        response = client.post(f"{ADMIN}/works/1/suggestion/{action}", headers=user)
        assert response.status_code == 403, action
