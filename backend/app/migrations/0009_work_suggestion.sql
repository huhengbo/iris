-- 匹配建议（ADR 0030）：后台任务模型为未匹配作品给出的首选候选。
-- 候选为空表示模型查过但没有把握。管理员撤销 AI 自动匹配后，status 记为 reverted，
-- 这一行留作「不再自动匹配同一个候选」的依据；管理员要求重新分析后记为 retired，
-- 不再展示，但仍禁止再次推荐。
CREATE TABLE work_suggestion (
    id INTEGER PRIMARY KEY,
    work_id INTEGER NOT NULL REFERENCES work (id) ON DELETE CASCADE,
    provider_id TEXT,
    remote_id TEXT,
    -- 候选的标题等信息在生成时保存一份，列表展示不必再查插件
    title TEXT,
    original_title TEXT,
    year INTEGER,
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    reason TEXT NOT NULL,
    -- 模型调用工具的记录：[{"tool", "label", "summary", "ok"}]
    steps TEXT NOT NULL DEFAULT '[]',
    model TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'adopted', 'ignored', 'auto_matched', 'reverted', 'retired')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX work_suggestion_work ON work_suggestion (work_id, status);
