"""M8 验收：定向同步只列出指定目录，不导出整个资料库。"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.sync.engine import SyncEngine
from app.sync.targets import DirTarget, SourceDir
from tests.conftest import ensure_cloud115_source
from tests.fake_cloud115 import FakeCloud115
from tests.fake_tmdb import FakeTmdb
from tests.test_browse import build_catalog, find
from tests.test_playback import episodes
from tests.test_sync import files, fresh_admin, job


@pytest.fixture
def catalog(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115, tmdb: FakeTmdb
) -> dict[str, int]:
    ids = build_catalog(client, admin, cloud115, tmdb)
    cloud115.calls.clear()
    return ids


@pytest.fixture
def source_id(client: TestClient, admin: dict[str, str]) -> int:
    return ensure_cloud115_source(client, admin)


def source(source_id: int, cid: str) -> SourceDir:
    return SourceDir(source_id, cid)


def targeted(client: TestClient, admin: dict[str, str], *targets: DirTarget) -> dict[str, Any]:
    engine: SyncEngine = client.app.state.container.sync  # type: ignore[attr-defined]
    started = engine.start_targeted("event", list(targets), "来源目录变动")
    return job(client, admin, started.id)


def sync_item(client: TestClient, admin: dict[str, str], item_id: int) -> dict[str, Any]:
    response = client.post(f"/api/admin/items/{item_id}/sync", headers=fresh_admin(client))
    assert response.status_code == 202, response.text
    body: dict[str, Any] = response.json()
    assert body["scope"] == "dir"
    return job(client, admin, body["id"])


def watch(client: TestClient, item_id: int) -> None:
    response = client.put(f"/api/items/{item_id}/watched", headers=fresh_admin(client))
    assert response.status_code == 204


@pytest.mark.usefixtures("catalog")
def test_item_sync_picks_up_new_episode(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115
) -> None:
    cloud115.make_path("影视/剧集/三体 (2023)/Season 1/三体.S01E03.1080p.mkv")
    series = find(client, "三体")

    body = sync_item(client, admin, series["id"])

    assert body["status"] == "succeeded", body
    assert body["trigger"] == "manual"
    assert body["target"] == "「三体」"
    assert body["stats"]["added"] == 1
    assert "S01E03" in episodes(client)
    # 只列目录，不导出
    assert "scan" not in cloud115.calls


@pytest.mark.usefixtures("catalog")
def test_item_sync_marks_deleted_version_missing(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115, config: AppConfig
) -> None:
    folder = cloud115.make_path("影视/电影/流浪地球 (2019)")
    node = cloud115.child(folder, "流浪地球.2019.2160p.WEB-DL.mkv")
    assert node is not None
    cloud115.remove(node)

    body = sync_item(client, admin, find(client, "流浪地球")["id"])

    assert body["stats"]["removed"] == 1
    assert files(config)["流浪地球 (2019)/流浪地球.2019.2160p.WEB-DL.mkv"][1] == "missing"
    assert files(config)["流浪地球 (2019)/流浪地球.2019.1080p.BluRay.mkv"][1] == "active"


def test_item_sync_unknown_item(client: TestClient, admin: dict[str, str]) -> None:
    response = client.post("/api/admin/items/999/sync", headers=admin)
    assert response.status_code == 404


@pytest.mark.usefixtures("catalog")
def test_new_dir_in_library_root_only_lists_what_changed(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
    source_id: int,
) -> None:
    tmdb.add_movie(9001, "新片", 2024)
    cloud115.make_path("影视/电影/新片 (2024)/新片.2024.1080p.mkv")

    body = targeted(client, admin, source(source_id, cloud115.make_path("影视/电影")))

    assert body["status"] == "succeeded", body
    assert body["trigger"] == "event"
    assert body["scope"] == "dir"
    assert body["target"] == "来源目录变动"
    assert body["stats"]["added"] == 1
    assert body["stats"]["removed"] == 0
    assert body["stats"]["matched"] == 1
    assert find(client, "新片")["year"] == 2024
    # 资料库根目录和新目录各列一次，已有的目录不展开
    assert cloud115.calls.count("browse") == 2
    assert "scan" not in cloud115.calls


@pytest.mark.usefixtures("catalog")
def test_renamed_dir_keeps_identity(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    config: AppConfig,
    source_id: int,
) -> None:
    stars = find(client, "星际穿越")
    watch(client, stars["id"])
    before = files(config)["星际穿越 (2014)/Interstellar.2014.1080p.mp4"][0]
    root = cloud115.make_path("影视/电影")
    folder = cloud115.make_path("影视/电影/星际穿越 (2014)")
    cloud115.move(folder, root, "Interstellar (2014)")

    body = targeted(client, admin, source(source_id, root))

    assert body["stats"]["renamed"] == 1
    assert body["stats"]["removed"] == 0
    assert files(config)["Interstellar (2014)/Interstellar.2014.1080p.mp4"][0] == before
    assert find(client, "星际穿越")["watched"] is True


@pytest.mark.usefixtures("catalog")
def test_file_moved_into_other_dir_keeps_identity(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    config: AppConfig,
    source_id: int,
) -> None:
    before = files(config)["星际穿越 (2014)/Interstellar.2014.1080p.mp4"][0]
    origin = cloud115.make_path("影视/电影/星际穿越 (2014)")
    target = cloud115.make_path("影视/电影/收藏")
    node = cloud115.child(origin, "Interstellar.2014.1080p.mp4")
    assert node is not None
    cloud115.move(node, target)

    # 插件报告移入的目录，宿主按来源文件身份认出这是同一个文件
    body = targeted(client, admin, source(source_id, target))

    assert body["stats"]["renamed"] == 1
    assert body["stats"]["removed"] == 0
    assert files(config)["收藏/Interstellar.2014.1080p.mp4"][0] == before
    assert "星际穿越 (2014)/Interstellar.2014.1080p.mp4" not in files(config)


@pytest.mark.usefixtures("catalog")
def test_file_moved_out_of_libraries_goes_missing(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    config: AppConfig,
    source_id: int,
) -> None:
    origin = cloud115.make_path("影视/电影/星际穿越 (2014)")
    parking = cloud115.make_path("暂存")
    node = cloud115.child(origin, "Interstellar.2014.1080p.mp4")
    assert node is not None
    cloud115.move(node, parking)

    # 移出的旧目录也发生了变动，插件会一起通知
    body = targeted(client, admin, source(source_id, origin))

    assert body["status"] == "succeeded", body
    assert body["stats"]["removed"] == 1
    assert files(config)["星际穿越 (2014)/Interstellar.2014.1080p.mp4"][1] == "missing"


@pytest.mark.usefixtures("catalog")
def test_deleted_dir_is_found_from_parent(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    config: AppConfig,
    source_id: int,
) -> None:
    root = cloud115.make_path("影视/电影")
    cloud115.remove(cloud115.make_path("影视/电影/星际穿越 (2014)"))
    gone = cloud115.make_path("影视/电影/不存在")
    cloud115.remove(gone)

    # 已删掉的目录跳过，上级目录照常比对
    body = targeted(client, admin, source(source_id, gone), source(source_id, root))

    assert body["status"] == "succeeded", body
    assert body["stats"]["removed"] == 1
    assert body["stats"]["failed"] == 0
    assert files(config)["星际穿越 (2014)/Interstellar.2014.1080p.mp4"][1] == "missing"
    # 定向同步不做同步删除
    assert body["stats"]["purged"] == 0


@pytest.mark.usefixtures("catalog")
def test_engine_source_changed_syncs_the_affected_library_folder(
    client: TestClient,
    admin: dict[str, str],
    cloud115: FakeCloud115,
    tmdb: FakeTmdb,
    source_id: int,
) -> None:
    """插件发 resource.changed 后，宿主按来源和定位符找到资料库目录做定向同步。"""
    tmdb.add_movie(9002, "另一部新片", 2024)
    cloud115.make_path("影视/电影/另一部新片 (2024)/另一部新片.2024.1080p.mkv")
    engine: SyncEngine = client.app.state.container.sync  # type: ignore[attr-defined]

    engine.source_changed(source_id, [cloud115.make_path("影视/电影")])

    latest = client.get("/api/admin/sync/jobs", headers=fresh_admin(client)).json()[0]
    body = job(client, admin, latest["id"])
    assert body["status"] == "succeeded", body
    assert body["trigger"] == "event"
    assert body["target"] == "来源目录变动（1 个目录）"
    assert body["stats"]["added"] == 1
    assert find(client, "另一部新片")["year"] == 2024
