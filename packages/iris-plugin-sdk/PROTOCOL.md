# Iris 插件协议 v1

本文是宿主与进程外插件之间的唯一契约。运行时实现见 `iris_plugin_sdk.runtime`，宿主端
（`PluginSupervisor` / `PluginRpcClient`）必须按这里的字段、错误和超时实现；两边都不做
兼容分支。

## 1. 传输

- 每个插件一个子进程，工作目录是插件目录，启动命令为
  `plugins/<id>/.venv/bin/python -m <manifest.entry>`。
- stdin/stdout 是一行一条 JSON 的 NDJSON 流；一行一个 JSON-RPC 2.0 消息，必须是 UTF-8。
- 单条消息上限 64 MB（含换行）。超限时运行时会回一个 `id: null` 的 parse error 并退出；
  不是合法 JSON 时同样回 parse error，但连接继续处理下一条。宿主收到不合法 JSON 或超限
  消息时断开该连接，按插件崩溃处理（supervisor 负责重启）。
- 请求和响应都带 `id`（整数或字符串）；通知没有 `id`。两个方向各自维护 ID 空间，同一时刻
  两个方向可以使用相同 ID。
- 请求可以并发：插件端用 8 个线程处理宿主请求，插件在某个请求里还可以反过来调用宿主。
  插件实现必须自己保证共享状态的线程安全。
- stdout 只用于协议；插件自己的 print 会破坏协议。日志一律通过 `log` 通知：运行时给根
  logger 装了转发 handler，`context.logger` 和标准 `logging.getLogger` 走同一条路，带
  `exc_info` 时附堆栈。
- stderr 只作为进程崩溃诊断，宿主按 debug 记录并带插件 id；正常日志不能写 stderr。

## 2. 初始化

### 2.1 `initialize`（宿主 → 插件）

必须是插件收到的第一个请求；成功后插件已 `start` 并可以处理其他方法。

请求参数：

```json
{
  "sdk_version": "1.0.0",
  "data_dir": "/绝对路径/data/plugins/org.example.fake",
  "media_dir": "/绝对路径/data/media",
  "proxy": "http://127.0.0.1:7890",
  "host": {"version": "0.1.0", "url": "http://127.0.0.1:8080"}
}
```

- `sdk_version`：宿主实现的 SDK 版本，必须是 `x.y.z`。
- `data_dir`：插件独占的数据目录，必须是绝对路径；运行时创建它和 `state/` 子目录。
- `media_dir`：宿主本地媒体挂载目录的绝对路径，总是有值。本地来源插件用它解析来源配置里的
  相对根目录，其他插件用不到。
- `proxy`：当前有效代理，没有时为 `null`。插件直连外部服务时使用 `context.proxy` 或
  SDK 的 `DirectHttpClient`；宿主的 `http` 能力会自行处理代理，不需要插件传。
- `host.version` 宿主版本；`host.url` 宿主对插件可见的地址，没有时为 `null`。公开地址变化
  时宿主发 `host.config_changed` 更新它。

返回：

```json
{
  "sdk_version": "1.0.0",
  "manifest": {
    "id": "org.example.fake",
    "name": "假插件",
    "version": "1.0.0",
    "description": "",
    "sdk": ">=1.0,<2.0",
    "entry": "fake_plugin",
    "provides": ["resource.source"],
    "capabilities": ["logger", "http"],
    "ui": null,
    "pages": []
  }
}
```

- 插件运行时启动时从工作目录的 `plugin.json` 读取 manifest；`initialize` 时校验
  `manifest.sdk` 是否包含宿主的 `sdk_version`，不包含时返回 `-32000` 错误，
  `data.type = "PluginSdkIncompatibleError"`，宿主映射为 `plugin_sdk_incompatible`。
- 宿主用自身 SDK 版本再校验一次；同时要求返回的 `manifest.id` 与 `plugin.json` 一致。
- 重复调用 `initialize` 返回 `-32600`。
- 未初始化时除 `initialize` 外的请求返回 `-32002`。

### 2.2 `host.config_changed`（宿主 → 插件，通知）

```json
{"proxy": "socks5://127.0.0.1:1080", "host_url": "https://iris.example.com"}
```

代理或公开地址变化时发送，没有 `id`，插件不回包。两个字段都总是出现，没有值时是 `null`；
启用状态变化不单独下发。运行时更新 `PluginContext.proxy` 和 `PluginContext.host.url`；如果
插件实现了可选的 `config_changed(*, proxy, host_url)` 钩子，会在工作线程里调用它。

## 3. 生命周期

| 方法 | 参数 | 返回 | 说明 |
|---|---|---|---|
| `plugin.flush` | `{}` | `null` | 备份前调用；插件实现可选的 `flush()` 把内存状态写入 `data_dir`。 |
| `plugin.shutdown` | `{}` | `null` | 先 `flush` 再 `stop`，回包后进程退出。宿主不再发送其他请求。 |

超时：`flush` 与 `shutdown` 各 10 秒。超时后宿主杀进程。插件对象实现 `flush()` 或
`config_changed(*, proxy, host_url)` 时，运行时会调用它们；没有实现就跳过对应通知。

### 3.1 插件端上下文

`initialize` 成功后 `plugin.start(context)` 收到：

- `context.plugin_id`、`context.data_dir`、`context.media_dir`、`context.proxy`、
  `context.host`（版本和宿主地址）。
- `context.logger`：总是有，方法同标准 logger；标准 `logging` 的记录也会转成 `log` 通知。
- `context.http`：只在 `capabilities` 申请后非空。
- `context.notify`：`resource_changed(source_id, locators)`、`progress(until, reason)` 和
  `heartbeat()`；运行时把它们转成第 9 节的通知。来源插件在 `browse` / `scan` 里直接调用
  `context.notify.progress` / `heartbeat`，运行时不再注入 `waiter` 回调。
- `metadata.*` / `image.*` 请求的优先级通过 `iris_plugin_sdk.current_priority()` 读取；
  运行时在调用 provider 前按第 6 节的 `priority` 参数设置 contextvar。

## 4. 能力与 provider

插件在 `provides` 里声明业务能力，在 `capabilities` 里声明宿主能力：

- `provides` 取值：`metadata.search`、`metadata.details`、`metadata.images`、
  `metadata.people`、`image.fetch`、`subtitle.search`、`subtitle.download`、
  `resource.source`、`admin.ui`。
- `capabilities` 取值：`logger`（总是注入）、`http`。没有申请的 `context.http` 为 `null`。

同一能力可以对应多个 RPC 方法。方法找不到、插件没有声明对应能力时返回 `-32601`。

### 4.1 直连外部服务

插件可以直接访问外部服务，这是 SDK 的正式能力，不需要在 `capabilities` 里申请；`http`
能力只表示“由宿主代发请求”。直连必须遵守：

- 使用 `context.proxy`（或 SDK 的 `DirectHttpClient`，它会在 `host.config_changed` 后重建
  连接池），不能读取环境变量里的代理，也不能绕过管理员配置的代理。
- 查询参数、认证头等敏感内容不得进入日志；`DirectHttpClient` 只记录方法、主机、状态和
  耗时，并脱敏 httpx2 自身的请求日志。
- 宿主不审计直连请求，也不为直连的远端故障做统一重试；插件自己负责超时、限速和错误映射
  （`raise_for_status` / `json_object`）。
- 直连失败只影响该插件，不能把远端错误伪装成宿主 `http` 能力的 `HostCallError`。

### 4.2 `provider.unavailable_reason`

```json
{"capability": "metadata.details"}
```

返回 `string | null`；插件没有实现该方法时返回 `-32601`。用于在不发网络请求的情况下
说明为什么当前不可用（例如 Key 没填）。超时 5 秒。

## 5. 资源来源（`resource.source`）

### 5.1 数据与配置约定

- `config` 是宿主保存的来源配置，只由插件解释；宿主不做字段级处理，也不会往里面塞
  任何宿主字段。
- `resource.describe` 和 `resource.validate_config` 之外的方法都带 `source_id`（整数）。
  它是独立参数，不进入 `config`；`provider.open(source_id, config)` 收到它，插件发
  `resource.changed` 时原样带回。
- `locator` 是来源定位符，宿主不解释、原样保存；`browse` 和 `scan` 都统一用 `null`
  表示来源根目录，不再用空字符串。
- 运行时按 `source_id` 缓存来源实例，`config` 变化时重新调用
  `provider.open(source_id, config)`；`host.config_changed` 会清空缓存。
- 所有方法失败时抛 SDK 异常；scan 必须返回完整结果或抛错，不能返回部分结果。
- 来源能力由 `provider.capabilities` 声明，取值是
  `{"status", "changes", "delete", "known_scan"}` 的子集。宿主只调用声明过的能力。

### 5.2 `resource.describe`

```json
{}
```

返回：

```json
{
  "id": "fake.source",
  "multiple": true,
  "capabilities": ["changes", "delete", "known_scan", "status"],
  "config_fields": [
    {"key": "root", "label": "服务器目录", "help": "", "required": true, "default": ""}
  ]
}
```

`config_fields` 是 `ResourceConfigField` 的编码形式。超时 5 秒。

### 5.3 `resource.validate_config`

```json
{"config": {"root": "/media"}}
```

返回规范化后的配置对象。不合法时插件抛 `InvalidSourceConfig`，宿主映射为
`invalid_source_config`。超时 10 秒。

### 5.4 `resource.check`

```json
{"source_id": 1, "config": {"root": "/media"}}
```

返回 `null`。不可用时抛 `ResourceUnavailable`（`fatal` 表示拒绝继续创建同步任务）。
超时 15 秒。

### 5.5 `resource.status`

```json
{"source_id": 1, "config": {"root": "/media"}}
```

返回 `SourceStatus`：

```json
{"state": "ok", "message": null, "resume_at": null, "page": "settings"}
```

- `state`：`ok` | `pending` | `failed` | `limited`。
- `message`：给管理员看的说明。
- `resume_at`：建议重试时间，ISO 8601；没有时为 `null`。
- `page`：插件设置页的页面 id；宿主据此让客户端跳到「插件 › 该插件 › 该页」。

只有 `provider.capabilities` 含 `status` 时宿主才会调用。超时 15 秒。

### 5.6 `resource.browse`

```json
{"source_id": 1, "config": {"root": "/media"}, "locator": null}
```

返回 `ResourceListing`：

```json
{
  "locator": "12",
  "path": "/电影",
  "entries": [
    {"locator": "13", "relative_path": "2024", "kind": "video", "size": null,
     "sha1": null, "is_dir": true, "playback_locator": null, "key": null,
     "identity": "13", "root_path": null}
  ],
  "ancestors": [["12", "电影"]],
  "selectable": true
}
```

- `entries` 只包含直接子项；`relative_path` 是子项名字（不含 `/`）。
- `ancestors` 从来源根到当前目录，包含当前目录；每项是 `[locator, name]`。根目录的
  `ancestors` 为空。
- `selectable` 表示这个目录能否作为资料库目录。来源根、导出临时目录之类的专用目录照常
  返回列表，但 `selectable` 为 `false`；宿主据此拒绝添加，不靠异常判断，也不回退到其他判断。
- 目录不存在抛 `ResourceNotFound`；暂时不可用抛 `ResourceUnavailable`。

超时 60 秒。`locator: null`（或缺席）表示来源根。

### 5.7 `resource.scan`

```json
{
  "source_id": 1,
  "config": {"root": "/media"},
  "locator": null,
  "known": {
    "电影/a.mkv": {
      "locator": "file-1", "relative_path": "电影/a.mkv", "kind": "video",
      "size": 100, "sha1": "abc", "is_dir": false, "playback_locator": "file-1",
      "key": "file-a", "identity": "file-a", "root_path": null
    }
  }
}
```

返回 `ResourceEntry` 数组，`relative_path` 相对于 `locator`，用 `/` 分隔。
`locator: null` 表示来源根目录；参数缺席也按 `null` 处理。

- `known` 只会在 `known_scan` 能力声明后出现；没有已知条目时为 `null`。插件可以复用
  这些条目，但返回结果仍必须是完整集合，不能包含已经不存在于来源的路径。
- 扫描靠 `progress` / `heartbeat` 通知续期：插件等待时发
  `progress {reason, until}`，`until` 是带时区的 ISO 8601，`reason` 必须是
  `rate_limited` 或 `source_busy`；宿主把任务标记为等待到该时间。长时间操作（例如轮询
  导出任务）发 `heartbeat`，即 `progress {"until": null}`，不带 `reason`，只重置空闲
  超时（120 秒）。
- 其他错误同 browse。

### 5.8 `resource.read`

```json
{"source_id": 1, "config": {}, "locator": "file-1", "max_bytes": 1048576}
```

返回 base64 编码的 `bytes`（codec 的 `$bytes`）。文件大于 `max_bytes` 时抛
`ResourceUnavailable`；文件不存在抛 `ResourceNotFound`。超时 60 秒。

### 5.9 `resource.resolve`

```json
{
  "source_id": 1,
  "config": {},
  "locator": "file-1",
  "user_agent": "Iris/1.0",
  "throttle_key": "session-token-hash"
}
```

返回 `PlaybackTarget`，`url` 和 `path` 必须恰好有一个非空：

```json
{"url": "https://cdn.example/a.mkv", "path": null,
 "expires_at": "2026-01-02T03:04:05+00:00", "headers": {}}
```

- `url`：播放器直接访问，宿主返回 302。
- `path`：服务器本地文件，必须位于 `data/media` 内；宿主解析真实路径后校验，越界拒绝。
- `expires_at`：ISO 8601 或 `null`。
- `throttle_key`：同一播放会话的摘要；多端同时播放的缓存按它区分。

超时 15 秒。“文件已不存在”由插件转成 `ResourceNotFound`；限流抛
`ResourceRateLimited`。

### 5.10 `resource.delete`

```json
{"source_id": 1, "config": {}, "locator": "file-1"}
```

返回 `null`。只有 `delete` 能力声明后宿主才会调用；没有声明时宿主不显示删除入口。

### 5.11 `resource.changed`（插件 → 宿主，通知）

```json
{"source_id": 12, "locators": ["12", "34"]}
```

插件发现来源目录有变动时发送。`locators` 是发生变动的目录定位符（不是文件）；宿主据此
做定向同步。只有 `changes` 能力声明后宿主才接受该通知。

## 6. 元数据（`metadata.*`、`image.*`）

第 6 节所有方法的 `params` 都带 `priority`，取 `interactive`（管理员操作、播放）或
`background`（同步任务），缺省为 `interactive`。宿主按请求的实际来源传值；运行时在调用
provider 前把它放进插件端的 contextvar，插件用 `iris_plugin_sdk.current_priority()` 读取，
按它决定限速队列。请求对象本身不再带 `priority`。

```json
{"request": {"$type": "MetadataSearchRequest", "fields": {"title": "..."}}, "priority": "background"}
{"remote_id": "157336", "kind": "movie", "priority": "interactive"}
```

| 方法 | 参数 | 返回 | 超时 |
|---|---|---|---|
| `metadata.search` | `{"request": MetadataSearchRequest}` | `MetadataCandidate[]` | 15 秒 |
| `metadata.find` | `{"kind": "movie"\|"series", "scheme": "tmdb"\|"imdb", "value": "..."}` | `string \| null` | 15 秒 |
| `metadata.details` | `{"remote_id": "...", "kind": "movie"\|"series"}` | `MetadataRecord` | 15 秒 |
| `metadata.season` | `{"remote_id": "...", "number": 1}` | `MetadataSeason` | 15 秒 |
| `metadata.page_url` | `{"kind": "...", "remote_id": "..."}` | `string \| null` | 5 秒 |
| `metadata.person` | `{"remote_id": "..."}` | `MetadataPerson` | 15 秒 |
| `image.images` | `{"request": ImageLookupRequest}` | `RemoteImage[]` | 15 秒 |
| `image.fetch` | `{"source": "...", "width": 780}` | `bytes` | 60 秒 |
| `metadata.image` | `{"source": "...", "width": 780}` | `bytes` | 60 秒 |

- `metadata.search` / `metadata.find` / `metadata.details` / `metadata.season` /
  `metadata.page_url` 走 `metadata.details` 能力的同一个 provider 对象。
- `metadata.person` 走 `metadata.people`。
- `image.images` 和 `image.fetch` 走 `image.fetch`；`metadata.image` 走 `metadata.images`
  （只下载本插件在 `MetadataRecord` 里给出的图片来源）。
- 找不到抛 `MetadataNotFound`，暂时不可用抛 `MetadataUnavailable`，其他异常宿主按
  `MetadataUnavailable` 处理。

`MetadataSearchRequest` / `ImageLookupRequest` / `SubtitleSearchRequest` 等对象按第 9 节
的 `$type` 编码。

## 7. 字幕（`subtitle.*`）

| 方法 | 参数 | 返回 | 超时 |
|---|---|---|---|
| `subtitle.search` | `{"request": SubtitleSearchRequest}` | `SubtitleCandidate[]` | 15 秒 |
| `subtitle.download` | `{"candidate": SubtitleCandidate}` | `SubtitleDownload`（`content` 为 bytes） | 60 秒 |

暂时不可用抛 `SubtitleUnavailable`（可带 `retry_after` 秒数）。

## 8. 管理页面（`admin.ui`）

### 8.1 `admin.request`

```json
{"request": PluginRequest}
```

返回 `PluginResponse`；插件返回 `null` 时宿主按 404 处理。宿主负责静态文件、CSP、
作用域令牌、请求体上限和审计；插件只处理 `/api/plugins/{id}/` 下的业务路径。

- `PluginRequest.query` 只保留同名参数的最后一个值。
- 插件拒绝请求抛 `PluginRequestError`（`status` 只允许 4xx，`code` 原样回给客户端）。
- 其他异常宿主统一映射为 `plugin_failed`，不向页面泄露细节。

超时 15 秒。

## 9. 插件 → 宿主

这些方法由插件发起，宿主在同一个连接上响应；插件运行时按 ID 多路复用。

| 方法 | 参数 | 返回 | 超时 |
|---|---|---|---|
| `http.request` | `{"method": "GET", "url": "https://...", "kwargs": {...}}` | `PluginHttpResponse` | 30 秒 |

`http.request` 的 `kwargs` 只允许 `params`、`headers`、`json`、`data`、`content`；
`method` 只允许 `GET/HEAD/POST/PUT/PATCH/DELETE`。插件等宿主回复最多 30 秒；宿主对远端的
请求超时固定为 15 秒，并负责限速、重定向（最多 5 跳）和审计。响应
`PluginHttpResponse` 包含 `status_code`、`headers`、`content`、`url`、`history`。

以下方法没有 `id`，是通知，宿主不回包：

| 通知 | 参数 | 说明 |
|---|---|---|
| `log` | `{"level": "debug"\|"info"\|"warning"\|"error", "message": "..."}` | 宿主按插件 id 记日志。 |
| `progress` | `{"reason": "source_busy", "until": "2026-01-02T03:04:05+00:00"}` | 同步任务等待到 `until`。`until` 必须带时区，`reason` 必填且只能是 `rate_limited` 或 `source_busy`；缺 `reason`、原因未知、`until` 不带时区或格式不合法时宿主记 error 并丢弃整条通知，不续期。 |
| `progress`（心跳） | `{"until": null}` | 纯心跳：不等待，只续期扫描超时。不能带 `reason`，带了整条通知丢弃。 |
| `resource.changed` | `{"source_id": 12, "locators": ["12"]}` | 目录变动，宿主做定向同步。 |

宿主拒绝插件请求或上游 HTTP 请求失败时返回错误对象，`data.type = "HostCallError"`，
`data.code` 说明原因：

- `host_rejected`：宿主拒绝了参数、资产名、URL、HTTP 方法或重定向等（包括 `PluginHostError`）。
- `host_http_failed`：宿主代发 HTTP 时与远端通信失败（httpx 传输错误）。
- `host_call_failed`：连接断开、超时或宿主返回了没有 `data` 的标准 JSON-RPC 错误。

插件运行时按 `data` 还原成 `HostCallError`，`message` 是宿主给出的真实原因；插件可以读取
`code` 做分支，不能把它当成“插件返回了无法识别的错误”。其他已知 SDK 异常（如
`ResourceUnavailable`）同样按 `type`/`code`/`message` 还原。宿主编码这些错误时不附带堆栈。

## 10. 编解码

JSON 只传基础类型；SDK 类型用带标记的对象表示，宿主和插件都必须用
`iris_plugin_sdk.codec` 编码/解码，不能自己拼字段。

| 值 | 编码 |
|---|---|
| dataclass | `{"$type": "ResourceListing", "fields": {...}}` |
| bytes | `{"$bytes": "<base64>"}` |
| tuple | `{"$tuple": [...]}` |
| frozenset | `{"$frozenset": [...]}` |
| set | `{"$set": [...]}` |
| Path | `{"$path": "/abs/path"}` |
| datetime | `{"$datetime": "2026-01-02T03:04:05+00:00"}` |
| date | `{"$date": "2026-01-02"}` |

只登记 SDK `types.py` 里的 dataclass；未知 `$type` 解码失败。

## 11. 错误

JSON-RPC 错误对象：

```json
{
  "code": -32000,
  "message": "来源登录已失效",
  "data": {
    "type": "ResourceUnavailable",
    "message": "来源登录已失效",
    "code": "resource_credential_invalid",
    "status_code": 409,
    "fatal": true,
    "retry_after": null
  }
}
```

`data` 里只放 `type`、`code`、`message`、`retry_after`、`fatal`、`status_code`、`status`，
**不含堆栈**。`status_code` 用于资源/HTTP 类异常，`status` 只用于 `PluginRequestError` 的
4xx 状态。宿主按 `type` 还原 SDK 异常；未知类型一律还原成
`ResourceUnavailable("插件内部错误")`，不向调用方泄露细节。

标准错误码：

| code | 含义 |
|---|---|
| `-32700` | 不是合法 JSON / 超过 64 MB |
| `-32600` | 不是 JSON-RPC 2.0 / 重复 initialize |
| `-32601` | 方法不存在或插件没有声明对应能力 |
| `-32602` | 参数类型错误 |
| `-32603` | 运行时内部错误 |
| `-32000` | 插件业务异常，细节在 `data` |
| `-32002` | 还没有完成 `initialize` |

## 12. manifest 字段

`plugin.json` 必须是 JSON 对象，字段只能出现一次，未知字段直接拒绝。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | 是 | 小写字母、数字、点、下划线、连字符，最长 64；用作目录名和日志名。 |
| `name` | string | 是 | 展示名称。 |
| `version` | string | 是 | 插件版本。 |
| `sdk` | string | 是 | SDK 兼容范围，逗号分隔的 `>=`、`<=`、`==`、`>`、`<`，例如 `>=1.0,<2.0`；必须包含当前 SDK 版本。 |
| `entry` | string | 是 | 点分模块名，宿主执行 `python -m <entry>`。 |
| `provides` | string[] | 是 | 业务能力，取值见第 4 节；不能重复。 |
| `capabilities` | string[] | 否 | 宿主能力，取值 `logger`、`http`；不能重复。 |
| `description` | string | 否 | 最长 200 字。 |
| `ui` | string \| null | 否 | 相对插件目录的静态文件目录；有页面时必填。 |
| `pages` | object[] | 否 | 页面列表；声明 `admin.ui` 时必须有，反之亦然。 |
| `pages[].id` | string | 是 | 小写字母、数字、连字符，最长 32。 |
| `pages[].title` | string | 是 | 页面标题。 |
| `pages[].entry` | string | 否 | UI 目录内的入口文件，默认 `index.html`；必须存在。 |

`trust` / `built_in` 不是 manifest 字段：宿主只记录一个 `built_in` 标志，插件不能自己声明。
当前从宿主插件目录发现的插件都记录为内置；第三方安装流程以后显式登记为外部插件。

## 13. host 侧超时汇总

| 方法 | 超时 |
|---|---|
| `initialize` | 30 秒 |
| `resource.browse` | 60 秒 |
| `resource.scan` | 120 秒空闲；插件必须用 `progress` / `heartbeat` 续期 |
| `resource.read` | 60 秒 |
| `resource.resolve` | 15 秒 |
| `resource.describe` | 5 秒 |
| `resource.validate_config` | 10 秒 |
| `resource.delete` / `resource.check` / `resource.status` | 60 / 15 / 15 秒 |
| `provider.unavailable_reason` | 5 秒 |
| `metadata.*` | 15 秒（`page_url` 5 秒） |
| `image.*` | 列表 15 秒，下载 60 秒 |
| `subtitle.*` | 搜索 15 秒，下载 60 秒 |
| `admin.request` | 15 秒 |
| `plugin.flush` / `plugin.shutdown` | 10 秒 |
| `http.request` | 30 秒 |

崩溃时进行中的调用转成对应的 Unavailable 异常（同步跳过目录、播放 503、刮削留待下次）；
宿主按 1、2、4…60 秒退避重启，连续 5 次失败后标记 `failed`，直到管理员重新启用。
