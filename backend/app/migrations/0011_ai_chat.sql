-- AI 对话（ADR 0030）：按用户保存，超过 30 天由清理任务删除，用户也可以随时删除。
-- 不保存模型的思考原文，只保存用时。
CREATE TABLE ai_chat (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES user (id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX ai_chat_user ON ai_chat (user_id, updated_at);

CREATE TABLE ai_chat_message (
    id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL REFERENCES ai_chat (id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    -- 模型调用工具的记录：[{"name", "label", "summary", "ok", "item_ids"}]
    steps TEXT NOT NULL DEFAULT '[]',
    -- 回答里出现的条目，读取时按用户当前的可见范围解析成卡片
    item_ids TEXT NOT NULL DEFAULT '[]',
    suggestions TEXT NOT NULL DEFAULT '[]',
    thinking_seconds REAL,
    -- done 正常结束；stopped 被用户停止（或连接断开）；error 出错
    status TEXT NOT NULL DEFAULT 'done' CHECK (status IN ('done', 'stopped', 'error')),
    error TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX ai_chat_message_chat ON ai_chat_message (chat_id, id);

-- 对话保留 30 天；每天清理一次，默认启用
INSERT OR IGNORE INTO scheduled_task (key, enabled, interval) VALUES ('cleanup_ai_chats', 1, 86400);
