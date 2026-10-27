"""后端用到的每个错误码都要写进 docs/api/errors.md（M25）。"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "api" / "errors.md"


def used_error_codes() -> dict[str, set[int]]:
    """扫描源码里 AppError(...) 和子类 super().__init__(...) 的字面量状态码与错误码。"""
    codes: dict[str, set[int]] = {}
    for path in sorted((ROOT / "backend" / "app").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            # supervisor 的细分错误码由 registry 原样转成 409 AppError，没有字面量调用点。
            if isinstance(node, ast.ClassDef) and any(
                isinstance(base, ast.Name) and base.id == "PluginRuntimeError"
                for base in node.bases
            ):
                for statement in node.body:
                    if (
                        isinstance(statement, ast.Assign)
                        and any(
                            isinstance(target, ast.Name) and target.id == "code"
                            for target in statement.targets
                        )
                        and isinstance(statement.value, ast.Constant)
                        and isinstance(statement.value.value, str)
                    ):
                        codes.setdefault(statement.value.value, set()).add(409)
            if not isinstance(node, ast.Call) or len(node.args) < 2:
                continue
            func = node.func
            is_app_error = isinstance(func, ast.Name) and func.id == "AppError"
            is_super_init = (
                isinstance(func, ast.Attribute)
                and func.attr == "__init__"
                and isinstance(func.value, ast.Call)
                and isinstance(func.value.func, ast.Name)
                and func.value.func.id == "super"
            )
            status, code = node.args[0], node.args[1]
            if (is_app_error or is_super_init) and (
                isinstance(status, ast.Constant)
                and isinstance(status.value, int)
                and isinstance(code, ast.Constant)
                and isinstance(code.value, str)
            ):
                codes.setdefault(code.value, set()).add(status.value)
    return codes


def documented_error_codes() -> set[str]:
    return set(re.findall(r"^\| `([a-z0-9_]+)` \|", DOC.read_text(encoding="utf-8"), re.M))


def test_every_error_code_is_documented() -> None:
    missing = sorted(set(used_error_codes()) - documented_error_codes())
    assert not missing, f"docs/api/errors.md 缺少这些错误码：{missing}"


def test_documented_error_codes_still_exist() -> None:
    stale = sorted(documented_error_codes() - set(used_error_codes()))
    assert not stale, f"docs/api/errors.md 里这些错误码已不再使用：{stale}"
