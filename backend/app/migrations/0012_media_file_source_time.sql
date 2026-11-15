-- 来源给出的文件创建（上传）时间，用作条目的“最近添加”依据。旧记录为空，下次完整同步时回填。
ALTER TABLE media_file ADD COLUMN source_created_at TEXT;
