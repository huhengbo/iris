"""设备会话接口：用户管理自己的会话，管理员管理他人的会话。"""

from __future__ import annotations

from fastapi import APIRouter, Response

from app.api.schemas import Out
from app.auth.devices import DeviceSession, DeviceSessionRepository
from app.auth.users import UserRepository
from app.deps import (
    AdminUser,
    AppClock,
    CurrentUser,
    Db,
    ReadDb,
)

router = APIRouter(prefix="/admin", tags=["admin"])
public_router = APIRouter(tags=["sessions"])


class DeviceSessionOut(Out):
    id: int
    # app：Web/App 登录；emby：第三方客户端设备
    kind: str
    client_name: str | None
    device_name: str | None
    platform: str | None
    client_version: str | None
    ip: str | None
    created_at: str
    expires_at: str
    last_seen_at: str | None


def _session_outs(sessions: list[DeviceSession]) -> list[DeviceSessionOut]:
    return [DeviceSessionOut.model_validate(session) for session in sessions]


@public_router.get("/me/sessions")
def my_sessions(user: CurrentUser, db: ReadDb) -> list[DeviceSessionOut]:
    return _session_outs(DeviceSessionRepository(db).list_by_activity(user.id))


@public_router.delete("/me/sessions/{session_id}", status_code=204)
def delete_my_session(session_id: int, user: CurrentUser, db: Db) -> Response:
    DeviceSessionRepository(db).revoke(user.id, session_id)
    return Response(status_code=204)


@router.get("/users/{user_id}/sessions")
def user_sessions(
    user_id: int, _: AdminUser, db: ReadDb, clock: AppClock
) -> list[DeviceSessionOut]:
    UserRepository(db, clock).require(user_id)
    return _session_outs(DeviceSessionRepository(db).list_by_creation(user_id))


@router.delete("/users/{user_id}/sessions/{session_id}", status_code=204)
def delete_user_session(user_id: int, session_id: int, _: AdminUser, db: Db) -> Response:
    DeviceSessionRepository(db).revoke(user_id, session_id)
    return Response(status_code=204)


@router.delete("/users/{user_id}/sessions", status_code=204)
def delete_all_user_sessions(user_id: int, _: AdminUser, db: Db) -> Response:
    DeviceSessionRepository(db).revoke_all(user_id)
    return Response(status_code=204)
