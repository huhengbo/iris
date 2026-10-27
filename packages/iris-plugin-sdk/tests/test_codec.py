from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from iris_plugin_sdk import (
    HostCallError,
    InvalidSourceConfig,
    MetadataCredit,
    MetadataNotFound,
    MetadataRecord,
    MetadataUnavailable,
    PlaybackTarget,
    PluginRequestError,
    ResourceEntry,
    ResourceListing,
    ResourceRateLimited,
    ResourceUnavailable,
    SubtitleDownload,
    codec,
)


def test_round_trip_resource_listing_and_bytes() -> None:
    value = ResourceListing(
        "root/子目录",
        "/root/子目录",
        (
            ResourceEntry("root/子目录/电影.mkv", "电影.mkv", "video", 10, "sha", identity="id"),
            ResourceEntry("root/子目录", "子目录", "video", is_dir=True),
        ),
        (("root", "root"), ("root/子目录", "子目录")),
        selectable=False,
    )

    assert codec.loads(codec.dumps(value)) == value
    assert codec.loads(codec.dumps(value)).selectable is False


def test_round_trip_metadata_record_with_tags() -> None:
    value = MetadataRecord(
        "tmdb",
        "550",
        "搏击俱乐部",
        kind="movie",
        genres=("剧情",),
        artwork={"poster": ("a.jpg", "b.jpg")},
        cast=(MetadataCredit("1", "爱德华", profile="p.jpg", character="叙述者"),),
        external_ids={"tmdb": "550", "imdb": "tt0137523"},
        fallback_fields=frozenset({"overview"}),
    )

    restored = codec.loads(codec.dumps(value))

    assert restored == value
    assert isinstance(restored.genres, tuple)
    assert isinstance(restored.fallback_fields, frozenset)


def test_round_trip_playback_target_path_and_datetime() -> None:
    moment = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    value = PlaybackTarget(path=Path("/data/media/a.mkv"), expires_at=moment)

    restored = codec.loads(codec.dumps(value))

    assert restored == value
    assert isinstance(restored.path, Path)
    assert restored.expires_at == moment


def test_round_trip_subtitle_download_bytes() -> None:
    value = SubtitleDownload(b"\x00\x01subtitle", "zh", "srt")

    restored = codec.loads(codec.dumps(value))

    assert restored == value
    assert restored.content == value.content


def test_exception_round_trip() -> None:
    cases = [
        ResourceUnavailable("挂了", fatal=True, status_code=409, code="custom"),
        ResourceRateLimited("太快了", 30, 503),
        MetadataNotFound("没有这个条目"),
        MetadataUnavailable("暂时不可用"),
        PluginRequestError("参数不对", status=422, code="invalid"),
        InvalidSourceConfig("配置不合法"),
        HostCallError("宿主拒绝", code="host_rejected"),
    ]

    for error in cases:
        restored = codec.decode_exception(codec.encode_exception(error))
        assert type(restored) is type(error)
        assert getattr(restored, "message", str(restored)) == getattr(error, "message", str(error))
        for name in ("retry_after", "fatal", "status", "status_code", "code"):
            if hasattr(error, name):
                assert getattr(restored, name) == getattr(error, name)


def test_unknown_exception_becomes_unavailable_without_details() -> None:
    restored = codec.decode_exception(
        {"type": "KeyError", "message": "secret internal detail", "stack": "traceback"}
    )

    assert isinstance(restored, ResourceUnavailable)
    assert "secret internal detail" not in restored.message
    assert "traceback" not in restored.message


def test_unknown_dataclass_is_rejected() -> None:
    import pytest

    with pytest.raises(codec.CodecError):
        codec.decode({"$type": "Nope", "fields": {}})


def test_unknown_dataclass_field_is_codec_error() -> None:
    import pytest

    payload = {
        "$type": "ResourceEntry",
        "fields": {"locator": "a", "relative_path": "a", "kind": "video", "unknown": 1},
    }

    with pytest.raises(codec.CodecError, match="ResourceEntry"):
        codec.decode(payload)


def test_dataclass_validation_error_is_codec_error() -> None:
    import pytest

    # PlaybackTarget 的 __post_init__ 要求 url 和 path 恰好有一个
    with pytest.raises(codec.CodecError, match="PlaybackTarget"):
        codec.decode({"$type": "PlaybackTarget", "fields": {}})
