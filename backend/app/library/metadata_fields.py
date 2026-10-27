from __future__ import annotations

from typing import Literal

MetadataField = Literal[
    "title",
    "original_title",
    "en_title",
    "overview",
    "poster_path",
    "backdrop_path",
    "thumb_path",
    "logo_path",
    "sort_title",
    "tagline",
    "studios",
    "countries",
    "release_date",
    "rating",
    "genres",
]

METADATA_FIELDS: tuple[MetadataField, ...] = (
    "title",
    "original_title",
    "en_title",
    "overview",
    "poster_path",
    "backdrop_path",
    "thumb_path",
    "logo_path",
    "sort_title",
    "tagline",
    "studios",
    "countries",
    "release_date",
    "rating",
    "genres",
)

IMAGE_METADATA_FIELDS: tuple[MetadataField, ...] = (
    "poster_path",
    "backdrop_path",
    "thumb_path",
    "logo_path",
)

ALL_METADATA_FIELDS: tuple[MetadataField, ...] = METADATA_FIELDS
