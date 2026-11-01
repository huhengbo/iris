#!/usr/bin/env bash
# 本地一条命令跑完全部检查：后端 ruff、mypy、pytest，前端格式、静态分析、测试。
# 加 --coverage 时后端测试同时输出覆盖率（含未覆盖的行号）。
set -euo pipefail

pytest_args=(-q)
if [[ "${1:-}" == "--coverage" ]]; then
  pytest_args+=(--cov --cov-report=term-missing:skip-covered)
fi

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> version"
uv run --python 3.13 "$root/scripts/version.py" check

echo "==> backend"
cd "$root/backend"
uv sync --locked --quiet
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest "${pytest_args[@]}"

echo "==> plugins"
if [[ -d "$root/plugins" ]]; then
  # 插件包以后可以整体搬到独立仓库，不能依赖宿主的 app.*
  if matches=$(grep -RInE --include='*.py' --exclude-dir=.venv --exclude-dir=__pycache__ \
    '^[[:space:]]*(from|import)[[:space:]]+app([.[:space:]]|$)' "$root/plugins"); then
    echo "$matches" >&2
    echo "plugins/ 下的代码不得 import app.*" >&2
    exit 1
  fi
fi

echo "==> client"
cd "$root/client"
flutter pub get >/dev/null
dart format --output=none --set-exit-if-changed lib test
# flutter analyze 在含中文的路径下会因分析服务器的 LSP 编码问题崩溃，dart analyze 不受影响
dart analyze --fatal-infos
flutter test

echo "==> all checks passed"
