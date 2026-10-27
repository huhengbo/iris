from __future__ import annotations

import os
from pathlib import Path

import pytest
from iris_plugin_sdk import (
    InvalidSourceConfig,
    ResourceNotFound,
    ResourceUnavailable,
    SourceStatus,
)

from iris_plugin_source_local.plugin import LocalResourceProvider


def _provider(media: Path, root: str | None = None) -> tuple[LocalResourceProvider, dict[str, str]]:
    provider = LocalResourceProvider(media, "source.local")
    return provider, provider.validate_config({"root": str(media) if root is None else root})


def test_local_source_scans_reads_and_resolves_inside_media_dir(tmp_path: Path) -> None:
    media = tmp_path / "media"
    movie = media / "movies" / "Film (2024)"
    movie.mkdir(parents=True)
    (movie / "Film.mkv").write_bytes(b"video")
    (movie / "Film.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n")
    (movie / "Film.nfo").write_text("<movie />")
    (movie / ".hidden.mkv").write_bytes(b"x")
    (movie / "notes.txt").write_text("x")
    provider, config = _provider(media, "movies")
    assert config == {"root": str((media / "movies").resolve())}
    source = provider.open(1, config)

    entries = {entry.relative_path: entry for entry in source.scan("Film (2024)")}

    # 相对路径相对于扫描的目录，locator 相对于来源根目录
    assert set(entries) == {"Film.mkv", "Film.srt", "Film.nfo"}
    assert entries["Film.mkv"].locator == "Film (2024)/Film.mkv"
    assert entries["Film.mkv"].size == 5
    assert source.read("Film (2024)/Film.nfo", 100) == b"<movie />"
    target = source.resolve("Film (2024)/Film.mkv", "test")
    assert target.url is None and target.path == movie / "Film.mkv"
    listing = source.browse(None)
    assert listing.path == "/"
    assert listing.locator == ""
    assert [(entry.locator, entry.relative_path) for entry in listing.entries] == [
        ("Film (2024)", "Film (2024)")
    ]
    assert source.browse("Film (2024)").path == "/Film (2024)"


def test_scan_fails_when_stat_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    media = tmp_path / "media"
    media.mkdir()
    (media / "good.mkv").write_bytes(b"x")
    (media / "broken.mkv").write_bytes(b"x")
    original_stat = Path.stat

    def fail_on_broken(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if path.name == "broken.mkv" and follow_symlinks:
            raise OSError(5, "Input/output error")
        return original_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", fail_on_broken)
    provider, config = _provider(media)
    source = provider.open(1, config)

    # 单个文件 stat 失败时不能跳过，否则宿主会把它当成已删除
    with pytest.raises(ResourceUnavailable, match="broken.mkv"):
        source.scan(None)


def test_local_source_rejects_escape_and_reports_missing(tmp_path: Path) -> None:
    media = tmp_path / "media"
    media.mkdir()
    (tmp_path / "outside.mkv").write_bytes(b"x")
    provider, config = _provider(media)
    source = provider.open(1, config)

    with pytest.raises(ResourceNotFound, match="越界"):
        source.read("../outside.mkv", 100)
    with pytest.raises(ResourceNotFound, match="越界"):
        source.read(str(tmp_path / "outside.mkv"), 100)
    with pytest.raises(ResourceNotFound, match="越界"):
        source.resolve("../outside.mkv", "test")
    with pytest.raises(ResourceNotFound, match="越界"):
        source.browse("../outside")
    with pytest.raises(ResourceNotFound):
        source.resolve("missing.mkv", "test")
    with pytest.raises(ResourceNotFound):
        source.browse("missing")
    with pytest.raises(ResourceUnavailable):
        source.delete("anything.mkv")


def test_scan_and_browse_use_sdk_media_kinds(tmp_path: Path) -> None:
    media = tmp_path / "media"
    media.mkdir()
    expected = {
        "movie.m2ts": "video",
        "movie.iso": "video",
        "movie.rmvb": "video",
        "movie.wmv": "video",
        "movie.flv": "video",
        "movie.vob": "video",
        "movie.idx": "subtitle",
        "movie.smi": "subtitle",
        "movie.nfo": "nfo",
    }
    for name in expected:
        (media / name).write_bytes(b"x")
    (media / "notes.txt").write_text("x")
    (media / "poster.jpg").write_bytes(b"x")
    (media / "extras").mkdir()
    provider, config = _provider(media)
    source = provider.open(1, config)

    assert {entry.relative_path: entry.kind for entry in source.scan(None)} == expected

    browsed = {entry.relative_path: entry for entry in source.browse(None).entries}
    assert set(browsed) == set(expected) | {"extras"}
    assert browsed["extras"].is_dir and browsed["extras"].kind == "video"
    assert browsed["movie.m2ts"].kind == "video"
    assert browsed["movie.smi"].kind == "subtitle"
    assert "notes.txt" not in browsed
    assert "poster.jpg" not in browsed


def test_local_source_config_is_relative_to_media_dir(tmp_path: Path) -> None:
    media = tmp_path / "media"
    (media / "movies").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    provider = LocalResourceProvider(media, "source.local")

    movies = str((media / "movies").resolve())
    assert provider.validate_config({"root": "movies"}) == {"root": movies}
    assert provider.validate_config({"root": str(media / "movies")}) == {"root": movies}
    assert provider.validate_config({"root": str(media)}) == {"root": str(media.resolve())}
    with pytest.raises(InvalidSourceConfig, match="请填写服务器目录"):
        provider.validate_config({"root": ""})
    with pytest.raises(InvalidSourceConfig, match="请填写服务器目录"):
        provider.validate_config({"root": "   "})
    with pytest.raises(InvalidSourceConfig, match="之内"):
        provider.validate_config({"root": "../outside"})
    with pytest.raises(InvalidSourceConfig, match="之内"):
        provider.validate_config({"root": str(outside)})
    with pytest.raises(InvalidSourceConfig, match="不存在"):
        provider.validate_config({"root": "nope"})
    with pytest.raises(InvalidSourceConfig, match="请填写服务器目录"):
        provider.validate_config({"root": 1})
    with pytest.raises(ResourceUnavailable):
        provider.open(1, {"root": "../outside"})


def test_local_source_requires_media_dir(tmp_path: Path) -> None:
    provider = LocalResourceProvider(None, "source.local")

    with pytest.raises(ResourceUnavailable, match="本地媒体目录"):
        provider.validate_config({"root": "/media"})
    with pytest.raises(ResourceUnavailable, match="本地媒体目录"):
        provider.open(1, {"root": "/media"})
    with pytest.raises(ResourceUnavailable, match="本地媒体目录"):
        provider.check({"root": "/media"})
    status = provider.status({"root": "/media"})
    assert status.state == "failed"
    assert "本地媒体目录" in (status.message or "")


def test_local_source_ignores_symlink_escape(tmp_path: Path) -> None:
    media = tmp_path / "media"
    media.mkdir()
    outside = tmp_path / "outside.mkv"
    outside.write_bytes(b"x")
    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()
    (media / "inside.mkv").write_bytes(b"y")
    (media / "link.mkv").symlink_to(outside)
    (media / "link-dir").symlink_to(outside_dir)
    provider, config = _provider(media)
    source = provider.open(1, config)

    assert [entry.relative_path for entry in source.browse(None).entries] == ["inside.mkv"]
    assert [entry.relative_path for entry in source.scan(None)] == ["inside.mkv"]
    with pytest.raises(ResourceNotFound, match="符号链接"):
        source.read("link.mkv", 100)
    with pytest.raises(InvalidSourceConfig, match="之内"):
        provider.validate_config({"root": "link-dir"})


def test_symlinks_are_never_followed_even_inside_root(tmp_path: Path) -> None:
    media = tmp_path / "media"
    movies = media / "movies"
    movies.mkdir(parents=True)
    (movies / "inside.mkv").write_bytes(b"x")
    (media / "link.mkv").symlink_to(movies / "inside.mkv")
    (media / "link-dir").symlink_to(movies)
    provider, config = _provider(media)
    source = provider.open(1, config)

    # 指向根目录内部的符号链接同样不跟随
    assert [entry.relative_path for entry in source.scan(None)] == ["movies/inside.mkv"]
    listing = source.browse(None)
    assert [(entry.relative_path, entry.is_dir) for entry in listing.entries] == [("movies", True)]
    for locator in ("link.mkv", "link-dir", "link-dir/inside.mkv"):
        with pytest.raises(ResourceNotFound, match="符号链接"):
            source.browse(locator)
        with pytest.raises(ResourceNotFound, match="符号链接"):
            source.scan(locator)
        with pytest.raises(ResourceNotFound, match="符号链接"):
            source.read(locator, 100)
        with pytest.raises(ResourceNotFound, match="符号链接"):
            source.resolve(locator, "test")


def test_unmounted_local_root_is_unavailable_not_empty(tmp_path: Path) -> None:
    media = tmp_path / "media"
    (media / "disk").mkdir(parents=True)
    provider, config = _provider(media, "disk")
    source = provider.open(1, config)
    (media / "disk").rmdir()

    # 挂载丢失时报错，不能当成空目录，否则同步会把文件都标成失效
    with pytest.raises(ResourceUnavailable):
        source.scan(None)
    assert provider.status(config).state == "failed"


def test_local_status_reports_missing_and_ready(tmp_path: Path) -> None:
    media = tmp_path / "media"
    media.mkdir()
    provider = LocalResourceProvider(media, "source.local")

    assert provider.status({"root": str(media)}) == SourceStatus("ok")
    assert provider.status({}).state == "failed"
    assert provider.status({"root": str(media / "missing")}).state == "failed"
