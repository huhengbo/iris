"""Emby 对外 ID：内部 ID × 10 + 类型位（ADR 0024）。

对外是十进制字符串，用来区分资料库、条目、季、单集、人物和版本；解码失败或类型
不符时按不存在处理，避免把内部 ID 暴露成可直接猜测的资源地址。
"""

from __future__ import annotations

from app.errors import NotFound

LIBRARY = 1
ITEM = 2
SEASON = 3
EPISODE = 4
PERSON = 5
VERSION = 6

_TYPE_BITS = {
    "CollectionFolder": LIBRARY,
    "Movie": ITEM,
    "Series": ITEM,
    "Season": SEASON,
    "Episode": EPISODE,
    "Person": PERSON,
    "MediaSource": VERSION,
}


def encode(internal_id: int, type_bit: int) -> str:
    return str(internal_id * 10 + type_bit)


def decode(value: str | int, *, expected: int | None = None) -> tuple[int, int]:
    """返回 (内部 ID, 类型位)；格式不对、类型不符或 ID 不存在时抛 404。"""
    try:
        raw = int(value)
    except (TypeError, ValueError):
        raise NotFound("ID 不合法") from None
    if raw <= 0:
        raise NotFound("ID 不合法")
    internal_id, type_bit = divmod(raw, 10)
    if internal_id <= 0 or type_bit not in (LIBRARY, ITEM, SEASON, EPISODE, PERSON, VERSION):
        raise NotFound("ID 不合法")
    if expected is not None and type_bit != expected:
        raise NotFound("ID 类型不匹配")
    return internal_id, type_bit


def type_bit_for(item_type: str) -> int:
    return _TYPE_BITS[item_type]
