from __future__ import annotations

import pytest

from iris_plugin_sdk import current_priority, priority_scope


def test_priority_defaults_to_interactive() -> None:
    assert current_priority() == "interactive"


def test_priority_scope_restores_previous_value() -> None:
    with priority_scope("background"):
        assert current_priority() == "background"
        with priority_scope("interactive"):
            assert current_priority() == "interactive"
        assert current_priority() == "background"
    assert current_priority() == "interactive"


def test_priority_scope_rejects_unknown_value() -> None:
    with pytest.raises(ValueError, match="未知的优先级"), priority_scope("urgent"):  # type: ignore[arg-type]
        pass
