from __future__ import annotations

import builtins
import sqlite3
from dataclasses import dataclass
from typing import Literal

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.clock import Clock, to_iso
from app.errors import AppError, NotFound

Role = Literal["admin", "user"]
MIN_PASSWORD_LENGTH = 8

_hasher = PasswordHasher()


@dataclass(frozen=True)
class User:
    id: int
    username: str
    role: Role
    disabled: bool
    token_version: int
    created_at: str
    # 能访问全部资料库，包括以后新建的；管理员也按它和分配的资料库决定
    all_libraries: bool = False
    last_seen_at: str | None = None
    # 普通用户是否可以下载在线字幕；管理员始终可以
    allow_subtitle_download: bool = True

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def validate_new_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AppError(400, "weak_password", f"密码至少需要 {MIN_PASSWORD_LENGTH} 个字符")


class UserRepository:
    """用户的持久化与管理规则。所有会让旧令牌失效的变更都递增 token_version。"""

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        self._db = connection
        self._clock = clock

    def get(self, user_id: int) -> User | None:
        row = self._db.execute(f"SELECT {_COLUMNS} FROM user WHERE id = ?", (user_id,)).fetchone()
        return _to_user(row)

    def require(self, user_id: int) -> User:
        user = self.get(user_id)
        if user is None:
            raise NotFound("用户不存在")
        return user

    def find_credentials(self, username: str) -> tuple[User, str] | None:
        row = self._db.execute(
            f"SELECT {_COLUMNS}, password_hash FROM user WHERE username = ?", (username,)
        ).fetchone()
        user = _to_user(row)
        return None if user is None else (user, row["password_hash"])

    def list(self) -> list[User]:
        rows = self._db.execute(f"SELECT {_COLUMNS} FROM user ORDER BY id").fetchall()
        return [user for row in rows if (user := _to_user(row)) is not None]

    def has_admin(self) -> bool:
        return (
            self._db.execute("SELECT 1 FROM user WHERE role = 'admin' LIMIT 1").fetchone()
            is not None
        )

    def create(
        self,
        username: str,
        password: str,
        role: Role,
        *,
        all_libraries: bool = False,
        library_ids: builtins.list[int] | None = None,
        allow_subtitle_download: bool = True,
    ) -> User:
        """library_ids 为 None 时分配当时的全部资料库（ADR 0009）。"""
        username = username.strip()
        if not username:
            raise AppError(400, "invalid_username", "用户名不能为空")
        validate_new_password(password)
        if library_ids is not None:
            self._require_libraries(set(library_ids))
        now = to_iso(self._clock.now())
        try:
            cursor = self._db.execute(
                "INSERT INTO user (username, password_hash, role, all_libraries, "
                "allow_subtitle_download, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    username,
                    hash_password(password),
                    role,
                    int(all_libraries),
                    int(allow_subtitle_download),
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise AppError(409, "username_taken", "用户名已存在") from exc
        assert cursor.lastrowid is not None
        user_id = cursor.lastrowid
        if library_ids is None:
            self._db.execute(
                "INSERT INTO library_access (user_id, library_id) SELECT ?, id FROM library",
                (user_id,),
            )
        else:
            self._db.executemany(
                "INSERT INTO library_access (user_id, library_id) VALUES (?, ?)",
                [(user_id, library_id) for library_id in set(library_ids)],
            )
        return self.require(user_id)

    def touch(self, user_id: int) -> None:
        """记下最近活动时间，登录和刷新令牌时调用。"""
        self._db.execute(
            "UPDATE user SET last_seen_at = ? WHERE id = ?",
            (to_iso(self._clock.now()), user_id),
        )

    def library_ids(self, user_id: int) -> builtins.list[int]:
        """分配给用户的资料库。打开 all_libraries 时它不起作用，见 visible_library_ids。"""
        self.require(user_id)
        rows = self._db.execute(
            "SELECT a.library_id FROM library_access a JOIN library l ON l.id = a.library_id "
            "WHERE a.user_id = ? ORDER BY l.sort_order, l.id",
            (user_id,),
        ).fetchall()
        return [row["library_id"] for row in rows]

    def set_library_ids(
        self, user_id: int, library_ids: builtins.list[int], *, all_libraries: bool = False
    ) -> builtins.list[int]:
        """调整可见范围。有变化时递增 token_version，已签发的令牌（包括播放令牌）随之失效。

        all_libraries 打开时仍保存勾选的资料库，关掉后按它们分配。
        """
        wanted = set(library_ids)
        self._require_libraries(wanted)
        user = self.require(user_id)
        current = set(self.library_ids(user_id))
        if current != wanted or user.all_libraries != all_libraries:
            self._db.execute("DELETE FROM library_access WHERE user_id = ?", (user_id,))
            self._db.executemany(
                "INSERT INTO library_access (user_id, library_id) VALUES (?, ?)",
                [(user_id, library_id) for library_id in wanted],
            )
            self._db.execute(
                "UPDATE user SET all_libraries = ?, token_version = token_version + 1, "
                "updated_at = ? WHERE id = ?",
                (int(all_libraries), to_iso(self._clock.now()), user_id),
            )
        return self.library_ids(user_id)

    def _require_libraries(self, wanted: set[int]) -> None:
        if not wanted:
            return
        marks = ", ".join("?" for _ in wanted)
        known = {
            row["id"]
            for row in self._db.execute(
                f"SELECT id FROM library WHERE id IN ({marks})", tuple(wanted)
            )
        }
        if known != wanted:
            raise AppError(400, "unknown_library", "资料库不存在，请刷新后重试")

    def update(
        self,
        actor: User,
        user_id: int,
        *,
        role: Role | None,
        disabled: bool | None,
        allow_subtitle_download: bool | None = None,
    ) -> User:
        target = self.require(user_id)
        if target.id == actor.id and (
            (role is not None and role != target.role) or (disabled and not target.disabled)
        ):
            raise AppError(400, "cannot_modify_self", "不能禁用自己或修改自己的角色")
        new_role = role if role is not None else target.role
        new_disabled = disabled if disabled is not None else target.disabled
        new_subtitle = (
            allow_subtitle_download
            if allow_subtitle_download is not None
            else target.allow_subtitle_download
        )
        if target.is_admin and not target.disabled and (new_role != "admin" or new_disabled):
            self._ensure_other_active_admin(target.id)
        changed = new_role != target.role or new_disabled != target.disabled
        if changed:
            self._db.execute(
                "UPDATE user SET role = ?, disabled = ?, token_version = token_version + 1, "
                "updated_at = ? WHERE id = ?",
                (new_role, int(new_disabled), to_iso(self._clock.now()), target.id),
            )
        # 字幕下载权限不改变已签发的令牌：改了之后下一次请求就按新权限判断
        if new_subtitle != target.allow_subtitle_download:
            self._db.execute(
                "UPDATE user SET allow_subtitle_download = ?, updated_at = ? WHERE id = ?",
                (int(new_subtitle), to_iso(self._clock.now()), target.id),
            )
        return self.require(target.id)

    def set_password(self, user_id: int, password: str) -> User:
        validate_new_password(password)
        self._db.execute(
            "UPDATE user SET password_hash = ?, token_version = token_version + 1, updated_at = ? "
            "WHERE id = ?",
            (hash_password(password), to_iso(self._clock.now()), user_id),
        )
        return self.require(user_id)

    def delete(self, actor: User, user_id: int) -> None:
        target = self.require(user_id)
        if target.id == actor.id:
            raise AppError(400, "cannot_modify_self", "不能删除自己")
        if target.is_admin and not target.disabled:
            self._ensure_other_active_admin(target.id)
        self._db.execute("DELETE FROM user WHERE id = ?", (target.id,))

    def _ensure_other_active_admin(self, excluding_id: int) -> None:
        row = self._db.execute(
            "SELECT 1 FROM user WHERE role = 'admin' AND disabled = 0 AND id != ? LIMIT 1",
            (excluding_id,),
        ).fetchone()
        if row is None:
            raise AppError(400, "last_admin", "至少需要保留一个可用的管理员")


_COLUMNS = (
    "id, username, role, disabled, token_version, created_at, all_libraries, "
    "last_seen_at, allow_subtitle_download"
)


def _to_user(row: sqlite3.Row | None) -> User | None:
    if row is None:
        return None
    return User(
        id=row["id"],
        username=row["username"],
        role=row["role"],
        disabled=bool(row["disabled"]),
        token_version=row["token_version"],
        created_at=row["created_at"],
        all_libraries=bool(row["all_libraries"]),
        last_seen_at=row["last_seen_at"],
        allow_subtitle_download=bool(row["allow_subtitle_download"]),
    )
