"""关键接口契约：真实请求，按运行时 OpenAPI 校验必需字段、类型和错误结构。

快照比对（test_openapi_contract）只能发现 schema 变了；这里确认接口实际返回的内容符合 schema。
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, bearer, create_user, login
from tests.fake_cloud115 import FakeCloud115
from tests.fake_tmdb import FakeTmdb
from tests.test_browse import build_catalog, find
from tests.test_sync import fresh_admin
from tests.test_tasks import make_task

JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
    "null": (type(None),),
}


def resolve(spec: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        node: Any = spec
        for part in schema["$ref"].removeprefix("#/").split("/"):
            node = node[part]
        schema = node
    return schema


def violations(spec: dict[str, Any], schema: dict[str, Any], value: Any, path: str) -> list[str]:
    """返回 value 不符合 schema 的位置；只覆盖本项目 schema 用到的关键字。"""
    schema = resolve(spec, schema)
    if "anyOf" in schema:
        attempts = [violations(spec, option, value, path) for option in schema["anyOf"]]
        return [] if any(not found for found in attempts) else [f"{path}: 不匹配任何 anyOf 分支"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{path}: {value!r} 不在枚举 {schema['enum']} 中"]
    expected = schema.get("type")
    if expected is None:
        return []
    allowed = JSON_TYPES[expected]
    members: Any = value
    # bool 是 int 的子类，整数/数字字段不接受布尔值
    if not isinstance(value, allowed) or (isinstance(value, bool) and expected != "boolean"):
        return [f"{path}: 期望 {expected}，实际 {type(value).__name__}"]
    found: list[str] = []
    if expected == "object":
        for name in schema.get("required", []):
            if name not in members:
                found.append(f"{path}.{name}: 缺少必需字段")
        for name, child in schema.get("properties", {}).items():
            if name in members:
                found += violations(spec, child, members[name], f"{path}.{name}")
    if expected == "array" and "items" in schema:
        for index, element in enumerate(members):
            found += violations(spec, schema["items"], element, f"{path}[{index}]")
    return found


def response_schema(spec: dict[str, Any], path: str, method: str, status: str) -> dict[str, Any]:
    response = spec["paths"][path][method]["responses"][status]
    schema: dict[str, Any] = response["content"]["application/json"]["schema"]
    return schema


def assert_matches(
    client: TestClient, path: str, method: str, status: int, body: Any, label: str | None = None
) -> None:
    spec = cast(FastAPI, client.app).openapi()
    schema = response_schema(spec, path, method, str(status))
    assert violations(spec, schema, body, label or path) == []


def assert_error(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert set(body) == {"error"}, body
    error = body["error"]
    assert isinstance(error, dict)
    assert isinstance(error["code"], str)
    assert isinstance(error["message"], str)
    assert error["code"] == code
    assert error["message"].strip()
    assert set(error) <= {"code", "message", "details"}
    assert "detail" not in body
    result: dict[str, Any] = error
    return result


@pytest.fixture
def catalog(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115, tmdb: FakeTmdb
) -> dict[str, int]:
    return build_catalog(client, admin, cloud115, tmdb)


def test_validator_detects_missing_wrong_type_and_extra_null(client: TestClient) -> None:
    """校验器自身：确认它不是对任何输入都放行。"""
    spec = cast(FastAPI, client.app).openapi()
    schema = response_schema(spec, "/api/libraries", "get", "200")
    good = [{"id": 1, "name": "电影", "kind": "movie", "item_count": 3, "cover": []}]
    assert violations(spec, schema, good, "x") == []
    assert violations(
        spec, schema, [{"id": 1, "name": "电影", "kind": "movie", "cover": []}], "x"
    ) == ["x[0].item_count: 缺少必需字段"]
    assert violations(spec, schema, [{**good[0], "id": "1"}], "x") == [
        "x[0].id: 期望 integer，实际 str"
    ]
    assert violations(spec, schema, [{**good[0], "id": True}], "x") == [
        "x[0].id: 期望 integer，实际 bool"
    ]
    assert violations(spec, schema, [{**good[0], "kind": "book"}], "x") == [
        "x[0].kind: 'book' 不在枚举 ['movie', 'series'] 中"
    ]
    assert violations(spec, schema, {"id": 1}, "x") == ["x: 期望 array，实际 dict"]


def test_login_response_contract(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert_matches(client, "/api/auth/login", "post", 200, body)
    assert {"access_token", "refresh_token", "expires_in", "user"} <= set(body)
    assert body["access_token"] and body["refresh_token"]
    assert body["expires_in"] > 0
    assert body["token_type"] == "bearer"
    user = body["user"]
    assert user["username"] == ADMIN_USERNAME
    assert user["role"] == "admin"
    assert "password" not in str(body) and "password_hash" not in user


def test_home_libraries_and_item_contracts(
    client: TestClient, admin: dict[str, str], catalog: dict[str, int]
) -> None:
    headers = fresh_admin(client)

    home = client.get("/api/home", headers=headers)
    assert home.status_code == 200, home.text
    home_body = home.json()
    assert_matches(client, "/api/home", "get", 200, home_body)
    assert {"libraries", "recent", "favorites", "watchlist", "shelves"} <= set(home_body)
    assert home_body["libraries"], "目录已同步，首页应有资料库"
    assert home_body["shelves"], "目录已同步，首页应有推荐行"

    libraries = client.get("/api/libraries", headers=headers)
    assert libraries.status_code == 200, libraries.text
    libraries_body = libraries.json()
    assert_matches(client, "/api/libraries", "get", 200, libraries_body)
    assert {library["kind"] for library in libraries_body} == {"movie", "series"}
    assert all(library["item_count"] > 0 for library in libraries_body)

    movie = find(client, "流浪地球")
    detail = client.get(f"/api/items/{movie['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    detail_body = detail.json()
    assert_matches(client, "/api/items/{item_id}", "get", 200, detail_body)
    assert detail_body["id"] == movie["id"]
    assert detail_body["title"] == "流浪地球"
    assert detail_body["kind"] == "movie"
    assert len(detail_body["versions"]) == 2
    assert detail_body["cast"], "演职员应随详情返回"
    for version in detail_body["versions"]:
        assert isinstance(version["id"], int)

    show = find(client, "三体")
    series = client.get(f"/api/items/{show['id']}", headers=headers).json()
    assert_matches(client, "/api/items/{item_id}", "get", 200, series, "series")
    assert series["kind"] == "series"
    assert series["seasons"]


def test_admin_tasks_contract(client: TestClient, admin: dict[str, str], clock: Any) -> None:
    task = make_task(client, clock, "demo")
    headers = fresh_admin(client)

    listing = client.get("/api/admin/tasks", headers=headers)
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert_matches(client, "/api/admin/tasks", "get", 200, body)
    entry = next(item for item in body if item["id"] == task.id)
    assert entry["kind"] == "demo"
    assert entry["status"] == "running"
    assert entry["finished_at"] is None
    assert isinstance(entry["progress_done"], int)
    assert isinstance(entry["progress_total"], int)
    assert isinstance(entry["cancel_requested"], bool)
    assert isinstance(entry["result"], dict)

    cancelled = client.post(f"/api/admin/tasks/{task.id}/cancel", headers=headers)
    assert cancelled.status_code == 200, cancelled.text
    assert_matches(client, "/api/admin/tasks/{task_id}/cancel", "post", 200, cancelled.json())
    assert cancelled.json()["cancel_requested"] is True


def test_error_responses_share_the_error_envelope(
    client: TestClient, admin: dict[str, str], admin_tokens: dict[str, str]
) -> None:
    # 未登录
    assert_error(client.get("/api/home"), 401, "unauthorized")
    # 登录失败
    wrong = client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": "wrong"})
    assert_error(wrong, 401, "invalid_credentials")
    # 请求体校验失败
    invalid = assert_error(client.post("/api/auth/login", json={}), 422, "validation_error")
    assert invalid["message"]
    # 资源不存在
    headers = fresh_admin(client)
    assert_error(client.get("/api/items/999999", headers=headers), 404, "not_found")
    assert_error(client.post("/api/admin/tasks/999999/cancel", headers=headers), 404, "not_found")
    # 无权限
    create_user(client, admin_tokens, "alice", "alice-password")
    user = bearer(login(client, "alice", "alice-password"))
    assert_error(client.get("/api/admin/tasks", headers=user), 403, "forbidden")
    # 路由不存在也走同一结构
    assert_error(client.get("/api/no-such-endpoint", headers=headers), 404, "not_found")


def test_openapi_declares_the_runtime_error_envelope(
    client: TestClient, admin_tokens: dict[str, str]
) -> None:
    spec = cast(FastAPI, client.app).openapi()
    schemas = spec["components"]["schemas"]
    # 默认的 HTTPValidationError（{detail:[...]}）已被替换
    assert "HTTPValidationError" not in schemas
    assert set(schemas["ErrorResponse"]["required"]) == {"error"}
    body_schema = resolve(spec, schemas["ErrorResponse"]["properties"]["error"])
    assert set(body_schema["required"]) == {"code", "message"}
    assert set(body_schema["properties"]) == {"code", "message", "details"}

    # 实际返回的 401 / 422 与声明的结构一致
    unauthorized = client.get("/api/home")
    assert_error(unauthorized, 401, "unauthorized")
    assert (
        violations(
            spec, response_schema(spec, "/api/home", "get", "4XX"), unauthorized.json(), "401"
        )
        == []
    )
    invalid = client.post("/api/auth/login", json={})
    assert_error(invalid, 422, "validation_error")
    declared = response_schema(spec, "/api/auth/login", "post", "422")
    assert violations(spec, declared, invalid.json(), "422") == []
    assert declared == response_schema(spec, "/api/auth/login", "post", "4XX")
    assert set(invalid.json()["error"]["details"][0]) == {"loc", "type"}
