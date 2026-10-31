from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.container import AppContainer
from tests.conftest import bearer
from tests.repository_server import (
    RepositoryHTTPServer,
    index_entry,
    make_plugin_zip,
    publish_index,
)


def container_of(client: TestClient) -> AppContainer:
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    return container


def add_repository(
    client: TestClient,
    headers: dict[str, str],
    url: str,
    *,
    accept_risk: bool = True,
) -> Any:
    return client.post(
        "/api/admin/plugin-repositories",
        json={"url": url, "accept_risk": accept_risk},
        headers=headers,
    )


def publish_plugin(
    server: RepositoryHTTPServer,
    tmp_path: Path,
    *,
    path: str = "/index.json",
    versions: tuple[str, ...] = ("1.0.0",),
    plugin_id: str = "org.example.fake",
    sdk: str = ">=1.0,<2.0",
    provides: tuple[str, ...] | None = None,
    name: str = "测试仓库",
) -> list[dict[str, Any]]:
    entries = []
    for version in versions:
        archive = make_plugin_zip(
            tmp_path / f"{plugin_id}-{version}.zip", version=version, plugin_id=plugin_id
        )
        server.add_file(f"/{plugin_id}-{version}.zip", archive.read_bytes())
        entries.append(
            index_entry(
                plugin_id=plugin_id,
                version=version,
                url=f"/{plugin_id}-{version}.zip",
                archive=archive,
                sdk=sdk,
                provides=provides,
            )
        )
    publish_index(server, entries, name=name, path=path)
    return entries


def test_repository_crud_and_risk_confirmation(
    client: TestClient,
    admin_tokens: dict[str, str],
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
) -> None:
    headers = bearer(admin_tokens)
    publish_plugin(repository_server, tmp_path)
    url = repository_server.index_url()

    refused = add_repository(client, headers, url, accept_risk=False)
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "risk_not_accepted"

    invalid = add_repository(client, headers, "http://example.com/index.json")
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_repository_url"

    created = add_repository(client, headers, url)
    assert created.status_code == 201, created.text
    repository = created.json()
    assert repository["url"] == url
    assert repository["official"] is False
    assert repository["enabled"] is True
    assert repository["status"] == "ok"
    assert repository["name"] == "测试仓库"
    assert repository["schema"] == 1
    assert repository["plugin_count"] == 1
    assert repository["invalid_count"] == 0
    assert repository["last_checked_at"] and repository["last_success_at"]

    duplicate = add_repository(client, headers, url)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "repository_exists"

    listed = client.get("/api/admin/plugin-repositories", headers=headers)
    assert [item["id"] for item in listed.json()["repositories"]] == [repository["id"]]

    disabled = client.patch(
        f"/api/admin/plugin-repositories/{repository['id']}",
        json={"enabled": False},
        headers=headers,
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    catalog = client.get("/api/admin/plugin-catalog", headers=headers).json()
    assert catalog["plugins"] == []
    assert catalog["repositories"] == []

    deleted = client.delete(f"/api/admin/plugin-repositories/{repository['id']}", headers=headers)
    assert deleted.status_code == 204
    assert client.get("/api/admin/plugin-repositories", headers=headers).json() == {
        "repositories": []
    }


def test_invalid_and_unsupported_index_are_rejected(
    client: TestClient,
    admin_tokens: dict[str, str],
    repository_server: RepositoryHTTPServer,
) -> None:
    headers = bearer(admin_tokens)
    repository_server.add_file("/index.json", b"not json")
    response = add_repository(client, headers, repository_server.index_url())
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "repository_invalid"

    publish_index(repository_server, [], schema=2)
    response = add_repository(client, headers, repository_server.index_url())
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "repository_schema_unsupported"


def test_refresh_failure_keeps_cache_and_etag_revalidation(
    client: TestClient,
    admin_tokens: dict[str, str],
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
) -> None:
    headers = bearer(admin_tokens)
    publish_plugin(repository_server, tmp_path)
    repository = add_repository(client, headers, repository_server.index_url()).json()

    repository_server.fail = True
    refreshed = client.post("/api/admin/plugin-repositories/refresh", headers=headers)
    assert refreshed.status_code == 200
    failed = refreshed.json()["repositories"][0]
    assert failed["status"] == "error"
    assert failed["error"]
    assert failed["plugin_count"] == 1
    catalog = client.get("/api/admin/plugin-catalog", headers=headers)
    assert [item["id"] for item in catalog.json()["plugins"]] == ["org.example.fake"]
    report = container_of(client).plugin_repositories.refresh_all()
    assert report["repositories"] == 1
    assert report["failed"] == 1

    repository_server.fail = False
    repository_server.requests.clear()
    recovered = client.post("/api/admin/plugin-repositories/refresh", headers=headers)
    assert recovered.status_code == 200
    item = recovered.json()["repositories"][0]
    assert item["status"] == "ok"
    assert item["error"] is None
    # 第二次拉取带上了上次的 ETag，服务端可以返回 304
    assert any(headers.get("If-None-Match") for _, headers in repository_server.requests)
    assert repository["id"] == item["id"]


def test_unsupported_refresh_keeps_cache_but_hides_catalog(
    client: TestClient,
    admin_tokens: dict[str, str],
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
) -> None:
    headers = bearer(admin_tokens)
    publish_plugin(repository_server, tmp_path)
    repository = add_repository(client, headers, repository_server.index_url()).json()
    assert client.get("/api/admin/plugin-catalog", headers=headers).json()["plugins"]

    publish_index(repository_server, [], schema=2, name="未来仓库")
    refreshed = client.post("/api/admin/plugin-repositories/refresh", headers=headers)
    item = refreshed.json()["repositories"][0]
    assert item["status"] == "unsupported"
    assert item["plugin_count"] == 1  # 旧缓存仍在，只是不展示
    assert client.get("/api/admin/plugin-catalog", headers=headers).json()["plugins"] == []
    # schema 记录的是最近一次成功解析的版本
    assert item["schema"] == 1
    assert item["id"] == repository["id"]


def test_official_and_in_use_repositories_cannot_be_deleted(
    client: TestClient,
    admin_tokens: dict[str, str],
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
) -> None:
    headers = bearer(admin_tokens)
    publish_plugin(repository_server, tmp_path)
    url = repository_server.index_url()
    container = container_of(client)
    container.plugin_repositories.ensure_defaults((url,))
    official = container.plugin_repositories.list()[0]
    assert official["official"] is True
    response = client.delete(f"/api/admin/plugin-repositories/{official['id']}", headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "repository_official"

    repository = add_repository(client, headers, f"{repository_server.base_url}/other/index.json")
    # 上面这个地址还没有索引，添加会失败；换一个用户仓库
    assert repository.status_code == 422
    publish_index(repository_server, [], path="/other/index.json")
    repository = add_repository(
        client, headers, f"{repository_server.base_url}/other/index.json"
    ).json()
    with container.database.session() as connection:
        connection.execute(
            "INSERT INTO plugin_install "
            "(plugin_id, source, repository_id, version, sha256, status, installed_at, updated_at) "
            "VALUES ('org.example.inuse', 'repository', ?, '1.0.0', ?, 'installed', 'x', 'x')",
            (repository["id"], "0" * 64),
        )
    response = client.delete(f"/api/admin/plugin-repositories/{repository['id']}", headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "repository_in_use"


def test_default_repository_slots_keep_row_ids(
    client: TestClient,
    repository_server: RepositoryHTTPServer,
) -> None:
    container = container_of(client)
    repositories = container.plugin_repositories
    url_a = f"{repository_server.base_url}/a/index.json"
    url_b = f"{repository_server.base_url}/b/index.json"
    url_c = f"{repository_server.base_url}/c/index.json"

    repositories.ensure_defaults((url_a, url_b))
    first = {item["url"]: item["id"] for item in repositories.list()}
    assert len(first) == 2
    assert all(item["official"] for item in repositories.list())

    repositories.ensure_defaults((url_a,))
    current = {item["url"]: item for item in repositories.list()}
    assert current[url_a]["id"] == first[url_a]
    assert current[url_a]["official"] is True
    assert current[url_b]["official"] is False

    repositories.ensure_defaults((url_c, url_b))
    updated = {item["url"]: item for item in repositories.list()}
    assert updated[url_c]["id"] == first[url_a]
    assert updated[url_c]["official"] is True
    assert updated[url_b]["id"] == first[url_b]
    assert updated[url_b]["official"] is True
    assert url_a not in updated


def test_default_alignment_promotes_existing_user_repository(
    client: TestClient,
    admin_tokens: dict[str, str],
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
) -> None:
    headers = bearer(admin_tokens)
    publish_plugin(repository_server, tmp_path, path="/b/index.json")
    url_b = f"{repository_server.base_url}/b/index.json"
    user_repository = add_repository(client, headers, url_b).json()
    container = container_of(client)
    url_a = f"{repository_server.base_url}/a/index.json"
    publish_index(repository_server, [], path="/a/index.json")
    container.plugin_repositories.ensure_defaults((url_a, url_b))

    repositories = {item["url"]: item for item in container.plugin_repositories.list()}
    assert repositories[url_b]["id"] == user_repository["id"]
    assert repositories[url_b]["official"] is True
    assert repositories[url_a]["official"] is True


def test_catalog_statuses(
    client: TestClient,
    admin_tokens: dict[str, str],
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
) -> None:
    headers = bearer(admin_tokens)
    # A 仓库：正常插件 1.0/1.1、不兼容插件、系统插件 id
    fake_zip = make_plugin_zip(tmp_path / "fake.zip", version="1.0.0")
    fake_zip_11 = make_plugin_zip(tmp_path / "fake-11.zip", version="1.1.0")
    incompatible_zip = make_plugin_zip(
        tmp_path / "incompatible.zip", version="1.0.0", plugin_id="org.example.incompatible"
    )
    system_zip = make_plugin_zip(tmp_path / "system.zip", version="1.0.0", plugin_id="source.115")
    for name, archive in (
        ("fake-1.0.0.zip", fake_zip),
        ("fake-1.1.0.zip", fake_zip_11),
        ("incompatible.zip", incompatible_zip),
        ("system.zip", system_zip),
    ):
        repository_server.add_file(f"/{name}", archive.read_bytes())
    entries = [
        index_entry(version="1.0.0", url="/fake-1.0.0.zip", archive=fake_zip),
        index_entry(version="1.1.0", url="/fake-1.1.0.zip", archive=fake_zip_11),
        index_entry(
            plugin_id="org.example.incompatible",
            version="1.0.0",
            url="/incompatible.zip",
            archive=incompatible_zip,
            sdk=">=2.0,<3.0",
        ),
        index_entry(
            plugin_id="source.115",
            version="1.0.0",
            url="/system.zip",
            archive=system_zip,
        ),
    ]
    publish_index(repository_server, entries, path="/a/index.json")
    repo_a = add_repository(client, headers, f"{repository_server.base_url}/a/index.json").json()

    publish_index(repository_server, [entries[0]], path="/b/index.json")
    add_repository(client, headers, f"{repository_server.base_url}/b/index.json")

    catalog = client.get("/api/admin/plugin-catalog", headers=headers).json()
    by_repo = {(item["repository_id"], item["id"]): item for item in catalog["plugins"]}
    assert by_repo[(repo_a["id"], "org.example.fake")]["status"] == "available"
    assert by_repo[(repo_a["id"], "org.example.fake")]["version"] == "1.1.0"
    assert by_repo[(repo_a["id"], "org.example.incompatible")]["status"] == "incompatible"
    assert by_repo[(repo_a["id"], "source.115")]["status"] == "conflict"

    # 从 A 安装 1.0 后：A 显示有更新，B 显示冲突
    with container_of(client).database.session() as connection:
        connection.execute(
            "INSERT INTO plugin_install "
            "(plugin_id, source, repository_id, version, sha256, status, installed_at, updated_at) "
            "VALUES ('org.example.fake', 'repository', ?, '1.0.0', ?, 'installed', 'x', 'x')",
            (repo_a["id"], "0" * 64),
        )
    catalog = client.get("/api/admin/plugin-catalog", headers=headers).json()
    by_repo = {(item["repository_id"], item["id"]): item for item in catalog["plugins"]}
    assert by_repo[(repo_a["id"], "org.example.fake")]["status"] == "update_available"
    assert by_repo[(repo_a["id"], "org.example.fake")]["installed_version"] == "1.0.0"
    assert by_repo[(repo_a["id"], "org.example.fake")]["version"] == "1.1.0"
    repo_b_id = next(
        item["repository_id"]
        for item in catalog["plugins"]
        if item["id"] == "org.example.fake" and item["repository_id"] != repo_a["id"]
    )
    assert by_repo[(repo_b_id, "org.example.fake")]["status"] == "conflict"


def test_catalog_marks_local_install_as_conflict(
    client: TestClient,
    admin_tokens: dict[str, str],
    repository_server: RepositoryHTTPServer,
    tmp_path: Path,
) -> None:
    headers = bearer(admin_tokens)
    publish_plugin(repository_server, tmp_path)
    add_repository(client, headers, repository_server.index_url())
    with container_of(client).database.session() as connection:
        connection.execute(
            "INSERT INTO plugin_install "
            "(plugin_id, source, repository_id, version, sha256, status, installed_at, updated_at) "
            "VALUES ('org.example.fake', 'local', NULL, '1.0.0', ?, 'installed', 'x', 'x')",
            ("0" * 64,),
        )
    catalog = client.get("/api/admin/plugin-catalog", headers=headers).json()
    item = catalog["plugins"][0]
    assert item["status"] == "conflict"
    assert item["reason"]


def test_catalog_requires_admin(client: TestClient) -> None:
    assert client.get("/api/admin/plugin-catalog").status_code == 401


def test_index_with_invalid_entries_reports_invalid_count(
    client: TestClient,
    admin_tokens: dict[str, str],
    repository_server: RepositoryHTTPServer,
) -> None:
    headers = bearer(admin_tokens)
    bad = {
        "id": "Bad Id",
        "name": "bad",
        "version": "1.0.0",
        "sdk": ">=1.0,<2.0",
        "provides": ["metadata.search"],
        "url": "/x.zip",
        "sha256": "0" * 64,
        "size": 1,
    }
    publish_index(repository_server, [bad])
    repository = add_repository(client, headers, repository_server.index_url()).json()
    assert repository["plugin_count"] == 0
    assert repository["invalid_count"] == 1


def test_catalog_sdk_version(client: TestClient, admin_tokens: dict[str, str]) -> None:
    headers = bearer(admin_tokens)
    body = client.get("/api/admin/plugin-catalog", headers=headers).json()
    assert body["sdk_version"]
    assert body == {"sdk_version": body["sdk_version"], "repositories": [], "plugins": []}


def test_json_fixture_is_serializable(repository_server: RepositoryHTTPServer) -> None:
    # 保证测试服务本身不会返回非 JSON 内容（间接验证 index 构造方式）
    publish_index(repository_server, [])
    assert json.loads(repository_server.files["/index.json"])["schema"] == 1


def test_unreachable_and_unknown_repositories(
    client: TestClient,
    admin_tokens: dict[str, str],
    repository_server: RepositoryHTTPServer,
) -> None:
    headers = bearer(admin_tokens)
    repository_server.fail = True
    response = add_repository(client, headers, repository_server.index_url())
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "repository_unreachable"

    response = client.patch(
        "/api/admin/plugin-repositories/424242", json={"enabled": False}, headers=headers
    )
    assert response.status_code == 404
    response = client.delete("/api/admin/plugin-repositories/424242", headers=headers)
    assert response.status_code == 404


def test_default_repository_environment_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import OFFICIAL_PLUGIN_REPOSITORY, _plugin_repos

    assert _plugin_repos(None) == (OFFICIAL_PLUGIN_REPOSITORY,)
    assert _plugin_repos("") == ()
    assert _plugin_repos(" https://a.example/index.json ,https://b.example/index.json, ") == (
        "https://a.example/index.json",
        "https://b.example/index.json",
    )
    with pytest.raises(RuntimeError):
        _plugin_repos("http://example.com/index.json")


def test_default_repository_reorder_keeps_row_ids(
    client: TestClient,
    repository_server: RepositoryHTTPServer,
) -> None:
    repositories = container_of(client).plugin_repositories
    url_a = f"{repository_server.base_url}/a/index.json"
    url_b = f"{repository_server.base_url}/b/index.json"
    url_c = f"{repository_server.base_url}/c/index.json"
    repositories.ensure_defaults((url_a, url_b))
    original = {item["url"]: item["id"] for item in repositories.list()}

    # 交换两个默认仓库的位置：两行的 id 都不能变
    repositories.ensure_defaults((url_b, url_a))
    swapped = {item["url"]: item for item in repositories.list()}
    assert swapped[url_a]["id"] == original[url_a]
    assert swapped[url_b]["id"] == original[url_b]
    assert swapped[url_a]["official"] and swapped[url_b]["official"]

    # 槽位 0 换成新地址，但 url_a 仍在槽位 1：url_a 的行必须保留，不能复用给 url_c
    repositories.ensure_defaults((url_c, url_a))
    mixed = {item["url"]: item for item in repositories.list()}
    assert mixed[url_a]["id"] == original[url_a]
    assert mixed[url_c]["id"] == original[url_b]
    assert mixed[url_c]["official"] is True
    assert url_b not in mixed
