-- 待确认动作（ADR 0030）：AI 想执行、但按风险等级需要管理员确认的写操作。
-- 确认人、时间和执行结果都留在这一行里，作为审计记录。
CREATE TABLE ai_pending_action (
    id INTEGER PRIMARY KEY,
    tool TEXT NOT NULL,
    arguments TEXT NOT NULL,
    -- 给管理员看的一句话，例如「把《沙丘》匹配到 TMDB 438631」
    summary TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    -- 发起者；用户删除后动作仍保留
    user_id INTEGER REFERENCES user (id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'executed', 'failed', 'rejected', 'expired')),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by INTEGER REFERENCES user (id) ON DELETE SET NULL,
    result TEXT,
    error TEXT
);

CREATE INDEX ai_pending_action_status ON ai_pending_action (status, created_at);
