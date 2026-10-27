# 0028 插件全部进程外运行，核心不再认识 115

- 状态：已接受
- 日期：2026-10-26
- 修订：ADR 0021（第三方进程外运行提前为全部插件；宿主能力收缩）、ADR 0025（备份只备份 SQLite 单文件；插件密钥和配置存宿主）、[ADR 0013](../../plugins/source-115/docs/adr/0013-115-account-connection.md)（115 凭据存宿主）、[ADR 0027](../../plugins/source-115/docs/adr/0027-115-writable-same-playback-and-grouped-breaker.md)（熔断状态存宿主 `setting`）

## 背景

ADR 0021 把资源来源定义成 `resource.source` provider，本地存储已经是第二个实现，同步、播放、删除的主链路只依赖协议。但 115 仍然深嵌在核心里：

- 资料库添加目录对 115 走专用分支（`api/admin/libraries.py` 的 `_resolve_cloud115`），`library_folder.cid` / `cid_path` 是 115 的目录 ID 和 ID 链。
- 生活事件监听 `LifeMonitor` 直接依赖 `Cloud115ResourceProvider`，启动时 `source.115` 不是这个类就报错，115 来源由启动流程自动创建、不能删除。
- 健康检查、配置向导、汇总只认 115 账号状态。
- 核心捕获 `app.cloud115.errors.FileGone`，返回 `cloud115_rate_limited` 错误码。
- `Cloud115Service`、`TmdbService` 挂在容器和依赖注入上，115 凭据、熔断状态、生活事件状态直接写宿主 `setting` 表；`p115client` 是宿主依赖。
- 表名和列名带 115 痕迹：`cloud_file`、`file_id`、`pickcode`、`cid`、`cid_path`。

接入另一种网盘时，要么复制这些特判，要么只能得到比 115 差的体验。用户还计划以后把插件放到单独的仓库。

## 决策

- **全部插件进程外运行**：每个已启用插件一个子进程，经 stdio JSON-RPC 与宿主通信。内置的 115、本地存储、TMDB、豆瓣、Fanart.tv、射手网都搬出去；宿主进程内不再运行插件代码。
- **隔离目的是稳定和依赖，不是沙箱**：插件进程可以自己联网（宿主下发有效代理）。直连是 SDK 的正式能力，`http` 只是可选的宿主代发能力；宿主不审计直连请求，插件负责日志脱敏、超时、限速和错误映射。崩溃、内存泄漏、依赖冲突不影响宿主。已安装的插件仍属于管理员信任的代码。
- **插件代码独立**：公共 SDK 是独立包 `iris-plugin-sdk`，插件只依赖它，不得导入宿主的 `app.*`。每个插件是独立包、独立 venv，目录结构可以原样搬到单独的仓库。宿主依赖不再包含 `p115client`。
- **插件拥有自己的全部数据**：密钥、配置、运行状态都存在 `data/plugins/<id>/state/`，格式由插件决定，可以是明文。宿主只保存启用状态、代理模式、资源来源和资料库目录（定位符对宿主不透明）。`plugin_secret` 表和宿主的 `secrets`、`config` 能力移除。
- **115 是普通可选来源**：不再自动创建，可以停用和删除；多端同时播放、风控熔断、生活事件、导出临时目录都只存在于 115 插件里。
- **资源协议补可选能力**，核心按插件声明调用：`status`（健康检查、向导、汇总）、`changes`（插件推送变动目录，宿主做定向同步）、`delete`、`known_scan`。目录身份统一用 `browse` 返回的 `ResourceListing.ancestors`，所有来源都能追踪目录改名和移动。
- **核心只认 SDK 异常**，错误码去掉来源名（`cloud115_rate_limited` → `resource_rate_limited`）。
- **表名和列名去掉 115 痕迹**：`cloud_file` → `media_file`，`file_id` → `identity`，`pickcode` → `playback_locator`，`cloud_file_id` → `media_file_id`；`library_folder.cid` 删除（目录由 `resource_root_id` 定位），`cid_path` → `scope_key`。
- **备份改成打包**：主库快照加各插件的 `state/` 目录；旧的 `.sqlite3` 备份不再支持恢复。
- **不做兼容**：表结构用 `0002` 迁移改名以保住资料库数据；插件的旧凭据、配置、运行状态直接清除，升级后重新登录、重新填写；旧接口、旧格式、旧代码路径全部删除。
- **本地外挂字幕降为宿主逻辑**：它只按路径匹配、不联网，同步时每个视频都要调用，不再作为插件。
- **第三方插件安装、升级不在这次范围**：宿主从插件目录按 manifest 加载插件，运行时已经支持第三方包，安装界面以后再做。
- **一步到位**：不分阶段发布，回归风险靠资源协议契约测试控制。

## 取舍

- 每个插件一个 Python 进程，内存多出约 200–350 MB；只拉起已启用的插件。
- 图片和扫描结果经 JSON 传输，有 base64 和序列化开销；单条消息先设上限，不够再改分块。
- 插件数据明文存在插件目录：插件可以独立演进、独立仓库；代价是数据目录和备份包里有明文凭据，需要按敏感文件保管。
- 不做沙箱：插件直连外部服务是正式能力，使用宿主下发的有效代理；宿主不审计直连请求，
  插件必须自己做日志脱敏、超时和错误映射。宿主 `http` 的审计只覆盖主动使用它的请求。
- 一步到位、迁移不可逆：中间不发布，问题集中在最后一次暴露；升级后 115 要重新登录，各插件 Key 要重新填写。

## 结果

- 宿主不再导入任何 115 代码，停用或删除 115 后系统照常运行。
- 新的资源插件实现协议并声明能力，就能进入同步、播放、删除、健康检查、向导和定向同步，不需要改核心。
- 插件目录可以整体拆到独立仓库，宿主按 SDK 版本做兼容检查。

## 实施记录（2026-10-26）

- 宿主只从 `APP_PLUGINS_DIR`（镜像里 `/app/plugins`，开发时默认仓库 `plugins/`）发现插件；`PluginRegistry` 改为基于 `PluginSupervisor`，`provider()` 返回进程外代理，按需启动、失败返回 `plugin_start_failed`。同一个插件进程崩溃后由 supervisor 退避重启，代理每次调用都取当前连接。
- `resource.changed` 接到 `SyncEngine.source_changed`；备份维护接到 supervisor 的 `flush_all` / `stop_all` / `start_all`；全局和插件级代理变化会发 `host.config_changed`；`media_dir` 固定为 `data/media`，插件返回的本地播放路径由宿主解析真实路径并校验在 `media_dir` 内。
- `app/cloud115/`、`app/tmdb/`、`app/douban/`、`app/fanart/`、`app/subtitles/assrt*`、`app/resources/cloud115*`、`app/resources/local.py`、`app/resources/plugins.py`、`app/plugins/{inprocess,builtin_deps,tmdb_builtin,examples}.py` 全部删除；宿主插件目录只保留 HTTP 和有效代理。
- `0002` 迁移直接删除 `plugin_secret` 表和 `plugin.config` 列；`p115client` 从宿主锁文件移除。
- 宿主测试改为用 `backend/tests/fixtures/fake_plugin` 的副本走真实子进程，停止和删除 115、杀掉插件进程自动恢复、`resource.changed` 定向同步都有端到端覆盖；六个真实插件包的握手测试保留。

### 偏差与取舍

- 当前没有第三方安装流程，从宿主插件目录发现的插件统一记录为内置（`built_in`）。`trust` 不是插件 manifest 字段，插件列表里的信任级别由这个宿主标志推导；将来做第三方安装时在安装入口把它记录为外部插件。
- 字幕插件的未知异常不会被误当成“字幕站暂时不可用”：`RemoteSubtitleProvider` 只原样传递插件声明的 `SubtitleUnavailable`；SDK 对未知异常还原出的 `ResourceUnavailable` 继续向上抛，宿主仍按 `failed` / 502 处理。显式的 `SubtitleUnavailable` 仍映射为 `unavailable` / 409。
- 播放的按令牌跳转限流留在宿主（ADR 0015）。115 插件自己的按 UA 直链缓存、取链配额和多端复制仍由插件负责；宿主 `PlayThrottle` 只作用于 `/play/{token}` 跳转，每分钟每令牌 120 次，超出返回 429 和 `Retry-After`，不改变播放签发和字幕接口。该接线单独提交，见提交记录。

### 后续收敛（2026-10-27）

- SDK 增加插件直连 HTTP 的正式实现（代理、连接池重建、日志脱敏、状态码映射和优先级限速），宿主不审计直连请求的约定不变。
- `assets` 能力和 `plugin.stop` 方法删除；`media_dir`、`context.notify` 总是有值；公开地址变化随 `host.config_changed` 下发。
- `trust` / `built_in` 从插件 manifest 移除，宿主只记录 `built_in`。supervisor 成为启动状态和错误码的唯一来源。
