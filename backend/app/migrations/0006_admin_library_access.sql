-- 管理员的资料库范围改为和普通用户一样可配置（ADR 0009）。
-- 此前管理员无条件看到全部资料库；给已有管理员打开「全部资料库」，升级后可见范围不变。
UPDATE user SET all_libraries = 1 WHERE role = 'admin';
