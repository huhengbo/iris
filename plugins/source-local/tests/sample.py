"""契约测试和单元测试共用的本地目录样本。"""

from __future__ import annotations

from pathlib import Path

FILES = {
    "readme.nfo": b"<movie/>",
    "movies/a.mkv": b"a" * 100,
    "movies/2024/b.srt": b"subtitle" * 4,
    "temp/delete.mkv": b"temporary",
}


def build_tree(root: Path) -> None:
    for name, content in FILES.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
