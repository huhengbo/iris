-- 语义索引（ADR 0030）：记录每个条目的向量是用哪一版元数据生成的。
-- 向量本身存在 sqlite-vec 的 item_vec 虚表里（按向量模型的维度在运行时创建，rowid 就是条目 ID），
-- 迁移里不创建它：扩展加载失败时不能让启动失败。
CREATE TABLE item_embedding (
    item_id INTEGER PRIMARY KEY REFERENCES media_item (id) ON DELETE CASCADE,
    -- 生成向量时条目的刮削时间和字段修改时间，变了就要检查是否需要重新生成
    stamp TEXT NOT NULL,
    -- 送去生成向量的文本的哈希；文本没变时只更新 stamp，不再调用模型
    text_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
