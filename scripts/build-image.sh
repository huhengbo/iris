#!/usr/bin/env bash
# 构建 linux/amd64 和 linux/arm64 两个架构的镜像。
#   scripts/build-image.sh <镜像:标签>          只构建，确认两个架构都能编译通过（结果留在构建缓存里）
#   scripts/build-image.sh <镜像:标签> --push   构建并推送到镜像仓库，推送前先 docker login
# 需要 docker buildx 和支持多平台的 builder，没有时先创建：
#   docker buildx create --name multiarch --driver docker-container --use
# 用别的 builder 时设置 IRIS_BUILDER。
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image="${1:-}"
if [[ -z $image || $image == -* ]]; then
  echo "用法：$0 <镜像:标签> [--push]" >&2
  exit 2
fi

flags=(--platform linux/amd64,linux/arm64 --tag "$image")
case "${2:-}" in
  "") ;;
  --push) flags+=(--push) ;;
  *) echo "用法：$0 <镜像:标签> [--push]" >&2; exit 2 ;;
esac
if [[ -n ${IRIS_BUILDER:-} ]]; then
  flags+=(--builder "$IRIS_BUILDER")
fi

docker buildx build "${flags[@]}" "$root"
