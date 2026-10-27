"""插件状态文件的 JSON 读写。

写文件使用同目录临时文件加原子替换，权限固定为 0600：状态里可能有凭据，也会被同步线程和
设置页同时读写，不能留下半截文件或让其他用户读到。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def read_json(path: Path, default: Any = None) -> Any:
    """读取整份 JSON；文件不存在时返回 default，内容损坏时抛出 ValueError。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 文件已损坏：{path}") from exc


def write_json(path: Path, data: Any) -> None:
    """原子写入 JSON，并把文件权限设为 0600。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
