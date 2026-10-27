"""宿主执行 `python -m iris_plugin_source_local` 时的入口：stdio JSON-RPC 运行时。"""

from iris_plugin_sdk import run

from iris_plugin_source_local.plugin import LocalPlugin

if __name__ == "__main__":
    run(LocalPlugin())
