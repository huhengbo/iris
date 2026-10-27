"""条目图片管理接口：候选图、选择、上传。"""

from __future__ import annotations

import hashlib
import ipaddress
import socket
import urllib.request
from typing import cast
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.api.schemas import ImageOut, Out
from app.clock import Clock
from app.db import Database
from app.deps import (
    AdminUser,
    AppClock,
    AppDatabase,
    Images,
    MetadataPlugins,
    ReadDb,
)
from app.errors import AppError
from app.library.artwork import ItemArtwork, ItemArtworkRepository, check_artwork_kind
from app.library.covers import clear_library_cover, library_cover, set_library_cover
from app.library.images import SOURCE_WIDTH, ImageError, ImageKind, ImageStore
from app.library.metadata_providers import MetadataProviders
from app.priority import Priority

router = APIRouter(prefix="/admin", tags=["admin"])


class ArtworkOut(Out):
    kind: str
    current: str | None
    candidates: list[str]


def _artwork_view(
    row: ItemArtwork, kind: str, current: str | None, providers: MetadataProviders
) -> ArtworkOut:
    """当前图片加插件给出的候选；候选要走网络，调用时不能持有数据库事务。"""
    candidates = [current] if current else []
    try:
        record = providers.get(row.provider_id).details(
            row.kind, row.remote_id, Priority.INTERACTIVE
        )
        candidates = list(dict.fromkeys([*record.artwork.get(kind, ()), *candidates]))
    except AppError:
        # 候选图不可用时仍返回当前图片，不能阻塞元数据编辑。
        pass
    return ArtworkOut(kind=kind, current=current, candidates=candidates)


@router.get("/items/{item_id}/artwork/{kind}")
def item_artwork(
    item_id: int, kind: str, _: AdminUser, db: ReadDb, clock: AppClock, providers: MetadataPlugins
) -> ArtworkOut:
    row = ItemArtworkRepository(db, clock).get(item_id, kind)
    return _artwork_view(row, kind, row.current, providers)


class ArtworkIn(BaseModel):
    # provider：条目所属插件给出的图片来源
    source: str = Field(pattern="^(provider|url)$")
    value: str = Field(min_length=1, max_length=1000)


def _store_artwork(
    database: Database,
    clock: Clock,
    images: ImageStore,
    item_id: int,
    kind: str,
    image_source: str,
    data: bytes,
    current: str | None,
) -> str:
    """图片数据已经拿到手，这里只开一个短写事务落库，网络和上传都不在事务里。返回写入的图片路径。"""
    with database.session() as db:
        return ItemArtworkRepository(db, clock).store(
            images, item_id, kind, image_source, data, current
        )


@router.put("/items/{item_id}/artwork/{kind}")
def set_item_artwork(
    item_id: int,
    kind: str,
    body: ArtworkIn,
    _: AdminUser,
    db: ReadDb,
    database: AppDatabase,
    clock: AppClock,
    images: Images,
    providers: MetadataPlugins,
) -> ArtworkOut:
    check_artwork_kind(kind)
    if body.source == "url" and not _safe_image_url(body.value):
        raise AppError(400, "invalid_artwork_url", "图片 URL 必须使用公开 HTTP(S) 地址")
    row = ItemArtworkRepository(db, clock).get(item_id, kind)
    source = body.value
    try:
        if body.source == "url":
            data = _download_image(body.value)
        else:
            # 选中的是当前图时，它可能来自补缺插件，用那个插件下载
            provider_id = row.provider_id
            if row.current is not None and body.value == row.current and row.current_provider:
                provider_id = row.current_provider
            data = _provider_image(providers, provider_id, body.value, kind)
        _store_artwork(
            database,
            clock,
            images,
            item_id,
            kind,
            f"manual:{item_id}:{kind}:{source}",
            data,
            source,
        )
    except (ImageError, ValueError) as exc:
        raise AppError(400, "invalid_artwork", "图片格式无效或超过大小限制") from exc
    except Exception as exc:
        raise AppError(
            400, "artwork_download_failed", "图片下载失败，请检查地址或稍后重试"
        ) from exc
    return _artwork_view(row, kind, source, providers)


def _provider_image(
    providers: MetadataProviders, provider_id: str, source: str, kind: str
) -> bytes:
    bound = providers.image_source(provider_id)
    if bound is not None:
        return bound.image(source, SOURCE_WIDTH[cast(ImageKind, kind)], Priority.INTERACTIVE)
    return providers.get(provider_id).image(
        source, SOURCE_WIDTH[cast(ImageKind, kind)], Priority.INTERACTIVE
    )


def _safe_image_url(value: str) -> bool:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
        return False
    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        return False
    try:
        addresses = [ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(host, None)]
    except (OSError, ValueError):
        return False
    return bool(addresses) and all(
        not (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
        )
        for address in addresses
    )


def _download_image(value: str) -> bytes:
    request = urllib.request.Request(value, headers={"User-Agent": "Iris/1.0"})
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - validated URL
        content_type = response.headers.get_content_type()
        if content_type and not content_type.startswith("image/"):
            raise ImageError("内容不是图片")
        data = cast(bytes, response.read(8 * 1024 * 1024 + 1))
    if len(data) > 8 * 1024 * 1024:
        raise ImageError("图片过大")
    return data


@router.put("/items/{item_id}/artwork/{kind}/upload")
async def upload_item_artwork(
    item_id: int,
    kind: str,
    request: Request,
    _: AdminUser,
    db: ReadDb,
    database: AppDatabase,
    clock: AppClock,
    images: Images,
    providers: MetadataPlugins,
) -> ArtworkOut:
    check_artwork_kind(kind)
    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data"):
        raise AppError(415, "multipart_required", "图片上传必须使用 multipart/form-data")
    raw = await request.body()
    data = _multipart_file(raw, content_type)
    if not data or len(data) > 8 * 1024 * 1024:
        raise AppError(400, "invalid_artwork", "图片为空或超过 8 MB")
    row = ItemArtworkRepository(db, clock).get(item_id, kind)
    source = f"upload:{item_id}:{kind}:{hashlib.sha256(data).hexdigest()}"
    try:
        current = _store_artwork(database, clock, images, item_id, kind, source, data, None)
    except ImageError as exc:
        raise AppError(400, "invalid_artwork", "上传内容不是有效图片") from exc
    return _artwork_view(row, kind, current, providers)


class LibraryCoverOut(Out):
    # 横版封面；custom 为 False 时是自动取的最近入库条目背景图，资料库还没有图片时为空
    cover: list[ImageOut]
    custom: bool


def _cover_out(database: Database, library_id: int) -> LibraryCoverOut:
    with database.read_session() as db:
        cover = library_cover(db, library_id)
    if cover is None:
        return LibraryCoverOut(cover=[], custom=False)
    return LibraryCoverOut(
        cover=[ImageOut(width=size.width, url=size.url) for size in cover.sizes],
        custom=cover.custom,
    )


@router.put("/libraries/{library_id}/cover")
async def upload_library_cover(
    library_id: int,
    request: Request,
    _: AdminUser,
    database: AppDatabase,
    images: Images,
) -> LibraryCoverOut:
    """上传资料库封面，Iris 客户端和 Emby 接口都用它；建议横版 16:9。"""
    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data"):
        raise AppError(415, "multipart_required", "图片上传必须使用 multipart/form-data")
    data = _multipart_file(await request.body(), content_type)
    if not data or len(data) > 8 * 1024 * 1024:
        raise AppError(400, "invalid_artwork", "图片为空或超过 8 MB")
    source = f"library-cover:{library_id}:{hashlib.sha256(data).hexdigest()}"
    try:
        with database.session() as db:
            set_library_cover(db, images, library_id, source, data)
    except ImageError as exc:
        raise AppError(400, "invalid_artwork", "上传内容不是有效图片") from exc
    return _cover_out(database, library_id)


@router.delete("/libraries/{library_id}/cover")
def reset_library_cover(library_id: int, _: AdminUser, database: AppDatabase) -> LibraryCoverOut:
    """去掉上传的封面，恢复自动选取。"""
    with database.session() as db:
        clear_library_cover(db, library_id)
    return _cover_out(database, library_id)


def _multipart_file(body: bytes, content_type: str) -> bytes:
    """解析单文件上传，避免为一个小接口引入额外 multipart 运行时依赖。"""
    marker = "boundary="
    boundary = content_type.split(marker, 1)[1].strip().strip('"') if marker in content_type else ""
    if not boundary:
        raise AppError(400, "invalid_multipart", "缺少 multipart boundary")
    delimiter = b"--" + boundary.encode()
    for part in body.split(delimiter):
        if b"Content-Disposition:" not in part or b"\r\n\r\n" not in part:
            continue
        headers, value = part.split(b"\r\n\r\n", 1)
        if b'name="file"' not in headers and b'name="upload"' not in headers:
            continue
        return value[:-2] if value.endswith(b"\r\n") else value
    raise AppError(400, "invalid_multipart", "multipart 中没有 file 字段")
