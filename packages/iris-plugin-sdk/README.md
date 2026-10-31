# Iris Plugin SDK

`iris-plugin-sdk` 是 Iris 进程外插件的公共 SDK，包含协议类型、`plugin.json` manifest、
NDJSON/JSON-RPC 编解码、插件进程运行时、设置页工具和测试辅助。插件只依赖这个包，不导入
宿主的 `app.*`；宿主也使用同一份类型和校验逻辑，两边不做兼容分支。

- 需要 Python 3.13 及以上。
- 完整协议（传输、RPC 方法、manifest 字段、超时）见 SDK 源码仓库中的 `PROTOCOL.md`。
- 许可证：Apache-2.0。

## 安装

```bash
uv add iris-plugin-sdk
# 或
pip install iris-plugin-sdk
```

## 最小插件

插件目录至少包含 `plugin.json` 和入口模块；入口名是 `python -m` 使用的点分模块名。
下面这个例子只实现 `metadata.people` 能力，提供人物资料：

`plugin.json`：

```json
{
  "id": "org.example.people",
  "name": "示例人物资料",
  "version": "0.1.0",
  "description": "演示 metadata.people 能力",
  "sdk": ">=1.0,<2.0",
  "entry": "example_plugin",
  "provides": ["metadata.people"],
  "capabilities": ["logger"]
}
```

`example_plugin.py`：

```python
from __future__ import annotations

from collections.abc import Mapping

from iris_plugin_sdk import MetadataPerson, PluginContext, run


class ExamplePlugin:
    def __init__(self) -> None:
        self.context: PluginContext | None = None

    def start(self, context: PluginContext) -> None:
        self.context = context
        context.logger.info("示例插件已启动")

    def stop(self) -> None:
        pass

    def providers(self) -> Mapping[str, object]:
        return {"metadata.people": self}

    def person(self, remote_id: str) -> MetadataPerson:
        # 换成自己的数据源；找不到时抛 MetadataNotFound。
        return MetadataPerson(remote_id=remote_id, name="示例人物")


if __name__ == "__main__":
    run(ExamplePlugin())
```

插件的 `pyproject.toml` 按普通依赖声明 SDK：

```toml
[project]
name = "iris-plugin-example"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = ["iris-plugin-sdk"]
```

宿主启动插件时先读取同目录的 `plugin.json`，再用插件自己的虚拟环境执行
`python -m <entry>`。`providers()` 返回的键必须和 manifest 的 `provides` 完全一致。

## 版本兼容

- `iris_plugin_sdk.SDK_VERSION` 是当前 SDK 版本（`1.0.0`），`SDK_RANGE` 是默认兼容范围
  （`>=1.0,<2.0`）。
- 每个插件的 `plugin.json` 必须声明 `sdk`，值是逗号分隔的比较式，只支持 `>=`、`<=`、
  `==`、`>`、`<`，例如 `>=1.0,<2.0`。不支持 `~=`、`!=` 和通配符。
- 这个范围必须包含宿主运行时的 SDK 版本。宿主在安装/加载插件和 `initialize` 时都会校验；
  不通过时拒绝加载，初始化会返回 `PluginSdkIncompatibleError`，宿主映射为
  `plugin_sdk_incompatible`。
- SDK 升级大版本时，插件需要声明新的范围并重新跑通契约测试。

测试辅助在 `iris_plugin_sdk.testing`：`RpcClient` 拉起真实子进程做 RPC 冒烟，
`FakeHttp` / `RecordingNotifier` 等用于 provider 单测，`ResourceSourceContract` 校验资源
来源实现。
