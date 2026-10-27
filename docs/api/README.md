# API 契约

`openapi.json` 是后端运行时 OpenAPI 的排序快照。后端契约测试会比较运行时 schema 和快照，接口字段、分页、状态或错误模型发生变化时，必须先审查变更，再运行下面的命令更新快照：

```bash
backend/.venv/bin/python scripts/update-openapi.py
```

快照不包含密钥、Cookie、播放令牌或运行时数据。
