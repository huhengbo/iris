"""设置页公共工具在插件自己的 state 上的行为。

对应后端 ``tests/test_plugin_settings_page.py``：字段声明、校验、密钥处理和测试连接。
差别只是存储从宿主的 config/secrets 换成 ``JsonSettingsStore``（``state/settings.json``）。
"""

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


def _endpoint(
    tmp_path: Path,
    fields: tuple[SettingField, ...] = DEFAULT_FIELDS,
    *,
    values: dict[str, Any] | None = None,
    tester: Any = None,
) -> tuple[SettingsEndpoint, JsonSettingsStore]:
    store = JsonSettingsStore(tmp_path)
    saved: dict[str, Any] = {"api_token": "stored-token", "other": "keep"}
    saved.update(values or {})
    for name, value in saved.items():
        store.put(name, value)
    return (
        SettingsEndpoint(fields, store=store, tester=cast(Any, tester)),
        store,
    )


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


def test_settings_endpoint_rejects_duplicate_names(tmp_path: Path) -> None:
    field = SettingField("greeting", "问候语", "text")
    with pytest.raises(ValueError, match="不能重复"):
        SettingsEndpoint((field, field), store=JsonSettingsStore(tmp_path))


def test_get_hides_secret_and_describes_fields(tmp_path: Path) -> None:
    endpoint, _ = _endpoint(
        tmp_path,
        values={"greeting": "你好", "interval": 2, "mode": "b"},
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


def test_put_saves_plain_values_and_keeps_unknown_config(tmp_path: Path) -> None:
    endpoint, store = _endpoint(tmp_path)

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

    saved = (tmp_path / "state" / "settings.json").read_text(encoding="utf-8")
    assert '"other": "keep"' in saved
    assert page["values"]["greeting"] == "hello"
    assert page["values"]["interval"] == 2.5
    assert page["values"]["api_token"] == {"set": True}


def test_put_leaves_missing_fields_unchanged(tmp_path: Path) -> None:
    endpoint, _ = _endpoint(tmp_path, values={"greeting": "旧问候", "interval": 3})

    _page(endpoint, "PUT", {"mode": "b"})

    page = _page(endpoint)
    assert page["values"]["greeting"] == "旧问候"
    assert page["values"]["interval"] == 3
    assert page["values"]["mode"] == "b"


def test_put_null_restores_default_and_empty_url_clears(tmp_path: Path) -> None:
    endpoint, _ = _endpoint(
        tmp_path, values={"interval": 3, "endpoint": "https://a.example", "extra": 1}
    )

    page = _page(endpoint, "PUT", {"interval": None, "endpoint": ""})

    assert page["values"]["interval"] == 1
    assert page["values"]["endpoint"] == ""
    saved = (tmp_path / "state" / "settings.json").read_text(encoding="utf-8")
    assert '"extra": 1' in saved


def test_required_field_cannot_be_reset_to_empty_default(tmp_path: Path) -> None:
    fields = (SettingField("endpoint", "接口地址", "url", required=True),)
    endpoint, _ = _endpoint(tmp_path, fields, values={"endpoint": "https://a.example"})

    error = _error(endpoint, {"endpoint": None})

    assert "不能为空" in error.message


def test_secret_empty_or_missing_keeps_saved_value(tmp_path: Path) -> None:
    endpoint, _ = _endpoint(tmp_path)

    assert _page(endpoint, "PUT", {"api_token": ""})["values"]["api_token"] == {"set": True}
    assert _page(endpoint, "PUT", {})["values"]["api_token"] == {"set": True}


def test_secret_null_clears_value(tmp_path: Path) -> None:
    endpoint, _ = _endpoint(tmp_path)

    page = _page(endpoint, "PUT", {"api_token": None})

    assert page["values"]["api_token"] == {"set": False}


def test_secret_new_value_is_saved_and_not_echoed(tmp_path: Path) -> None:
    endpoint, _ = _endpoint(tmp_path)

    page = _page(endpoint, "PUT", {"api_token": "new-token"})

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
def test_put_validates_field_types(
    tmp_path: Path, body: dict[str, Any], label: str, reason: str
) -> None:
    endpoint, _ = _endpoint(tmp_path)

    error = _error(endpoint, body)

    assert error.message.startswith(f"{label}：")
    assert reason in error.message


def test_put_does_not_apply_anything_when_validation_fails(tmp_path: Path) -> None:
    endpoint, _ = _endpoint(tmp_path, values={"greeting": "旧问候"})

    _error(endpoint, {"greeting": "x" * 11})

    assert _page(endpoint)["values"]["greeting"] == "旧问候"


def test_test_merges_saved_and_unsaved_values_without_saving(tmp_path: Path) -> None:
    seen: list[dict[str, Any]] = []

    def tester(values: dict[str, Any]) -> str | None:
        seen.append(values)
        return None

    endpoint, _ = _endpoint(tmp_path, values={"greeting": "旧问候"}, tester=tester)

    response = endpoint.handle(
        _request(
            "POST",
            path="settings/test",
            body={"greeting": "新问候", "interval": 3, "api_token": "unsaved"},
        )
    )

    assert response is not None
    assert response.body == {"ok": True}
    assert seen[0]["greeting"] == "新问候"
    assert seen[0]["api_token"] == "unsaved"
    assert _page(endpoint)["values"]["greeting"] == "旧问候"


def test_test_exception_is_logged_and_hidden(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def tester(values: dict[str, Any]) -> str | None:
        raise RuntimeError("SECRET-DETAIL")

    endpoint, _ = _endpoint(tmp_path, tester=tester)

    with caplog.at_level(logging.ERROR):
        response = endpoint.handle(_request("POST", path="settings/test", body={}))

    assert response is not None
    assert response.body == {"ok": False, "message": "测试失败"}
    assert "SECRET-DETAIL" not in str(response.body)
    assert "SECRET-DETAIL" in caplog.text


def test_unknown_path_is_left_to_plugin(tmp_path: Path) -> None:
    endpoint, _ = _endpoint(tmp_path)

    assert endpoint.handle(_request("GET", path="other")) is None


def test_values_returns_plain_secret_for_plugin_runtime(tmp_path: Path) -> None:
    endpoint, _ = _endpoint(tmp_path, values={"greeting": "你好", "interval": 2, "mode": "b"})

    values = endpoint.values()

    assert values["api_token"] == "stored-token"
    assert values["greeting"] == "你好"
