from __future__ import annotations

import tempfile
from pathlib import Path

from iris_plugin_sdk import ResourceProvider, ResourceSource
from iris_plugin_sdk.testing import ResourceSourceContract

from iris_plugin_source_local.plugin import LocalResourceProvider
from tests.sample import FILES, build_tree


class TestLocalSourceContract(ResourceSourceContract):
    def _media_dir(self) -> Path:
        root = getattr(self, "_root", None)
        if root is None:
            root = Path(tempfile.mkdtemp(prefix="iris-local-contract-"))
            build_tree(root)
            self._root = root
        return root

    def make_source(self) -> ResourceSource:
        provider = LocalResourceProvider(self._media_dir(), "source.local")
        return provider.open(1, provider.validate_config({"root": str(self._media_dir())}))

    def make_provider(self) -> ResourceProvider:
        return LocalResourceProvider(self._media_dir(), "source.local")

    def expected_paths(self) -> set[str]:
        return set(FILES)
