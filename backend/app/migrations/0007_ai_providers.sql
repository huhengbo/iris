-- AI 助手的模型接入（ADR 0030）。
-- 提供方的 Key 用应用密钥加密后保存；三种用途（对话、后台任务、向量）各选一个「提供方 + 模型名」。
CREATE TABLE ai_provider (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('openai', 'anthropic')),
    base_url TEXT NOT NULL,
    api_key TEXT,
    -- 最近一次测试连接的结果，没测过为 NULL
    last_test_ok INTEGER CHECK (last_test_ok IN (0, 1)),
    last_test_error TEXT,
    last_tested_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE ai_model_role (
    role TEXT PRIMARY KEY CHECK (role IN ('chat', 'task', 'embedding')),
    -- 被用途引用的提供方不能删除，要先改用途
    provider_id INTEGER NOT NULL REFERENCES ai_provider (id) ON DELETE RESTRICT,
    model TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 每次模型调用一行，只记录不拦截；用户和提供方删除后保留统计
CREATE TABLE ai_usage (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('chat', 'task', 'embedding')),
    user_id INTEGER REFERENCES user (id) ON DELETE SET NULL,
    provider_id INTEGER REFERENCES ai_provider (id) ON DELETE SET NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL,
    ok INTEGER NOT NULL CHECK (ok IN (0, 1))
);

CREATE INDEX ai_usage_created_at ON ai_usage (created_at);
