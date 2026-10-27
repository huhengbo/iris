"""豆瓣条目页和搜索建议的解析。

条目页里的 JSON-LD 给名称、日期、类型、评分、简介和海报，#info 给 IMDb、制片国家和
片长，完整简介在 property="v:summary" 里。字段缺失时返回空值；页面本身无法解析时记日志并
抛 MetadataUnavailable，由 provider 统一报「解析失败」。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

from iris_plugin_sdk import MetadataUnavailable

logger = logging.getLogger(__name__)

_SPACES = re.compile(r"\s+")
_IMDB = re.compile(r"\b(tt\d{5,})\b")
_RUNTIME = re.compile(r"(\d+)\s*分钟")
_YEAR = re.compile(r"(\d{4})")
_INFO_LINE = re.compile(r"([^:：]{1,20})[:：]\s*(.*)")
_VOID = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
# 只匹配豆瓣反爬页的完整提示，正常页面里的“异常请求”字样不能触发
_BLOCKED = ("检测到有异常请求", "有异常请求从你的 IP 发出")


@dataclass(frozen=True)
class DoubanSubject:
    title: str
    release_date: str | None
    year: int | None
    genres: tuple[str, ...]
    rating: float | None
    overview: str | None
    poster: str | None
    imdb_id: str | None
    countries: tuple[str, ...]
    runtime: int | None


def parse_subject(html: str) -> DoubanSubject:
    parser = _SubjectParser()
    try:
        parser.feed(html)
    except Exception as exc:
        # html.parser 对畸形页面可能抛错；这里统一转成解析失败，避免裸异常穿过插件边界
        logger.exception("豆瓣页面解析失败")
        raise MetadataUnavailable("豆瓣页面解析失败，请稍后再试") from exc
    data = _json_ld(parser.scripts)
    info = _info_values(parser.info)
    title = _text(data.get("name")) or _title(parser.title) or ""
    release_date = _text(data.get("datePublished"))
    genre_value = data.get("genre")
    if isinstance(genre_value, list):
        genres = tuple(
            value.strip() for value in genre_value if isinstance(value, str) and value.strip()
        )
    else:
        genres = _split(_text(genre_value) or info.get("类型"))
    aggregate = data.get("aggregateRating")
    rating = _number(aggregate.get("ratingValue")) if isinstance(aggregate, dict) else None
    if rating is None:
        rating = _number(parser.rating)
    overview = _text(parser.summary) or _text(data.get("description"))
    poster = _image(data.get("image")) or parser.poster
    imdb_value = info.get("IMDb")
    imdb_match = _IMDB.search(imdb_value or "") or _IMDB.search("".join(parser.info))
    imdb_id = imdb_match.group(1) if imdb_match else None
    date = release_date or info.get("上映日期")
    return DoubanSubject(
        title=title,
        release_date=_date(date),
        year=_year(date),
        genres=genres,
        rating=rating,
        overview=_normalize(overview),
        poster=poster,
        imdb_id=imdb_id,
        countries=_split(info.get("制片国家/地区")),
        runtime=_runtime(info.get("片长")),
    )


def parse_suggest(payload: Any) -> list[str]:
    """搜索建议里只取条目 id；非列表或字段不对时返回空列表。"""
    if not isinstance(payload, list):
        return []
    result: list[str] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        value = item.get("id")
        remote_id = (
            str(value) if isinstance(value, int | str) and not isinstance(value, bool) else ""
        )
        if remote_id.isdigit() and remote_id not in result:
            result.append(remote_id)
    return result


def is_blocked(text: str) -> bool:
    return any(marker in text for marker in _BLOCKED)


class _SubjectParser(HTMLParser):
    """收集 JSON-LD、#info、v:summary、海报和评分；不构建完整 DOM。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[str] = []
        self._info_depth: int | None = None
        self._summary_depth: int | None = None
        self._poster_depth: int | None = None
        self._rating_depth: int | None = None
        self._script_depth: int | None = None
        self._title_depth: int | None = None
        self.info: list[str] = []
        self.summary: list[str] = []
        self.scripts: list[str] = []
        self.poster: str | None = None
        self.title: str | None = None
        self.rating: str | None = None
        self._script_parts: list[str] = []
        self._title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if tag == "br":
            if self._info_depth is not None:
                self.info.append("\n")
            return
        if tag == "meta":
            prop = values.get("property") or values.get("name")
            content = values.get("content")
            if prop in ("og:title", "title") and content and self.title is None:
                self.title = content
            if prop == "og:image" and content and self.poster is None:
                self.poster = content
            return
        if tag == "img":
            if self._poster_depth is not None and self.poster is None:
                self.poster = values.get("src") or values.get("data-src") or None
            return
        if tag in _VOID:
            return
        self._stack.append(tag)
        depth = len(self._stack)
        if tag == "div" and values.get("id") == "info":
            self._info_depth = depth
        if values.get("property") == "v:summary":
            self._summary_depth = depth
        if tag == "div" and values.get("id") == "mainpic":
            self._poster_depth = depth
        if values.get("property") == "v:average":
            self._rating_depth = depth
            self.rating = ""
        if tag == "script" and "ld+json" in values.get("type", "").lower():
            self._script_depth = depth
            self._script_parts = []
        if tag == "title":
            self._title_depth = depth
            self._title_parts = []

    def handle_data(self, data: str) -> None:
        if self._info_depth is not None:
            self.info.append(data)
        if self._summary_depth is not None:
            self.summary.append(data)
        if self._rating_depth is not None:
            self.rating = (self.rating or "") + data
        if self._script_depth is not None:
            self._script_parts.append(data)
        if self._title_depth is not None:
            self._title_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        depth = len(self._stack)
        if self._info_depth == depth and tag == "div":
            self._info_depth = None
        if self._summary_depth == depth:
            self._summary_depth = None
        if self._poster_depth == depth and tag == "div":
            self._poster_depth = None
        if self._rating_depth == depth:
            self._rating_depth = None
        if self._script_depth == depth and tag == "script":
            self.scripts.append("".join(self._script_parts))
            self._script_depth = None
            self._script_parts = []
        if self._title_depth == depth and tag == "title":
            self.title = "".join(self._title_parts).strip()
            self._title_depth = None
            self._title_parts = []
        if self._stack:
            if self._stack[-1] == tag:
                self._stack.pop()
            elif tag in self._stack:
                while self._stack and self._stack[-1] != tag:
                    self._stack.pop()
                if self._stack:
                    self._stack.pop()


def _json_ld(scripts: list[str]) -> dict[str, Any]:
    for script in scripts:
        try:
            payload = json.loads(script)
        except ValueError:
            continue
        if isinstance(payload, list):
            payload = next((item for item in payload if isinstance(item, dict)), None)
        if isinstance(payload, dict):
            return payload
    return {}


def _info_values(parts: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in "".join(parts).replace("\xa0", " ").splitlines():
        line = raw.strip()
        found = _INFO_LINE.match(line)
        if found is None:
            continue
        key = found.group(1).strip()
        value = found.group(2).strip()
        if key and value:
            values.setdefault(key, value)
    return values


def _split(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in re.split(r"\s*/\s*", value) if part.strip())


def _title(value: str | None) -> str | None:
    if not value:
        return None
    title = re.sub(r"\s*\(豆瓣\)\s*$", "", value).strip()
    return title or None


def _date(value: str | None) -> str | None:
    """从「1994-09-10(电影节) / ...」里取出第一个 ISO 日期。"""
    if not value:
        return None
    found = re.search(r"\d{4}-\d{2}-\d{2}", value)
    return found.group(0) if found else None


def _year(value: str | None) -> int | None:
    if not value:
        return None
    found = _YEAR.search(value)
    return int(found.group(1)) if found else None


def _runtime(value: str | None) -> int | None:
    if not value:
        return None
    found = _RUNTIME.search(value)
    return int(found.group(1)) if found else None


def _image(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        return _text(value.get("url"))
    if isinstance(value, list):
        return next((_image(item) for item in value if _image(item) is not None), None)
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 1) if number > 0 else None


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _normalize(value: str | None) -> str | None:
    if not value:
        return None
    text = _SPACES.sub(" ", value).strip()
    return text or None
