"""插件设置页的公共逻辑：字段声明、校验、保存和测试连接。

数据保存在插件自己的 `data_dir/state/settings.json`，包括密钥在内的值都是明文；宿主不再
提供 secrets/config 能力。设置页仍然由插件自己实现，这里只处理 settings 和 settings/test。
"""

from __future__ import annotations

import logging
import math
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from iris_plugin_sdk.jsonio import read_json, write_json
from iris_plugin_sdk.types import PluginRequest, PluginRequestError, PluginResponse

logger = logging.getLogger(__name__)

SettingKind = Literal["text", "secret", "url", "number", "bool", "select"]

_KINDS: frozenset[str] = frozenset({"text", "secret", "url", "number", "bool", "select"})
_NAME = re.compile(r"[a-z0-9_]+")
DEFAULT_MAX_LENGTH = 1000


class SettingsStore(Protocol):
    """设置值的存储；密钥和普通值存在同一份数据里。"""

    def values(self) -> dict[str, Any]:
        """一次读取整份设置，避免逐个字段重复读文件。"""
        ...

    def get(self, name: str) -> Any: ...

    def put(self, name: str, value: Any) -> None: ...

    def delete(self, name: str) -> None: ...


class JsonSettingsStore(SettingsStore):
    """把设置整体存成 data_dir/state/settings.json；读改写由实例锁串行化。"""

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / "state" / "settings.json"
        self._lock = threading.Lock()

    def values(self) -> dict[str, Any]:
        with self._lock:
            return self._load()

    def get(self, name: str) -> Any:
        return self.values().get(name)

    def put(self, name: str, value: Any) -> None:
        with self._lock:
            values = self._load()
            values[name] = value
            write_json(self._path, values)

    def delete(self, name: str) -> None:
        with self._lock:
            values = self._load()
            if name in values:
                del values[name]
                write_json(self._path, values)

    def _load(self) -> dict[str, Any]:
        value = read_json(self._path, {})
        if not isinstance(value, dict):
            raise ValueError(f"设置文件必须是 JSON 对象：{self._path}")
        return value


def _invalid(item: SettingField, reason: str) -> PluginRequestError:
    """字段错误统一带上 label，页面可以直接展示。"""
    return PluginRequestError(f"{item.label}：{reason}", status=400, code="invalid_setting")


@dataclass(frozen=True)
class SettingField:
    """设置页的一项。secret 类型的值不会回显给页面，但和普通值存在同一份明文 JSON 里。"""

    name: str
    label: str
    kind: SettingKind
    help: str = field(default="", kw_only=True)
    required: bool = field(default=False, kw_only=True)
    default: Any = field(default=None, kw_only=True)
    min: float | None = field(default=None, kw_only=True)
    max: float | None = field(default=None, kw_only=True)
    options: tuple[str, ...] = field(default=(), kw_only=True)
    max_length: int = field(default=DEFAULT_MAX_LENGTH, kw_only=True)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME.fullmatch(self.name):
            raise ValueError("设置字段名只能包含小写字母、数字和下划线")
        if not isinstance(self.label, str) or not self.label:
            raise ValueError("设置字段的标题不能为空")
        if self.kind not in _KINDS:
            raise ValueError(f"未知的设置字段类型：{self.kind}")
        if not isinstance(self.help, str):
            raise ValueError("设置字段的帮助文字必须是文本")
        if not isinstance(self.required, bool):
            raise ValueError("设置字段的 required 必须是布尔值")
        if not isinstance(self.max_length, int) or isinstance(self.max_length, bool):
            raise ValueError("设置字段的 max_length 必须是正整数")
        if self.max_length <= 0:
            raise ValueError("设置字段的 max_length 必须是正整数")
        if self.kind == "select":
            if not self.options:
                raise ValueError("select 字段必须提供 options")
            if any(not isinstance(option, str) or not option for option in self.options):
                raise ValueError("select 的选项必须是非空文本")
            if len(set(self.options)) != len(self.options):
                raise ValueError("select 的选项不能重复")
        elif self.options:
            raise ValueError("只有 select 字段可以提供 options")
        if self.kind == "number":
            for name, value in (("min", self.min), ("max", self.max)):
                if value is not None and (
                    not isinstance(value, (int, float)) or isinstance(value, bool)
                ):
                    raise ValueError(f"number 字段的 {name} 必须是数字")
            if self.min is not None and self.max is not None and self.min > self.max:
                raise ValueError("number 字段的 min 不能大于 max")
        elif self.min is not None or self.max is not None:
            raise ValueError("只有 number 字段可以设置 min 和 max")
        self._validate_default()

    def _validate_default(self) -> None:
        if self.default is None:
            return
        if self.kind == "secret":
            raise ValueError("secret 字段不能有默认值")
        if self.kind == "bool":
            if not isinstance(self.default, bool):
                raise ValueError("bool 字段的默认值必须是布尔值")
            return
        if self.kind == "number":
            if not isinstance(self.default, (int, float)) or isinstance(self.default, bool):
                raise ValueError("number 字段的默认值必须是数字")
            if self.min is not None and self.default < self.min:
                raise ValueError("number 字段的默认值不能小于 min")
            if self.max is not None and self.default > self.max:
                raise ValueError("number 字段的默认值不能大于 max")
            return
        if not isinstance(self.default, str):
            raise ValueError(f"{self.kind} 字段的默认值必须是文本")
        if len(self.default) > self.max_length:
            raise ValueError("设置字段的默认值超过 max_length")
        if self.kind == "select" and self.default not in self.options:
            raise ValueError("select 字段的默认值必须在 options 内")
        if self.kind == "url":
            reason = _url_reason(self.default)
            if reason is not None:
                raise ValueError(reason)


def _url_reason(value: str) -> str | None:
    """只接受完整的 http/https 地址；插件自己再决定路径和查询参数。"""
    if re.search(r"\s", value):
        return "地址不能包含空白字符"
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "地址格式不正确"
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "只允许 http 或 https 地址"
    return None


def _check_url(item: SettingField, value: str) -> None:
    reason = _url_reason(value)
    if reason is not None:
        raise _invalid(item, reason)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _empty(item: SettingField, value: Any) -> bool:
    """required 判断：secret 未设置、文本为空、数字或选项为 None 都算空。"""
    if item.kind == "secret":
        return not value
    if item.kind == "bool":
        return False
    if item.kind == "number":
        return value is None
    return not value


@dataclass
class SettingsEndpoint:
    """插件设置接口。handle 只处理 settings 和 settings/test，其他路径交给插件自己。"""

    fields: tuple[SettingField, ...]
    store: SettingsStore = field(kw_only=True)
    tester: Callable[[dict[str, Any]], str | None] | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        names = [item.name for item in self.fields]
        if len(set(names)) != len(names):
            raise ValueError("设置字段名不能重复")

    def bind_tester(self, tester: Callable[[dict[str, Any]], str | None]) -> None:
        """provider 构造完成后再绑定测试连接；避免 plugin 里用 holder 字典绕循环依赖。"""
        self.tester = tester

    def handle(self, request: PluginRequest) -> PluginResponse | None:
        if request.path == "settings":
            if request.method == "GET":
                return PluginResponse(self._page())
            if request.method == "PUT":
                self._update(_body(request))
                return PluginResponse(self._page())
            raise PluginRequestError("不支持的请求方法", status=405, code="method_not_allowed")
        if request.path == "settings/test":
            if request.method != "POST":
                raise PluginRequestError("不支持的请求方法", status=405, code="method_not_allowed")
            if self.tester is None:
                raise PluginRequestError("这个插件不支持测试连接", status=404, code="not_found")
            return PluginResponse(self._test(_body(request)))
        return None

    def values(self) -> dict[str, Any]:
        """插件运行时读取的当前设置；secret 返回明文，只供插件内部使用。"""
        stored = self.store.values()
        return {item.name: self._value(item, stored.get(item.name)) for item in self.fields}

    def _value(self, item: SettingField, value: Any) -> Any:
        if item.kind == "secret":
            return value if isinstance(value, str) and value else None
        if item.kind in ("text", "url"):
            return value if isinstance(value, str) else (item.default or "")
        if item.kind == "bool":
            return value if isinstance(value, bool) else (item.default or False)
        if item.kind == "number":
            return value if _is_number(value) else item.default
        return value if isinstance(value, str) and value in item.options else item.default

    def _default(self, item: SettingField) -> Any:
        if item.kind == "secret":
            return None
        if item.kind == "bool":
            return item.default or False
        if item.kind == "number":
            return item.default
        return item.default if item.default is not None else ""

    def _page(self) -> dict[str, Any]:
        """GET/PUT 的响应；secret 只告诉页面是否已设置，绝不回显。"""
        values = self.values()
        for item in self.fields:
            if item.kind == "secret":
                values[item.name] = {"set": bool(values[item.name])}
        return {
            "fields": [self._describe(item) for item in self.fields],
            "values": values,
            "testable": self.tester is not None,
        }

    def _describe(self, item: SettingField) -> dict[str, Any]:
        return {
            "name": item.name,
            "label": item.label,
            "kind": item.kind,
            "help": item.help,
            "required": item.required,
            "default": item.default,
            "min": item.min,
            "max": item.max,
            "options": list(item.options),
            "max_length": item.max_length,
        }

    def _update(self, body: Mapping[str, Any]) -> None:
        updates: dict[str, Any] = {}
        deletes: set[str] = set()
        secrets: dict[str, str | None] = {}
        stored = self.store.values()
        for item in self.fields:
            if item.name not in body:
                continue
            raw = body[item.name]
            if item.kind == "secret":
                # 不传和空字符串都表示保持不变，null 表示清除
                if raw is None:
                    secrets[item.name] = None
                elif not isinstance(raw, str):
                    raise _invalid(item, "必须是文本")
                elif raw != "":
                    if len(raw) > item.max_length:
                        raise _invalid(item, f"不能超过 {item.max_length} 个字符")
                    secrets[item.name] = raw
            elif raw is None:
                # 非密钥字段传 null 表示恢复默认值
                deletes.add(item.name)
            else:
                updates[item.name] = self._normalize(item, raw)
        for item in self.fields:
            if not item.required:
                continue
            if item.kind == "secret":
                value = secrets[item.name] if item.name in secrets else stored.get(item.name)
            elif item.name in updates:
                value = updates[item.name]
            elif item.name in deletes:
                value = self._default(item)
            else:
                value = self._value(item, stored.get(item.name))
            if _empty(item, value):
                raise _invalid(item, "不能为空")
        for name in deletes:
            self.store.delete(name)
        for name, value in updates.items():
            self.store.put(name, value)
        for name, value in secrets.items():
            if value is None:
                self.store.delete(name)
            else:
                self.store.put(name, value)
        logger.info("插件设置已更新：%s", ", ".join(item.name for item in self.fields))

    def _normalize(self, item: SettingField, raw: Any) -> Any:
        """校验一个非 secret 字段，返回规范化后的值。"""
        if item.kind == "bool":
            if not isinstance(raw, bool):
                raise _invalid(item, "必须是布尔值")
            return raw
        if item.kind == "number":
            if not _is_number(raw):
                raise _invalid(item, "必须是数字")
            assert isinstance(raw, (int, float))
            if not math.isfinite(raw):
                raise _invalid(item, "必须是有限数字")
            if item.min is not None and raw < item.min:
                raise _invalid(item, f"不能小于 {item.min}")
            if item.max is not None and raw > item.max:
                raise _invalid(item, f"不能大于 {item.max}")
            return raw
        if not isinstance(raw, str):
            raise _invalid(item, "必须是文本")
        value = raw.strip()
        if len(value) > item.max_length:
            raise _invalid(item, f"不能超过 {item.max_length} 个字符")
        if item.kind == "url" and value:
            _check_url(item, value)
        if item.kind == "select" and value not in item.options:
            raise _invalid(item, "不是可选项")
        return value

    def _test(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """合并已保存值和请求里未保存的值；secret 不传时沿用已保存的值。"""
        values = self.values()
        for item in self.fields:
            if item.name not in body:
                continue
            raw = body[item.name]
            if item.kind == "secret":
                if raw is None:
                    values[item.name] = None
                elif not isinstance(raw, str):
                    raise _invalid(item, "必须是文本")
                elif raw != "":
                    if len(raw) > item.max_length:
                        raise _invalid(item, f"不能超过 {item.max_length} 个字符")
                    values[item.name] = raw
            elif raw is None:
                values[item.name] = self._default(item)
            else:
                values[item.name] = self._normalize(item, raw)
        assert self.tester is not None
        try:
            message = self.tester(values)
        except Exception:
            logger.exception("插件设置测试失败")
            return {"ok": False, "message": "测试失败"}
        if message is None:
            return {"ok": True}
        return {"ok": False, "message": message}


class SettingsAdminUi:
    """设置型管理页的公共骨架：status、settings、settings/test 和 404 兜底。

    插件自己的额外路径可以在子类里覆盖 handle；settings PUT 成功后调用
    ``settings_updated(before)``，子类据此刷新 provider 或清掉连接校验结果。
    """

    def __init__(
        self,
        settings: SettingsEndpoint,
        ui_root: Path,
        *,
        status: Callable[[], Mapping[str, Any]] | None = None,
    ) -> None:
        self._settings = settings
        self._ui_root = ui_root
        self._status = status

    @property
    def ui_root(self) -> Path:
        return self._ui_root

    def handle(self, request: PluginRequest) -> PluginResponse:
        if request.path == "status":
            if request.method != "GET":
                raise PluginRequestError("不支持的请求方法", status=405, code="method_not_allowed")
            if self._status is None:
                raise PluginRequestError("接口不存在", status=404, code="not_found")
            return PluginResponse(dict(self._status()))
        if request.path == "settings" and request.method == "PUT":
            before = self._settings.values()
            response = self._settings.handle(request)
            if response is None:
                raise PluginRequestError("接口不存在", status=404, code="not_found")
            self.settings_updated(before)
            return response
        response = self._settings.handle(request)
        if response is not None:
            return response
        raise PluginRequestError("接口不存在", status=404, code="not_found")

    def settings_updated(self, before: Mapping[str, Any]) -> None:
        """设置保存成功后的钩子；before 是保存前的值。"""


def _body(request: PluginRequest) -> Mapping[str, Any]:
    body = request.body
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise PluginRequestError("请求体必须是 JSON 对象", status=400, code="invalid_setting")
    return body
