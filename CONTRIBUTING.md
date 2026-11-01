# 参与贡献

欢迎提交 Issue 和 Pull Request。参与前请先阅读 [行为准则](CODE_OF_CONDUCT.md)；发现安全问题请按 [SECURITY.md](SECURITY.md) 私下报告。

## 先沟通再动手

- 修 bug、补测试、改文档可以直接提 PR。
- 新功能和较大的改动请先开 Issue 讨论，避免方向不一致白费功夫。
- 架构层面的取舍记录在 [`docs/adr/`](docs/adr/)，术语以 [`CONTEXT.md`](CONTEXT.md) 为准，界面规范见 [`DESIGN.md`](DESIGN.md)。改动与某条 ADR 冲突时，请在 PR 里说明并新增或修订 ADR。

## 开发环境

需要 Python 3.13 和 [uv](https://docs.astral.sh/uv/)、Flutter 3.47（Dart 3.13）。启动方式见 [README 的「本地开发」](README.md#本地开发)。

```sh
scripts/sync-plugins.sh   # 首次拉取代码或修改插件依赖后执行
scripts/check.sh          # 全部检查，提交前必须通过
```

`scripts/check.sh` 依次运行后端 `ruff format --check`、`ruff check`、`mypy`、`pytest`，以及客户端 `dart format`、`dart analyze --fatal-infos`、`flutter test`。CI（`.github/workflows/ci.yml`）跑同样的检查。

## 代码约定

- 后端：Python 类型标注完整（mypy strict），用 ruff 格式化；`plugins/` 下的代码不得 import `app.*`，只依赖 `packages/iris-plugin-sdk`。
- 客户端：`dart format` 后无差异，`dart analyze --fatal-infos` 无告警；颜色、间距、动效从主题取，不在页面里写散值。
- 修缺陷要附能复现该缺陷的测试；新功能要有对应测试，不要靠删除或放宽断言通过检查。
- 不提交密钥、Cookie、`.env`、`data/` 和 `secrets/`。

## 提交与 PR

- 提交信息使用 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/)，例如 `fix(sync): 修复定向同步误失效`、`feat(client): 收藏页`。
- 一个 PR 只做一件事，说明动机、做法和验证方式；界面改动附截图。
- 用户可感知的变更（新功能、修复、行为调整）记在 [CHANGELOG.md](CHANGELOG.md) 的 `[Unreleased]` 下；发版流程见 [README 的「发布流程」](README.md#发布流程)。
- 接口变更后运行 `backend/.venv/bin/python scripts/update-openapi.py` 更新 `docs/api/openapi.json`。

## 许可

提交贡献即表示你同意以 [Apache-2.0](LICENSE) 授权你的贡献。
