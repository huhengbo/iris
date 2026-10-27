"""直接调用 115 来源，跑 SDK 的资源协议契约测试套件（录制网关响应）。"""

from __future__ import annotations

from typing import Any

import pytest
from iris_plugin_sdk import ResourceProvider, ResourceSource
from iris_plugin_sdk.testing import ResourceSourceContract

from tests.conftest import FakeClock, Stack, make_stack
from tests.fake_gateway import CONTRACT_PATHS, VALID_COOKIE, FakeCloud115, build_contract_tree


class Test115SourceContract(ResourceSourceContract):
    stack: Stack
    ids: dict[str, str]

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path: Any) -> None:
        self.stack = make_stack(tmp_path, clock=FakeClock())
        self.ids = build_contract_tree(self.stack.gateway)
        self.stack.credentials.save(VALID_COOKIE, None, "家庭影院")

    def make_source(self) -> ResourceSource:
        return self.stack.provider.open(1, {})

    def make_provider(self) -> ResourceProvider:
        return self.stack.provider

    def expected_paths(self) -> set[str]:
        return set(CONTRACT_PATHS)

    def delete_target(self) -> str:
        # 删除用的是扫描拿到的文件定位符（pickcode）
        for entry in self.source.scan(None):
            if entry.relative_path == "删除我.mkv":
                return entry.locator
        raise AssertionError("契约测试的删除目标不存在")

    def unselectable_locator(self) -> str:
        return self.ids["scratch"]


def test_contract_tree_is_stable() -> None:
    """父子进程构建同一棵树时节点 ID 相同；契约测试的删除目标据此计算。"""
    first = FakeCloud115(FakeClock())
    second = FakeCloud115(FakeClock())

    assert build_contract_tree(first) == build_contract_tree(second)
