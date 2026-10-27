"""NFO：只从中读取 TMDB ID 和 IMDb ID，用于直接匹配（ADR 0008）。标题、简介、图片仍以 TMDB 为准。

用正则而不是 XML 解析器：资料库里的 NFO 由不同工具生成，常有不规范的写法，
有的只有一行链接；正则也不会触发实体展开之类的 XML 解析风险。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.library.recognize import LibraryKind, normalize

# NFO 一般只有几 KB，超过这个大小的不读
MAX_NFO_BYTES = 256 * 1024
SERIES_NFO = "tvshow.nfo"
MOVIE_NFO = "movie.nfo"

_UNIQUE_ID = re.compile(
    r"<uniqueid\b[^>]*\btype\s*=\s*[\"']?(tmdb|imdb)[\"']?[^>]*>\s*([^<\s]+)\s*</uniqueid>", re.I
)
_TMDB_TAG = re.compile(r"<tmdbid>\s*(\d+)\s*</tmdbid>", re.I)
_IMDB_TAG = re.compile(r"<(?:imdbid|imdb_id|imdb|id)>\s*(tt\d{5,10})\s*</", re.I)
_TMDB_URL = re.compile(r"themoviedb\.org/(movie|tv)/(\d+)", re.I)
_IMDB_URL = re.compile(r"imdb\.com/title/(tt\d{5,10})", re.I)
_IMDB_ID = re.compile(r"^tt\d{5,10}$")


@dataclass(frozen=True)
class NfoIds:
    tmdb_id: int | None
    imdb_id: str | None


def parse_nfo(data: bytes, kind: LibraryKind) -> NfoIds:
    text = data.decode("utf-8", errors="replace")
    tmdb_id = None
    imdb_id = None
    for source, value in _UNIQUE_ID.findall(text):
        if source.lower() == "tmdb" and value.isdigit() and tmdb_id is None:
            tmdb_id = int(value)
        elif source.lower() == "imdb" and _IMDB_ID.match(value) and imdb_id is None:
            imdb_id = value
    if tmdb_id is None and (match := _TMDB_TAG.search(text)):
        tmdb_id = int(match.group(1))
    if tmdb_id is None:
        wanted = "movie" if kind == "movie" else "tv"
        for path, value in _TMDB_URL.findall(text):
            if path.lower() == wanted:
                tmdb_id = int(value)
                break
    if imdb_id is None and (match := _IMDB_TAG.search(text) or _IMDB_URL.search(text)):
        imdb_id = match.group(1)
    return NfoIds(tmdb_id=tmdb_id or None, imdb_id=imdb_id)


def nfo_for_work(
    kind: LibraryKind, work_path: str, video_paths: list[str], nfo_paths: list[str]
) -> str | None:
    """找出作品对应的 NFO。路径都是相对资料库目录的原始路径，work_path 是识别器规范化过的。

    剧集：作品目录下的 tvshow.nfo。电影：一个文件单独成为作品时是同名的 NFO；作品是目录时
    优先 movie.nfo，其次和某个视频同名的 NFO，目录下只有一个 NFO 时就用它。
    """
    by_dir: dict[str, list[str]] = {}
    for path in nfo_paths:
        by_dir.setdefault(normalize(path.rpartition("/")[0]), []).append(path)

    def name_of(path: str) -> str:
        return normalize(path.rpartition("/")[2]).lower()

    def stem_of(path: str) -> str:
        return name_of(path).rpartition(".")[0]

    if kind == "series":
        return next((p for p in by_dir.get(work_path, []) if name_of(p) == SERIES_NFO), None)
    single = [v for v in video_paths if normalize(v) == work_path]
    if single:
        folder = normalize(single[0].rpartition("/")[0])
        stem = stem_of(single[0])
        return next((p for p in by_dir.get(folder, []) if stem_of(p) == stem), None)
    candidates = by_dir.get(work_path, [])
    for path in candidates:
        if name_of(path) == MOVIE_NFO:
            return path
    stems = {stem_of(v) for v in video_paths}
    for path in candidates:
        if stem_of(path) in stems:
            return path
    return candidates[0] if len(candidates) == 1 else None
