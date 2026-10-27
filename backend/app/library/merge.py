"""补缺的纯合并逻辑：文字字段、评分和图片挑选，不读写数据库（ADR 0025）。

元数据补缺按资料库的下载器顺序依次进行，每个来源只填当前为空（或主插件用其他语言顶上）
且没有锁定的字段；图片按 G2 的语言分档挑选，同档按评分降序、再按获取器给出的顺序。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from iris_plugin_sdk import ImageType, MetadataRecord, RemoteImage

# 补缺可以填的文字字段；title 只在主插件用了其他语言顶上时才补
TEXT_FIELDS: tuple[str, ...] = (
    "original_title",
    "en_title",
    "overview",
    "tagline",
    "year",
    "release_date",
    "runtime",
    "genres",
    "studios",
    "countries",
    "content_rating",
)
# 在库里存成 JSON 数组的字段
LIST_FIELDS = frozenset({"genres", "studios", "countries"})
IMAGE_TYPES: tuple[ImageType, ...] = ("poster", "backdrop", "thumb", "logo")


def is_empty(value: object) -> bool:
    """NULL、空串和空数组都算没有值。"""
    if value is None:
        return True
    if isinstance(value, str):
        return value == "" or value == "[]"
    if isinstance(value, (list, tuple)):
        return len(value) == 0
    return False


def _record_value(record: MetadataRecord, field: str) -> object:
    value: object = getattr(record, field)
    if isinstance(value, tuple):
        return list(value)
    return value


def merge_text(
    current: Mapping[str, Any],
    records: Sequence[MetadataRecord],
    fallback_fields: frozenset[str],
    locked: frozenset[str],
) -> dict[str, object]:
    """按来源顺序补文字字段，返回要写入的列和值（列表字段已经转成 list）。

    当前值为空，或者主插件在这个字段上用了其他语言（fallback_fields）时才补；一旦某个来源
    填上，后面的来源不会再覆盖。title 只在 fallback 时补，避免补缺插件改掉条目标题。
    """
    merged: dict[str, object] = {}
    pending_fallback = set(fallback_fields)
    for record in records:
        if "title" in pending_fallback and "title" not in locked and "title" not in merged:
            title = record.title
            if not is_empty(title):
                merged["title"] = title
                pending_fallback.discard("title")
        for field in TEXT_FIELDS:
            if field in locked or field in merged:
                continue
            value = _record_value(record, field)
            if is_empty(value):
                continue
            if not (is_empty(current.get(field)) or field in pending_fallback):
                continue
            merged[field] = value
            pending_fallback.discard(field)
    return merged


def fill_rating(current: float | None, records: Sequence[MetadataRecord]) -> float | None:
    """条目没有评分时按来源顺序取第一个评分。"""
    if current is not None:
        return None
    return next((record.rating for record in records if record.rating is not None), None)


def _language_base(language: str) -> str:
    return language.strip().lower().split("-", 1)[0]


def _language_rank(language: str | None, metadata_language: str, kind: ImageType) -> int:
    """G2 的语言分档：backdrop 无文字优先，其余元数据语言优先。"""
    if kind == "backdrop":
        if language is None:
            return 0
        if _language_base(language) == _language_base(metadata_language):
            return 1
        return 2 if _language_base(language) == "en" else 3
    if language is not None and _language_base(language) == _language_base(metadata_language):
        return 0
    if language is None:
        return 1
    return 2 if _language_base(language) == "en" else 3


def pick_image(
    images: Sequence[RemoteImage], kind: ImageType, metadata_language: str
) -> RemoteImage | None:
    """同一获取器的候选里挑一张：先按语言分档，再按评分降序，最后按原顺序。"""
    candidates = [image for image in images if image.type == kind and image.source]
    if not candidates:
        return None

    def order(item: tuple[int, RemoteImage]) -> tuple[int, float, int]:
        index, image = item
        # score 为空排最后
        score = float("inf") if image.score is None else -float(image.score)
        return (_language_rank(image.language, metadata_language, kind), score, index)

    return min(enumerate(candidates), key=order)[1]
