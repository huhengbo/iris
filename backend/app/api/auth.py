from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from app.auth.sessions import SessionService, TokenPair
from app.auth.users import User, UserRepository, verify_password
from app.deps import (
    AccessSigner,
    AppClock,
    AppDatabase,
    CurrentUser,
    Db,
    LoginThrottleDep,
    Sessions,
)
from app.errors import AppError

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    device_name: str | None = Field(default=None, max_length=120)
    platform: str | None = Field(default=None, max_length=40)
    client_version: str | None = Field(default=None, max_length=40)
    # 客户端安装时生成的固定 ID：同一设备再次登录时替换旧会话，不再多出一台设备
    device_id: str | None = Field(default=None, min_length=1, max_length=64)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=256)
    device_name: str | None = Field(default=None, max_length=120)
    platform: str | None = Field(default=None, max_length=40)
    client_version: str | None = Field(default=None, max_length=40)
    device_id: str | None = Field(default=None, min_length=1, max_length=64)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    id: int
    username: str
    role: str
    disabled: bool
    created_at: str
    # 普通用户是否可以下载在线字幕；管理员始终可以
    allow_subtitle_download: bool

    @classmethod
    def of(cls, user: User) -> UserOut:
        return cls(
            id=user.id,
            username=user.username,
            role=user.role,
            disabled=user.disabled,
            created_at=user.created_at,
            allow_subtitle_download=user.allow_subtitle_download,
        )


class TokensOut(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "bearer"

    @classmethod
    def of(cls, tokens: TokenPair) -> TokensOut:
        return cls(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_in=tokens.expires_in,
        )


class LoginOut(TokensOut):
    user: UserOut


@router.post("/login")
def login(
    body: LoginRequest,
    request: Request,
    database: AppDatabase,
    clock: AppClock,
    throttle: LoginThrottleDep,
    signer: AccessSigner,
) -> LoginOut:
    client = request.client.host if request.client else "unknown"
    wait = throttle.retry_after_seconds(body.username, client)
    if wait:
        raise AppError(
            429, "too_many_attempts", f"登录失败次数过多，请 {wait // 60 + 1} 分钟后再试"
        )
    # 密码哈希很耗时：在只读连接上校验，通过后才开写事务签发会话，失败的登录不占写锁
    with database.read_session() as connection:
        user = SessionService(connection, clock, signer).check_credentials(
            body.username, body.password
        )
    if user is None:
        throttle.record_failure(body.username, client)
        raise AppError(401, "invalid_credentials", "用户名或密码错误")
    with database.session() as connection:
        tokens = SessionService(connection, clock, signer).start_session(
            user,
            device_name=body.device_name,
            platform=body.platform,
            client_version=body.client_version,
            device_id=body.device_id,
            ip=client,
        )
    throttle.record_success(body.username)
    return LoginOut(**TokensOut.of(tokens).model_dump(), user=UserOut.of(user))


@router.post("/refresh")
def refresh(body: RefreshRequest, sessions: Sessions, request: Request) -> TokensOut:
    client = request.client.host if request.client else None
    return TokensOut.of(
        sessions.refresh(
            body.refresh_token,
            device_name=body.device_name,
            platform=body.platform,
            client_version=body.client_version,
            device_id=body.device_id,
            ip=client,
        )
    )


@router.post("/logout", status_code=204)
def logout(body: RefreshRequest, sessions: Sessions) -> Response:
    sessions.logout(body.refresh_token)
    return Response(status_code=204)


@router.get("/me")
def me(user: CurrentUser) -> UserOut:
    return UserOut.of(user)


@router.post("/password")
def change_password(
    body: ChangePasswordRequest, user: CurrentUser, db: Db, clock: AppClock, sessions: Sessions
) -> TokensOut:
    """修改自己的密码。其他设备的登录全部失效，当前设备换发新令牌继续使用。"""
    users = UserRepository(db, clock)
    credentials = users.find_credentials(user.username)
    if credentials is None or not verify_password(credentials[1], body.current_password):
        raise AppError(400, "wrong_password", "当前密码不正确")
    updated = users.set_password(user.id, body.new_password)
    return TokensOut.of(sessions.issue_for(updated))
