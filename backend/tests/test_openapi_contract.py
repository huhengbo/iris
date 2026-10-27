from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_openapi_snapshot_matches_runtime_contract(client: TestClient) -> None:
    """接口字段、分页和错误模型变化必须显式更新契约快照。"""
    snapshot_path = Path(__file__).parents[2] / "docs" / "api" / "openapi.json"
    expected = json.loads(snapshot_path.read_text(encoding="utf-8"))

    assert cast(FastAPI, client.app).openapi() == expected
