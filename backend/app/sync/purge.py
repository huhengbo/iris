from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import timedelta

from app.clock import Clock, to_iso
from app.library.catalog import CatalogRepository

# 到期的视频超过资料库的一成、并且多于 5 个时不自动删除，留给管理员确认：
# 成批消失更可能是目录被误移走或 115 出了问题，而不是真的删了这么多
HOLD_RATIO = 0.1
HOLD_MIN_COUNT = 5


@dataclass(frozen=True)
class PurgePlan:
    library_id: int
    library_name: str
    # 失效超过保留期的视频数和资料库的视频总数（不含附加内容）
    due: int
    total: int

    @property
    def held(self) -> bool:
        return self.due > HOLD_MIN_COUNT and self.due > self.total * HOLD_RATIO


class MissingFilePurger:
    """同步删除：115 上消失超过保留期的文件连同它的版本、条目和播放记录一起删除（ADR 0007）。

    只删数据库记录，不动 115 上的文件。图片由调用方随后清理。
    """

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        self._db = connection
        self._clock = clock

    def plan(self, library_id: int, grace_days: int) -> PurgePlan | None:
        row = self._db.execute(
            "SELECT l.name, "
            "COUNT(c.id) FILTER (WHERE c.kind = 'video' AND c.state != 'ignored') AS total, "
            "COUNT(c.id) FILTER (WHERE c.kind = 'video' AND c.state = 'missing' "
            "AND c.missing_at <= ?) AS due "
            "FROM library l LEFT JOIN library_folder f ON f.library_id = l.id "
            "LEFT JOIN media_file c ON c.folder_id = f.id WHERE l.id = ? GROUP BY l.id",
            (self._deadline(grace_days), library_id),
        ).fetchone()
        if row is None:
            return None
        return PurgePlan(library_id, row["name"], due=row["due"], total=row["total"])

    def held(self, grace_days: int) -> list[PurgePlan]:
        """等管理员确认的资料库。"""
        ids = [row["id"] for row in self._db.execute("SELECT id FROM library ORDER BY id")]
        plans = [self.plan(library_id, grace_days) for library_id in ids]
        return [plan for plan in plans if plan is not None and plan.held]

    def purge(self, library_id: int, grace_days: int) -> int:
        """删除资料库里失效超过保留期的视频、字幕和 NFO，返回删掉的视频数。"""
        deadline = self._deadline(grace_days)
        folders = "SELECT id FROM library_folder WHERE library_id = ?"
        deleted = self._db.execute(
            f"DELETE FROM media_file WHERE folder_id IN ({folders}) "
            "AND state = 'missing' AND missing_at <= ? RETURNING kind",
            (library_id, deadline),
        ).fetchall()
        videos = sum(1 for row in deleted if row["kind"] == "video")
        if deleted:
            # 版本和字幕随文件级联删除；再删掉没有文件的作品，以及因此变空的条目
            self._db.execute(
                f"DELETE FROM work WHERE folder_id IN ({folders}) AND id NOT IN "
                "(SELECT work_id FROM media_file WHERE work_id IS NOT NULL)",
                (library_id,),
            )
            CatalogRepository(self._db, self._clock).prune()
        return videos

    def _deadline(self, grace_days: int) -> str:
        return to_iso(self._clock.now() - timedelta(days=grace_days))
