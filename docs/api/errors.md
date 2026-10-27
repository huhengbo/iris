# 错误码

所有接口错误统一为 `{"error": {"code", "message", "details"?}}`：`code` 是稳定的机器可读标识，`message` 是给人看的中文说明（客户端不应依赖其文案），`details` 只在 `validation_error` 里出现，只含字段位置和类型，不回显输入值。

下表由 `backend/app` 里的 `AppError` 用法整理。`backend/tests/test_error_codes_documented.py` 会检查两个方向：代码里新增或删除错误码而没有同步本表，测试会失败。同一个错误码在不同场景可能带不同的 HTTP 状态码，这里都列出。

另有三类错误不经过 `AppError`：请求体校验失败返回 `validation_error`（422），框架级 HTTP 错误返回 `not_found` 或 `http_<状态码>`，未捕获的异常返回 500 且不带内部信息。

客户端按 `code` 和 HTTP 状态把错误归为网络、未登录、无权限、风控、凭据、文件失效、不存在、冲突、校验、服务端等类别（`client/lib/core/api/api_error.dart` 的 `ApiErrorKind`）。新增会影响这些类别的错误码时，同步更新该映射。

| 错误码 | HTTP 状态 |
|---|---|
| `alert_already_open` | 409 |
| `artwork_download_failed` | 400 |
| `batch_in_progress` | 409 |
| `cannot_modify_self` | 400 |
| `database_corrupt` | 500 |
| `delete_confirmation_required` | 409 |
| `empty_selection` | 400 |
| `episode_required` | 400 |
| `file_in_use` | 409 |
| `file_not_ignored` | 409 |
| `file_not_unplaced` | 409 |
| `forbidden` | 403 |
| `idempotency_key_conflict` | 409 |
| `invalid_artwork` | 400 |
| `invalid_artwork_kind` | 400 |
| `invalid_artwork_url` | 400 |
| `invalid_backup` | 422 / 500 |
| `invalid_batch_action` | 400 |
| `invalid_credentials` | 401 |
| `invalid_downloader` | 400 |
| `invalid_extras_threshold` | 400 |
| `invalid_filter` | 400 |
| `invalid_folder` | 400 |
| `invalid_grace_days` | 400 |
| `invalid_json` | 400 |
| `invalid_library_hub` | 400 |
| `invalid_library_name` | 400 |
| `invalid_merge` | 400 |
| `invalid_metadata_field` | 400 |
| `invalid_metadata_value` | 400 |
| `invalid_multipart` | 400 |
| `invalid_order` | 400 |
| `invalid_proxy` | 400 |
| `invalid_public_url` | 400 |
| `invalid_schedule` | 400 |
| `invalid_server_name` | 400 |
| `invalid_source_config` | 400 |
| `invalid_source_name` | 400 |
| `invalid_subtitle` | 422 / 500 |
| `invalid_subtitle_language` | 400 |
| `invalid_username` | 400 |
| `item_has_no_dir` | 400 |
| `last_admin` | 400 |
| `library_folder_overlap` | 409 |
| `library_folder_required` | 400 |
| `library_folder_not_selectable` | 400 |
| `library_name_taken` | 409 |
| `library_setting_reserved` | 400 |
| `media_folder_shared` | 409 |
| `merge_kind_mismatch` | 409 |
| `merge_series_unsupported` | 409 |
| `metadata_downloader_required` | 400 |
| `metadata_not_found` | 404 |
| `metadata_provider_disabled` | 409 |
| `metadata_provider_not_ready` | 409 |
| `metadata_unavailable` | 502 |
| `multipart_required` | 415 |
| `no_library` | 400 |
| `no_work` | 409 |
| `not_favoriteable` | 400 |
| `not_found` | 404 |
| `not_pending_file` | 409 |
| `not_playable` | 400 |
| `not_watchlistable` | 400 |
| `payload_too_large` | 413 |
| `plugin_disabled` | 409 |
| `plugin_in_use` | 409 |
| `plugin_installed` | 409 |
| `plugin_manifest_invalid` | 409 |
| `plugin_manifest_mismatch` | 409 |
| `plugin_not_installed` | 409 |
| `plugin_sdk_incompatible` | 409 |
| `plugin_start_failed` | 409 |
| `primary_cannot_match` | 400 |
| `public_url_not_configured` | 409 |
| `resource_delete_unsupported` | 409 |
| `resource_not_found` | 404 |
| `resource_plugin_disabled` | 409 / 503 |
| `resource_plugin_required` | 422 |
| `resource_rate_limited` | 429 / 503 |
| `resource_source_disabled` | 409 / 503 |
| `resource_source_in_use` | 409 |
| `resource_source_singleton` | 409 |
| `resource_unavailable` | 502 / 503 |
| `restore_confirmation_required` | 400 |
| `restore_failed` | 500 |
| `selection_too_large` | 413 |
| `session_not_stoppable` | 409 |
| `source_name_taken` | 409 |
| `subtitle_download_not_allowed` | 403 |
| `subtitle_limit_reached` | 409 |
| `subtitle_provider_failed` | 502 |
| `subtitle_provider_unavailable` | 409 |
| `subtitle_rate_limited` | 429 |
| `sync_running` | 409 |
| `task_already_failed` | 409 |
| `task_already_running` | 409 |
| `task_cancelled` | 409 |
| `task_not_retryable` | 409 |
| `task_retry_failed` | 500 |
| `too_many_attempts` | 429 |
| `unauthorized` | 401 |
| `unknown_library` | 400 |
| `unknown_metadata_provider` | 400 / 409 |
| `username_taken` | 409 |
| `weak_password` | 400 |
| `work_matched` | 409 |
| `work_not_unmatched` | 409 |
| `wrong_password` | 400 |
