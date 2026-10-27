"""Emby 的 PascalCase DTO。接口不套用 Iris 的 Out 模型（ADR 0024）。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from app.emby.ids import ITEM, SEASON, VERSION, encode, type_bit_for
from app.library.browse_models import MediaInfo
from app.library.emby import EmbyEntry
from app.playback.emby import EmbyVersionSource

TICKS = 10_000_000


def ticks(seconds: int | None) -> int:
    return 0 if seconds is None else int(seconds) * TICKS


def emby_date(value: str | None) -> str | None:
    """日期统一成 Emby 的格式：UTC、七位小数、Z 结尾。只有日期的当作当天零点。"""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC)
    return parsed.strftime("%Y-%m-%dT%H:%M:%S.") + f"{parsed.microsecond:06d}0Z"


def _without_nulls(dto: dict[str, Any]) -> dict[str, Any]:
    """Emby 省略空字段；有的客户端把字段声明成非空，收到 null 会解析失败。"""
    return {key: value for key, value in dto.items() if value is not None}


def item_dto(
    entry: EmbyEntry,
    *,
    server_id: str,
    include_media_sources: bool = False,
    api_key: str | None = None,
    media_sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    bit = type_bit_for(entry.type)
    item_id = encode(entry.id, bit)
    dto: dict[str, Any] = {
        "Name": entry.title,
        "ServerId": server_id,
        "Id": item_id,
        "Type": entry.type,
        "IsFolder": entry.type in ("Series", "Season"),
        "MediaType": "Video" if entry.type in ("Movie", "Episode") else "Unknown",
        "SortName": entry.sort_name,
        "DateCreated": emby_date(entry.added_at),
        "PremiereDate": emby_date(entry.release_date),
        "ProductionYear": entry.year,
        "CommunityRating": entry.rating,
        "RunTimeTicks": ticks(entry.runtime * 60 if entry.runtime is not None else None),
        "Overview": entry.overview,
        "Genres": _genres(entry.genres),
        "ContentRating": entry.content_rating,
        "ImageTags": _image_tags(entry),
        "BackdropImageTags": [entry.backdrop_hash] if entry.backdrop_hash else [],
        "ParentBackdropImageTags": [],
        "UserData": user_data(entry),
        "CanDownload": False,
        "LocationType": "FileSystem" if entry.playable_id is not None else None,
        "MediaSources": media_sources or [],
    }
    parent_id = _parent_id(entry)
    if parent_id is not None:
        dto["ParentId"] = parent_id
    if entry.series_id is not None and entry.type != "Series":
        dto["SeriesId"] = encode(entry.series_id, ITEM)
    if entry.series_name:
        dto["SeriesName"] = entry.series_name
    if entry.season_id is not None and entry.type != "Season":
        dto["SeasonId"] = encode(entry.season_id, SEASON)
    if entry.season_name:
        dto["SeasonName"] = entry.season_name
    if entry.season_number is not None:
        dto["ParentIndexNumber"] = entry.season_number
    if entry.index_number is not None:
        dto["IndexNumber"] = entry.index_number
    if entry.child_count:
        dto["ChildCount"] = entry.child_count
        dto["RecursiveItemCount"] = entry.child_count
    if entry.type == "Person":
        dto["BirthDate"] = emby_date(entry.birthday)
        dto["DeathDate"] = emby_date(entry.deathday)
        dto["PlaceOfBirth"] = entry.place_of_birth
        dto["PremiereDate"] = emby_date(entry.birthday)
    if include_media_sources:
        dto["MediaSources"] = media_sources or []
        if media_sources:
            dto["Container"] = media_sources[0].get("Container")
            dto["Path"] = media_sources[0].get("Path")
            dto["Size"] = media_sources[0].get("Size")
    return _without_nulls(dto)


def user_data(entry: EmbyEntry) -> dict[str, Any]:
    position = entry.position if entry.position is not None else 0
    percentage = (
        round(position / entry.duration * 100, 2)
        if entry.duration is not None and entry.duration > 0
        else None
    )
    data: dict[str, Any] = {
        "PlaybackPositionTicks": ticks(position),
        "PlayCount": entry.play_count,
        "IsFavorite": entry.favorite,
        "Played": entry.watched,
        "LastPlayedDate": emby_date(entry.last_played_at),
        "PlayedPercentage": percentage,
    }
    if entry.type in ("Series", "Season"):
        data["UnplayedItemCount"] = entry.unplayed_count
    return _without_nulls(data)


def media_source_dto(
    source: EmbyVersionSource,
    entry: EmbyEntry,
    *,
    api_key: str,
    is_default: bool,
) -> dict[str, Any]:
    version_id = encode(source.version_id, VERSION)
    item_id = encode(entry.id, type_bit_for(entry.type))
    streams, audio_index, subtitle_index = _streams(source.media)
    external = []
    for index, subtitle in enumerate(source.subtitles):
        external_index = 1000 + index
        extension = subtitle.format or "srt"
        delivery = (
            f"/Videos/{item_id}/{version_id}/Subtitles/{external_index}"
            f"/Stream.{extension}?api_key={quote(api_key)}"
        )
        external.append(
            {
                "Type": "Subtitle",
                "Index": external_index,
                "Codec": extension,
                "Language": subtitle.language,
                "DisplayTitle": subtitle.label,
                "Title": subtitle.label,
                "IsExternal": True,
                "DeliveryMethod": "External",
                "DeliveryUrl": delivery,
                "IsDefault": index == 0,
                "IsForced": subtitle.forced,
            }
        )
    streams.extend(external)
    default_source_id = encode(source.version_id, VERSION)
    dto = {
        "Id": version_id,
        "Name": source.label,
        "Path": source.rel_path,
        "Protocol": "File",
        "Container": source.container,
        "Size": source.size,
        "RunTimeTicks": ticks(source.duration),
        "SupportsDirectPlay": True,
        "SupportsDirectStream": True,
        "SupportsTranscoding": False,
        "IsRemote": True,
        "DefaultAudioStreamIndex": audio_index,
        "DefaultSubtitleStreamIndex": subtitle_index,
        "MediaStreams": [_without_nulls(stream) for stream in streams],
        "DirectStreamUrl": (
            f"/Videos/{item_id}/stream?Static=true&MediaSourceId={default_source_id}"
            f"&api_key={quote(api_key)}"
        ),
        "IsDefault": is_default,
    }
    return _without_nulls(dto)


def _streams(media: MediaInfo | None) -> tuple[list[dict[str, Any]], int | None, int | None]:
    if media is None:
        return [], None, None
    streams: list[dict[str, Any]] = [
        {
            "Type": "Video",
            "Index": 0,
            "Codec": media.video_codec,
            "Width": media.width,
            "Height": media.height,
            "IsDefault": True,
            "VideoRange": "HDR" if media.hdr else "SDR",
        }
    ]
    audio_index: int | None = None
    for index, track in enumerate(media.audio, start=1):
        streams.append(
            {
                "Type": "Audio",
                "Index": index,
                "Codec": track.codec,
                "Language": track.language,
                "Title": track.title,
                "DisplayTitle": track.title or track.language or track.codec,
                "Channels": track.channels,
                "IsDefault": track.default or audio_index is None,
            }
        )
        if audio_index is None:
            audio_index = index
    subtitle_index: int | None = None
    base = 1 + len(media.audio)
    for offset, subtitle in enumerate(media.subtitles):
        index = base + offset
        streams.append(
            {
                "Type": "Subtitle",
                "Index": index,
                "Codec": subtitle.codec,
                "Language": subtitle.language,
                "Title": subtitle.title,
                "DisplayTitle": subtitle.title or subtitle.language or subtitle.codec,
                "IsExternal": False,
                "IsDefault": subtitle.default or subtitle_index is None,
                "IsForced": subtitle.forced,
            }
        )
        if subtitle_index is None:
            subtitle_index = index
    return streams, audio_index, subtitle_index


def _image_tags(entry: EmbyEntry) -> dict[str, str]:
    if entry.type == "Person":
        return {"Primary": entry.profile_hash} if entry.profile_hash else {}
    tags = {}
    if entry.poster_hash:
        tags["Primary"] = entry.poster_hash
    if entry.thumb_hash:
        tags["Thumb"] = entry.thumb_hash
    if entry.logo_hash:
        tags["Logo"] = entry.logo_hash
    return tags


def _parent_id(entry: EmbyEntry) -> str | None:
    if entry.type in ("Movie", "Series") and entry.library_id is not None:
        return encode(entry.library_id, 1)
    if entry.type == "Season" and entry.series_id is not None:
        return encode(entry.series_id, ITEM)
    if entry.type == "Episode" and entry.season_id is not None:
        return encode(entry.season_id, SEASON)
    return None


def _genres(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [str(genre) for genre in parsed] if isinstance(parsed, list) else []
