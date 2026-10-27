"""设置页工具包：字段声明、校验、密钥处理和测试连接。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

import pytest

from iris_plugin_sdk import (
    JsonSettingsStore,
    PluginRequest,
    PluginRequestError,
    SettingField,
    SettingsEndpoint,
)

DEFAULT_FIELDS = (
    SettingField("greeting", "问候语", "text", default="hi", max_length=10),
    SettingField("api_token", "API 令牌", "secret"),
    SettingField("endpoint", "接口地址", "url"),
    SettingField("interval", "间隔", "number", min=0.5, max=10, default=1),
    SettingField("enabled", "启用", "bool", default=False),
    SettingField("mode", "模式", "select", options=("a", "b"), default="a"),
)


class MemorySettingsStore:
    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.plain = {"api_token": "stored-token", **(values or {})}

    def values(self) -> dict[str, Any]:
        return dict(self.plain)

    def get(self, name: str) -> Any:
        return self.plain.get(name)

    def put(self, name: str, value: Any) -> None:
        self.plain[name] = value

    def delete(self, name: str) -> None:
        self.plain.pop(name, None)


def _endpoint(
    fields: tuple[SettingField, ...] = DEFAULT_FIELDS,
    *,
    store: MemorySettingsStore | None = None,
    tester: Any = None,
) -> tuple[SettingsEndpoint, MemorySettingsStore]:
    store = store or MemorySettingsStore()
    endpoint = SettingsEndpoint(fields, store=store, tester=cast(Any, tester))
    return endpoint, store


def _request(method: str, *, path: str = "settings", body: Any = None) -> PluginRequest:
    return PluginRequest(method=method, path=path, query={}, body=body, user_id=1)


def _page(endpoint: SettingsEndpoint, method: str = "GET", body: Any = None) -> dict[str, Any]:
    response = endpoint.handle(_request(method, body=body))
    assert response is not None
    assert response.status == 200
    return cast(dict[str, Any], response.body)


def _error(endpoint: SettingsEndpoint, body: Any) -> PluginRequestError:
    with pytest.raises(PluginRequestError) as error:
        endpoint.handle(_request("PUT", body=body))
    assert error.value.status == 400
    assert error.value.code == "invalid_setting"
    return error.value


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "BadName"},
        {"name": "a-b"},
        {"name": ""},
        {"label": ""},
        {"kind": "email"},
        {"kind": "select"},
        {"kind": "select", "options": ("a", "a")},
        {"kind": "text", "options": ("a",)},
        {"kind": "number", "min": 2, "max": 1},
        {"kind": "text", "min": 1},
        {"kind": "secret", "default": "x"},
        {"kind": "number", "default": "1"},
        {"kind": "select", "options": ("a",), "default": "b"},
        {"kind": "url", "default": "ftp://example.com"},
        {"max_length": 0},
    ],
)
def test_setting_field_rejects_invalid_declarations(kwargs: dict[str, Any]) -> None:
    arguments: dict[str, Any] = {"name": "value", "label": "值", "kind": "text"}
    arguments.update(kwargs)
    with pytest.raises(ValueError):
        SettingField(**arguments)


def test_settings_endpoint_rejects_duplicate_names() -> None:
    field = SettingField("greeting", "问候语", "text")
    with pytest.raises(ValueError, match="不能重复"):
        SettingsEndpoint((field, field), store=MemorySettingsStore())


def test_get_hides_secret_and_describes_fields() -> None:
    endpoint, _ = _endpoint(
        store=MemorySettingsStore({"greeting": "你好", "interval": 2, "mode": "b"}),
        tester=lambda values: None,
    )

    page = _page(endpoint)

    assert page["testable"] is True
    assert page["values"]["greeting"] == "你好"
    assert page["values"]["interval"] == 2
    assert page["values"]["mode"] == "b"
    assert page["values"]["api_token"] == {"set": True}
    assert "stored-token" not in str(page)
    fields = {field["name"]: field for field in page["fields"]}
    assert fields["api_token"]["kind"] == "secret"
    assert fields["mode"]["options"] == ["a", "b"]
    assert fields["interval"]["min"] == 0.5
    assert fields["greeting"]["max_length"] == 10


def test_get_reports_unset_secret() -> None:
    store = MemorySettingsStore()
    store.plain.pop("api_token")
    endpoint, _ = _endpoint(store=store)

    assert _page(endpoint)["values"]["api_token"] == {"set": False}


def test_put_saves_plain_values_and_keeps_unknown_config() -> None:
    endpoint, store = _endpoint(store=MemorySettingsStore({"other": "keep"}))

    page = _page(
        endpoint,
        "PUT",
        {
            "greeting": " hello ",
            "endpoint": " https://example.com/a ",
            "interval": 2.5,
            "enabled": True,
            "mode": "b",
        },
    )

    assert store.plain == {
        "api_token": "stored-token",
        "other": "keep",
        "greeting": "hello",
        "endpoint": "https://example.com/a",
        "interval": 2.5,
        "enabled": True,
        "mode": "b",
    }
    assert page["values"]["greeting"] == "hello"
    assert page["values"]["interval"] == 2.5
    assert page["values"]["api_token"] == {"set": True}


def test_put_leaves_missing_fields_unchanged() -> None:
    endpoint, store = _endpoint(store=MemorySettingsStore({"greeting": "旧问候", "interval": 3}))

    _page(endpoint, "PUT", {"mode": "b"})

    assert store.plain["greeting"] == "旧问候"
    assert store.plain["interval"] == 3
    assert store.plain["mode"] == "b"


def test_put_null_restores_default_and_empty_url_clears() -> None:
    endpoint, store = _endpoint(
        store=MemorySettingsStore({"interval": 3, "endpoint": "https://a.example", "extra": 1})
    )

    page = _page(endpoint, "PUT", {"interval": None, "endpoint": ""})

    assert "interval" not in store.plain
    assert store.plain["endpoint"] == ""
    assert store.plain["extra"] == 1
    assert page["values"]["interval"] == 1


def test_required_field_cannot_be_reset_to_empty_default() -> None:
    fields = (SettingField("endpoint", "接口地址", "url", required=True),)
    endpoint, _ = _endpoint(fields, store=MemorySettingsStore({"endpoint": "https://a.example"}))

    error = _error(endpoint, {"endpoint": None})

    assert "不能为空" in error.message


def test_secret_empty_or_missing_keeps_saved_value() -> None:
    endpoint, store = _endpoint()

    page = _page(endpoint, "PUT", {"api_token": ""})
    assert store.plain["api_token"] == "stored-token"
    assert page["values"]["api_token"] == {"set": True}

    _page(endpoint, "PUT", {})
    assert store.plain["api_token"] == "stored-token"


def test_secret_null_clears_value() -> None:
    endpoint, store = _endpoint()

    page = _page(endpoint, "PUT", {"api_token": None})

    assert store.plain.get("api_token") is None
    assert page["values"]["api_token"] == {"set": False}


def test_secret_new_value_is_saved_and_not_echoed() -> None:
    endpoint, store = _endpoint()

    page = _page(endpoint, "PUT", {"api_token": "new-token"})

    assert store.plain["api_token"] == "new-token"
    assert page["values"]["api_token"] == {"set": True}
    assert "new-token" not in str(page)


@pytest.mark.parametrize(
    ("body", "label", "reason"),
    [
        ({"interval": "2"}, "间隔", "必须是数字"),
        ({"interval": True}, "间隔", "必须是数字"),
        ({"interval": 0.1}, "间隔", "不能小于 0.5"),
        ({"interval": 11}, "间隔", "不能大于 10"),
        ({"enabled": 1}, "启用", "必须是布尔值"),
        ({"mode": "c"}, "模式", "不是可选项"),
        ({"endpoint": "ftp://example.com"}, "接口地址", "只允许 http 或 https"),
        ({"endpoint": "https://"}, "接口地址", "只允许 http 或 https"),
        ({"endpoint": "https://exa mple.com"}, "接口地址", "空白"),
        ({"greeting": 1}, "问候语", "必须是文本"),
        ({"greeting": "x" * 11}, "问候语", "不能超过 10 个字符"),
    ],
)
def test_put_validates_field_types(body: dict[str, Any], label: str, reason: str) -> None:
    endpoint, _ = _endpoint()

    error = _error(endpoint, body)

    assert label in error.message
    assert reason in error.message


def test_invalid_update_is_not_saved() -> None:
    endpoint, store = _endpoint(store=MemorySettingsStore({"greeting": "旧"}))

    with pytest.raises(PluginRequestError):
        endpoint.handle(_request("PUT", body={"greeting": "新", "interval": "bad"}))

    assert store.plain["greeting"] == "旧"


def test_settings_test_merges_saved_and_provided_values() -> None:
    seen: list[dict[str, Any]] = []

    def tester(values: dict[str, Any]) -> str | None:
        seen.append(values)
        return "连不上"

    endpoint, _ = _endpoint(tester=tester)

    response = endpoint.handle(
        _request("POST", path="settings/test", body={"greeting": "临时", "api_token": "new"})
    )

    assert response is not None
    assert response.body == {"ok": False, "message": "连不上"}
    assert seen[0]["greeting"] == "临时"
    assert seen[0]["api_token"] == "new"
    assert seen[0]["interval"] == 1


def test_settings_test_reports_ok_and_handles_exception() -> None:
    endpoint, _ = _endpoint(tester=lambda values: None)

    ok = endpoint.handle(_request("POST", path="settings/test", body={}))
    assert ok is not None and ok.body == {"ok": True}

    def broken(values: dict[str, Any]) -> str | None:
        raise RuntimeError("boom")

    endpoint, _ = _endpoint(tester=broken)
    failed = endpoint.handle(_request("POST", path="settings/test", body={}))
    assert failed is not None
    assert failed.body == {"ok": False, "message": "测试失败"}


def test_unknown_path_is_left_to_plugin() -> None:
    endpoint, _ = _endpoint()

    assert endpoint.handle(_request("GET", path="status")) is None


def test_json_store_persists_plaintext_and_unknown_values(tmp_path: Path) -> None:
    store = JsonSettingsStore(tmp_path)

    store.put("token", "secret-value")
    store.put("name", "hello")

    assert (tmp_path / "state" / "settings.json").is_file()
    assert "secret-value" in (tmp_path / "state" / "settings.json").read_text(encoding="utf-8")
    assert store.get("token") == "secret-value"
    assert store.get("name") == "hello"

    store.delete("token")
    assert store.get("token") is None


def test_put_rejects_non_object_body() -> None:
    endpoint, _ = _endpoint()

    with pytest.raises(PluginRequestError) as error:
        endpoint.handle(_request("PUT", body=["not", "an", "object"]))

    assert error.value.status == 400
    assert error.value.code == "invalid_setting"


def test_required_text_cannot_be_empty() -> None:
    endpoint, _ = _endpoint(
        (SettingField("title", "标题", "text", required=True),),
        store=MemorySettingsStore({"title": "旧标题"}),
    )

    error = _error(endpoint, {"title": "   "})

    assert "标题" in error.message and "不能为空" in error.message


def test_required_secret_must_be_set() -> None:
    endpoint, _ = _endpoint(
        (SettingField("token", "令牌", "secret", required=True),),
        store=MemorySettingsStore(),
    )

    error = _error(endpoint, {"token": ""})

    assert "令牌" in error.message and "不能为空" in error.message


def test_test_returns_tester_message() -> None:
    endpoint, _ = _endpoint(tester=lambda values: "令牌无效")

    response = endpoint.handle(_request("POST", path="settings/test", body={}))

    assert response is not None
    assert response.body == {"ok": False, "message": "令牌无效"}


def test_test_exception_is_logged_and_hidden(caplog: pytest.LogCaptureFixture) -> None:
    def tester(values: dict[str, Any]) -> str | None:
        raise RuntimeError("SECRET-DETAIL")

    endpoint, _ = _endpoint(tester=tester)

    with caplog.at_level(logging.ERROR):
        response = endpoint.handle(_request("POST", path="settings/test", body={}))

    assert response is not None
    assert response.body == {"ok": False, "message": "测试失败"}
    assert "SECRET-DETAIL" not in str(response.body)
    assert "SECRET-DETAIL" in caplog.text


def test_test_without_tester_is_not_found() -> None:
    endpoint, _ = _endpoint()

    with pytest.raises(PluginRequestError) as error:
        endpoint.handle(_request("POST", path="settings/test", body={}))

    assert error.value.status == 404
    assert error.value.code == "not_found"


def test_settings_rejects_unsupported_methods() -> None:
    endpoint, _ = _endpoint()

    with pytest.raises(PluginRequestError) as settings_error:
        endpoint.handle(_request("DELETE"))
    with pytest.raises(PluginRequestError) as test_error:
        endpoint.handle(_request("GET", path="settings/test"))

    assert settings_error.value.status == 405
    assert test_error.value.status == 405


def test_values_returns_plain_secret_for_plugin_runtime() -> None:
    endpoint, store = _endpoint(
        store=MemorySettingsStore({"greeting": "你好", "interval": 2, "mode": "b"})
    )

    values = endpoint.values()

    assert values == {
        "greeting": "你好",
        "api_token": "stored-token",
        "endpoint": "",
        "interval": 2,
        "enabled": False,
        "mode": "b",
    }
    values["greeting"] = "changed"
    assert store.get("greeting") == "你好"


def test_deferred_tester_can_be_bound_after_provider_exists() -> None:
    endpoint, _ = _endpoint()

    endpoint.bind_tester(lambda values: "已绑定")

    response = endpoint.handle(_request("POST", path="settings/test", body={}))
    assert response is not None
    assert response.body == {"ok": False, "message": "已绑定"}


def test_settings_admin_ui_handles_status_settings_and_404(tmp_path: Path) -> None:
    from iris_plugin_sdk import SettingsAdminUi

    endpoint, store = _endpoint()
    before: list[dict[str, Any]] = []

    class Admin(SettingsAdminUi):
        def settings_updated(self, old: Any) -> None:
            before.append(dict(old))

    admin = Admin(
        endpoint,
        tmp_path / "ui",
        status=lambda: {"token_set": bool(endpoint.values()["api_token"])},
    )

    assert admin.ui_root == tmp_path / "ui"
    status = admin.handle(_request("GET", path="status"))
    assert status.body == {"token_set": True}

    with pytest.raises(PluginRequestError) as method_error:
        admin.handle(_request("POST", path="status"))
    assert method_error.value.status == 405

    saved = admin.handle(_request("PUT", path="settings", body={"greeting": "新"}))
    assert saved.body is not None
    assert store.plain["greeting"] == "新"
    assert before == [endpoint.values() | {"greeting": "hi"}]

    assert admin.handle(_request("GET", path="settings")).body is not None
    with pytest.raises(PluginRequestError) as missing:
        admin.handle(_request("GET", path="other"))
    assert missing.value.status == 404


def test_settings_admin_ui_without_status_returns_404(tmp_path: Path) -> None:
    from iris_plugin_sdk import SettingsAdminUi

    endpoint, _ = _endpoint()
    admin = SettingsAdminUi(endpoint, tmp_path)

    with pytest.raises(PluginRequestError) as error:
        admin.handle(_request("GET", path="status"))
    assert error.value.status == 404
