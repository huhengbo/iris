"""工具注册表（ADR 0030）。

工具是 AI 能做的全部事情：名称、人话文案、参数结构、风险等级、允许的角色和实现。
实现只能调用已有的服务层，以发起者的身份执行；工具里不写 SQL，也不访问 115 网关。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from app.ai.types import ToolSpec
from app.auth.users import User

if TYPE_CHECKING:
    from app.container import AppContainer

# read 只读；reversible_write 可撤销的写；destructive 破坏性写（第一期不开放）
Risk = Literal["read", "reversible_write", "destructive"]
UserRole = Literal["admin", "user"]
ALL_ROLES: frozenset[UserRole] = frozenset({"admin", "user"})
ADMIN_ONLY: frozenset[UserRole] = frozenset({"admin"})


class ToolError(Exception):
    """工具认为这次调用不合理（越出范围、参数语义不对）。消息会原样回给模型，让它纠正。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# 后台任务没有发起用户时使用：管理员角色，编号 0 不对应任何真实用户
SYSTEM_USER = User(0, "system", "admin", False, 0, "")


@dataclass(frozen=True)
class ToolContext:
    """工具执行时的发起者和服务；可见范围等权限由服务层按 user 判断。"""

    user: User
    container: AppContainer
    # 某类任务自带的范围信息（例如匹配建议正在处理哪个作品）；通用工具不使用
    scope: Any = None


@dataclass(frozen=True)
class ToolResult:
    # 回给模型的内容，必须能序列化成 JSON
    content: Any
    # 给用户看的一句话结果（「找到 12 部」），工具调用完成后显示
    summary: str
    # 结果里涉及的条目，客户端据此展示条目卡片
    item_ids: tuple[int, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Tool:
    name: str
    # 给模型看的说明
    description: str
    params: type[BaseModel]
    risk: Risk
    roles: frozenset[UserRole]
    run: Callable[[ToolContext, Any], ToolResult]
    # 进行中的人话文案（「正在搜索资料库…」）
    running: Callable[[Any], str]
    # 写操作需要确认时，给管理员看的一句话
    describe: Callable[[Any], str] | None = None

    def spec(self) -> ToolSpec:
        schema = self.params.model_json_schema()
        schema.pop("title", None)
        return ToolSpec(self.name, self.description, schema)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具重名：{tool.name}")
        if tool.risk == "destructive":
            raise ValueError(f"第一期不开放破坏性工具：{tool.name}")
        if tool.risk != "read" and tool.describe is None:
            raise ValueError(f"写操作工具要提供确认时显示的描述：{tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def available(self, user: User, names: set[str] | None = None) -> list[Tool]:
        """这个用户能用的工具；names 进一步限定场景（例如对话只开放只读工具）。"""
        return [
            tool
            for tool in self._tools.values()
            if user.role in tool.roles and (names is None or tool.name in names)
        ]
