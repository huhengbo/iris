#!/usr/bin/env bash
# 同步 plugins/ 下每个插件的独立虚拟环境；新增插件或修改依赖后执行一次。
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
plugins="$root/plugins"

if [[ ! -d "$plugins" ]]; then
  echo "没有 plugins 目录，跳过"
  exit 0
fi

found=0
for project in "$plugins"/*/; do
  [[ -f "${project}pyproject.toml" ]] || continue
  found=1
  echo "==> $(basename "$project")"
  (cd "$project" && uv sync --frozen)
done

if [[ "$found" == "0" ]]; then
  echo "plugins 目录下没有插件"
fi
