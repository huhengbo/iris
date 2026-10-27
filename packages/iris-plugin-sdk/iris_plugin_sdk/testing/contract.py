"""资源来源契约测试套件：插件自己的测试继承 ResourceSourceContract 即可复用。

子类只需要提供测试用的来源实例、扫描根目录和期望路径。基类不假设来源是本地目录还是
网盘；所有断言都只通过 ResourceSource 协议观察。
"""

from __future__ import annotations

import pytest

from iris_plugin_sdk.types import (
    PlaybackTarget,
    ResourceEntry,
    ResourceListing,
    ResourceNotFound,
    ResourceProvider,
    ResourceSource,
    ResourceUnavailable,
)


class ResourceSourceContract:
    """资源来源的契约测试。子类命名为 Test*，pytest 会收集继承来的 test_* 方法。"""

    def make_source(self) -> ResourceSource:
        raise NotImplementedError

    def make_provider(self) -> ResourceProvider | None:
        """需要检查 capabilities 时提供；默认返回 None。"""
        return None

    def expected_paths(self) -> set[str]:
        """scan(根目录) 必须返回的全部 relative_path。"""
        raise NotImplementedError

    def root_locator(self) -> str | None:
        """来源根目录的定位符；协议统一用 None。"""
        return None

    def supports_known(self) -> bool:
        provider = self.make_provider()
        return provider is not None and "known_scan" in provider.capabilities

    def supports_delete(self) -> bool:
        provider = self.make_provider()
        return provider is not None and "delete" in provider.capabilities

    def delete_target(self) -> str | None:
        """可删除的临时文件定位符；子类不提供时跳过删除测试。"""
        return None

    def unselectable_locator(self) -> str | None:
        """来源不允许作为资料库目录的目录定位符；子类不提供时跳过。"""
        return None

    @property
    def source(self) -> ResourceSource:
        cached = getattr(self, "_contract_source", None)
        if cached is None:
            cached = self.make_source()
            self._contract_source = cached
        return cached

    def _entries(self) -> list[ResourceEntry]:
        return list(self.source.scan(self.root_locator()))

    def _video(self) -> ResourceEntry:
        for entry in self._entries():
            if entry.kind == "video" and not entry.is_dir:
                return entry
        pytest.skip("契约测试需要一个视频条目")

    def _nested_listing(self) -> ResourceListing:
        pending: list[str | None] = [self.root_locator()]
        seen: set[str | None] = set()
        while pending and len(seen) < 64:
            locator = pending.pop(0)
            if locator in seen:
                continue
            seen.add(locator)
            listing = self.source.browse(locator)
            if listing.ancestors:
                return listing
            pending.extend(
                entry.locator
                for entry in listing.entries
                if entry.is_dir and entry.locator not in seen
            )
        pytest.skip("测试来源没有嵌套目录，无法验证 ancestors")

    # ---- 浏览 ----

    def test_browse_returns_direct_children(self) -> None:
        listing = self.source.browse(self.root_locator())

        assert isinstance(listing, ResourceListing)
        # 根目录用 None 请求，但返回的 locator 是来源自己的根标识（例如 "0"）。
        assert isinstance(listing.locator, str)
        assert listing.path.startswith("/")
        for entry in listing.entries:
            assert isinstance(entry, ResourceEntry)
            assert entry.relative_path
            assert "/" not in entry.relative_path
            assert entry.kind in ("video", "subtitle", "nfo")

    def test_browse_nested_ancestors(self) -> None:
        listing = self._nested_listing()

        assert listing.ancestors
        for locator, name in listing.ancestors:
            assert isinstance(locator, str) and locator
            assert isinstance(name, str) and name
        assert listing.ancestors[-1][0] == listing.locator
        assert listing.ancestors[-1][1] == listing.path.rstrip("/").rsplit("/", 1)[-1]

    # ---- 扫描 ----

    def test_scan_is_complete(self) -> None:
        entries = self._entries()

        assert {entry.relative_path for entry in entries} == self.expected_paths()
        for entry in entries:
            assert not entry.relative_path.startswith("/")
            assert "\\" not in entry.relative_path

    def test_scan_known_reuses_entries(self) -> None:
        if not self.supports_known():
            pytest.skip("来源没有声明 known_scan")
        first = {entry.relative_path: entry for entry in self._entries()}

        second = self.source.scan(self.root_locator(), known=first)

        assert {entry.relative_path for entry in second} == self.expected_paths()
        assert {entry.relative_path: entry.locator for entry in second} == {
            path: entry.locator for path, entry in first.items()
        }

    # ---- 读取 ----

    def test_read_respects_limit(self) -> None:
        entry = self._video()
        content = self.source.read(entry.locator, 16 * 1024 * 1024)
        if len(content) <= 1:
            pytest.skip("契约测试需要一个非空视频文件")
        assert content

        with pytest.raises(ResourceUnavailable):
            self.source.read(entry.locator, 1)

    # ---- 播放 ----

    def test_resolve_returns_one_target(self) -> None:
        entry = self._video()

        target = self.source.resolve(entry.locator, "IrisContract/1.0")

        assert isinstance(target, PlaybackTarget)
        assert (target.url is None) != (target.path is None)
        if target.url is not None:
            assert target.url.startswith(("http://", "https://"))

    # ---- 删除 ----

    def test_delete_removes_entry(self) -> None:
        if not self.supports_delete():
            pytest.skip("来源没有声明 delete")
        locator = self.delete_target()
        if locator is None:
            pytest.skip("子类没有提供可删除的临时文件")

        self.source.delete(locator)

        with pytest.raises(ResourceNotFound):
            self.source.read(locator, 1024)

    # ---- 异常 ----

    def test_missing_locator_is_not_found(self) -> None:
        missing = "iris-contract-missing-locator"

        with pytest.raises(ResourceNotFound):
            self.source.browse(missing)
        with pytest.raises(ResourceNotFound):
            self.source.scan(missing)
        with pytest.raises(ResourceNotFound):
            self.source.read(missing, 1024)

    def test_unselectable_directory_is_flagged(self) -> None:
        locator = self.unselectable_locator()
        if locator is None:
            pytest.skip("子类没有提供不允许作为资料库目录的位置")

        listing = self.source.browse(locator)

        assert listing.selectable is False
