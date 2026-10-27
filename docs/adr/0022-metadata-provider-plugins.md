# ADR 0022：资料库选择元数据插件

## 状态

已采用（2026-10-05）。

## 背景

ADR 0021 只把 TMDB 的搜索暴露为插件能力，刮削、图片、人物仍直接调用 `TmdbService`，数据库按 `tmdb_id` 绑定条目和人物。这样无法像 Emby 那样给每个资料库选择刮削来源，外置元数据插件也接不进来。

## 决策

- **插件协议**：`MetadataProvider` 提供 `unavailable_reason`、`search`、`find`（按外部 ID 查条目，可选）、`details`、`season`、`page_url`；图片和人物分别是可选的 `metadata.images`（`MetadataImageProvider.image`）和 `metadata.people`（`MetadataPeopleProvider.person`）。同时提供 `metadata.search` 和 `metadata.details` 的插件才能被资料库选用。插件只返回规范化数据，写库、图片缓存、锁定字段仍由核心完成。
- **身份**：条目和人物的身份改为「插件 ID + 插件里的 ID」（`provider_id`、`remote_id`，后者是字符串）。内置 TMDB 的 `provider_id` 是 `builtin.tmdb`；`metadata_field` 的来源用 `provider` 代替 `tmdb`；不再有只写不读的 `media_item_metadata`。项目尚未对外发布，这些结构直接写进基线迁移 `0001_baseline.sql`，不提供旧数据的升级路径。
- **资料库设置**：`library_setting` 的 `metadata_provider` 记录资料库选用的插件，缺省为内置 TMDB。在新建、编辑资料库的对话框里选择（资料库设置接口 `PUT /api/admin/libraries/{id}/settings/metadata_provider` 同样校验）；只能选已安装且已启用的插件，未配置好（例如没填 Key）的可以先选。
- **分派**：新匹配的条目用资料库当前选择的插件；条目记住负责它的插件，之后的刷新、季信息、图片和人物资料都找这个插件，资料库换插件不影响已有条目，需要换来源时对条目「改为其他条目」或重新匹配。
- **不可用时**：插件未安装、停用或报告 `unavailable_reason` 时，只有选用它的资料库的作品进入等待，其他资料库照常匹配。待处理页、健康检查给出第一个问题和处理入口：TMDB 指向系统设置，其他插件指向插件页。
- **接口**：管理端搜索统一为 `GET /api/admin/metadata/search?kind=&query=&library_id=`，按资料库选择的插件搜索；指定、批量指定、重新匹配的请求体用 `provider_id`（可省略，缺省为资料库的插件）和 `remote_id`。`GET /api/admin/libraries/metadata-providers` 列出可选插件，内置的排在前面。删除旧的 `POST /api/tmdb/search`、`GET /api/admin/tmdb/search` 和请求体里的 `tmdb_id`；后台任务重试时把旧任务输入里的 `tmdb_id` 转换为新字段。
- **TMDB 专有功能**：按 TMDB 系列自动建合集只对 TMDB 刮削的条目生效（`tmdb_collection_id`）；TMDB 的 Key 和代理仍在系统设置里配置。图片来源键对 TMDB 保持原路径，其他插件加上插件 ID 前缀，避免缓存冲突。

## 结果

- 资料库可以像 Emby 一样选择刮削插件，内置和外置插件走同一条管线。
- 旧版 Android 客户端的管理页面（待处理、条目管理）与新接口不兼容，需要升级客户端；浏览和播放接口不受影响。
- SDK 版本仍为 `1`：外置插件的子进程协议尚未发布，没有已发布的插件需要兼容。
- 浏览端的评分标签仍显示为 TMDB 评分，换用其他插件后需要按条目的插件显示来源。
