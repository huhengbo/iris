from __future__ import annotations

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from app.api.auth import UserOut
from app.auth.users import Role, User, UserRepository
from app.deps import AdminUser, AppClock, Db, ReadDb

router = APIRouter(prefix="/admin/users", tags=["admin"])


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    role: Role = "user"
    # 能访问全部资料库，包括以后新建的
    all_libraries: bool = False
    # 分配的资料库；不传时分配当时的全部资料库
    library_ids: list[int] | None = Field(default=None, max_length=1000)
    # 是否允许下载在线字幕；管理员始终允许
    allow_subtitle_download: bool = True


class AdminUserOut(UserOut):
    """管理后台的用户列表：比登录接口多出可见范围和最近活动。"""

    all_libraries: bool
    # 最近一次登录或刷新令牌的时间，从没登录过时为空
    last_seen_at: str | None

    @classmethod
    def of(cls, user: User) -> AdminUserOut:
        return cls(
            **UserOut.of(user).model_dump(),
            all_libraries=user.all_libraries,
            last_seen_at=user.last_seen_at,
        )


class UpdateUserRequest(BaseModel):
    role: Role | None = None
    disabled: bool | None = None
    allow_subtitle_download: bool | None = None


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=1, max_length=256)


@router.get("")
def list_users(_: AdminUser, db: ReadDb, clock: AppClock) -> list[AdminUserOut]:
    return [AdminUserOut.of(user) for user in UserRepository(db, clock).list()]


@router.post("", status_code=201)
def create_user(body: CreateUserRequest, _: AdminUser, db: Db, clock: AppClock) -> AdminUserOut:
    user = UserRepository(db, clock).create(
        body.username,
        body.password,
        body.role,
        all_libraries=body.all_libraries,
        library_ids=body.library_ids,
        allow_subtitle_download=body.allow_subtitle_download,
    )
    return AdminUserOut.of(user)


@router.patch("/{user_id}")
def update_user(
    user_id: int, body: UpdateUserRequest, admin: AdminUser, db: Db, clock: AppClock
) -> AdminUserOut:
    users = UserRepository(db, clock)
    return AdminUserOut.of(
        users.update(
            admin,
            user_id,
            role=body.role,
            disabled=body.disabled,
            allow_subtitle_download=body.allow_subtitle_download,
        )
    )


@router.post("/{user_id}/password")
def reset_password(
    user_id: int, body: ResetPasswordRequest, _: AdminUser, db: Db, clock: AppClock
) -> AdminUserOut:
    users = UserRepository(db, clock)
    users.require(user_id)
    return AdminUserOut.of(users.set_password(user_id, body.new_password))


class LibraryAccessBody(BaseModel):
    library_ids: list[int] = Field(max_length=1000)
    # 能访问全部资料库，包括以后新建的；打开时 library_ids 仍保存，关掉后按它分配
    all_libraries: bool = False


@router.get("/{user_id}/libraries")
def get_library_access(
    user_id: int, _: AdminUser, db: ReadDb, clock: AppClock
) -> LibraryAccessBody:
    """分配给用户的资料库。管理员始终能访问全部资料库，不受这里限制。"""
    users = UserRepository(db, clock)
    return LibraryAccessBody(
        library_ids=users.library_ids(user_id),
        all_libraries=users.require(user_id).all_libraries,
    )


@router.put("/{user_id}/libraries")
def set_library_access(
    user_id: int, body: LibraryAccessBody, _: AdminUser, db: Db, clock: AppClock
) -> LibraryAccessBody:
    """调整可见范围；有变化时该用户需要重新登录。"""
    users = UserRepository(db, clock)
    library_ids = users.set_library_ids(user_id, body.library_ids, all_libraries=body.all_libraries)
    return LibraryAccessBody(
        library_ids=library_ids, all_libraries=users.require(user_id).all_libraries
    )


@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: int, admin: AdminUser, db: Db, clock: AppClock) -> Response:
    UserRepository(db, clock).delete(admin, user_id)
    return Response(status_code=204)
