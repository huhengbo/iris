# ADR 0021：插件 SDK 能力边界

## 状态

已采用（2026-10-04）。部分由 ADR 0028 修订：全部插件改为进程外运行，宿主 `secrets`、`config`、`assets` 能力移除，插件自己保存数据；插件直连外部服务是正式能力，信任级别由宿主记录。

## 决策

插件通过 manifest 声明两类内容：`provides` 表示业务 provider 能力，`requires` 表示需要的宿主 SDK 能力。宿主只向插件提供已批准的 `PluginContext`，插件不能直接访问 SQLite、115 网关、用户令牌、任意文件路径或进程执行接口。

第一版业务能力为：

- `metadata.search`、`metadata.details`、`metadata.images`、`metadata.people`
- `subtitle.search`、`subtitle.download`
- `resource.source`：资源来源的浏览、扫描、读取和播放解析

第一版宿主能力为：

- `http`：带超时、限速和审计的 HTTP 客户端
- `secrets`：插件自己的密钥空间
- `assets`：插件隔离的本地资产存储
- `media_context`：当前媒体的只读上下文
- `logger`：结构化日志

Metadata provider 只返回规范化的候选和元数据；Subtitle provider 只返回候选和下载内容。数据库写入、版本绑定、哈希去重、格式校验、权限和播放地址全部由核心服务完成。

内置插件可在进程内注册；未来第三方插件使用子进程 JSON-RPC，并沿用同一份 manifest 和 SDK 版本，不允许直接加载不受信任的 Python 代码。

## 生命周期与宿主能力（2026-10-04 补充）

- `PluginRegistry.install()` 登记插件并把 manifest 同步到 `plugin` 表，保留管理员设置的启用状态和配置；manifest 未变时不改 `updated_at`。
- 启动时 `start_enabled()` 启动已启用的插件，单个插件启动失败只记日志；管理员启用时启动失败返回 `plugin_start_failed`，启用状态不变。停用时调用 `stop()` 并释放宿主资源，进程退出时统一 `shutdown()`。
- 启用状态以数据库为准（备份恢复会直接改写它），`provider()` 每次检查，已启用但未启动的插件按需启动一次；数据库已停用但进程里还在运行的插件，在下次解析时停止并释放资源。启动失败的插件不再按需重试，直到管理员重新启用。
- 同一插件的启停串行，不同插件各有一把锁，慢插件不会阻塞其他插件。未认证的请求（插件静态页面）用 `running_provider()`，只读取已启动的插件，不触发启动。
- 插件列表返回运行状态 `state`：`running`、`stopped`、`failed`、`not_installed`。数据库里有记录、但当前进程没安装的插件不能启用（`plugin_not_installed`）；管理员可以 `DELETE /api/admin/plugins/{id}` 移除这类旧记录，密钥随外键级联删除，仍被资源来源引用时拒绝（`plugin_in_use`），已安装的插件不能删除（`plugin_installed`）。
- `PluginHost` 按 `requires` 构造 `PluginContext`，未申请的能力为 `None`：
  - `http`：每插件独立限速和连接池，只允许 http/https，超时由宿主固定；只接受 `params`、`headers`、`json`、`data`、`content` 参数，其他参数（包括 `timeout`、`follow_redirects`）直接拒绝。重定向由宿主跟随，最多 5 跳，每一跳都重新校验协议和目标地址并写入审计日志；303 以及 POST 遇到 301/302 时改为 GET。跨源跳转时去掉 `Authorization`、`Cookie`、`Proxy-Authorization` 头。外部插件（`trust == "external"`）不能请求回环、私有、链路本地、保留、组播和未指定地址，主机名在检查时解析；内置插件不受这条限制，以便连接局域网服务。DNS 重绑定造成的「先检查后连接」时间差仍然存在，暂时接受。响应最多 20 MB，按流读取，超出即关闭连接。审计日志只记录真实的方法、主机、状态和耗时，不记录查询参数和请求体。
  - `secrets`：`plugin_secret` 表，按插件 id 派生加密子密钥。
  - `assets`：`data/plugins/<id>/assets/`，只接受单层安全文件名；`content_type` 存在资产目录下插件无法命名的 `.meta/` 子目录里，支持 `get`/`delete`，单个资产上限 10 MB。
  - `media_context`：仅作为 manifest 声明保留，避免已有清单校验失败；宿主不会注入任何对象，媒体上下文随请求对象（如 `SubtitleSearchRequest.media`）传入。
- ~~内置 TMDB 插件只暴露搜索，核心刮削仍走 `TmdbService`。~~ 已由 ADR 0022 取代：元数据协议扩展到详情、季、图片和人物，刮削全部经插件完成，资料库可以选择插件。

## 插件自定义页面（2026-10-04 补充）

声明式配置表单无法覆盖插件的交互需求，插件改为自带管理页面：

- manifest 提供 `admin.ui` 时必须声明 `pages`（页面 id、标题、入口文件），插件实现 `AdminUiProvider`：`ui_root` 指向静态文件目录，`handle(PluginRequest)` 处理页面发来的 API 请求。需要保存设置的插件申请 `config` 宿主能力（插件自己的 JSON 配置，上限 64KB），敏感值放 `secrets`。
- 静态页面由 `/plugins/{id}/ui/...` 提供，响应带 CSP `sandbox allow-scripts allow-forms`，页面运行在不透明来源中，读不到管理后台的 localStorage、Cookie 和 DOM。公共 SDK（`iris-plugin.js`、`iris-plugin.css`）由 `/plugins/_sdk/` 提供。
- 页面只能调用 `/api/plugins/{id}/...`，凭插件作用域令牌访问：由管理员通过 `POST /api/admin/plugins/{id}/session` 申请，HMAC 签名，绑定插件 id、用户和令牌版本，15 分钟过期。它不能当登录令牌用，也不能访问其他插件；用户被停用、降级或改密后立即失效。插件 API 只接受 Bearer，因此对任意来源开放 CORS。
- 令牌经 postMessage 桥交给页面（`iris:ready`/`iris:init`、`iris:refresh-token`/`iris:token`、`iris:toast`），宿主按白名单处理消息。Web 端用 sandbox iframe，Android 端用 WebView（`IrisHost` JavaScriptChannel，导航限制在插件的 ui 目录内），电视端提示到网页或手机上配置。
- 插件处理器的异常和无法序列化的响应统一转为 `plugin_failed`，不向页面泄露细节；`PluginRequestError` 只允许 4xx。先校验令牌再读取请求体，请求体按流累计，上限 1MB。每次调用记一条审计日志（插件、用户、方法、路径、状态、耗时），不记录查询参数和请求体。同名查询参数只保留最后一个。
- 设置页公共组件（2026-10-24 补充，ADR 0025）：服务端 `SettingsEndpoint` 按插件声明的 `SettingField`（text、secret、url、number、bool、select）处理 `GET/PUT settings` 和 `POST settings/test`；secret 存进 `secrets`，只回报是否已设置，传空串保持不变、`null` 清除；非密钥字段传 `null` 恢复默认值。所有字段校验通过后才写入。页面端 `IrisPlugin.settingsForm` 按字段渲染表单，只提交改过的字段。
- 插件网络代理（ADR 0025）：`PluginHttp` 每次请求解析有效代理（插件 `proxy_mode` 为 inherit 时用全局 `plugin.proxy`，none 不走代理，custom 用插件自己的地址），代理变化时重建连接池；不读取环境变量代理，审计日志只记 `proxy=on|off`。代理切换的瞬间正在进行的请求可能失败一次，暂时接受。

已知风险：

- 恶意插件可以把自己的作用域令牌发送出去，但令牌只能调用该插件自己的 API，15 分钟后过期。插件本身属于需要管理员信任安装的代码，这一层隔离只防止它借页面拿到管理员权限。
- 插件静态文件不需要登录即可读取，只包含代码，不包含数据。
- Android WebView 尚未在真机上验证。

## 结果

- provider 可以按能力注册和解析，停用插件后核心不会调用它。
- 插件 API 与 TMDB、字幕站点的具体实现解耦。
- 本地存储和 115 网盘通过 `resource.source` provider 接入；TMDB 和字幕 provider 只消费媒体上下文，与资源来源无关。
- `resource.source` provider 是来源工厂：核心按来源配置调用 `open(config)` 得到来源实例。115 绑定全局账号，只有一个来源；同步、直链、读取、删除和导出等待都由 `source.115` 实例完成，核心只依赖资源协议。来源停用或插件停用时，只影响用到它的资料库目录。
- 以后增加资源搜索、通知、索引等能力时，只需增加新的 capability 和协议，不扩张已有 provider 的权限。
