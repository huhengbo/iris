# source-115

Iris 的 115 网盘资源来源插件。进程外运行，只依赖 `iris-plugin-sdk`、`p115client` 和
`python-socks`，不导入宿主的 `app.*`。宿主通过 stdio JSON-RPC 调用 `resource.source` 和
`admin.ui`。

## 术语

宿主的通用术语见根目录 `CONTEXT.md`，下面是只在本插件里有意义的概念。

| 术语 | 定义 |
|---|---|
| 115 凭据 | 系统唯一 115 账号的登录 Cookie，通过扫码或手动填写获得。 |
| 设备身份 | 扫码登录时选择的 115 客户端类型（网页、安卓、支付宝小程序等）。同一身份同一时间只允许一个会话。 |
| 请求调度器 | 所有 115 请求的唯一出口：全局限速、播放类请求优先于同步类请求、遇到风控时熔断。 |
| 风控 | 115 以 HTTP 405 返回 HTML 拦截页，表示请求被防火墙拦截。被拦的那组接口要暂停并退避，不当作凭据失效处理；插件把它映射成来源状态 `limited`，由宿主展示恢复时间。 |
| 熔断 | 遇到风控后在退避时间内拒绝同一组接口的请求，状态持久化，重启不会提前解除。 |
| 接口分组 | 115 接口按风控拦截范围分成网页版（列目录、导出、复制、删除等）和 App 版（取直链、App 版生活事件）两组，一组被拦不影响另一组。 |
| 直链 | 115 返回的带时效 CDN 下载地址，访问时的 User-Agent 必须与取链时一致，所以宿主播放跳转时按播放器自己的 UA 取链。 |
| 生活事件 | 115「最近记录」里的操作记录（上传、接收、复制、移动、改名、删除）。开启监听后定时拉取，把有变动的目录攒一会儿，以 `resource.changed` 推给宿主做定向同步；默认关闭，开启时会打开账号的「最近记录」开关。 |
| 多端同时播放 | 可选功能：同一文件已有别的播放会话拿着直链时，复制一份副本并用副本取直链，避开单个直链 3 个连接的上限；副本几秒后删除。会写入 115，默认关闭。 |
| 导出目录树 | 115 提供的目录快照导出。扫描时用它一次拿到整棵目录，只对有变化的目录再补全，避免逐级列目录触发风控。 |

## 使用

1. 在管理后台「插件」页启用「115 网盘」，打开它的账号页，选择设备身份后扫码登录，或粘贴 Cookie。设备身份选页面推荐的那个（ADR 0013）。
2. 在设置页按需开启「生活事件监听」和「多端同时播放」。
3. 在「资料库」里添加资源来源目录并同步。

## 目录

```
plugin.json                 manifest：id source.115、入口 iris_source_115、两个页面
pyproject.toml              iris-plugin-sdk（path 依赖）、p115client、python-socks；ruff/mypy/pytest 配置
src/iris_source_115/
  __main__.py               python -m iris_source_115：run(Source115Plugin())
  plugin.py                 装配来源、设置、生活事件和页面
  provider.py               ResourceProvider / ResourceSource：浏览、扫描、读取、解析、删除
  life.py                   生活事件后台线程、双游标、resource.changed 推送
  admin.py                  账号页/设置页接口（admin.request）
  service.py                凭据 + 扫码登录 + 经调度器的网关调用
  credential.py             state/credential.json
  scheduler.py              全局限速、按网页版/App 版分组的熔断
  p115.py                   p115client 网关
  gateway.py                网关数据类型和协议
  copies.py                 多端同时播放的副本
  links.py / throttle.py    直链缓存和播放限流
ui/                         index.html（账号）、settings.html（设置）、对应 JS
tests/                      fake 网关、直接调用测试、真实子进程 RPC 契约测试
```

## 状态文件

全部在 `data_dir/state/`，明文 JSON，权限 `0600`。`data_dir` 由宿主的 `initialize` 下发。

| 文件 | 字段 | 说明 |
|---|---|---|
| `credential.json` | `cookie` | 115 Cookie 明文，只在调用网关时读取，不经过页面 |
| | `device` | 设备身份（`alipaymini`、`tv` 等），扫码时保存，Cookie 登录时从 UID 推断 |
| | `user_name` | 登录时 115 返回的账号名 |
| | `saved_at` | 保存时间，ISO 8601 |
| | `invalid_at` | 115 明确拒绝这份凭据的时间；`null` 表示可用 |
| `breaker-web.json` | `level` | 连续风控次数，决定退避档位 |
| | `resume_at` | 熔断截止时间，ISO 8601；`null` 表示正常 |
| `breaker-app.json` | 同上 | App 版接口（取直链、App 版生活事件）单独一组 |
| `life.json` | `armed` | 是否已打开「最近记录」并确定起始游标 |
| | `cursor` | 已处理的最新生活事件 ID；`null` 表示还没开始 |
| | `cursor_time` | 已处理事件里最新的 Unix 秒时间（双游标） |
| | `pending` | 待推送目录：`{目录 ID: {"first": ISO, "last": ISO}}`，用于去抖 |
| | `web_until` | App 版生活事件接口被拒后改用网页版接口的截止时间 |
| | `last_polled_at` / `last_error` | 上次检查时间和管理员可见的错误 |
| `settings.json` | `same_playback` | 多端同时播放开关 |
| | `life_enabled` | 生活事件监听开关 |
| | `life_interval` | 生活事件检查间隔，10–600 秒 |

## 行为说明

- 生活事件在插件自己的后台线程里拉取。有变动的目录安静 45 秒后通过
  `resource.changed(source_id, locators)` 推给宿主；持续有变动时最多等 5 分钟。宿主负责定向同步。
- 本插件自己写入的 `光圈临时目录`（含 `多端播放`）在根列表里隐藏，扫描时跳过；直接浏览时
  照常返回列表，但 `ResourceListing.selectable` 为 `false`，不能作为资料库目录。其中的生活事件被忽略。
- 删除文件和删除目录的事件都推送其上级目录，由宿主列出目录后判断失效（插件拿不到宿主的文件表）。
  移动事件只给出新位置；目录移出资料库的旧位置要等宿主的定时同步发现。
- 115 的根目录 ID 是 `"0"`：`browse(None)` / `scan(None)` 表示浏览来源根，返回的
  `ResourceListing.selectable` 为 `false`；宿主据此拒绝把来源根添加为资料库目录。定位符 `"0"`
  也按根目录处理，不再用异常表达“不能选”。
- 插件直接用 `p115client` 联网，并使用宿主下发的 `context.proxy`：http/https 代理走
  `urllib3_future.ProxyManager`，socks4/4a/5/5h 代理走 `SOCKSProxyManager`（依赖 `python-socks`）。
  API 请求和 CDN 直链下载共用同一个连接池；`host.config_changed` 更新代理时重建连接池。
- 风控熔断按接口分组；网页版受限时同步暂停，App 版受限时播放取不到直链，另一组不受影响。
- 导出目录树遇到 115 上已有导出任务时，通过 `progress` 心跳等待（没有 progress 回调时按 60 秒退避）。
- Cookie 明文保存是 ADR 0028 的决定；数据目录和备份包按敏感数据保管。

## 开发

```bash
cd plugins/source-115
uv sync
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

契约测试有两份：`tests/test_contract.py` 直接调用来源对象，`tests/test_contract_rpc.py`
通过 `tests/subprocess_driver.py` 启动真实插件子进程、走 NDJSON JSON-RPC，均只依赖本包和 SDK。
