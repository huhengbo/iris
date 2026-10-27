from __future__ import annotations

from importlib.metadata import version

from fastapi import APIRouter

from app.api.schemas import Out
from app.db import schema_version
from app.deps import AppDatabase

router = APIRouter()


class ServerHealthOut(Out):
    status: str
    version: str
    schema_version: int


@router.get("/health")
def health(database: AppDatabase) -> ServerHealthOut:
    with database.read_session() as connection:
        current = schema_version(connection)
    return ServerHealthOut(status="ok", version=version("iris-backend"), schema_version=current)
