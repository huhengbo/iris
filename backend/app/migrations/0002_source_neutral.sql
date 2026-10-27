-- 来源中立化：表名、列名和资料库目录都不再假设来源是 115。
-- 只做结构改名和目录键换算，资料库、作品、文件、版本、字幕、播放记录全部保留。

-- 云端文件改成来源中立的媒体文件；来源文件标识和播放定位符不再叫 115 的名字。
ALTER TABLE cloud_file RENAME TO media_file;

ALTER TABLE media_file RENAME COLUMN file_id TO identity;

ALTER TABLE media_file RENAME COLUMN pickcode TO playback_locator;

-- 索引跟着表名和列名改；SQLite 不支持重命名索引，只能删了重建
DROP INDEX cloud_file_state;

DROP INDEX cloud_file_sha1;

DROP INDEX cloud_file_work;

DROP INDEX cloud_file_folder_state_kind;

DROP INDEX cloud_file_file_id;

CREATE INDEX media_file_state ON media_file (state);

CREATE INDEX media_file_sha1 ON media_file (sha1);

CREATE INDEX media_file_work ON media_file (work_id);

CREATE INDEX media_file_folder_state_kind ON media_file (folder_id, state, kind);

CREATE INDEX media_file_identity ON media_file (identity);

-- 引用文件行的外键列统一叫 media_file_id
ALTER TABLE version RENAME COLUMN cloud_file_id TO media_file_id;

ALTER TABLE subtitle RENAME COLUMN cloud_file_id TO media_file_id;

ALTER TABLE media_info RENAME COLUMN cloud_file_id TO media_file_id;

-- 资料库目录：删掉 115 专用的目录 ID，cid_path 换成所有来源通用的 scope_key。
-- cid 带 UNIQUE 约束，SQLite 不能直接 DROP COLUMN，按官方 12 步做法重建表。
-- scope_key 必须和新代码 FolderRef.of(source_id, browse(locator)) 对同一目录生成的值相同：
--   115 用定位符链（旧 cid_path 去掉根目录 0），如 /0/12/34/ → /source/{id}/12/34/；
--   其他来源的定位符是相对来源根的路径，ancestors 是逐级累积路径，
--   如 a/b → /source/{id}/a/a/b/。
CREATE TABLE library_folder_new (
    id INTEGER PRIMARY KEY,
    library_id INTEGER NOT NULL REFERENCES library (id) ON DELETE CASCADE,
    scope_key TEXT NOT NULL,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resource_root_id INTEGER NOT NULL REFERENCES resource_root (id)
);

INSERT INTO library_folder_new (id, library_id, scope_key, path, created_at, resource_root_id)
WITH RECURSIVE
-- 本地来源的旧路径按 / 拆开；每个目录的键要包含逐级累积的定位符，不是每级的名字。
base(folder_id, rest) AS (
    SELECT f.id, trim(f.path, '/')
    FROM library_folder f
    JOIN resource_root rr ON rr.id = f.resource_root_id
    JOIN resource_source rs ON rs.id = rr.source_id
    WHERE rs.plugin_id <> 'source.115'
),
split(folder_id, rest, head, acc, suffix) AS (
    SELECT
        folder_id,
        rest,
        CASE WHEN instr(rest, '/') > 0 THEN substr(rest, 1, instr(rest, '/') - 1) ELSE rest END,
        '',
        ''
    FROM base
    UNION ALL
    SELECT
        p.folder_id,
        CASE WHEN instr(p.rest, '/') > 0
            THEN substr(p.rest, instr(p.rest, '/') + 1) ELSE '' END,
        CASE WHEN instr(CASE WHEN instr(p.rest, '/') > 0
            THEN substr(p.rest, instr(p.rest, '/') + 1) ELSE '' END, '/') > 0
            THEN substr(CASE WHEN instr(p.rest, '/') > 0
                THEN substr(p.rest, instr(p.rest, '/') + 1) ELSE '' END, 1,
                instr(CASE WHEN instr(p.rest, '/') > 0
                    THEN substr(p.rest, instr(p.rest, '/') + 1) ELSE '' END, '/') - 1)
            ELSE CASE WHEN instr(p.rest, '/') > 0
                THEN substr(p.rest, instr(p.rest, '/') + 1) ELSE '' END END,
        CASE WHEN p.acc = '' THEN p.head ELSE p.acc || '/' || p.head END,
        p.suffix || CASE WHEN p.acc = '' THEN p.head ELSE p.acc || '/' || p.head END || '/'
    FROM split p
    WHERE p.rest <> ''
),
keys(folder_id, scope_key) AS (
    SELECT
        f.id,
        '/source/' || rr.source_id || '/' || CASE
            WHEN trim(substr(f.cid_path, 3), '/') = '' THEN ''
            ELSE trim(substr(f.cid_path, 3), '/') || '/'
        END
    FROM library_folder f
    JOIN resource_root rr ON rr.id = f.resource_root_id
    JOIN resource_source rs ON rs.id = rr.source_id
    WHERE rs.plugin_id = 'source.115'
    UNION ALL
    SELECT s.folder_id, '/source/' || rr.source_id || '/' || s.suffix
    FROM split s
    JOIN library_folder f ON f.id = s.folder_id
    JOIN resource_root rr ON rr.id = f.resource_root_id
    WHERE s.rest = ''
)
SELECT f.id, f.library_id, k.scope_key, f.path, f.created_at, f.resource_root_id
FROM library_folder f
JOIN keys k ON k.folder_id = f.id;

DROP TABLE library_folder;

ALTER TABLE library_folder_new RENAME TO library_folder;

CREATE UNIQUE INDEX library_folder_scope_key ON library_folder (scope_key);

CREATE INDEX library_folder_library ON library_folder (library_id);

CREATE INDEX library_folder_resource_root ON library_folder (resource_root_id);

-- 插件的凭据、熔断和生活事件状态搬进插件自己的 state 目录；TMDB 的 Key、代理和校验结果
-- 也由插件自己保存。这些宿主设置不再读取，直接清除，升级后由管理员重新填写。
DELETE FROM setting
WHERE key LIKE 'cloud115.%' OR key LIKE 'life.%' OR key LIKE 'tmdb.%';

-- 插件密钥和普通配置都由插件自己保存在 data/plugins/<id>/state/ 下。宿主不再提供
-- secrets/config 能力，plugin_secret 表和 plugin.config 列直接删除；升级后重新登录、重新填写。
-- config 没有索引、触发器或外键引用，SQLite 可以直接 DROP COLUMN。
DROP TABLE plugin_secret;

ALTER TABLE plugin DROP COLUMN config;

-- 外挂字幕匹配是宿主内置功能，不再登记为插件。
DELETE FROM plugin WHERE id = 'builtin.local-subtitles';
