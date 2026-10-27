from __future__ import annotations

from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.container import AppContainer


def test_app_container_is_the_only_service_entry(client: TestClient) -> None:
    app = cast(FastAPI, client.app)
    container = app.state.container

    assert isinstance(container, AppContainer)
    assert not hasattr(app.state, "database")
