from __future__ import annotations

import base64
import binascii
import logging
import mimetypes
import sqlite3
from dataclasses import dataclass
from html import escape
from urllib.parse import quote
from xml.etree import ElementTree

from fastapi import APIRouter, Request
from pydantic import BaseModel
from starlette.responses import FileResponse, PlainTextResponse, RedirectResponse, Response

from app.api.schemas import Out
from app.auth.users import User
from app.auth.webdav import WebDavInfo
from app.deps import CurrentUser, Playback, Settings, WebDav
from app.library.browse import visible_library_ids
from app.library.webdav_files import MediaFile, NfoFile, OnlineSubtitleFile, WebDavFileQueries
from app.playback.service import PlayDenied
from app.subtitles.content import subtitle_media_type

router = APIRouter(prefix="/me/webdav", tags=["webdav"])
webdav_router = APIRouter(prefix="/webdav", include_in_schema=False)
logger = logging.getLogger(__name__)


class WebDavOut(Out):
    enabled: bool
    username: str
    url: str | None


class WebDavIn(BaseModel):
    enabled: bool


@router.get("")
def get_webdav(user: CurrentUser, webdav: WebDav, settings: Settings) -> WebDavOut:
    return _out(webdav.info(user), settings.public_url())


@router.patch("")
def save_webdav(body: WebDavIn, user: CurrentUser, webdav: WebDav, settings: Settings) -> WebDavOut:
    return _out(webdav.set_enabled(user, body.enabled), settings.public_url())


def _out(info: WebDavInfo, public_url: str | None) -> WebDavOut:
    return WebDavOut(
        enabled=info.enabled,
        username=info.username,
        url=f"{public_url}/webdav" if public_url else None,
    )


@webdav_router.api_route("", methods=["OPTIONS", "PROPFIND", "GET", "HEAD"])
@webdav_router.api_route("/{path:path}", methods=["OPTIONS", "PROPFIND", "GET", "HEAD"])
def webdav(
    request: Request,
    webdav: WebDav,
    playback: Playback,
    path: str = "",
) -> Response:
    user = _authenticate(request, webdav)
    if user is None:
        return _unauthorized()
    if request.method == "OPTIONS":
        return Response(
            status_code=200,
            headers={
                "Allow": "OPTIONS, PROPFIND, GET, HEAD",
                "DAV": "1",
                "MS-Author-Via": "DAV",
            },
        )
    parts = _path_parts(path)
    if parts is None:
        return PlainTextResponse("invalid path", status_code=400)
    nodes = webdav.tree(user, lambda connection: _nodes(connection, user))
    node = nodes.get(parts)
    if node is None:
        return PlainTextResponse("not found", status_code=404)
    if request.method == "PROPFIND":
        return _propfind(request, node, nodes)
    if node.collection:
        if not request.url.path.endswith("/"):
            location = request.url.path + "/"
            return RedirectResponse(location, status_code=301)
        return Response(status_code=405, headers={"Allow": "OPTIONS, PROPFIND"})
    if node.online_subtitle_id is not None:
        if request.method == "HEAD":
            headers = {
                "Content-Type": node.content_type or "application/octet-stream",
                "Cache-Control": "no-store",
            }
            if node.size is not None:
                headers["Content-Length"] = str(node.size)
            return Response(status_code=200, headers=headers)
        try:
            inline = playback.inline_subtitle(user, node.version_id or 0, node.online_subtitle_id)
        except PlayDenied as exc:
            return _denied(exc)
        return Response(
            inline.content,
            media_type=inline.media_type,
            headers={"Cache-Control": "no-store"},
        )
    if node.media_file_id is None:
        return PlainTextResponse("not found", status_code=404)
    if request.method == "HEAD":
        filename = node.path[-1]
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        headers = {"Content-Type": content_type, "Cache-Control": "no-store"}
        if node.size is not None:
            headers["Content-Length"] = str(node.size)
        return Response(status_code=200, headers=headers)
    try:
        user_agent = request.headers.get("user-agent", "")
        target = playback.resolve_file(user, node.media_file_id, user_agent)
    except PlayDenied as exc:
        return _denied(exc)
    if target.path is not None:
        # 本地来源的文件直接提供内容；WebDAV 客户端拿不到服务器上的路径
        return FileResponse(target.path, headers={"Cache-Control": "no-store"})
    assert target.url is not None
    location = target.url
    logger.info(
        "webdav direct redirect method=%s user-agent=%r range=%s",
        request.method,
        user_agent,
        bool(request.headers.get("range")),
    )
    # Match Go's http.Redirect response used by AList: some WebDAV clients
    # inspect the HTML body instead of relying on Location alone.
    body = f'<a href="{escape(location, quote=True)}">Found</a>.\n'
    return Response(
        body,
        status_code=302,
        headers={"Location": location, "Content-Type": "text/html; charset=utf-8"},
    )


def _denied(exc: PlayDenied) -> PlainTextResponse:
    headers = {"Cache-Control": "no-store"}
    if exc.retry_after is not None:
        headers["Retry-After"] = str(exc.retry_after)
    return PlainTextResponse(exc.message, status_code=exc.status, headers=headers)


def _authenticate(request: Request, webdav: WebDav) -> User | None:
    value = request.headers.get("authorization", "")
    scheme, _, encoded = value.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return None
    try:
        raw = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return None
    username, separator, password = raw.partition(":")
    return webdav.authenticate(username, password) if separator else None


def _unauthorized() -> Response:
    return Response(
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Iris WebDAV", charset="UTF-8"'},
    )


@dataclass(frozen=True)
class _Node:
    path: tuple[str, ...]
    collection: bool
    size: int | None = None
    media_file_id: int | None = None
    updated_at: str | None = None
    # 在线字幕没有媒体文件，直接由本服务返回内容
    online_subtitle_id: int | None = None
    version_id: int | None = None
    content_type: str | None = None


def _nodes(connection: sqlite3.Connection, user: User) -> dict[tuple[str, ...], _Node]:
    library_ids = visible_library_ids(connection, user)
    files = WebDavFileQueries(connection)
    nodes: dict[tuple[str, ...], _Node] = {(): _Node((), True)}
    for name in files.library_names(library_ids):
        library_path = _library_parts(name)
        for index in range(1, len(library_path) + 1):
            parent = library_path[:index]
            nodes.setdefault(parent, _Node(parent, True))
    if not library_ids:
        return nodes
    videos = files.videos(library_ids)
    subtitles = files.subtitles(library_ids)
    video_counts: dict[int, int] = {}
    for video in videos:
        video_counts[video.playable_id] = video_counts.get(video.playable_id, 0) + 1
    for video in videos:
        _add_file_node(nodes, _media_path(video, video_counts[video.playable_id]), video)
    for subtitle in subtitles:
        path = _media_path(subtitle, video_counts.get(subtitle.playable_id, 1), subtitle=True)
        _add_file_node(nodes, path, subtitle)
    for online in files.online_subtitles(library_ids):
        _add_online_node(
            nodes,
            _online_path(online, video_counts.get(online.video.playable_id, 1)),
            online,
        )
    for episode_nfo in files.episode_nfos(library_ids):
        path = _media_path(episode_nfo, video_counts[episode_nfo.playable_id])
        if path not in nodes:
            _add_file_node(nodes, path, episode_nfo)
    for nfo in files.nfos(library_ids):
        # 同一条目被多个作品合并时只留第一个 NFO
        path = _nfo_path(nfo)
        if path not in nodes:
            _add_file_node(nodes, path, nfo)
    return nodes


def _add_file_node(
    nodes: dict[tuple[str, ...], _Node], path: tuple[str, ...], file: MediaFile | NfoFile
) -> None:
    if not path:
        return
    for index in range(1, len(path)):
        parent = path[:index]
        nodes.setdefault(parent, _Node(parent, True))
    nodes[path] = _Node(
        path,
        False,
        size=int(file.size) if file.size is not None else None,
        media_file_id=file.id,
        updated_at=str(file.updated_at),
    )


def _add_online_node(
    nodes: dict[tuple[str, ...], _Node], path: tuple[str, ...], online: OnlineSubtitleFile
) -> None:
    path = _unique_path(nodes, path)
    for index in range(1, len(path)):
        parent = path[:index]
        nodes.setdefault(parent, _Node(parent, True))
    nodes[path] = _Node(
        path,
        False,
        size=online.size,
        updated_at=online.created_at,
        online_subtitle_id=online.subtitle_id,
        version_id=online.version_id,
        content_type=subtitle_media_type(online.format),
    )


def _online_path(online: OnlineSubtitleFile, version_count: int) -> tuple[str, ...]:
    """在线字幕用合成文件名，和视频放在同一个目录里。"""
    video = _media_path(online.video, version_count)
    leaf = video[-1]
    stem = leaf.rpartition(".")[0] or leaf
    language = _safe_segment(online.language or "und")
    return (*video[:-1], f"{stem}.{language}.{online.format}")


def _unique_path(nodes: dict[tuple[str, ...], _Node], path: tuple[str, ...]) -> tuple[str, ...]:
    """重名时在扩展名前加序号，例如「影片.zh-Hans (2).srt」。"""
    if path not in nodes:
        return path
    directory, leaf = path[:-1], path[-1]
    stem, dot, extension = leaf.rpartition(".")
    base = stem if dot else leaf
    suffix = 2
    while True:
        name = f"{base} ({suffix}){'.' + extension if dot else ''}"
        candidate = (*directory, name)
        if candidate not in nodes:
            return candidate
        suffix += 1


def _media_path(file: MediaFile, version_count: int, *, subtitle: bool = False) -> tuple[str, ...]:
    library = _library_parts(file.library_name)
    media = _safe_segment(file.title)
    media_dir = f"{media} ({int(file.year)})" if file.year is not None else media
    if file.media_kind == "series":
        season_number = int(file.season_number or 0)
        episode_number = int(file.episode_number or 0)
        season_dir = f"Season {season_number:02d}"
        episode = f"S{season_number:02d}E{episode_number:02d}"
        episode_name = _safe_segment(str(file.episode_name or ""))
        stem = f"{media} - {episode}"
        if episode_name:
            stem += f" - {episode_name}"
        directory = (*library, media_dir, season_dir)
    else:
        stem = media_dir
        directory = (*library, media_dir)
    if version_count > 1:
        label = _safe_segment(str(file.version_label or "version"))
        stem += f" - {label}"
    if subtitle:
        language = _safe_segment(str(file.subtitle_language or "sub"))
        stem += f".{language}"
    return (*directory, stem + _extension(file.rel_path))


def _nfo_path(nfo: NfoFile) -> tuple[str, ...]:
    """剧集的 NFO 放在作品目录下叫 tvshow.nfo；电影的和视频同名。"""
    media = _safe_segment(nfo.title)
    media_dir = f"{media} ({int(nfo.year)})" if nfo.year is not None else media
    name = "tvshow.nfo" if nfo.media_kind == "series" else f"{media_dir}.nfo"
    return (*_library_parts(nfo.library_name), media_dir, name)


def _safe_segment(value: str) -> str:
    value = value.strip().replace("/", "／").replace("\\", "＼")
    if value in (".", ".."):
        value = value.replace(".", "．")
    return value or "未命名"


def _extension(path: str) -> str:
    name = path.rpartition("/")[2]
    suffix = name.rpartition(".")[2]
    return f".{suffix}" if suffix and suffix != name else ".bin"


def _join_parts(*values: str) -> tuple[str, ...]:
    parts: list[str] = []
    for value in values:
        parts.extend(part for part in value.strip("/").split("/") if part)
    return tuple(parts)


def _library_parts(value: str) -> tuple[str, ...]:
    return tuple(_safe_segment(part) for part in _join_parts(value))


def _path_parts(path: str) -> tuple[str, ...] | None:
    path = path.strip("/")
    if not path:
        return ()
    parts = tuple(path.split("/"))
    return None if any(not part or part in (".", "..") or "\\" in part for part in parts) else parts


def _propfind(request: Request, node: _Node, nodes: dict[tuple[str, ...], _Node]) -> Response:
    depth = request.headers.get("depth", "0").lower()
    selected = [node]
    if node.collection and depth != "0":
        prefix = node.path
        selected.extend(
            child
            for child in nodes.values()
            if len(child.path) == len(prefix) + 1 and child.path[: len(prefix)] == prefix
        )
    root = ElementTree.Element("{DAV:}multistatus")
    for entry in sorted(selected, key=lambda item: item.path):
        response = ElementTree.SubElement(root, "{DAV:}response")
        href = "/webdav/" + quote("/".join(entry.path), safe="/")
        if entry.collection:
            href += "/"
        ElementTree.SubElement(response, "{DAV:}href").text = href
        propstat = ElementTree.SubElement(response, "{DAV:}propstat")
        prop = ElementTree.SubElement(propstat, "{DAV:}prop")
        ElementTree.SubElement(prop, "{DAV:}displayname").text = (
            entry.path[-1] if entry.path else "WebDAV"
        )
        resource = ElementTree.SubElement(prop, "{DAV:}resourcetype")
        if entry.collection:
            ElementTree.SubElement(resource, "{DAV:}collection")
            content_type = "httpd/unix-directory"
        else:
            content_type = "application/octet-stream"
            if entry.size is not None:
                ElementTree.SubElement(prop, "{DAV:}getcontentlength").text = str(entry.size)
        ElementTree.SubElement(prop, "{DAV:}getcontenttype").text = content_type
        ElementTree.SubElement(propstat, "{DAV:}status").text = "HTTP/1.1 200 OK"
    body = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
    return Response(body, status_code=207, media_type="application/xml; charset=utf-8")
