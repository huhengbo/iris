from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.schemas import Out
from app.deps import AdminUser, AppClock, AppDatabase, Backups, Settings
from app.errors import AppError
from app.ops.backup import BackupInfo
from app.ops.tasks import TaskRepository, tracked_task

router = APIRouter(prefix="/admin/backups", tags=["admin"])


class BackupOut(Out):
    name: str
    size: int
    created_at: str
    schema_version: int

    @classmethod
    def of(cls, info: BackupInfo) -> BackupOut:
        return cls(
            name=info.name,
            size=info.size,
            created_at=info.created_at,
            schema_version=info.schema_version,
        )


class RestoreOut(Out):
    restored: BackupOut
    safety_backup: BackupOut
    task_id: int


class BackupCreateOut(BackupOut):
    task_id: int


class ConfigServerOut(Out):
    name: str
    public_url: str | None


class ConfigLibraryOut(Out):
    extras_min_mb: int
    missing_grace_days: int


class ConfigExportOut(Out):
    format_version: int
    server: ConfigServerOut
    library: ConfigLibraryOut
    secrets_excluded: bool


@router.get("")
def list_backups(_: AdminUser, backups: Backups) -> list[BackupOut]:
    return [BackupOut.of(info) for info in backups.list()]


@router.post("", status_code=201)
def create_backup(
    admin: AdminUser,
    backups: Backups,
    database: AppDatabase,
    clock: AppClock,
) -> BackupCreateOut:
    with tracked_task(database, clock, "backup_create", created_by=admin.id, total=1) as task:
        info = backups.create()
        task.progress("writing", 1)
        task.result["name"] = info.name
    return BackupCreateOut(**BackupOut.of(info).model_dump(), task_id=task.id)


@router.post("/{name}/restore")
def restore_backup(
    name: str,
    admin: AdminUser,
    backups: Backups,
    database: AppDatabase,
    clock: AppClock,
    confirm: bool = Query(default=False),
) -> RestoreOut:
    if not confirm:
        raise AppError(400, "restore_confirmation_required", "恢复会替换当前数据库，请确认后重试")

    # 恢复会替换数据库文件：先建任务并让安全备份带上它，失败回滚后仍能记录失败。
    with database.session() as connection:
        pending = TaskRepository(connection, clock).create(
            "backup_restore", total=1, created_by=admin.id
        )
    try:
        restored, safety = backups.restore(name)
    except Exception as error:
        with database.session() as connection:
            TaskRepository(connection, clock).finish(pending.id, error=str(error))
        raise

    # 成功恢复后，源备份不包含刚才创建的 pending 任务；在新数据库中建立完成记录。
    with database.session() as connection:
        task = TaskRepository(connection, clock).create(
            "backup_restore", total=1, created_by=admin.id
        )
        repository = TaskRepository(connection, clock)
        repository.progress(task.id, "restoring", 1)
        repository.finish(task.id, result={"name": restored.name, "safety_backup": safety.name})
    return RestoreOut(
        restored=BackupOut.of(restored),
        safety_backup=BackupOut.of(safety),
        task_id=task.id,
    )


@router.get("/config/export")
def export_config(_: AdminUser, settings: Settings) -> ConfigExportOut:
    server = settings.server()
    return ConfigExportOut(
        format_version=1,
        server=ConfigServerOut(name=server.name, public_url=settings.public_url()),
        library=ConfigLibraryOut(
            extras_min_mb=settings.extras_min_mb(),
            missing_grace_days=settings.missing_grace_days(),
        ),
        secrets_excluded=True,
    )
