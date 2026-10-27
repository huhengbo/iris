"""定向同步的目标：来源目录的变动，或资料库目录下的相对路径。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceDir:
    """来源里发生变动的目录。

    定位符只由来源插件解释；同步时先用 browse 找到它属于哪个资料库目录、相对路径是什么，
    再只比对这一个目录。
    """

    source_id: int
    locator: str


@dataclass(frozen=True)
class LibraryDir:
    """定向同步的目标：资料库目录下的相对路径。已不存在时改为同步它还在的上级目录。

    deep 为 True 时逐级列出全部子目录，用于管理员手动同步一个条目的目录。
    """

    folder_id: int
    rel_dir: str
    deep: bool = False


DirTarget = SourceDir | LibraryDir


def relative_path(root: str, current: str) -> str | None:
    root = "/" + root.strip("/") if root.strip("/") else "/"
    current = "/" + current.strip("/") if current.strip("/") else "/"
    if current == root:
        return ""
    prefix = root.rstrip("/") + "/"
    return current[len(prefix) :] if current.startswith(prefix) else None
