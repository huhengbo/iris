-- progress 的等待原因改成来源中立的名字。export_busy 是 115 导出任务专用名，
-- export_wait 是旧实现把心跳误写成等待原因的产物；两者含义都是来源正忙。
UPDATE sync_job
SET waiting_reason = 'source_busy'
WHERE waiting_reason IN ('export_busy', 'export_wait');
