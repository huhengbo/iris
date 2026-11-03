from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.api.schemas import Out
from app.library.recognize import LibraryKind
from app.library.review import WorkState


class PendingSummaryOut(Out):
    # 资料库选用的元数据插件不能用的原因（没配置、停用等）和处理页面；都能用时为空
    metadata_problem: str | None
    metadata_action: str | None
    # 等待匹配的作品（元数据插件不能用，或上次请求失败），下次同步时匹配
    waiting: int
    unmatched: int
    unplaced: int
    ignored: int


class ItemRefOut(Out):
    id: int
    provider_id: str
    remote_id: str
    title: str


class CandidateOut(BaseModel):
    provider_id: str
    remote_id: str
    title: str
    original_title: str | None
    year: int | None
    # 条目在插件网站上的页面，插件不提供时为空
    url: str | None
    # 匹配度（0–100）和依据，列表已按分数降序；旧数据没有，分数为空
    score: int | None
    reasons: list[str]


class SuggestionStepOut(BaseModel):
    # 模型调用的工具：metadata_search、metadata_details、metadata_season
    tool: str
    # 进行中的人话文案，例如「正在搜索「沙丘」…」
    label: str
    # 完成后的一句话结果
    summary: str
    ok: bool


class SuggestionOut(BaseModel):
    """后台任务模型给出的匹配建议。没有 remote_id 表示模型查过但没有把握。"""

    id: int
    provider_id: str | None
    remote_id: str | None
    title: str | None
    original_title: str | None
    year: int | None
    # 条目在插件网站上的页面
    url: str | None
    # 模型自评的把握，0 到 1
    confidence: float
    reason: str
    steps: list[SuggestionStepOut]
    model: str
    # pending 待处理；adopted 已采用；ignored 已忽略；
    # auto_matched AI 已自动匹配；reverted 自动匹配已撤销
    status: Literal["pending", "adopted", "ignored", "auto_matched", "reverted"]
    created_at: str


class WorkOut(BaseModel):
    id: int
    library_id: int
    library_name: str
    kind: LibraryKind
    path: str
    title: str | None
    year: int | None
    state: WorkState
    manual: bool
    reason: str | None
    candidates: list[CandidateOut]
    file_count: int
    item: ItemRefOut | None
    # 最新的 AI 匹配建议；没有分析过（或没有配置 AI）时为 null
    suggestion: SuggestionOut | None = None


class WorkPage(BaseModel):
    items: list[WorkOut]
    total: int


class FileOut(Out):
    id: int
    library_id: int
    library_name: str
    kind: LibraryKind
    path: str
    size: int | None
    reason: str
    item: ItemRefOut | None


class FilePage(BaseModel):
    items: list[FileOut]
    total: int
    # 当前状态和资料库范围内出现过的原因，供按原因筛选
    reasons: list[str]


class MetadataTarget(BaseModel):
    """手动指定的元数据条目。不传 provider_id 时用资料库选择的插件。"""

    provider_id: str | None = Field(default=None, min_length=1, max_length=100)
    remote_id: str = Field(min_length=1, max_length=200)


class AssignWorkRequest(MetadataTarget):
    pass


class AssignFileRequest(MetadataTarget):
    # 剧集资料库必填：季号（0 是特别篇）和集号
    season: int | None = Field(default=None, ge=0)
    episode: int | None = Field(default=None, ge=1)


MAX_BATCH_SIZE = 100


class WorkIdsRequest(BaseModel):
    work_ids: list[int] = Field(min_length=1, max_length=MAX_BATCH_SIZE)

    @field_validator("work_ids")
    @classmethod
    def deduplicate_ids(cls, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))


class AssignWorkBatchRequest(WorkIdsRequest, MetadataTarget):
    pass


class RematchWorkBatchRequest(WorkIdsRequest):
    pass


class AssignFileBatchRequest(MetadataTarget):
    media_file_ids: list[int] = Field(min_length=1, max_length=MAX_BATCH_SIZE)
    # 剧集资料库批量指定时，所有文件使用同一季集目标。
    season: int | None = Field(default=None, ge=0)
    episode: int | None = Field(default=None, ge=1)

    @field_validator("media_file_ids")
    @classmethod
    def deduplicate_ids(cls, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))


class MediaFileIdsRequest(BaseModel):
    media_file_ids: list[int] = Field(min_length=1, max_length=MAX_BATCH_SIZE)

    @field_validator("media_file_ids")
    @classmethod
    def deduplicate_ids(cls, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))


class PendingDeleteRequest(BaseModel):
    work_ids: list[int] = Field(default_factory=list, max_length=MAX_BATCH_SIZE)
    media_file_ids: list[int] = Field(default_factory=list, max_length=MAX_BATCH_SIZE)

    @field_validator("work_ids", "media_file_ids")
    @classmethod
    def deduplicate_ids(cls, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def not_empty(self) -> PendingDeleteRequest:
        if not self.work_ids and not self.media_file_ids:
            raise ValueError("至少选择一个作品或文件")
        return self


class BatchItemError(BaseModel):
    code: str
    message: str


class BatchAssignmentResult(BaseModel):
    id: int
    item_id: int | None = None
    error: BatchItemError | None = None


class BatchAssignmentOut(Out):
    task_id: int
    requested: int
    succeeded: int
    failed: int
    unprocessed: int
    results: list[BatchAssignmentResult]


class MetadataResultOut(BaseModel):
    provider_id: str
    remote_id: str
    title: str
    original_title: str | None
    year: int | None
    overview: str | None
    url: str | None
