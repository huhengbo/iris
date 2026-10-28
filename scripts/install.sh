#!/usr/bin/env bash
# 一键部署（docker compose）：生成目录、compose 文件和 .env，拉取镜像并启动，等健康检查通过。
#   scripts/install.sh              首次部署；已部署时保留 .env 和数据，只确保服务在运行
#   scripts/install.sh upgrade      拉取最新镜像并重建容器，数据不受影响
#   scripts/install.sh --help
# 也可以不克隆仓库直接运行：
#   curl -fsSL https://raw.githubusercontent.com/huhengbo/iris/main/scripts/install.sh | bash
# 可用环境变量（首次部署时写入 .env，之后以 .env 为准）：
#   IRIS_DIR              部署目录，默认 ./iris
#   IRIS_IMAGE            镜像，默认 ghcr.io/huhengbo/iris
#   IRIS_VERSION          镜像标签，默认 latest
#   APP_HTTP_PORT         宿主机端口，默认 8080
#   APP_ADMIN_USERNAME    首个管理员用户名，默认 admin
#   APP_ADMIN_PASSWORD    首个管理员密码，默认随机生成
#   TZ                    时区，默认 Asia/Shanghai
set -euo pipefail

dir="${IRIS_DIR:-iris}"
image="${IRIS_IMAGE:-ghcr.io/huhengbo/iris}"
version="${IRIS_VERSION:-latest}"
port="${APP_HTTP_PORT:-8080}"
admin_user="${APP_ADMIN_USERNAME:-admin}"
timezone="${TZ:-Asia/Shanghai}"
# 镜像里的运行用户，数据目录要交给它
app_uid=1000
app_gid=1000

die() { echo "错误：$*" >&2; exit 1; }

usage() {
  [[ -f ${BASH_SOURCE[0]:-} ]] && sed -n "2,15p" "${BASH_SOURCE[0]}" | sed "s/^# \{0,1\}//"; return 0
}

# 随机字母数字串。tr 在 head 关闭管道后会收到 SIGPIPE，pipefail 下不能当作失败
random() {
  { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$1"; } || true
}

mode="install"
case "${1:-}" in
  "") ;;
  upgrade) mode="upgrade" ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

command -v docker >/dev/null 2>&1 || die "没有找到 docker，请先安装：https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || die "需要 Docker Compose v2（docker compose 命令）"
docker info >/dev/null 2>&1 || die "连不上 Docker 守护进程，请确认 Docker 正在运行，且当前用户有权限使用它"
[[ $port =~ ^[0-9]+$ ]] || die "APP_HTTP_PORT 必须是数字：$port"

if [[ $mode == upgrade && ! -f $dir/docker-compose.yml ]]; then
  die "$dir 下没有 docker-compose.yml，请先执行首次部署（不带 upgrade）"
fi

mkdir -p "$dir/data"
cd "$dir"

# 数据目录要让容器用户可写；Docker Desktop（macOS / Windows）不需要这一步
if [[ $(uname -s) == Linux && $(stat -c %u data) != "$app_uid" ]]; then
  if [[ $(id -u) == 0 ]]; then
    chown "$app_uid:$app_gid" data
  elif command -v sudo >/dev/null 2>&1; then
    echo "把 data/ 交给容器用户 $app_uid:$app_gid（需要 sudo）"
    sudo chown "$app_uid:$app_gid" data
  else
    die "data/ 需要归 $app_uid:$app_gid 所有：chown $app_uid:$app_gid $PWD/data"
  fi
fi

if [[ ! -f docker-compose.yml ]]; then
  cat >docker-compose.yml <<'YAML'
services:
  iris:
    image: "${IRIS_IMAGE:?set IRIS_IMAGE}"
    init: true
    restart: unless-stopped
    ports:
      - "${APP_HTTP_PORT:-8080}:8080"
    environment:
      APP_SECRET_KEY: "${APP_SECRET_KEY:?set APP_SECRET_KEY (at least 32 characters)}"
      APP_ADMIN_USERNAME: "${APP_ADMIN_USERNAME:-admin}"
      APP_ADMIN_PASSWORD: "${APP_ADMIN_PASSWORD:?set APP_ADMIN_PASSWORD}"
      TZ: "${TZ:-Asia/Shanghai}"
      APP_CORS_ORIGINS: "${APP_CORS_ORIGINS:-}"
    security_opt:
      - no-new-privileges:true
    volumes:
      - ./data:/data
      # 本地存储来源：把宿主机目录挂到 /data/media/ 下（只读），再到后台新建本地来源
      # - /mnt/movies:/data/media/movies:ro
    logging:
      options:
        max-size: 10m
        max-file: "3"
YAML
  echo "已生成 $PWD/docker-compose.yml"
fi

new_password=""
if [[ -f .env ]]; then
  echo "保留已有的 $PWD/.env"
  if [[ $mode == upgrade && $(grep -c '^IRIS_IMAGE=' .env) == 1 && -n ${IRIS_VERSION:-} ]]; then
    # 显式指定了版本才改标签，否则沿用 .env 里的
    sed "s|^IRIS_IMAGE=.*|IRIS_IMAGE=$image:$version|" .env >.env.new && mv .env.new .env
    chmod 600 .env
  fi
else
  secret="$(random 64)"
  [[ ${#secret} -eq 64 ]] || die "无法生成随机密钥（读取 /dev/urandom 失败）"
  if [[ -n ${APP_ADMIN_PASSWORD:-} ]]; then
    admin_password="$APP_ADMIN_PASSWORD"
  else
    admin_password="$(random 20)"
    new_password="$admin_password"
  fi
  (
    umask 077
    cat >.env <<ENV
# 由 install.sh 生成。不要提交到版本库，也不要丢失：
# APP_SECRET_KEY 更换后所有登录和播放令牌失效；备份数据时请一并保存它。
IRIS_IMAGE=$image:$version
APP_SECRET_KEY=$secret
APP_ADMIN_USERNAME=$admin_user
APP_ADMIN_PASSWORD=$admin_password
APP_HTTP_PORT=$port
TZ=$timezone
APP_CORS_ORIGINS=
ENV
  )
  echo "已生成 $PWD/.env（权限 600）"
fi

# compose 变量替换里环境变量优先于 .env。上面的环境变量只用来生成 .env，之后清掉，
# 否则它们会悄悄覆盖 .env（例如 IRIS_IMAGE 不带标签时盖掉 IRIS_VERSION）
unset IRIS_IMAGE APP_SECRET_KEY APP_ADMIN_USERNAME APP_ADMIN_PASSWORD APP_HTTP_PORT APP_CORS_ORIGINS TZ

# 以 .env 为准，端口用于健康检查和提示
port="$(sed -n 's/^APP_HTTP_PORT=//p' .env | tail -n 1)"
port="${port:-8080}"

echo "拉取镜像……"
if ! docker compose pull; then
  echo >&2
  echo "拉取失败。如果镜像在私有仓库，请先登录：docker login ghcr.io" >&2
  exit 1
fi
docker compose up -d

echo -n "等待服务就绪"
ready=""
for _ in $(seq 1 60); do
  if docker compose exec -T iris python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=3).read()" \
    >/dev/null 2>&1; then
    ready=1
    break
  fi
  echo -n "."
  sleep 2
done
echo
if [[ -z $ready ]]; then
  echo "服务 2 分钟内没有就绪，最近的日志：" >&2
  docker compose logs --tail 30 iris >&2 || true
  die "启动失败，完整日志：cd $PWD && docker compose logs -f"
fi

echo
echo "光圈已启动：http://127.0.0.1:$port"
if [[ -n $new_password ]]; then
  echo "管理员账号：$admin_user"
  echo "管理员密码：$new_password    （只显示这一次，之后在 $PWD/.env 里查看；登录后请尽快修改）"
fi
cat <<EOF

接下来：
  1. 在前面配好 HTTPS 反向代理（外部播放器和手机在外网使用都需要），见 docs/deploy.md
  2. 登录后台依次：插件（配置资源来源和元数据）→ 设置（填写对外访问地址）→ 资料库 → 同步
  3. 升级：scripts/install.sh upgrade（或在 $PWD 里执行 docker compose pull && docker compose up -d）
  4. 备份：data/ 目录和 .env 里的 APP_SECRET_KEY
EOF
