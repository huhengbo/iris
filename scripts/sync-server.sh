#!/usr/bin/env bash
# 把源码同步到测试服务器的 src/ 并重建容器。服务器上的 compose.yaml、.env 和 data/ 不受影响。
#   scripts/sync-server.sh             同步并重建、重启
#   scripts/sync-server.sh --no-build  只同步
#   scripts/sync-server.sh --dry-run   只列出会改动的文件
# 必须设置 IRIS_SERVER（ssh 目标，如 user@host）和 IRIS_SERVER_DIR（相对家目录的部署目录）。
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
server="${IRIS_SERVER:?set IRIS_SERVER (ssh target, e.g. user@host)}"
dir="${IRIS_SERVER_DIR:?set IRIS_SERVER_DIR (deploy dir relative to home)}"

build=1
rsync_flags=()
case "${1:-}" in
  "") ;;
  --no-build) build=0 ;;
  --dry-run) build=0; rsync_flags+=(--dry-run --itemize-changes) ;;
  *) echo "用法：$0 [--no-build|--dry-run]" >&2; exit 2 ;;
esac

# 密钥、本地数据和构建缓存不上传；--delete 只作用于 src/
# macOS 自带 bash 3.2 在 set -u 下展开空数组会报错，用 + 形式兼容
rsync -az --delete ${rsync_flags[@]+"${rsync_flags[@]}"} \
  --exclude=/.git --exclude=/.env --exclude='/.env.*' --exclude=/secrets \
  --exclude=/data --exclude=.DS_Store \
  --exclude=.venv --exclude='/packages/*/build' --exclude=__pycache__ --exclude=.pytest_cache \
  --exclude=.mypy_cache --exclude=.ruff_cache --exclude='*.egg-info' \
  --exclude=/backend/app/static \
  --exclude=/client/.dart_tool --exclude=/client/build \
  --exclude=/client/android/.gradle --exclude=/client/android/.kotlin \
  --exclude=/client/android/app/build --exclude=/client/android/local.properties \
  "$root/" "$server:$dir/src/"

if [[ $build == 1 ]]; then
  ssh "$server" "cd $dir && docker compose up -d --build && docker compose ps"
fi
