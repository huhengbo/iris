"""第四阶段验收：识别 → 匹配 → 刮削 → 图片，以及管理员整理资料库的接口。"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.admin import pending_batch
from app.config import AppConfig
from app.library.matcher import Candidate, _scored
from app.ops.tasks import TaskRepository
from tests.conftest import (
    FakeClock,
    SyncRunner,
    bearer,
    create_user,
    login,
    make_app,
    save_tmdb_key,
)
from tests.fake_cloud115 import FakeCloud115
from tests.fake_tmdb import FakeTmdb, TmdbSearchResult
from tests.test_sync import create_library, fresh_admin, sync

ADMIN = "/api/admin"
MB = 1024 * 1024


@pytest.fixture
def tmdb_key(client: TestClient, admin: dict[str, str]) -> None:
    save_tmdb_key(client, admin)


@pytest.fixture
def films(tmdb: FakeTmdb) -> None:
    tmdb.add_movie(535167, "流浪地球", 2019, original_title="The Wandering Earth")
    tmdb.add_movie(157336, "星际穿越", 2014, original_title="Interstellar", imdb_id="tt0816692")


def get(client: TestClient, path: str, **params: Any) -> Any:
    response = client.get(f"{ADMIN}{path}", params=params, headers=fresh_admin(client))
    assert response.status_code == 200, response.text
    return response.json()


def post(client: TestClient, path: str, body: Any = None, status: int = 200) -> Any:
    response = client.post(f"{ADMIN}{path}", json=body, headers=fresh_admin(client))
    assert response.status_code == status, response.text
    return response.json() if response.content else None


def test_background_task_is_failed_on_restart(
    client: TestClient,
    config: AppConfig,
    clock: FakeClock,
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
    sync_runner: SyncRunner,
) -> None:
    with client.app.state.container.database.session() as connection:  # type: ignore[attr-defined]
        task = TaskRepository(connection, clock).create(
            "delete_media", result={"input": {"item_id": 1}}
        )

    with TestClient(make_app(config, clock, sync_runner)) as restarted:
        response = restarted.get(f"/api/admin/tasks/{task.id}", headers=fresh_admin(restarted))
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "failed"
        assert body["error"] == "服务重启，任务中断"
        assert body["result"]["input"] == {"item_id": 1}


def items(client: TestClient) -> dict[str, dict[str, Any]]:
    return {i["title"]: i for i in get(client, "/items")["items"]}


def item(client: TestClient, title: str) -> dict[str, Any]:
    detail: dict[str, Any] = get(client, f"/items/{items(client)[title]['id']}")
    return detail


def media_file_id(config: AppConfig, rel_path: str) -> int:
    with sqlite3.connect(config.database_path) as connection:
        row = connection.execute(
            "SELECT id FROM media_file WHERE rel_path = ?", (rel_path,)
        ).fetchone()
    assert row is not None, rel_path
    return int(row[0])


def movie_library(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    paths: list[str],
) -> dict[str, Any]:
    for path in paths:
        cloud115.make_path(f"影视/电影/{path}")
    return create_library(client, admin, "电影", "movie", cloud115.make_path("影视/电影"))


# ---- 同步流水线 ----


@pytest.mark.usefixtures("tmdb_key", "films")
def test_versions_of_one_movie_are_merged_and_images_cached(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
) -> None:
    movie_library(
        client,
        admin,
        cloud115,
        [
            "流浪地球 (2019)/流浪地球.2019.2160p.WEB-DL.mkv",
            "流浪地球 (2019)/流浪地球.2019.1080p.BluRay.mkv",
            "流浪地球 (2019)/流浪地球.2019.chs.ass",
            "星际穿越 (2014)/Interstellar.2014.1080p.mp4",
        ],
    )

    body = sync(client, admin)

    assert body["status"] == "succeeded", body
    assert body["stats"]["matched"] == 2
    assert body["stats"]["unmatched"] == 0
    assert set(items(client)) == {"流浪地球", "星际穿越"}
    detail = item(client, "流浪地球")
    assert detail["remote_id"] == "535167"
    assert detail["provider_name"] == "TMDB 元数据"
    assert detail["external_url"] == "https://www.themoviedb.org/movie/535167"
    assert detail["version_count"] == 2
    labels = sorted(v["label"] for v in detail["versions"])
    assert labels[0].startswith("1080p · BluRay") and labels[1].startswith("2160p · WEB-DL")
    assert all(not v["manual"] for v in detail["versions"])
    # 图片：相对地址、各档宽度从小到大，不需要登录就能取到
    assert [p["width"] for p in detail["poster"]] == [342, 780]
    url = detail["poster"][0]["url"]
    assert url.startswith("/api/images/")
    image = client.get(url)
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/jpeg"
    assert "immutable" in image.headers["cache-control"]
    assert detail["logo"][0]["url"].endswith(".png")
    # 第二次同步没有新内容，不再请求 TMDB
    tmdb.calls.clear()
    assert sync(client, admin)["status"] == "succeeded"
    assert tmdb.calls == []


@pytest.mark.usefixtures("tmdb_key", "films")
def test_manual_metadata_lock_survives_refresh_and_unlocks_for_tmdb(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
) -> None:
    movie_library(
        client,
        admin,
        cloud115,
        ["流浪地球 (2019)/流浪地球.2019.1080p.mkv"],
    )
    assert sync(client, admin)["status"] == "succeeded"
    item_id = items(client)["流浪地球"]["id"]

    response = client.patch(
        f"{ADMIN}/items/{item_id}/metadata/title",
        json={"value": "我的片名", "locked": True},
        headers=fresh_admin(client),
    )
    assert response.status_code == 204, response.text
    metadata = get(client, f"/items/{item_id}/metadata")
    title_metadata = next(field for field in metadata if field["field"] == "title")
    assert title_metadata["field"] == "title"
    assert title_metadata["value"] == "我的片名"
    assert title_metadata["source"] == "manual"
    assert title_metadata["locked"] is True
    assert title_metadata["updated_at"].startswith("2026-01-01T00:00:")

    tmdb.rename("movie", 535167, "TMDB 新片名")
    post(client, f"/items/{item_id}/refresh")
    assert get(client, f"/items/{item_id}")["title"] == "我的片名"

    response = client.patch(
        f"{ADMIN}/items/{item_id}/metadata/title",
        json={"value": "我的片名", "locked": False},
        headers=fresh_admin(client),
    )
    assert response.status_code == 204, response.text
    post(client, f"/items/{item_id}/refresh")
    assert get(client, f"/items/{item_id}")["title"] == "TMDB 新片名"


@pytest.mark.usefixtures("tmdb_key", "films")
def test_extras_are_not_stored_as_versions(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    config: AppConfig,
) -> None:
    folder = cloud115.make_path("影视/电影/星际穿越 (2014)")
    cloud115.add_file(folder, "Interstellar.2014.1080p.mp4")
    cloud115.add_file(folder, "Interstellar.trailer.mp4")
    cloud115.add_file(folder, "Interstellar.making.of.mkv", size=150 * MB)
    cloud115.make_path("影视/电影/星际穿越 (2014)/Featurettes/幕后.mkv")
    create_library(client, admin, "电影", "movie", cloud115.make_path("影视/电影"))

    body = sync(client, admin)

    assert body["stats"]["ignored"] == 3
    detail = item(client, "星际穿越")
    assert [v["path"].rsplit("/", 1)[1] for v in detail["versions"]] == [
        "Interstellar.2014.1080p.mp4"
    ]
    ignored = get(client, "/files", status="ignored")
    assert ignored["total"] == 3
    reasons = {f["path"].rsplit("/", 1)[1]: f["reason"] for f in ignored["items"]}
    assert reasons["Interstellar.making.of.mkv"] == "小于 200 MB"
    # 批量恢复会把文件放回未归入列表；重复 ID 去重，失效 ID 单项失败。
    making_of = media_file_id(config, "星际穿越 (2014)/Interstellar.making.of.mkv")
    restored = post(
        client,
        "/files/batch-restore",
        {"media_file_ids": [making_of, making_of, 999999]},
    )
    assert restored["requested"] == 2
    assert restored["succeeded"] == 1
    assert restored["failed"] == 1
    assert get(client, "/files")["total"] == 1
    assert get(client, "/files", status="ignored")["total"] == 2

    ignored_again = post(client, "/files/batch-ignore", {"media_file_ids": [making_of]})
    assert ignored_again["succeeded"] == 1
    assert get(client, "/files")["total"] == 0
    assert get(client, "/files", status="ignored")["total"] == 3

    # 单个恢复后可以继续手动挂回条目，之后同步不会再忽略它。
    response = client.post(f"{ADMIN}/files/{making_of}/restore", headers=fresh_admin(client))
    assert response.status_code == 204
    post(client, f"/files/{making_of}/assign", {"remote_id": "157336"})
    assert sync(client, admin)["status"] == "succeeded"
    detail = item(client, "星际穿越")
    assert detail["version_count"] == 2
    assert [v["manual"] for v in detail["versions"] if v["media_file_id"] == making_of] == [True]
    assert get(client, "/files", status="ignored")["total"] == 2


@pytest.mark.usefixtures("tmdb_key", "films")
def test_files_can_be_filtered_by_reason(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    config: AppConfig,
) -> None:
    folder = cloud115.make_path("影视/电影/星际穿越 (2014)")
    cloud115.add_file(folder, "Interstellar.2014.1080p.mp4")
    cloud115.add_file(folder, "Interstellar.trailer.mp4")
    cloud115.add_file(folder, "Interstellar.making.of.mkv", size=150 * MB)
    create_library(client, admin, "电影", "movie", cloud115.make_path("影视/电影"))
    sync(client, admin)

    everything = get(client, "/files", status="ignored")
    assert everything["total"] == 2
    assert "小于 200 MB" in everything["reasons"]
    assert everything["reasons"] == sorted(everything["reasons"])

    small = get(client, "/files", status="ignored", reason="小于 200 MB")
    assert small["total"] == 1
    assert [f["path"].rsplit("/", 1)[1] for f in small["items"]] == ["Interstellar.making.of.mkv"]
    # 筛选不影响可选原因列表；没有这个原因的记录得到空页
    assert small["reasons"] == everything["reasons"]
    assert get(client, "/files", status="ignored", reason="不存在的原因")["total"] == 0

    # 恢复的文件被标记为拆出，按这个原因出现在未归入列表
    making_of = media_file_id(config, "星际穿越 (2014)/Interstellar.making.of.mkv")
    post(client, "/files/batch-restore", {"media_file_ids": [making_of]})
    detached = "已从条目中拆出，等待重新指定"
    unplaced = get(client, "/files", status="unplaced", reason=detached)
    assert [f["id"] for f in unplaced["items"]] == [making_of]
    assert unplaced["reasons"] == [detached]
    assert get(client, "/files", status="unplaced", reason="没有归入任何条目")["total"] == 0


@pytest.mark.usefixtures("tmdb_key", "films")
def test_batch_rejects_records_already_in_a_running_task(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    clock: FakeClock,
) -> None:
    folder = cloud115.make_path("影视/电影/星际穿越 (2014)")
    cloud115.add_file(folder, "Interstellar.2014.1080p.mp4")
    cloud115.add_file(folder, "Interstellar.trailer.mp4")
    cloud115.add_file(folder, "Interstellar.making.of.mkv", size=150 * MB)
    create_library(client, admin, "电影", "movie", cloud115.make_path("影视/电影"))
    sync(client, admin)
    ignored = get(client, "/files", status="ignored")["items"]
    first, second = ignored[0]["id"], ignored[1]["id"]

    database = client.app.state.container.database  # type: ignore[attr-defined]
    with database.session() as connection:
        running = TaskRepository(connection, clock).create(
            "batch_restore_file", result={"input": {"media_file_ids": [first]}}
        )

    response = client.post(
        f"{ADMIN}/files/batch-restore",
        json={"media_file_ids": [first, second]},
        headers=fresh_admin(client),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "batch_in_progress"
    # 被拒绝时没有处理任何记录，也没有留下新任务
    assert get(client, "/files")["total"] == 0
    assert len(get(client, "/tasks", kind="batch_restore_file")) == 1

    # 不重叠的记录不受影响
    assert post(client, "/files/batch-restore", {"media_file_ids": [second]})["succeeded"] == 1

    # 旧任务结束后可以再次提交
    with database.session() as connection:
        TaskRepository(connection, clock).finish(running.id)
    assert post(client, "/files/batch-restore", {"media_file_ids": [first]})["succeeded"] == 1


@pytest.mark.usefixtures("tmdb_key")
def test_manual_match_survives_resync(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
) -> None:
    tmdb.add_movie(1001, "无人知晓", 2020)
    tmdb.add_movie(1002, "无人知晓", 2020, original_title="Nobody Knows")
    movie_library(client, admin, cloud115, ["无人知晓 (2020)/无人知晓.2020.1080p.mkv"])

    body = sync(client, admin)

    # 同名同年的两个结果片名都一致，取相关度最高的那个；假设它是错的
    assert body["stats"]["matched"] == 1
    wrong = items(client)["无人知晓"]
    assert wrong["remote_id"] == "1001"
    result = post(client, f"/items/{wrong['id']}/rematch", {"remote_id": "1002"})
    assert result["item_id"] != wrong["id"]
    tmdb.calls.clear()

    assert sync(client, admin)["status"] == "succeeded"

    assert [i["remote_id"] for i in items(client).values()] == ["1002"]
    assert not any(c.startswith("search") for c in tmdb.calls)
    work = get(client, "/works", state="matched")["items"][0]
    assert work["manual"] is True and work["item"]["remote_id"] == "1002"


@pytest.mark.usefixtures("tmdb_key")
def test_candidates_are_ranked_by_score_with_reasons(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
) -> None:
    movie_library(client, admin, cloud115, ["奇怪的片名 (2021)/奇怪的片名.2021.mkv"])
    tmdb.search_results[("movie", "奇怪的片名")] = [
        TmdbSearchResult(1, "完全无关", None, 2010, None),
        TmdbSearchResult(2, "奇怪的片名之二", None, 2020, None),
        TmdbSearchResult(3, "奇怪的片", None, 2022, None),
    ]

    assert sync(client, admin)["stats"]["unmatched"] == 1

    candidates = get(client, "/works")["items"][0]["candidates"]
    assert [c["remote_id"] for c in candidates] == ["3", "2", "1"]
    assert [c["score"] for c in candidates] == [51, 48, 0]
    assert candidates[0]["reasons"] == ["片名相似度 89%", "年份相差 1 年（文件 2021，候选 2022）"]


def test_candidate_score_rules() -> None:
    names = {"奇怪的片名"}
    exact = _scored(Candidate("p", "1", "奇怪的片名", None, 2021), names, 2021, unique=True)
    assert exact.score == 100
    assert exact.reasons == ("片名完全一致", "年份一致", "搜索结果唯一")
    # 原名一致也算完全一致；候选或文件名缺年份时不加分并写明
    original = _scored(Candidate("p", "2", "别名", "奇怪的片名", None), names, 2021, unique=False)
    assert (original.score, original.reasons) == (60, ("片名完全一致", "候选没有年份"))
    no_year = _scored(Candidate("p", "3", "奇怪的片名", None, 2021), names, None, unique=False)
    assert (no_year.score, no_year.reasons) == (60, ("片名完全一致", "文件名没有年份"))


@pytest.mark.usefixtures("tmdb_key")
def test_unmatched_work_is_listed_and_can_be_assigned(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
) -> None:
    movie_library(client, admin, cloud115, ["奇怪的片名 (2021)/奇怪的片名.2021.mkv"])
    tmdb.add_movie(2002, "正确的片名", 2021)
    tmdb.search_results[("movie", "奇怪的片名")] = [
        TmdbSearchResult(3003, "奇怪的片", None, 2010, None)
    ]

    body = sync(client, admin)

    assert body["stats"]["unmatched"] == 1
    assert get(client, "/pending") == {
        "metadata_problem": None,
        "metadata_action": None,
        "waiting": 0,
        "unmatched": 1,
        "unplaced": 0,
        "ignored": 0,
    }
    works = get(client, "/works")
    assert works["total"] == 1
    work = works["items"][0]
    assert work["path"] == "/影视/电影/奇怪的片名 (2021)"
    assert work["reason"] == "有多个可能的结果，无法确定「奇怪的片名」（2021）"
    assert [(c["provider_id"], c["remote_id"]) for c in work["candidates"]] == [
        ("builtin.tmdb", "3003")
    ]
    only = work["candidates"][0]
    assert only["score"] == 46
    assert only["reasons"] == [
        "片名相似度 89%",
        "年份不一致（文件 2021，候选 2010）",
        "搜索结果唯一",
    ]
    assert work["file_count"] == 1
    found = get(client, "/metadata/search", kind="movie", query="正确的片名")
    assert [(r["remote_id"], r["url"]) for r in found] == [
        ("2002", "https://www.themoviedb.org/movie/2002")
    ]

    detail = post(client, f"/works/{work['id']}/assign", {"remote_id": "2002"})

    assert detail["title"] == "正确的片名"
    assert detail["version_count"] == 1
    assert detail["works"][0]["manual"] is True
    assert get(client, "/pending")["unmatched"] == 0
    assert sync(client, admin)["stats"]["unmatched"] == 0
    assert set(items(client)) == {"正确的片名"}


@pytest.mark.usefixtures("tmdb_key")
def test_batch_assign_reports_successes_and_item_errors(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
) -> None:
    movie_library(
        client,
        admin,
        cloud115,
        [
            "批量甲 (2021)/甲.2021.mkv",
            "批量乙 (2022)/乙.2022.mkv",
        ],
    )
    tmdb.add_movie(4101, "批量甲正确", 2021)
    tmdb.add_movie(4102, "批量乙正确", 2022)
    tmdb.search_results[("movie", "批量甲")] = [TmdbSearchResult(3101, "候选甲", None, 2021, None)]
    tmdb.search_results[("movie", "批量乙")] = [TmdbSearchResult(3102, "候选乙", None, 2022, None)]
    sync(client, admin)
    works = get(client, "/works")["items"]
    assert len(works) == 2

    result = post(
        client,
        "/works/batch-assign",
        {"work_ids": [works[1]["id"], works[1]["id"], 999999], "remote_id": "4101"},
    )

    assert result["requested"] == 2
    assert isinstance(result["task_id"], int)
    assert result["succeeded"] == 1
    assert result["failed"] == 1
    assert result["unprocessed"] == 0
    assert result["results"][0]["id"] == works[1]["id"]
    assert result["results"][0]["item_id"] is not None
    assert result["results"][0]["error"] is None
    assert result["results"][1] == {
        "id": 999999,
        "item_id": None,
        "error": {"code": "not_found", "message": "作品不存在"},
    }
    task = get(client, f"/tasks/{result['task_id']}")
    assert task["kind"] == "batch_assign_work"
    assert task["status"] == "failed"
    assert task["progress_done"] == 2
    assert task["result"]["requested"] == 2
    assert task["result"]["succeeded"] == 1
    assert task["result"]["failed"] == 1
    assert task["result"]["unprocessed"] == 0
    assert task["result"]["input"] == {
        "work_ids": [works[1]["id"], 999999],
        "provider_id": None,
        "remote_id": "4101",
    }
    retried = client.post(
        f"/api/admin/tasks/{result['task_id']}/retry", headers=fresh_admin(client)
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["kind"] == "batch_assign_work"
    assert get(client, "/pending")["unmatched"] == 1

    tmdb.search_results[("movie", "批量乙")] = [
        TmdbSearchResult(4102, "批量乙正确", None, 2022, None)
    ]
    rematch = post(
        client,
        "/works/batch-rematch",
        {"work_ids": [works[0]["id"], works[1]["id"]]},
    )
    assert rematch["requested"] == 2
    assert rematch["succeeded"] == 1
    assert rematch["failed"] == 1
    assert rematch["results"][0]["item_id"] is not None
    assert rematch["results"][1]["error"]["code"] == "work_not_unmatched"
    assert get(client, "/pending")["unmatched"] == 0


def test_batch_assign_rejects_more_than_one_hundred_ids(
    client: TestClient,
) -> None:
    response = client.post(
        f"{ADMIN}/works/batch-assign",
        json={"work_ids": list(range(101)), "remote_id": "1"},
        headers=fresh_admin(client),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.usefixtures("tmdb_key")
def test_batch_task_cancellation_reports_unprocessed_items(
    client: TestClient,
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    movie_library(
        client,
        fresh_admin(client),
        cloud115,
        ["取消甲 (2021)/甲.2021.mkv", "取消乙 (2022)/乙.2022.mkv"],
    )
    tmdb.add_movie(4201, "取消甲", 2021)
    tmdb.add_movie(4202, "取消乙", 2022)
    tmdb.search_results[("movie", "取消甲")] = [TmdbSearchResult(4301, "候选甲", None, 2021, None)]
    tmdb.search_results[("movie", "取消乙")] = [TmdbSearchResult(4302, "候选乙", None, 2022, None)]
    sync(client, fresh_admin(client))
    works = get(client, "/works")["items"]
    assert len(works) == 2

    original_progress = pending_batch._progress_task

    def cancel_after_first(database: Any, clock: Any, task_id: int, done: int) -> None:
        original_progress(database, clock, task_id, done)
        if done == 1:
            with database.session() as connection:
                TaskRepository(connection, clock).request_cancel(task_id)

    monkeypatch.setattr(pending_batch, "_progress_task", cancel_after_first)
    result = post(
        client,
        "/works/batch-assign",
        {"work_ids": [work["id"] for work in works], "remote_id": "4201"},
    )

    assert result["requested"] == 2
    assert result["succeeded"] == 1
    assert result["failed"] == 0
    assert result["unprocessed"] == 1
    task = get(client, f"/tasks/{result['task_id']}")
    assert task["status"] == "cancelled"
    assert task["progress_done"] == 1
    assert task["result"]["unprocessed"] == 1


@pytest.mark.usefixtures("tmdb_key", "films")
def test_nfo_ids_are_used_before_searching(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
) -> None:
    by_tmdb = cloud115.make_path("影视/电影/我的收藏 A")
    cloud115.add_file(by_tmdb, "a.mkv")
    cloud115.add_file(
        by_tmdb, "a.nfo", content=b'<movie><uniqueid type="tmdb">535167</uniqueid></movie>'
    )
    by_imdb = cloud115.make_path("影视/电影/我的收藏 B")
    cloud115.add_file(by_imdb, "b.mkv")
    cloud115.add_file(by_imdb, "movie.nfo", content=b"<movie><imdbid>tt0816692</imdbid></movie>")
    create_library(client, admin, "电影", "movie", cloud115.make_path("影视/电影"))

    body = sync(client, admin)

    assert body["stats"]["matched"] == 2, body
    assert {i["remote_id"] for i in items(client).values()} == {"535167", "157336"}
    assert tmdb.count("search") == 0
    assert tmdb.count("find:tt0816692") == 1


def test_works_wait_until_tmdb_is_configured(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    films: None,
    tmdb: FakeTmdb,
) -> None:
    movie_library(client, admin, cloud115, ["流浪地球 (2019)/流浪地球.2019.mkv"])
    tmdb.reason = "还没有配置 TMDB Key，在插件 › TMDB 元数据里填写"

    body = sync(client, admin)

    assert body["status"] == "succeeded"
    assert body["stats"]["waiting"] == 1
    pending = get(client, "/pending")
    assert pending["metadata_problem"] == "还没有配置 TMDB Key，在插件 › TMDB 元数据里填写"
    assert pending["metadata_action"] == "/settings/manage/plugins/builtin.tmdb"
    assert get(client, "/pending")["waiting"] == 1
    assert get(client, "/items")["total"] == 0

    save_tmdb_key(client, fresh_admin(client))
    body = sync(client, admin)

    assert body["stats"]["matched"] == 1
    assert get(client, "/pending")["waiting"] == 0
    assert set(items(client)) == {"流浪地球"}


# ---- 剧集 ----


@pytest.fixture
def three_body(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115, tmdb: FakeTmdb
) -> None:
    tmdb.add_series(108545, "三体", 2023, {1: 30, 2: 10})
    for path in [
        "三体 (2023)/Season 1/三体.S01E01.1080p.mkv",
        "三体 (2023)/Season 1/三体.S01E02.1080p.mkv",
        "三体 (2023)/Season 2/三体.S02E01.1080p.mkv",
    ]:
        cloud115.make_path(f"影视/剧集/{path}")
    create_library(client, admin, "剧集", "series", cloud115.make_path("影视/剧集"))


@pytest.mark.usefixtures("tmdb_key", "three_body")
def test_new_episode_refreshes_only_its_season(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
) -> None:
    assert sync(client, admin)["stats"]["matched"] == 1
    assert sorted(c for c in tmdb.calls if c.startswith("season")) == [
        "season:108545:1",
        "season:108545:2",
    ]
    detail = item(client, "三体")
    assert [(v["season"], v["episode"]) for v in detail["versions"]] == [(1, 1), (1, 2), (2, 1)]
    tmdb.calls.clear()

    cloud115.make_path("影视/剧集/三体 (2023)/Season 2/三体.S02E02.1080p.mkv")
    assert sync(client, admin)["status"] == "succeeded"

    assert [c for c in tmdb.calls if not c.startswith("image")] == ["season:108545:2"]
    detail = item(client, "三体")
    assert (2, 2) in [(v["season"], v["episode"]) for v in detail["versions"]]


@pytest.mark.usefixtures("tmdb_key", "three_body")
def test_new_episode_bumps_series_added_at(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    config: AppConfig,
    clock: FakeClock,
) -> None:
    def added_at() -> str:
        with sqlite3.connect(config.database_path) as connection:
            row = connection.execute("SELECT added_at FROM media_item").fetchone()
        return str(row[0])

    sync(client, admin)
    first = added_at()

    # 没有新文件：同步不改变 added_at
    clock.advance(seconds=60)
    sync(client, admin)
    assert added_at() == first

    clock.advance(seconds=60)
    cloud115.make_path("影视/剧集/三体 (2023)/Season 2/三体.S02E02.1080p.mkv")
    sync(client, admin)
    assert added_at() > first


@pytest.mark.usefixtures("tmdb_key", "three_body")
def test_episode_without_number_is_assigned_by_hand(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    config: AppConfig,
) -> None:
    cloud115.make_path("影视/剧集/三体 (2023)/Season 2/三体 第二季 先导片.mkv")
    sync(client, admin)

    unplaced = get(client, "/files")
    assert unplaced["total"] == 1
    entry = unplaced["items"][0]
    assert entry["reason"] == "无法从文件名识别季号和集号"
    assert entry["item"]["remote_id"] == "108545"
    response = client.post(
        f"{ADMIN}/files/{entry['id']}/assign",
        json={"remote_id": "108545"},
        headers=fresh_admin(client),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "episode_required"

    detail = post(
        client, f"/files/{entry['id']}/assign", {"remote_id": "108545", "season": 0, "episode": 1}
    )

    assert (0, 1) in [(v["season"], v["episode"]) for v in detail["versions"]]
    assert get(client, "/files")["total"] == 0


# ---- 条目操作 ----


@pytest.mark.usefixtures("tmdb_key", "films")
def test_detached_version_waits_for_manual_assignment(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
) -> None:
    movie_library(
        client,
        admin,
        cloud115,
        [
            "流浪地球 (2019)/流浪地球.2019.2160p.mkv",
            "流浪地球 (2019)/星际穿越.2014.1080p.mkv",
        ],
    )
    sync(client, admin)
    detail = item(client, "流浪地球")
    misplaced = next(v for v in detail["versions"] if "星际穿越" in v["path"])

    post(client, f"/versions/{misplaced['id']}/detach", status=204)

    assert item(client, "流浪地球")["version_count"] == 1
    unplaced = get(client, "/files")["items"]
    assert [(f["id"], f["reason"]) for f in unplaced] == [
        (misplaced["media_file_id"], "已从条目中拆出，等待重新指定")
    ]
    sync(client, admin)
    assert get(client, "/files")["total"] == 1

    detail = post(client, f"/files/{misplaced['media_file_id']}/assign", {"remote_id": "157336"})

    assert detail["title"] == "星际穿越"
    assert get(client, "/files")["total"] == 0
    assert item(client, "流浪地球")["version_count"] == 1


@pytest.mark.usefixtures("tmdb_key", "films")
def test_batch_assign_files_reports_successes_and_item_errors(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
) -> None:
    movie_library(client, admin, cloud115, ["流浪地球 (2019)/流浪地球.2019.mkv"])
    sync(client, admin)
    version = item(client, "流浪地球")["versions"][0]
    post(client, f"/versions/{version['id']}/detach", status=204)

    result = post(
        client,
        "/files/batch-assign",
        {
            "media_file_ids": [version["media_file_id"], version["media_file_id"], 999999],
            "remote_id": "535167",
        },
    )

    assert result["requested"] == 2
    assert result["succeeded"] == 1
    assert result["failed"] == 1
    assert result["results"][1]["error"]["code"] == "not_found"
    assert get(client, "/files")["total"] == 0


@pytest.mark.usefixtures("tmdb_key", "films")
def test_refresh_and_automatic_rematch(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
) -> None:
    movie_library(client, admin, cloud115, ["流浪地球 (2019)/流浪地球.2019.mkv"])
    sync(client, admin)
    item_id = items(client)["流浪地球"]["id"]

    tmdb.rename("movie", 535167, "流浪地球（导演剪辑版）")
    detail = post(client, f"/items/{item_id}/refresh")

    assert detail["title"] == "流浪地球（导演剪辑版）"
    assert detail["id"] == item_id

    # TMDB 上搜不到了：自动重新匹配没有结果，作品回到待处理
    tmdb.titles.clear()
    assert post(client, f"/items/{item_id}/rematch", {}) == {"item_id": None}
    assert get(client, "/items")["total"] == 0
    assert get(client, "/pending")["unmatched"] == 1


@pytest.mark.usefixtures("tmdb_key", "films")
def test_locked_metadata_survives_sync_and_rematch_until_unlocked(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
) -> None:
    movie_library(client, admin, cloud115, ["流浪地球 (2019)/流浪地球.2019.mkv"])
    sync(client, admin)
    item_id = items(client)["流浪地球"]["id"]
    path = f"{ADMIN}/items/{item_id}/metadata/title"
    patched = client.patch(
        path, json={"value": "我的片名", "locked": True}, headers=fresh_admin(client)
    )
    assert patched.status_code == 204

    # 重复同步、刷新、重新匹配到同一个远端条目，锁定的标题都不被覆盖
    tmdb.rename("movie", 535167, "流浪地球（导演剪辑版）")
    sync(client, admin)
    assert get(client, f"/items/{item_id}")["title"] == "我的片名"
    assert post(client, f"/items/{item_id}/refresh")["title"] == "我的片名"
    assert post(client, f"/items/{item_id}/rematch", {"remote_id": "535167"})["item_id"] == item_id
    assert get(client, f"/items/{item_id}")["title"] == "我的片名"
    fields = {f["field"]: f for f in get(client, f"/items/{item_id}/metadata")}
    assert (fields["title"]["source"], fields["title"]["locked"]) == ("manual", True)

    # 解锁后值先保持，下一次刷新才被插件数据覆盖
    removed = client.delete(path, headers=fresh_admin(client))
    assert removed.status_code == 204
    fields = {f["field"]: f for f in get(client, f"/items/{item_id}/metadata")}
    assert fields["title"]["locked"] is False
    assert get(client, f"/items/{item_id}")["title"] == "我的片名"
    assert post(client, f"/items/{item_id}/refresh")["title"] == "流浪地球（导演剪辑版）"
    fields = {f["field"]: f for f in get(client, f"/items/{item_id}/metadata")}
    assert fields["title"]["source"] == "provider"


@pytest.mark.usefixtures("tmdb_key", "films")
def test_unlock_metadata_validates_field_and_item(client: TestClient) -> None:
    headers = fresh_admin(client)
    bad = client.delete(f"{ADMIN}/items/1/metadata/not_a_field", headers=headers)
    assert bad.status_code == 422
    missing = client.delete(f"{ADMIN}/items/999999/metadata/title", headers=headers)
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "not_found")


@pytest.mark.usefixtures("tmdb_key", "films")
def test_reenrich_updates_sizes_and_marks_missing_files(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    sync_runner: SyncRunner,
) -> None:
    keep = cloud115.make_path("影视/电影/流浪地球 (2019)/流浪地球.2019.2160p.mkv")
    gone = cloud115.make_path("影视/电影/流浪地球 (2019)/流浪地球.2019.1080p.mkv")
    create_library(client, admin, "电影", "movie", cloud115.make_path("影视/电影"))
    sync(client, admin)
    item_id = items(client)["流浪地球"]["id"]
    cloud115.nodes[keep].size = 60 * 1024**3
    cloud115.remove(gone)

    result = post(client, f"/items/{item_id}/reenrich")

    assert result["updated"] == 1
    assert result["missing"] == 1
    assert isinstance(result["task_id"], int)
    versions = item(client, "流浪地球")["versions"]
    states = {v["path"].rsplit("/", 1)[1]: (v["state"], v["size"]) for v in versions}
    assert states["流浪地球.2019.2160p.mkv"] == ("active", 60 * 1024**3)
    assert states["流浪地球.2019.1080p.mkv"][0] == "missing"
    assert "60 GB" in next(v["label"] for v in versions if v["state"] == "active")

    sync_runner.deferred = True
    client.post("/api/admin/sync", json={"library_id": None}, headers=fresh_admin(client))
    response = client.post(f"{ADMIN}/items/{item_id}/reenrich", headers=fresh_admin(client))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "sync_running"


@pytest.mark.usefixtures("tmdb_key", "films")
def test_items_are_paginated_and_searchable(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
) -> None:
    movie_library(
        client,
        admin,
        cloud115,
        ["流浪地球 (2019)/流浪地球.2019.mkv", "星际穿越 (2014)/Interstellar.2014.mkv"],
    )
    sync(client, admin)

    page = get(client, "/items", limit=1)
    assert page["total"] == 2 and len(page["items"]) == 1
    assert [i["title"] for i in get(client, "/items", query="inter")["items"]] == ["星际穿越"]
    assert get(client, "/items", query="100%")["total"] == 0
    missing = client.get(f"{ADMIN}/items/99999", headers=fresh_admin(client))
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"


@pytest.mark.usefixtures("tmdb_key", "films")
def test_deleting_a_library_removes_its_images(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    config: AppConfig,
) -> None:
    library = movie_library(client, admin, cloud115, ["流浪地球 (2019)/流浪地球.2019.mkv"])
    sync(client, admin)
    images = config.data_dir / "images"
    assert any(p.is_file() for p in images.rglob("*"))

    response = client.delete(f"{ADMIN}/libraries/{library['id']}", headers=fresh_admin(client))

    assert response.status_code == 204
    assert [p.name for p in images.rglob("*") if p.is_file()] == []


# ---- 设置 ----


def test_server_and_library_settings(client: TestClient, admin: dict[str, str]) -> None:
    info = client.get("/api/system/info").json()
    assert info["product"] == "iris"
    assert info["api_version"] == 1
    assert info["server_name"] == "光圈"

    response = client.put(
        f"{ADMIN}/settings/server", json={"name": " 家里的服务器 "}, headers=admin
    )
    assert response.json()["server"] == {
        "id": info["server_id"],
        "name": "家里的服务器",
        "public_url": None,
    }
    assert client.get("/api/system/info").json()["server_name"] == "家里的服务器"
    blank = client.put(f"{ADMIN}/settings/server", json={"name": "  "}, headers=admin)
    assert blank.json()["error"]["code"] == "invalid_server_name"

    response = client.put(f"{ADMIN}/settings/library", json={"extras_min_mb": 500}, headers=admin)
    assert response.json()["library"]["extras_min_mb"] == 500
    too_big = client.put(f"{ADMIN}/settings/library", json={"extras_min_mb": -1}, headers=admin)
    assert too_big.status_code == 422


def test_server_id_is_stable_across_restarts(
    config: AppConfig,
    clock: FakeClock,
    cloud115: FakeCloud115,
    sync_runner: SyncRunner,
) -> None:
    with TestClient(make_app(config, clock, sync_runner)) as first:
        server_id = first.get("/api/system/info").json()["server_id"]
    with TestClient(make_app(config, clock, sync_runner)) as second:
        assert second.get("/api/system/info").json()["server_id"] == server_id


def test_cors_is_off_unless_origins_are_configured(
    client: TestClient,
    config: AppConfig,
    clock: FakeClock,
    cloud115: FakeCloud115,
    sync_runner: SyncRunner,
) -> None:
    origin = "http://192.168.1.20:3000"
    preflight = {"Origin": origin, "Access-Control-Request-Method": "GET"}
    assert (
        "access-control-allow-origin"
        not in client.options("/api/system/info", headers=preflight).headers
    )

    allowed = replace(config, cors_origins=(origin,))
    with TestClient(make_app(allowed, clock, sync_runner)) as cors_client:
        response = cors_client.options("/api/system/info", headers=preflight)
        assert response.headers["access-control-allow-origin"] == origin


def test_admin_catalog_requires_admin(client: TestClient, admin_tokens: dict[str, str]) -> None:
    create_user(client, admin_tokens)
    user = bearer(login(client, "alice", "alice-password"))
    for path in ["/pending", "/works", "/files", "/items", "/settings", "/health"]:
        assert client.get(f"{ADMIN}{path}", headers=user).status_code == 403
    assert client.get(f"{ADMIN}/items").status_code == 401
    assert client.get("/api/images/nothing_342.jpg").status_code == 404
