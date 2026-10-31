-- 插件仓库和已安装清单（ADR 0029）。系统插件仍只由插件目录发现，不写 plugin_install。
CREATE TABLE plugin_repository (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    -- 非空表示来自默认配置（官方仓库），是第几个默认地址；不能删除
    default_slot INTEGER UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','ok','error','unsupported')),
    last_error TEXT,
    index_schema INTEGER,
    index_json TEXT,
    etag TEXT,
    last_checked_at TEXT,
    last_success_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE plugin_install (
    plugin_id TEXT PRIMARY KEY,
    source TEXT NOT NULL CHECK (source IN ('repository', 'local')),
    repository_id INTEGER REFERENCES plugin_repository (id),
    version TEXT NOT NULL,
    -- 保留的上一版目录，供回滚和手工排查
    previous_version TEXT,
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'installed' CHECK (status IN ('installed','missing','failed')),
    error TEXT,
    installed_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK ((source = 'repository') = (repository_id IS NOT NULL))
);

INSERT INTO scheduled_task(key, enabled, interval) VALUES ('check_plugin_updates', 1, 86400);
