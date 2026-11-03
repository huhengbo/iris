# 部署说明

光圈是一个 Docker 镜像：后端 API 加上它托管的 Web 客户端，数据都在一个数据卷里（ADR 0002）。

## 准备

- 一台装有 Docker 和 Docker Compose 的机器，`linux/amd64` 或 `linux/arm64` 都可以。
- 一个外网能访问的域名和 HTTPS 反向代理。播放器从对外访问地址取片，手机在外面也要能访问到。
- 各插件需要的账号或 API Key，在管理后台的插件页填写，不用环境变量。

## 一键部署

`scripts/install.sh` 完成下面「首次部署（手动）」的全部步骤：

```sh
curl -fsSL https://raw.githubusercontent.com/huhengbo/iris/main/scripts/install.sh | bash
# 或克隆仓库后：scripts/install.sh
```

- 在 `IRIS_DIR`（默认 `./iris`）下生成 `docker-compose.yml`、`.env`（权限 600，含随机的 `APP_SECRET_KEY` 和管理员密码）和 `data/`，拉取镜像并启动，等 `/api/health` 通过后打印访问地址。管理员密码只在生成时显示一次，之后在 `.env` 里查看。
- 可选环境变量：`IRIS_DIR`、`IRIS_IMAGE`（默认 `ghcr.io/huhengbo/iris`）、`IRIS_VERSION`（默认 `latest`，生产环境建议固定版本号）、`APP_HTTP_PORT`、`APP_ADMIN_USERNAME`、`APP_ADMIN_PASSWORD`、`TZ`。这些变量只在首次生成 `.env` 时使用，之后以 `.env` 为准。
- 重复运行是安全的：已有的 `.env` 和 `data/` 不会被改动。
- 升级：`scripts/install.sh upgrade`（要换版本时加 `IRIS_VERSION=0.2.0`），等价于 `docker compose pull && docker compose up -d`。
- Linux 上 `data/` 会 `chown` 给容器用户 1000:1000，非 root 时需要 sudo。
- 镜像在私有仓库时先 `docker login ghcr.io`。
- 想挂载本地媒体目录，编辑生成的 `docker-compose.yml`，取消注释 `volumes` 里的示例行后执行 `docker compose up -d`。

## 首次部署（手动）

1. 复制仓库里的 `docker-compose.yml` 和 `.env.example`，把后者改名为 `.env`，然后填写：
   - `APP_SECRET_KEY`：至少 32 个字符，用 `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` 生成。**首次使用后不要改**：登录和播放令牌的签名依赖它。
   - `APP_ADMIN_USERNAME`、`APP_ADMIN_PASSWORD`：首个管理员，只在数据库里还没有管理员时创建一次，之后改这两项不再生效。
   - `TZ`：定时同步按这个时区解释，填 IANA 时区名，如 `Asia/Shanghai`。
   - `APP_HTTP_PORT`：映射到宿主机的端口，默认 8080。
   - `APP_PLUGINS_DIR`：插件根目录，镜像里已由 Dockerfile 设为 `/app/plugins`，通常不用改。
   - `APP_DEFAULT_PLUGIN_REPOS`：默认插件仓库地址，逗号分隔；不设置时是官方仓库 `https://huhengbo.github.io/iris-plugins/index.json`，设为空字符串表示不配置默认仓库。仓库地址必须是 HTTPS（本机开发可用 localhost）。
   - `UV_DEFAULT_INDEX`：插件依赖安装使用的 PyPI 索引，默认 `https://pypi.org/simple`；国内或内网可以换成镜像地址。也可以设置 `UV_EXTRA_INDEX_URL`、`UV_INDEX_URL` 等 uv 认识的环境变量。
2. 建好数据目录并交给容器用户（镜像里是 UID 1000，构建时用 `APP_UID`、`APP_GID` 改过的话换成对应的值）：

   ```sh
   mkdir data && sudo chown 1000:1000 data
   ```

   不建的话 Docker 会自动建成 root 所有，容器写不进去，启动时报「数据目录 /data 不可写」并不断重启。
3. 选择镜像来源：
   - 从源码构建：在仓库根目录执行 `docker compose up -d --build`。
   - 用镜像仓库里的镜像：在 `.env` 里设置 `IRIS_IMAGE=<镜像:标签>`，再执行 `docker compose pull && docker compose up -d`。
4. 确认启动：`curl http://127.0.0.1:8080/api/health` 返回 `"status": "ok"` 和数据库版本；`docker compose ps` 显示 healthy。
5. 用管理员登录 Web 或 App，在管理后台依次完成：
   1. 「插件」：配置资源来源插件和元数据插件（各插件的配置方式见 `plugins/<插件>/README.md`，例如 115 网盘见 [plugins/source-115](../plugins/source-115/README.md)）；
   2. 「设置」：填写**对外访问地址**（见下文）；
   3. 「资料库」：添加资料库目录；
   4. 「同步」：手动同步一次，再设置定时同步。

## 反向代理与 HTTPS

服务本身只提供 HTTP，HTTPS 由前面的反向代理负责。容器已开启 `--proxy-headers`，可以识别代理传来的 `X-Forwarded-*` 头。

- 把域名的全部路径转发到容器的 8080 端口，包括 `/api/`、`/play/` 和前端页面，不要改写路径。
- `/play/` 返回 302 跳转到资源来源的直链（本地来源直接返回文件），视频流量不经过服务器，代理不需要调大超时或缓冲。
- 不要给 `/api/` 和 `/play/` 加缓存；前端 JS 返回 `no-cache`，要让浏览器遵循服务端的缓存头。
  - 用 Cloudflare 时，「浏览器缓存 TTL」要设为「遵循现有标头」，否则 Cloudflare 会把 `main.dart.js` 改成缓存 4 小时，部署后浏览器可能拿旧的 JS 配新资源。
- 只有自己托管的 Web 页面和 App 访问时，不需要设置 `APP_CORS_ORIGINS`（ADR 0017）。
- AI 对话的回答用 SSE 流式返回（`POST /api/ai/chats/{id}/messages`），反向代理不能缓冲这个响应，读超时至少 120 秒，否则回答会攒成一整段才出现，或者中途被切断。服务已经在响应头里带了 `X-Accel-Buffering: no`，nginx 会遵守；其他代理的设置：
  - Traefik、Caddy 默认不缓冲，不用改；
  - nginx：`proxy_buffering off; proxy_read_timeout 300s;`（只对 `/api/ai/` 设置即可）；
  - Cloudflare 不缓冲 `text/event-stream`，但免费版的连接超过 100 秒没有数据会被切断，所以回答再慢也比这个短。

Traefik（容器和代理在同一个外部网络 `proxy` 里）的示例，替换 `docker-compose.yml` 里的 `ports`：

```yaml
    networks:
      - proxy
    labels:
      - traefik.enable=true
      - traefik.http.routers.iris.rule=Host(`movie.example.com`)
      - traefik.http.routers.iris.entrypoints=websecure
      - traefik.http.routers.iris.tls.certresolver=letsencrypt
      - traefik.http.services.iris.loadbalancer.server.port=8080

networks:
  proxy:
    external: true
```

Caddy 的示例：

```
movie.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

Traefik（容器和代理在同一个外部网络 `proxy` 里）的示例，替换 `docker-compose.yml` 里的 `ports`：

```yaml
    networks:
      - proxy
    labels:
      - traefik.enable=true
      - traefik.http.routers.iris.rule=Host(`movie.example.com`)
      - traefik.http.routers.iris.entrypoints=websecure
      - traefik.http.routers.iris.tls.certresolver=letsencrypt
      - traefik.http.services.iris.loadbalancer.server.port=8080

networks:
  proxy:
    external: true
```

Caddy 的示例：

```
movie.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

## 对外访问地址

在管理后台「设置」里填写用户访问服务的地址，例如 `https://movie.example.com`。外部播放器（Infuse、VLC、系统播放器等）拿到的播放链接以它开头，所以要用外网能访问的 HTTPS 地址，不能填 `127.0.0.1` 或内网 IP（ADR 0015）。App 的内置播放器用 App 当前连接的服务器地址，不依赖这一项。

## 第三方客户端（Emby 协议）

本服务同时提供一套 Emby 兼容接口，Infuse、网易爆米花等支持 Emby 的第三方客户端可以把本服务当成 Emby 服务器直接连接、浏览和播放（ADR 0024）。功能始终开启，不需要额外的环境变量。

- **服务器地址**：就是 Web 的访问地址和端口，例如 `https://movie.example.com`，不需要加 `/emby`。客户端请求的 `/emby/...` 和根路径下的 `/Users/...`、`/Items/...` 都能用。
- **登录**：在 Infuse 里选择「添加 Emby 服务器」，填写上面的地址，再输入本服务的用户名和密码。管理员账号通过这套接口登录也只有普通用户权限，不能管理服务器；能看到的内容仍按资料库可见范围决定。
- **只支持直接播放**：服务端不转码，也不代理视频流，只把客户端的播放请求 302 跳到资源来源的直链（本地来源则直接返回文件）。依赖转码的客户端和格式不在支持范围内。
- **设备令牌**：每次登录会生成一个长期有效、可在「我的设备」里撤销的客户端设备。用户被禁用、修改密码或资料库可见范围变化时，设备令牌立即失效；在「我的设备」里撤销后，正在使用的串流地址也会立刻返回 401。
- **日志里的令牌**：客户端会自己拼 `api_key=` 串流地址，设备令牌可能出现在反向代理的访问日志里。请让反代不要记录查询参数，或把访问日志当成敏感数据保管；令牌泄露后到「我的设备」撤销对应设备即可，不需要改密码。

## AI 助手

AI 助手是可选功能，默认关闭；没有配置对话模型时，App 和 Web 里看不到任何 AI 入口。管理员在「设置 → AI 助手」配置：

1. 添加模型提供方：选预设（DeepSeek、硅基流动、Anthropic、OpenAI、Ollama）或自定义地址，填 Key，点「测试连接」。Key 用 `APP_SECRET_KEY` 加密保存，所以更换 `APP_SECRET_KEY` 之后要重新填写各提供方的 Key。
2. 给三种用途选模型（模型名手填）：
   - 对话：AI 找片和 AI 对话；
   - 后台任务：为待处理的作品生成匹配建议；
   - 向量：语义索引，需要 OpenAI 兼容的 embedding 接口（例如硅基流动的 `BAAI/bge-m3`）。可以不配，只是「像某部片那样」这类找片会退化为只按条件筛选。

**数据会发给你配置的模型提供方**：片名、简介、网盘里的目录名和文件名、用户在 AI 对话里输入的内容，以及（用户使用 AI 对话时）这个用户的观看记录。在意隐私的话用本地 Ollama，数据不出内网。用量（次数和 token 数）只记录，不做限额；想控制花费请在提供方那里设置额度。

- 语义索引存在同一个 SQLite 数据库里，随数据库一起备份。更换向量模型会自动重建索引，大资料库需要几分钟到几十分钟（后台进行，进度在 AI 设置页）。
- 语义索引依赖 sqlite-vec 扩展。镜像里已经包含；万一在你的平台上加载失败，日志里会有一条警告，只有语义召回不可用，其他功能照常。
- AI 对话按用户保存 30 天，用户可以在对话列表里删除；由「清理 AI 对话」计划任务清理（默认每天一次）。

## 数据和备份

数据卷挂载在容器的 `/data`：

| 路径 | 内容 | 备份 |
|---|---|---|
| `iris.sqlite3`（以及 `-wal`、`-shm`） | 用户、资料库、资源来源、条目、播放记录、设置、在线字幕（压缩后存在数据库里） | 必须备份 |
| `plugins/<id>/state/` | 各插件自己的密钥、配置和运行状态，格式由插件决定 | 必须备份 |
| `plugins/<id>/.venv/`、`cache/` | 插件虚拟环境和缓存 | 不备份，由镜像和插件重建 |
| `plugin-packages/<id>/<版本>/` | 从插件仓库或本地压缩包安装的插件代码和独立虚拟环境 | 不备份；已安装清单在数据库里，恢复后按清单重新安装 |
| `plugin-packages/.uv-cache/` | 安装依赖时 uv 的缓存 | 不备份，可以删除 |
| `images/` | 从 TMDB 下载的海报、剧照和头像 | 可以不备份，丢了以后刷新条目信息会重新下载 |

数据库用 WAL 模式，直接复制正在使用的 `.sqlite3` 文件可能拿到不完整的数据。宿主只生成一种备份：`data/backups/iris-<时间>.tar.gz`，内含 `iris.sqlite3` 的在线快照和 `plugins/<id>/state/`，不包含 `cache/` 和插件虚拟环境。

在管理后台「备份」页创建即可；创建前宿主会对运行中的插件发 `flush`，超时的插件会记警告但备份照常进行。需要离线保存时，把 `data/backups/iris-*.tar.gz` 复制到别处；旧的 `.sqlite3` 备份文件不再识别，也不能恢复。

备份包里有「已安装清单」：从哪个仓库、装的什么版本。插件代码和虚拟环境不进备份包，所以**恢复后需要能访问对应仓库和 PyPI（或镜像）**：清单里存在但本地代码目录已丢失的插件，宿主会在后台重新下载、安装依赖并拉起；仓库或 PyPI 在恢复时不可用，插件会保持「缺失」状态，等下一次重启宿主时重试。本地安装的插件没有仓库可下载，需要管理员重新上传。

恢复：在管理后台选择备份并确认。宿主会停止所有插件，替换主库和各插件的 `state/`，然后重启插件。恢复前的安全备份也会出现在备份列表里。备份包里有插件保存的明文凭据，要按敏感文件保管，并同时保存 `.env` 里的 `APP_SECRET_KEY`（登录和播放令牌的签名依赖它）。

## 升级

- 从源码构建：更新代码后执行 `docker compose up -d --build`。
- 用镜像：改 `IRIS_IMAGE` 的标签，再执行 `docker compose pull && docker compose up -d`。

启动时自动执行数据库迁移，`/api/health` 里的 `schema_version` 是当前数据库版本。迁移不能回退，升级前先备份。数据库结构以基线迁移 `0001_baseline.sql` 为起点。

镜像构建时会先复制 `packages/iris-plugin-sdk`，再对 `plugins/*` 分别执行 `uv sync --frozen` 生成各自的 `.venv`；运行中的宿主从 `APP_PLUGINS_DIR`（镜像里是 `/app/plugins`）发现插件，每个已启用插件在独立子进程里运行，通过 JSON-RPC 与宿主通信。插件目录不存在时构建仍可完成，表示这个镜像没有内置插件；停用或删除某个插件不影响宿主和其他插件启动。

运行时镜像自带 `uv`。从插件仓库或本地压缩包安装插件时，宿主按插件自带的 `uv.lock` 导出带哈希的依赖清单，在 `data/plugin-packages/<id>/<版本>/` 里建独立 venv 并安装；**这一步需要联网访问 PyPI 或其镜像**，完全离线的环境不能安装或升级仓库插件。安装和升级在后台任务里执行，不重启宿主；升级失败会自动回滚到上一版。系统插件（`builtin.*`）随镜像发布，不能被仓库覆盖、升级或卸载。

插件仓库在管理后台「插件 › 仓库」里管理：默认有一个官方仓库，可以停用但不能删除；也可以添加其他 HTTPS 仓库。添加非官方仓库需要确认风险，安装前还会展示插件申请的 `provides` / `capabilities`。每天的计划任务会拉取各仓库索引检查更新；拉取失败时保留上次缓存并显示仓库错误。
资源来源配置保存在数据库中。本地来源的根目录只能位于数据目录下的 `media/` 之内（容器里是 `/data/media`），防止通过来源配置读取服务器上的其他文件。要用宿主机上的其他目录，把它挂载到 `media/` 下，例如在 `volumes` 里加一行 `- /mnt/movies:/data/media/movies:ro`，再到后台「资源来源」新建本地来源，服务器目录填 `movies`。本地来源只读，不支持在后台删除文件。

## 发布多架构镜像

`scripts/build-image.sh` 同时构建 `linux/amd64` 和 `linux/arm64` 两个架构。Web 客户端在构建机平台上只编译一次，后端分别按两个架构安装。

```sh
# 只构建，确认两个架构都能编译通过
scripts/build-image.sh ghcr.io/<用户>/iris:0.1.0
# 构建并推送（先 docker login 到对应仓库）
scripts/build-image.sh ghcr.io/<用户>/iris:0.1.0 --push
```

需要 docker buildx 和一个 `docker-container` 驱动的 builder，没有时先执行 `docker buildx create --name multiarch --driver docker-container --use`。

## 日志

- `docker compose logs -f` 能看到同步进度、来源限流暂停、来源请求失败的原因和播放跳转记录。
- 日志里不出现来源凭据（如 Cookie）、登录令牌、播放令牌和直链：访问日志里 `/play/` 后面的令牌替换为 `***`，播放跳转只记录结果和播放器 UA。
- 建议在 compose 里限制日志大小，例如：

  ```yaml
      logging:
        options:
          max-size: 10m
          max-file: "3"
  ```
