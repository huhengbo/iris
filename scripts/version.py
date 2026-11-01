#!/usr/bin/env python3
"""版本号工具：根目录 VERSION 是唯一来源。

  version.py check [tag]   校验 backend/pyproject.toml、client/pubspec.yaml（以及给定的 tag）与 VERSION 一致
  version.py code          打印 Android versionCode：major*10000 + minor*100 + patch
  version.py notes         从 CHANGELOG.md 取当前版本的章节，加上镜像和 APK 的升级说明，输出到标准输出
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
IMAGE = "ghcr.io/huhengbo/iris"


def current() -> str:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not SEMVER.match(version):
        sys.exit(f"VERSION 不是 x.y.z：{version!r}")
    return version


def version_code(version: str) -> int:
    major, minor, patch = (int(part) for part in version.split("."))
    return major * 10000 + minor * 100 + patch


def check(tag: str | None) -> None:
    version = current()
    errors: list[str] = []
    pyproject = tomllib.loads((ROOT / "backend/pyproject.toml").read_text(encoding="utf-8"))
    if pyproject["project"]["version"] != version:
        errors.append(f"backend/pyproject.toml 是 {pyproject['project']['version']}")
    match = re.search(r"^version:\s*(\S+)\s*$", (ROOT / "client/pubspec.yaml").read_text("utf-8"), re.M)
    expected = f"{version}+{version_code(version)}"
    if match is None or match.group(1) != expected:
        errors.append(f"client/pubspec.yaml 是 {match.group(1) if match else '缺失'}，应为 {expected}")
    if tag is not None and tag != f"v{version}":
        errors.append(f"tag {tag} 与 VERSION 对应的 v{version} 不一致")
    if f"## [{version}]" not in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"):
        errors.append(f"CHANGELOG.md 缺少 [{version}] 章节")
    if errors:
        sys.exit(f"版本不一致（VERSION = {version}）：\n- " + "\n- ".join(errors))
    print(f"版本一致：{version}（versionCode {version_code(version)}）")


def notes() -> str:
    version = current()
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)", changelog, re.M | re.S)
    if match is None:
        sys.exit(f"CHANGELOG.md 缺少 [{version}] 章节")
    major, minor, _ = version.split(".")
    return f"""> 本发布说明由 `CHANGELOG.md` 的 `{version}` 版本章节自动生成。

{match.group(1).strip()}

### 📦 获取

- 镜像：`{IMAGE}:{version}`，同时更新 `{major}.{minor}` 和 `latest`，支持 `linux/amd64` / `linux/arm64`。
- Android / Android TV：下载本页的 APK，按设备架构选 `arm64-v8a`（多数手机和电视盒子）、`armeabi-v7a`（老设备）或 `x86_64`（模拟器）。
"""


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "check":
        check(sys.argv[2] if len(sys.argv) > 2 else None)
    elif command == "code":
        print(version_code(current()))
    elif command == "notes":
        print(notes())
    else:
        sys.exit(__doc__)


main()
