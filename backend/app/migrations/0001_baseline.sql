-- 光圈数据库基线结构（首次发布，不保留旧版本的升级路径）。
-- 此后的结构变更以 0002 起的增量迁移追加。

CREATE TABLE setting (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    is_secret INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE user (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
    disabled INTEGER NOT NULL DEFAULT 0,
    token_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
, last_seen_at TEXT, all_libraries INTEGER NOT NULL DEFAULT 0,
    -- 允许下载在线字幕；管理员始终允许，不看这一列
    allow_subtitle_download INTEGER NOT NULL DEFAULT 1 CHECK (allow_subtitle_download IN (0, 1)));

CREATE TABLE auth_session (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES user (id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    token_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
, device_name TEXT, platform TEXT, client_version TEXT, ip TEXT, last_seen_at TEXT,
    -- app 是 Web/App 会话，emby 是 Emby 兼容接口的设备（长期有效的设备令牌）
    kind TEXT NOT NULL DEFAULT 'app',
    client_name TEXT,
    device_id TEXT);

CREATE INDEX auth_session_user ON auth_session (user_id);

CREATE INDEX auth_session_device ON auth_session (user_id, device_id) WHERE device_id IS NOT NULL;

CREATE TABLE library (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('movie', 'series')),
    sort_order INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    -- 管理员上传的横版封面；为空时自动取最近入库条目的背景图
    cover_image_id INTEGER REFERENCES image (id) ON DELETE SET NULL
);

CREATE TABLE library_folder (
    id INTEGER PRIMARY KEY,
    library_id INTEGER NOT NULL REFERENCES library (id) ON DELETE CASCADE,
    cid TEXT NOT NULL UNIQUE,
    cid_path TEXT NOT NULL,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resource_root_id INTEGER NOT NULL REFERENCES resource_root (id)
);

CREATE INDEX library_folder_library ON library_folder (library_id);

CREATE TABLE image (
    id INTEGER PRIMARY KEY,
    hash TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('poster', 'backdrop', 'thumb', 'logo', 'still', 'profile')),
    source TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE INDEX image_hash ON image (hash);

CREATE TABLE season (
    id INTEGER PRIMARY KEY,
    media_item_id INTEGER NOT NULL REFERENCES media_item (id) ON DELETE CASCADE,
    number INTEGER NOT NULL,
    name TEXT,
    overview TEXT,
    air_date TEXT,
    poster_path TEXT,
    poster_image_id INTEGER REFERENCES image (id) ON DELETE SET NULL,
    scraped_at TEXT,
    UNIQUE (media_item_id, number)
);

CREATE TABLE episode (
    id INTEGER PRIMARY KEY,
    season_id INTEGER NOT NULL REFERENCES season (id) ON DELETE CASCADE,
    number INTEGER NOT NULL,
    name TEXT,
    overview TEXT,
    air_date TEXT,
    runtime INTEGER,
    rating REAL,
    still_path TEXT,
    still_image_id INTEGER REFERENCES image (id) ON DELETE SET NULL,
    UNIQUE (season_id, number)
);

CREATE TABLE playable (
    id INTEGER PRIMARY KEY,
    media_item_id INTEGER NOT NULL REFERENCES media_item (id) ON DELETE CASCADE,
    episode_id INTEGER UNIQUE REFERENCES episode (id) ON DELETE CASCADE,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX playable_movie ON playable (media_item_id) WHERE episode_id IS NULL;

CREATE TABLE work (
    id INTEGER PRIMARY KEY,
    folder_id INTEGER NOT NULL REFERENCES library_folder (id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    title TEXT,
    alt_title TEXT,
    year INTEGER,
    tmdb_hint INTEGER,
    nfo_tmdb_id INTEGER,
    nfo_imdb_id TEXT,
    nfo_sha1 TEXT,
    state TEXT NOT NULL CHECK (state IN ('pending', 'matched', 'unmatched')),
    media_item_id INTEGER REFERENCES media_item (id) ON DELETE SET NULL,
    manual INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    candidates TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (folder_id, path)
);

CREATE INDEX work_state ON work (state);

CREATE INDEX work_media_item ON work (media_item_id);

CREATE TABLE "cloud_file" (
    id INTEGER PRIMARY KEY,
    folder_id INTEGER NOT NULL REFERENCES library_folder (id) ON DELETE CASCADE,
    rel_path TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('video', 'subtitle', 'nfo')),
    state TEXT NOT NULL CHECK (state IN ('pending_enrich', 'active', 'missing', 'ignored')),
    ignore_reason TEXT,
    file_id TEXT,
    pickcode TEXT,
    size INTEGER,
    sha1 TEXT,
    missing_at TEXT,
    work_id INTEGER REFERENCES work (id) ON DELETE SET NULL,
    detached INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (folder_id, rel_path)
);

CREATE INDEX cloud_file_state ON cloud_file (state);

CREATE INDEX cloud_file_sha1 ON cloud_file (sha1);

CREATE INDEX cloud_file_work ON cloud_file (work_id);

CREATE TABLE version (
    id INTEGER PRIMARY KEY,
    playable_id INTEGER NOT NULL REFERENCES playable (id) ON DELETE CASCADE,
    cloud_file_id INTEGER NOT NULL UNIQUE REFERENCES cloud_file (id) ON DELETE CASCADE,
    attributes TEXT NOT NULL DEFAULT '{}',
    label TEXT NOT NULL,
    manual INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX version_playable ON version (playable_id);

CREATE TABLE subtitle (
    id INTEGER PRIMARY KEY,
    version_id INTEGER NOT NULL REFERENCES version (id) ON DELETE CASCADE,
    -- 在线字幕不关联 cloud_file
    cloud_file_id INTEGER UNIQUE REFERENCES cloud_file (id) ON DELETE CASCADE,
    source TEXT NOT NULL DEFAULT 'external' CHECK (source IN ('external', 'online')),
    language TEXT,
    label TEXT NOT NULL,
    forced INTEGER NOT NULL DEFAULT 0,
    is_default INTEGER NOT NULL DEFAULT 0,
    hearing_impaired INTEGER NOT NULL DEFAULT 0,
    CHECK ((source = 'external') = (cloud_file_id IS NOT NULL))
);

CREATE INDEX subtitle_version ON subtitle (version_id);

-- 在线字幕（ADR 0025）：内容压缩后存在数据库里
CREATE TABLE online_subtitle (
    subtitle_id INTEGER PRIMARY KEY REFERENCES subtitle (id) ON DELETE CASCADE,
    provider_id TEXT NOT NULL,
    remote_id TEXT NOT NULL,
    release TEXT,
    format TEXT NOT NULL CHECK (format IN ('srt', 'ass', 'ssa', 'vtt')),
    sha256 TEXT NOT NULL,
    -- 解码后的 UTF-8 文本大小
    size INTEGER NOT NULL,
    -- zlib 压缩的无 BOM UTF-8 文本
    content BLOB NOT NULL,
    original_encoding TEXT NOT NULL,
    video_size INTEGER,
    video_sha1 TEXT,
    origin TEXT NOT NULL DEFAULT 'manual' CHECK (origin IN ('manual', 'auto')),
    created_by INTEGER REFERENCES user (id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX online_subtitle_sha256 ON online_subtitle (sha256);

-- 自动下载字幕：每个版本、每种语言的最近一次自动搜索，「没有结果」和「失败」按结果退避
CREATE TABLE subtitle_auto_search (
    version_id INTEGER NOT NULL REFERENCES version (id) ON DELETE CASCADE,
    language TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('downloaded', 'none', 'failed')),
    searched_at TEXT NOT NULL,
    PRIMARY KEY (version_id, language)
);

CREATE TABLE credit (
    id INTEGER PRIMARY KEY,
    media_item_id INTEGER NOT NULL REFERENCES media_item (id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES person (id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('cast', 'director', 'writer')),
    character TEXT,
    sort_order INTEGER NOT NULL
);

CREATE INDEX credit_media_item ON credit (media_item_id);

CREATE INDEX credit_person ON credit (person_id);

CREATE TABLE library_access (
    user_id INTEGER NOT NULL REFERENCES user (id) ON DELETE CASCADE,
    library_id INTEGER NOT NULL REFERENCES library (id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, library_id)
);

CREATE INDEX library_access_library ON library_access (library_id);

CREATE INDEX playable_media_item ON playable (media_item_id);

CREATE TABLE playback_state (
    user_id INTEGER NOT NULL REFERENCES user (id) ON DELETE CASCADE,
    playable_id INTEGER NOT NULL REFERENCES playable (id) ON DELETE CASCADE,
    position INTEGER,
    duration INTEGER,
    watched INTEGER NOT NULL DEFAULT 0,
    last_played_at TEXT,
    last_version_id INTEGER REFERENCES version (id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL, stop_requested INTEGER NOT NULL DEFAULT 0, stop_message TEXT,
    PRIMARY KEY (user_id, playable_id)
);

CREATE INDEX playback_state_recent ON playback_state (user_id, last_played_at);

CREATE TABLE play_event (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES user (id) ON DELETE CASCADE,
    playable_id INTEGER NOT NULL REFERENCES playable (id) ON DELETE CASCADE,
    version_id INTEGER REFERENCES version (id) ON DELETE SET NULL,
    client TEXT NOT NULL,
    created_at TEXT NOT NULL,
    device_id TEXT,
    mode TEXT NOT NULL DEFAULT 'web',
    ended_at TEXT,
    watched_seconds INTEGER
);

CREATE INDEX play_event_user ON play_event (user_id, created_at);

CREATE TABLE user_playback_preference (
    user_id INTEGER PRIMARY KEY REFERENCES user (id) ON DELETE CASCADE,
    subtitle_language TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE background_task (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    phase TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'cancel_requested', 'cancelled')),
    progress_done INTEGER NOT NULL DEFAULT 0,
    progress_total INTEGER NOT NULL DEFAULT 0,
    retry_count INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
    error TEXT,
    result TEXT NOT NULL DEFAULT '{}',
    created_by INTEGER REFERENCES user (id) ON DELETE SET NULL,
    idempotency_key TEXT UNIQUE,
    created_at TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE INDEX background_task_status ON background_task (status, id DESC);

CREATE INDEX background_task_kind ON background_task (kind, id DESC);

CREATE TABLE "sync_job" (
    id INTEGER PRIMARY KEY,
    trigger TEXT NOT NULL CHECK (trigger IN ('manual', 'scheduled', 'event')),
    scope TEXT NOT NULL CHECK (scope IN ('all', 'library', 'dir')),
    library_id INTEGER REFERENCES library (id) ON DELETE SET NULL,
    library_name TEXT,
    target TEXT,
    task_id INTEGER REFERENCES background_task(id) ON DELETE SET NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'cancelled')),
    phase TEXT NOT NULL,
    progress_done INTEGER NOT NULL DEFAULT 0,
    progress_total INTEGER NOT NULL DEFAULT 0,
    stats TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    waiting_until TEXT,
    waiting_reason TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE UNIQUE INDEX sync_job_single_running ON sync_job (status) WHERE status = 'running';

CREATE UNIQUE INDEX sync_job_task_id ON sync_job (task_id) WHERE task_id IS NOT NULL;

CREATE TABLE webdav_setting (
    user_id INTEGER PRIMARY KEY REFERENCES user (id) ON DELETE CASCADE,
    enabled INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE INDEX library_access_user ON library_access (user_id, library_id);

CREATE INDEX cloud_file_folder_state_kind
    ON cloud_file (folder_id, state, kind);

CREATE INDEX cloud_file_file_id ON cloud_file (file_id);

CREATE TABLE media_info (
    cloud_file_id INTEGER PRIMARY KEY REFERENCES cloud_file (id) ON DELETE CASCADE,
    size INTEGER,
    sha1 TEXT,
    -- 秒
    duration INTEGER,
    width INTEGER,
    height INTEGER,
    video_codec TEXT,
    -- hdr10、hlg、dolby_vision；SDR 或不知道时为空
    hdr TEXT,
    -- JSON 数组，按文件里的顺序
    audio TEXT NOT NULL DEFAULT '[]',
    subtitles TEXT NOT NULL DEFAULT '[]',
    reported_at TEXT NOT NULL
);

CREATE INDEX play_event_recent ON play_event (created_at);

CREATE INDEX playback_state_last_played ON playback_state (last_played_at);

CREATE TABLE item_label (
    media_item_id INTEGER NOT NULL REFERENCES media_item (id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    PRIMARY KEY (media_item_id, label)
);

CREATE INDEX item_label_label ON item_label (label);

CREATE TABLE collection (
    id INTEGER PRIMARY KEY,
    library_id INTEGER NOT NULL REFERENCES library (id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    sort_title TEXT,
    overview TEXT,
    poster TEXT,
    tmdb_collection_id INTEGER,
    sort_mode TEXT NOT NULL DEFAULT 'release' CHECK (sort_mode IN ('release', 'custom', 'name')),
    smart_filter TEXT,
    automatic INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX collection_library ON collection (library_id, sort_title, id);

CREATE TABLE collection_item (
    collection_id INTEGER NOT NULL REFERENCES collection (id) ON DELETE CASCADE,
    media_item_id INTEGER NOT NULL REFERENCES media_item (id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    manual INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (collection_id, media_item_id)
);

CREATE INDEX collection_item_item ON collection_item (media_item_id);

-- 资料库推荐行（Plex 的 Hub，ADR 0026）：一行可以同时出现在首页和资料库「推荐」标签，顺序按 position
CREATE TABLE library_hub (
    id INTEGER PRIMARY KEY,
    library_id INTEGER NOT NULL REFERENCES library (id) ON DELETE CASCADE,
    -- latest（最近添加）、released（最近上映）、rating（评分最高）、
    -- genre（某个类型）、continue（继续观看）
    kind TEXT NOT NULL,
    -- genre 行的类型名；其他行为 NULL
    arg TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    show_home INTEGER NOT NULL DEFAULT 1 CHECK (show_home IN (0, 1)),
    show_library INTEGER NOT NULL DEFAULT 1 CHECK (show_library IN (0, 1))
);

-- NULL 在 UNIQUE 里彼此不冲突，用 IFNULL 让同一个库的同一种行只能有一条
CREATE UNIQUE INDEX library_hub_unique ON library_hub (library_id, kind, IFNULL(arg, ''));
CREATE INDEX library_hub_order ON library_hub (library_id, position);

CREATE TABLE playlist (
    id INTEGER PRIMARY KEY,
    user_id INTEGER REFERENCES user (id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    shared INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE playlist_item (
    playlist_id INTEGER NOT NULL REFERENCES playlist (id) ON DELETE CASCADE,
    playable_id INTEGER NOT NULL REFERENCES playable (id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (playlist_id, playable_id)
);

CREATE TABLE alert (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    level TEXT NOT NULL CHECK (level IN ('info', 'warning', 'error')),
    message TEXT NOT NULL,
    fingerprint TEXT,
    created_at TEXT NOT NULL,
    read_at TEXT,
    resolved_at TEXT
);

CREATE UNIQUE INDEX alert_open_fingerprint ON alert (fingerprint) WHERE resolved_at IS NULL AND fingerprint IS NOT NULL;

CREATE TABLE scheduled_task (
    key TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    interval INTEGER NOT NULL DEFAULT 86400,
    last_run_at TEXT,
    last_task_id INTEGER REFERENCES background_task (id) ON DELETE SET NULL
);

CREATE TABLE webhook (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL,
    events TEXT NOT NULL DEFAULT '[]',
    secret TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE webhook_delivery (
    id INTEGER PRIMARY KEY,
    webhook_id INTEGER NOT NULL REFERENCES webhook (id) ON DELETE CASCADE,
    event TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'succeeded', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    response TEXT,
    created_at TEXT NOT NULL,
    delivered_at TEXT
);

CREATE INDEX webhook_delivery_recent ON webhook_delivery (webhook_id, id DESC);

CREATE TABLE library_setting (
    library_id INTEGER NOT NULL REFERENCES library (id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (library_id, key)
);

CREATE TABLE plugin (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    built_in INTEGER NOT NULL DEFAULT 0 CHECK (built_in IN (0, 1)),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    config TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
, sdk_version TEXT NOT NULL DEFAULT '1', provides TEXT NOT NULL DEFAULT '[]', requires TEXT NOT NULL DEFAULT '[]', trust TEXT NOT NULL DEFAULT 'built_in',
    -- 网络代理：inherit 沿用全局插件代理，none 不走代理，custom 使用插件自己的地址（ADR 0025）；
    -- proxy 只在 custom 时有值
    proxy_mode TEXT NOT NULL DEFAULT 'inherit' CHECK (proxy_mode IN ('inherit', 'none', 'custom')),
    proxy TEXT);

CREATE TABLE plugin_secret (
    plugin_id TEXT NOT NULL REFERENCES plugin (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (plugin_id, name)
);

CREATE TABLE resource_source (
    id INTEGER PRIMARY KEY,
    plugin_id TEXT NOT NULL REFERENCES plugin (id),
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    config TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'unknown',
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, checked_at TEXT,
    UNIQUE (plugin_id, name)
);

CREATE TABLE resource_root (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES resource_source (id) ON DELETE CASCADE,
    locator TEXT NOT NULL,
    display_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (source_id, locator)
);

CREATE INDEX resource_root_source ON resource_root (source_id);

CREATE INDEX library_folder_resource_root ON library_folder (resource_root_id);

CREATE TABLE "media_item" (
    id INTEGER PRIMARY KEY,
    library_id INTEGER NOT NULL REFERENCES library (id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('movie', 'series')),
    -- 负责这个条目元数据的插件和插件里的条目 ID；资料库换插件后，已有条目仍由原插件刷新
    provider_id TEXT NOT NULL,
    remote_id TEXT NOT NULL,
    title TEXT NOT NULL,
    original_title TEXT,
    en_title TEXT,
    year INTEGER,
    release_date TEXT,
    overview TEXT,
    rating REAL,
    genres TEXT NOT NULL DEFAULT '[]',
    runtime INTEGER,
    -- *_path 是插件给出的图片来源，只有对应插件能下载
    poster_path TEXT,
    poster_image_id INTEGER REFERENCES image (id) ON DELETE SET NULL,
    backdrop_path TEXT,
    backdrop_image_id INTEGER REFERENCES image (id) ON DELETE SET NULL,
    thumb_path TEXT,
    thumb_image_id INTEGER REFERENCES image (id) ON DELETE SET NULL,
    logo_path TEXT,
    logo_image_id INTEGER REFERENCES image (id) ON DELETE SET NULL,
    scraped_at TEXT NOT NULL,
    added_at TEXT NOT NULL,
    sort_title TEXT,
    tagline TEXT,
    studios TEXT NOT NULL DEFAULT '[]',
    countries TEXT NOT NULL DEFAULT '[]',
    -- TMDB 系列 ID，只有 TMDB 刮削的条目有，用于自动合集
    tmdb_collection_id INTEGER,
    content_rating TEXT,
    -- NULL 表示还没有补过缺（新匹配的条目由同步的 filling 阶段处理）
    filled_at TEXT,
    -- 主插件用其他语言顶上的字段（JSON 数组），补缺时当作缺失
    fallback_fields TEXT,
    -- 图片来自哪个插件；NULL 表示沿用条目自己的 provider_id
    poster_provider TEXT,
    backdrop_provider TEXT,
    thumb_provider TEXT,
    logo_provider TEXT,
    UNIQUE (library_id, provider_id, remote_id)
);

CREATE INDEX media_item_added ON media_item (library_id, added_at);

CREATE INDEX media_item_sort_title ON media_item (library_id, sort_title);

CREATE TABLE "person" (
    id INTEGER PRIMARY KEY,
    provider_id TEXT NOT NULL,
    remote_id TEXT NOT NULL,
    name TEXT NOT NULL,
    profile_path TEXT,
    profile_image_id INTEGER REFERENCES image (id) ON DELETE SET NULL,
    original_name TEXT,
    biography TEXT,
    birthday TEXT,
    deathday TEXT,
    place_of_birth TEXT,
    detail_at TEXT,
    UNIQUE (provider_id, remote_id)
);

CREATE TABLE "metadata_field" (
    entity_kind TEXT NOT NULL CHECK (entity_kind IN ('item', 'season', 'episode', 'collection')),
    entity_id INTEGER NOT NULL,
    field TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('provider', 'nfo', 'manual')),
    locked INTEGER NOT NULL DEFAULT 0 CHECK (locked IN (0, 1)),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (entity_kind, entity_id, field)
);

CREATE INDEX metadata_field_entity ON metadata_field (entity_kind, entity_id);

-- 资料库按顺序启用的下载器：元数据下载器、图片获取器、字幕下载器三个有序列表。
-- 某资料库在某个 role 下没有任何行时使用代码里的默认值。plugin_id 不加外键：插件行可能还没写入。
CREATE TABLE library_plugin (
    library_id INTEGER NOT NULL REFERENCES library (id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('metadata', 'image', 'subtitle')),
    plugin_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (library_id, role, plugin_id)
);

-- 条目的外部 ID（tmdb、imdb、tvdb、douban），补缺和取图都按它找
CREATE TABLE media_item_external_id (
    media_item_id INTEGER NOT NULL REFERENCES media_item (id) ON DELETE CASCADE,
    scheme TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (media_item_id, scheme)
);

CREATE INDEX media_item_external_id_value ON media_item_external_id (scheme, value);

-- 每个条目的每个元数据来源：评分按来源分别保存，remote_id 为 NULL 是找不到的负缓存
CREATE TABLE media_item_source (
    media_item_id INTEGER NOT NULL REFERENCES media_item (id) ON DELETE CASCADE,
    provider_id TEXT NOT NULL,
    remote_id TEXT,
    rating REAL,
    checked_at TEXT NOT NULL,
    PRIMARY KEY (media_item_id, provider_id)
);

-- 收藏：电影、剧集、单集和人物，按用户隔离。
-- 目标是多态的，没有外键；删除目标时用触发器清理，避免收藏表留下指向不存在目标的记录。
CREATE TABLE user_favorite (
    user_id INTEGER NOT NULL REFERENCES user (id) ON DELETE CASCADE,
    target_kind TEXT NOT NULL CHECK (target_kind IN ('item', 'episode', 'person')),
    target_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, target_kind, target_id)
);

CREATE INDEX user_favorite_recent ON user_favorite (user_id, created_at DESC);

CREATE TRIGGER user_favorite_media_item_delete AFTER DELETE ON media_item
BEGIN
    DELETE FROM user_favorite WHERE target_kind = 'item' AND target_id = OLD.id;
END;

CREATE TRIGGER user_favorite_episode_delete AFTER DELETE ON episode
BEGIN
    DELETE FROM user_favorite WHERE target_kind = 'episode' AND target_id = OLD.id;
END;

CREATE TRIGGER user_favorite_person_delete AFTER DELETE ON person
BEGIN
    DELETE FROM user_favorite WHERE target_kind = 'person' AND target_id = OLD.id;
END;

-- 稍后观看：想看的电影或整部剧集，按用户隔离；删除条目或用户时级联清理
CREATE TABLE user_watchlist (
    user_id INTEGER NOT NULL REFERENCES user (id) ON DELETE CASCADE,
    item_id INTEGER NOT NULL REFERENCES media_item (id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, item_id)
);

-- 列表按加入时间从新到旧分页
CREATE INDEX user_watchlist_recent ON user_watchlist (user_id, created_at DESC);

INSERT INTO scheduled_task(key, enabled, interval) VALUES
    ('download_subtitles', 1, 86400),
    ('backup', 0, 86400),
    ('optimize_database', 0, 604800),
    ('cleanup_images', 0, 86400),
    ('cleanup_sessions', 0, 86400),
    ('cleanup_play_events', 0, 86400),
    ('refresh_metadata', 0, 2592000),
    ('empty_recycle_bin', 0, 86400),
    ('vacuum', 0, 2592000);
