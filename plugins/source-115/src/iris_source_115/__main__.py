"""宿主执行 `python -m iris_source_115` 时的入口：stdio JSON-RPC 运行时。"""

from iris_plugin_sdk import run

from iris_source_115.plugin import Source115Plugin

if __name__ == "__main__":
    run(Source115Plugin())
