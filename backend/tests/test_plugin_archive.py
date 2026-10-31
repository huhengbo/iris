from __future__ import annotations

import stat
import zipfile
from pathlib import Path

import pytest

from app.plugins.archive import (
    ArchiveError,
    ArchiveTooLarge,
    PackageError,
    check_archive,
    check_package,
    extract_archive,
)
from tests.repository_server import make_plugin_zip


def _write_zip(path: Path, entries: list[tuple[zipfile.ZipInfo, bytes]]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for info, data in entries:
            archive.writestr(info, data)
    return path


def _required() -> list[tuple[zipfile.ZipInfo, bytes]]:
    return [
        (zipfile.ZipInfo("plugin.json"), b"{}"),
        (zipfile.ZipInfo("pyproject.toml"), b"[project]"),
        (zipfile.ZipInfo("uv.lock"), b"version = 1"),
    ]


def test_valid_package_extracts_with_fixed_permissions(tmp_path: Path) -> None:
    archive = make_plugin_zip(tmp_path / "package.zip")
    check_archive(archive)
    destination = tmp_path / "extracted"
    extract_archive(archive, destination)
    assert (destination / "plugin.json").is_file()
    assert stat.S_IMODE((destination / "ui" / "index.html").stat().st_mode) == 0o644
    manifest = check_package(destination)
    assert manifest.id == "org.example.fake"
    assert (destination / "ui" / "index.html").is_file()


def test_path_traversal_is_rejected(tmp_path: Path) -> None:
    archive = _write_zip(
        tmp_path / "traversal.zip",
        [*_required(), (zipfile.ZipInfo("../evil.txt"), b"x")],
    )
    with pytest.raises(ArchiveError):
        check_archive(archive)
    with pytest.raises(ArchiveError):
        extract_archive(archive, tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()


@pytest.mark.parametrize("name", ["/absolute.txt", "C:/drive.txt", "a\\b.txt", "a//b.txt"])
def test_unsafe_names_are_rejected(tmp_path: Path, name: str) -> None:
    archive = _write_zip(
        tmp_path / "unsafe.zip",
        [*_required(), (zipfile.ZipInfo(name), b"x")],
    )
    with pytest.raises(ArchiveError):
        check_archive(archive)


def test_duplicate_names_are_rejected(tmp_path: Path) -> None:
    archive = _write_zip(
        tmp_path / "duplicate.zip",
        [*_required(), (zipfile.ZipInfo("same.txt"), b"a"), (zipfile.ZipInfo("same.txt"), b"b")],
    )
    with pytest.raises(ArchiveError):
        check_archive(archive)


@pytest.mark.parametrize("kind", [stat.S_IFLNK, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFIFO])
def test_links_and_devices_are_rejected(tmp_path: Path, kind: int) -> None:
    info = zipfile.ZipInfo("special")
    info.external_attr = (kind | 0o777) << 16
    archive = _write_zip(tmp_path / "special.zip", [*_required(), (info, b"target")])
    with pytest.raises(ArchiveError):
        check_archive(archive)


@pytest.mark.parametrize(
    "name",
    [".venv/x", "tests/test_x.py", "__pycache__/x.pyc", "uv.toml"],
)
def test_forbidden_package_members(tmp_path: Path, name: str) -> None:
    archive = _write_zip(
        tmp_path / "forbidden.zip",
        [*_required(), (zipfile.ZipInfo(name), b"x")],
    )
    with pytest.raises(ArchiveError):
        check_archive(archive)


def test_missing_required_files(tmp_path: Path) -> None:
    archive = _write_zip(
        tmp_path / "missing.zip",
        [(zipfile.ZipInfo("plugin.json"), b"{}")],
    )
    with pytest.raises(PackageError):
        check_archive(archive)


def test_too_many_entries_are_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.plugins.archive.MAX_ARCHIVE_ENTRIES", 2)
    archive = _write_zip(
        tmp_path / "many.zip",
        [*_required(), (zipfile.ZipInfo("extra.txt"), b"x")],
    )
    with pytest.raises(ArchiveTooLarge):
        check_archive(archive)


def test_uncompressed_size_limit_is_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.plugins.archive.MAX_UNCOMPRESSED_BYTES", 1024)
    archive = _write_zip(
        tmp_path / "bomb.zip",
        [*_required(), (zipfile.ZipInfo("big.bin"), b"x" * 2048)],
    )
    with pytest.raises(ArchiveTooLarge):
        check_archive(archive)
    with pytest.raises(ArchiveTooLarge):
        extract_archive(archive, tmp_path / "bomb-out")
    assert not (tmp_path / "bomb-out").exists()


def _extracted_package(tmp_path: Path) -> Path:
    archive = make_plugin_zip(tmp_path / "package.zip")
    destination = tmp_path / "package"
    extract_archive(archive, destination)
    return destination


def test_pyproject_path_source_is_rejected(tmp_path: Path) -> None:
    package = _extracted_package(tmp_path)
    (package / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["iris-plugin-sdk"]\n'
        '[tool.uv.sources]\niris-plugin-sdk = { path = "../sdk" }\n',
        encoding="utf-8",
    )
    with pytest.raises(PackageError):
        check_package(package)


@pytest.mark.parametrize("key", ["git", "editable"])
def test_pyproject_git_and_editable_sources_are_rejected(tmp_path: Path, key: str) -> None:
    package = _extracted_package(tmp_path)
    (package / "pyproject.toml").write_text(
        f'[tool.uv.sources]\nsdk = {{ {key} = "https://example.com/sdk" }}\n',
        encoding="utf-8",
    )
    with pytest.raises(PackageError):
        check_package(package)


def test_pyproject_multi_source_list_rules(tmp_path: Path) -> None:
    package = _extracted_package(tmp_path)
    (package / "pyproject.toml").write_text(
        '[tool.uv.sources]\nsdk = [{ index = "https://example.com/simple" }]\n',
        encoding="utf-8",
    )
    assert check_package(package).id == "org.example.fake"
    (package / "pyproject.toml").write_text(
        "[tool.uv.sources]\nsdk = ["
        '{ index = "https://example.com/simple" }, { path = "../sdk" }]\n',
        encoding="utf-8",
    )
    with pytest.raises(PackageError):
        check_package(package)


@pytest.mark.parametrize(
    "source",
    [
        '{ git = "https://example.com/repo" }',
        '{ url = "http://example.com/a.whl" }',
        '{ directory = "../elsewhere" }',
        '{ unknown = "x" }',
    ],
)
def test_lockfile_source_rules(tmp_path: Path, source: str) -> None:
    package = _extracted_package(tmp_path)
    (package / "uv.lock").write_text(
        f'version = 1\nrevision = 3\n[[package]]\nname = "dep"\nversion = "1"\nsource = {source}\n',
        encoding="utf-8",
    )
    with pytest.raises(PackageError):
        check_package(package)


def test_lockfile_https_url_and_registry_are_allowed(tmp_path: Path) -> None:
    package = _extracted_package(tmp_path)
    (package / "uv.lock").write_text(
        "version = 1\nrevision = 3\n"
        '[[package]]\nname = "a"\nversion = "1"\n'
        'source = { registry = "https://pypi.org/simple" }\n'
        '[[package]]\nname = "b"\nversion = "1"\n'
        'source = { url = "https://example.com/b.whl" }\n'
        '[[package]]\nname = "c"\nversion = "1"\nsource = { virtual = "." }\n',
        encoding="utf-8",
    )
    assert check_package(package).id == "org.example.fake"


def test_manifest_version_is_validated_but_builtin_prefix_is_left_to_upload(
    tmp_path: Path,
) -> None:
    package = _extracted_package(tmp_path)
    (package / "plugin.json").write_text(
        '{"id": "builtin.fake", "name": "x", "version": "1.0.0", '
        '"sdk": ">=1.0,<2.0", "entry": "fake_plugin", "provides": ["admin.ui"], '
        '"ui": "ui", "pages": [{"id": "settings", "title": "设置"}]}',
        encoding="utf-8",
    )
    # builtin. 前缀由上传接口判断为系统插件冲突，归档层只校验 id/version 正则
    assert check_package(package).id == "builtin.fake"

    (package / "plugin.json").write_text(
        '{"id": "org.example.fake", "name": "x", "version": "v1", '
        '"sdk": ">=1.0,<2.0", "entry": "fake_plugin", "provides": ["admin.ui"], '
        '"ui": "ui", "pages": [{"id": "settings", "title": "设置"}]}',
        encoding="utf-8",
    )
    with pytest.raises(PackageError):
        check_package(package)


def test_missing_lockfile(tmp_path: Path) -> None:
    package = _extracted_package(tmp_path)
    (package / "uv.lock").unlink()
    with pytest.raises(PackageError):
        check_package(package)


def test_file_and_directory_with_same_name_are_rejected(tmp_path: Path) -> None:
    archive = _write_zip(
        tmp_path / "clash.zip",
        [*_required(), (zipfile.ZipInfo("a"), b"file"), (zipfile.ZipInfo("a/b.txt"), b"nested")],
    )
    with pytest.raises(ArchiveError, match="同名"):
        check_archive(archive)
    destination = tmp_path / "out"
    with pytest.raises(ArchiveError, match="同名"):
        extract_archive(archive, destination)
    assert not destination.exists()


def test_file_clash_with_deeper_directory_is_rejected(tmp_path: Path) -> None:
    archive = _write_zip(
        tmp_path / "clash.zip",
        [
            *_required(),
            (zipfile.ZipInfo("x/y/z.txt"), b"nested"),
            (zipfile.ZipInfo("x/y"), b"file"),
        ],
    )
    with pytest.raises(ArchiveError):
        extract_archive(archive, tmp_path / "out")


def test_extract_os_errors_become_archive_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = make_plugin_zip(tmp_path / "package.zip")

    def broken(self: Path, *args: object, **kwargs: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "chmod", broken)
    destination = tmp_path / "out"
    with pytest.raises(ArchiveError, match="Permission denied"):
        extract_archive(archive, destination)
