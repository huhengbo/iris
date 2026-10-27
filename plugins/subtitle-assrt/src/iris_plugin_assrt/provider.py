"""射手网 assrt 在线字幕 provider。

API 依据官方文档 https://assrt.net/api/doc：搜索条目使用 id、videoname、upload_time、
down_count、lang.langlist/lang.desc 和 filelist；详情额外提供顶层 filename/url，以及
filelist 内文件的下载地址。Token 只放在 Authorization 头里，不写日志、不放进查询参数，
返回给用户的错误也不包含 Token。
"""

from __future__ import annotations

import io
import re
import threading
import time
import zipfile
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from iris_plugin_sdk import (
    HostCallError,
    MediaContext,
    PluginHttpClient,
    PluginHttpError,
    PluginHttpResponse,
    SettingsEndpoint,
    SubtitleCandidate,
    SubtitleDownload,
    SubtitleSearchRequest,
    SubtitleUnavailable,
    json_object,
    raise_for_status,
)

TOKEN_SECRET = "api_token"
HOST_FIELD = "api_host"
RATE_FIELD = "requests_per_minute"
DEFAULT_HOST = "api.assrt.net"
HOSTS = ("api.assrt.net", "api.makedie.me")
DEFAULT_RATE = 5
MAX_SEARCH_RESULTS = 15
SEARCH_CACHE_TTL = 10 * 60
MAX_DOWNLOAD = 5 * 1024 * 1024
MAX_UNCOMPRESSED = MAX_DOWNLOAD
NO_TOKEN_MESSAGE = "还没有填写 API Token"
RATE_LIMITED_MESSAGE = "射手网请求太频繁，请稍后再试"
_SUBTITLE_EXTENSIONS = frozenset({"srt", "ass", "ssa", "vtt"})
_EPISODE = re.compile(r"[Ss](\d{1,2})[ ._-]?[Ee](\d{1,3})")
_LANGUAGE_LABELS = {
    "zh-Hans": "简体中文",
    "zh-Hant": "繁体中文",
    "en": "英文",
    "ja": "日文",
    "ko": "韩文",
}
# 中英双语文件按中文简体入库，标签写明双语
_DOUBLE_LABEL = "中英双语"
_LANG_CODES = (
    ("langchs", "zh-Hans"),
    ("langcht", "zh-Hant"),
    ("langdou", "zh-Hans"),
    ("langeng", "en"),
    ("langjpn", "ja"),
    ("langkor", "ko"),
)


@dataclass(frozen=True)
class _File:
    name: str
    url: str | None


class AssrtProvider:
    def __init__(
        self,
        http: PluginHttpClient,
        settings: SettingsEndpoint,
        plugin_id: str,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.id = plugin_id
        self._http = http
        self._settings = settings
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._requests: deque[float] = deque()
        self._cache: dict[tuple[str, int], tuple[float, list[dict[str, Any]]]] = {}

    # ---- 宿主协议 ----

    def unavailable_reason(self) -> str | None:
        return None if self._token() is not None else NO_TOKEN_MESSAGE

    def search(self, request: SubtitleSearchRequest) -> list[SubtitleCandidate]:
        token = self._require_token()
        host = self._host()
        rate = self._rate()
        media = request.media
        candidates: list[SubtitleCandidate] = []
        for query, by_file in self._queries(media):
            subs = self._search(query, by_file, token=token, host=host, rate=rate)
            candidates = self._candidates(subs, request)
            if candidates:
                break
        return candidates

    def download(self, candidate: SubtitleCandidate) -> SubtitleDownload:
        sub_id, _, filename = candidate.remote_id.partition("/")
        if not sub_id:
            raise SubtitleUnavailable("字幕文件地址不正确")
        token = self._require_token()
        detail = self._detail(
            sub_id,
            token=token,
            host=self._host(),
            rate=self._rate(),
        )
        selected = _select_file(_sub_files(detail), filename)
        if selected is None:
            raise SubtitleUnavailable("这个字幕暂时没有可下载的文件")
        assert selected.url is not None
        url = _download_url(selected.url)
        try:
            response = self._http.get(url)
        except (PluginHttpError, HostCallError) as exc:
            raise SubtitleUnavailable("下载字幕失败，请稍后再试") from exc
        _raise_http_error(response, auth_message="下载字幕失败，请稍后再试")
        content = _download_content(response)
        format = _extension(selected.name)
        if _is_archive(content, selected.name):
            content, format = self._unpack(content, selected.name)
        return SubtitleDownload(
            content=content,
            language=candidate.language,
            format=format,
            forced=candidate.forced,
            hearing_impaired=candidate.hearing_impaired,
        )

    def test_connection(self, values: Mapping[str, Any] | None = None) -> str | None:
        """设置页的测试连接：只请求配额接口。"""
        settings = self._settings.values()
        merged = {**settings, **(values or {})}
        token = merged.get(TOKEN_SECRET)
        if not isinstance(token, str) or not token.strip():
            return "请填写 API Token"
        host = merged.get(HOST_FIELD)
        if host not in HOSTS:
            return "API 地址不可用"
        try:
            self._request(
                "/v1/user/quota",
                {},
                token=token.strip(),
                host=str(host),
                rate=_rate_value(merged.get(RATE_FIELD)),
            )
        except SubtitleUnavailable as exc:
            return exc.message
        return None

    # ---- 搜索 ----

    def _queries(self, media: MediaContext) -> list[tuple[str, int]]:
        """先按视频文件名搜，没有再按片名和季集/年份搜；最多两次。"""
        queries: list[tuple[str, int]] = []
        stem = _stem(media.filename)
        if stem:
            queries.append((stem, 1))
        title = (media.original_title or "").strip() or media.title.strip()
        if media.kind == "series" and media.season is not None and media.episode is not None:
            title = f"{title} S{media.season:02d}E{media.episode:02d}"
        elif media.year is not None:
            title = f"{title} {media.year}"
        title = title.strip()
        if title and title not in {query for query, _ in queries}:
            queries.append((title, 0))
        return queries

    def _search(
        self,
        query: str,
        by_file: int,
        *,
        token: str,
        host: str,
        rate: int,
    ) -> list[dict[str, Any]]:
        key = (query, by_file)
        now = self._monotonic()
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and now - cached[0] < SEARCH_CACHE_TTL:
                return cached[1]
        payload = self._request(
            "/v1/sub/search",
            {
                "q": query,
                "cnt": MAX_SEARCH_RESULTS,
                "pos": 0,
                "is_file": by_file,
                "no_muxer": 1,
                "filelist": 1,
            },
            token=token,
            host=host,
            rate=rate,
        )
        subs = _subs(payload)
        with self._lock:
            self._prune_cache(now)
            self._cache[key] = (now, subs)
        return subs

    def _candidates(
        self, subs: list[dict[str, Any]], request: SubtitleSearchRequest
    ) -> list[SubtitleCandidate]:
        result: list[SubtitleCandidate] = []
        for sub in subs:
            languages = _languages(sub)
            language = _choose_language(languages, request.language)
            if request.language is not None and language is None:
                continue
            label = _label(language, sub)
            sub_id = _sub_id(sub)
            if not sub_id:
                continue
            release = _text(sub.get("videoname"))
            for file in _sub_files(sub):
                if request.media.kind == "series" and not _episode_matches(
                    f"{file.name} {label} {release}", request.media
                ):
                    continue
                format = _extension(file.name)
                if format not in _SUBTITLE_EXTENSIONS:
                    # 压缩包或未知扩展名交给下载阶段按内容判断
                    format = "srt"
                result.append(
                    SubtitleCandidate(
                        provider_id=self.id,
                        remote_id=f"{sub_id}/{file.name}",
                        label=f"{label} · {file.name}" if file.name else label,
                        language=language or "",
                        format=format,
                        forced=request.forced,
                        score=_number(sub.get("vote_score")),
                        release=release or None,
                        uploaded_at=_text(sub.get("upload_time")) or None,
                        downloads=_integer(sub.get("down_count")),
                    )
                )
        return result

    # ---- 详情与下载 ----

    def _detail(
        self,
        sub_id: str,
        *,
        token: str,
        host: str,
        rate: int,
    ) -> dict[str, Any]:
        payload = self._request(
            "/v1/sub/detail",
            {"id": sub_id},
            token=token,
            host=host,
            rate=rate,
        )
        subs = _subs(payload)
        if not subs:
            raise SubtitleUnavailable("这个字幕在射手网上已经不存在了")
        return subs[0]

    def _unpack(self, content: bytes, name: str) -> tuple[bytes, str]:
        lowered = name.lower()
        if lowered.endswith(".rar") or content.startswith(b"Rar!"):
            raise SubtitleUnavailable("压缩包格式暂不支持")
        if lowered.endswith(".7z") or content.startswith(b"7z\xbc\xaf\x27\x1c"):
            raise SubtitleUnavailable("压缩包格式暂不支持")
        try:
            archive = zipfile.ZipFile(io.BytesIO(content))
        except zipfile.BadZipFile as exc:
            raise SubtitleUnavailable("字幕压缩包无法解压") from exc
        with archive:
            infos = [
                info
                for info in archive.infolist()
                if not info.is_dir() and _extension(info.filename) in _SUBTITLE_EXTENSIONS
            ]
            if not infos:
                raise SubtitleUnavailable("压缩包里没有可用的字幕文件")
            total = 0
            for info in infos:
                if _unsafe_path(info.filename):
                    raise SubtitleUnavailable("字幕压缩包包含不安全的路径")
                total += info.file_size
                if total > MAX_UNCOMPRESSED:
                    raise SubtitleUnavailable("字幕压缩包解压后超过 5 MiB")
            selected = infos[0]
            with archive.open(selected) as handle:
                data = handle.read(MAX_UNCOMPRESSED + 1)
            if len(data) > MAX_UNCOMPRESSED:
                raise SubtitleUnavailable("字幕文件超过 5 MiB")
            return data, _extension(selected.filename)

    # ---- HTTP ----

    def _request(
        self,
        path: str,
        params: Mapping[str, Any],
        *,
        token: str,
        host: str,
        rate: int,
    ) -> dict[str, Any]:
        self._acquire(rate)
        url = f"https://{host}{path}"
        try:
            response = self._http.get(
                url,
                params=dict(params),
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        except (PluginHttpError, HostCallError) as exc:
            raise SubtitleUnavailable("连接射手网失败，请稍后再试") from exc
        _raise_http_error(response, auth_message="API Token 无效或已过期，请重新填写")
        payload = json_object(response, service="射手网", family="subtitle")
        status = payload.get("status")
        if status not in (0, "0"):
            message = _text(payload.get("errmsg")) or "射手网返回了错误"
            raise SubtitleUnavailable(_safe_text(message))
        return payload

    def _acquire(self, rate: int) -> None:
        now = self._monotonic()
        with self._lock:
            while self._requests and now - self._requests[0] >= 60:
                self._requests.popleft()
            if len(self._requests) >= max(1, min(60, rate)):
                raise SubtitleUnavailable(RATE_LIMITED_MESSAGE, retry_after=60)
            self._requests.append(now)

    def _prune_cache(self, now: float) -> None:
        for key in [
            key for key, (created, _) in self._cache.items() if now - created >= SEARCH_CACHE_TTL
        ]:
            del self._cache[key]

    def _require_token(self) -> str:
        token = self._token()
        if token is None:
            raise SubtitleUnavailable(NO_TOKEN_MESSAGE)
        return token

    def _token(self) -> str | None:
        value = self._settings.values().get(TOKEN_SECRET)
        return value.strip() if isinstance(value, str) and value.strip() else None

    def _host(self) -> str:
        value = self._settings.values().get(HOST_FIELD)
        return str(value) if value in HOSTS else DEFAULT_HOST

    def _rate(self) -> int:
        return _rate_value(self._settings.values().get(RATE_FIELD))


def _rate_value(value: object) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    return DEFAULT_RATE


def _raise_http_error(response: PluginHttpResponse, *, auth_message: str) -> None:
    if response.status_code == 509:
        raise SubtitleUnavailable(RATE_LIMITED_MESSAGE, retry_after=60)
    if response.status_code in (401, 403):
        raise SubtitleUnavailable(auth_message)
    raise_for_status(response, service="射手网", family="subtitle")


def _subs(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    sub = payload.get("sub")
    if not isinstance(sub, dict):
        return []
    subs = sub.get("subs")
    if not isinstance(subs, list):
        return []
    return [item for item in subs if isinstance(item, dict)]


def _sub_id(sub: Mapping[str, Any]) -> str:
    value = sub.get("id")
    if isinstance(value, int | str) and not isinstance(value, bool) and str(value):
        return str(value)
    return ""


def _sub_files(sub: Mapping[str, Any]) -> list[_File]:
    files: list[_File] = []
    filelist = sub.get("filelist")
    if isinstance(filelist, list):
        for item in filelist:
            if not isinstance(item, dict):
                continue
            name = _text(item.get("f"))
            if not name:
                continue
            files.append(_File(name=name, url=_text(item.get("url")) or None))
    if files:
        return files
    name = _text(sub.get("filename"))
    if not name:
        return []
    return [_File(name=name, url=_text(sub.get("url")) or None)]


def _select_file(files: list[_File], filename: str) -> _File | None:
    for item in files:
        if item.url is not None and item.name == filename:
            return item
    for item in files:
        if item.url is not None and _extension(item.name) in _SUBTITLE_EXTENSIONS:
            return item
    return None


def _download_url(value: str) -> str:
    try:
        parts = urlsplit(value)
    except ValueError:
        raise SubtitleUnavailable("字幕文件地址不正确") from None
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not (
        host == "assrt.net" or host.endswith(".assrt.net")
    ):
        raise SubtitleUnavailable("字幕文件地址不正确")
    return value


def _download_content(response: PluginHttpResponse) -> bytes:
    length = _content_length(response.headers)
    if length is not None and length > MAX_DOWNLOAD:
        raise SubtitleUnavailable("字幕文件超过 5 MiB")
    content = response.content
    if len(content) > MAX_DOWNLOAD:
        raise SubtitleUnavailable("字幕文件超过 5 MiB")
    return content


def _content_length(headers: Mapping[str, str]) -> int | None:
    value = next(
        (item for key, item in headers.items() if key.casefold() == "content-length"),
        None,
    )
    if value is None:
        return None
    try:
        length = int(value.strip())
    except ValueError:
        return None
    return length if length >= 0 else None


def _languages(sub: Mapping[str, Any]) -> list[str]:
    lang = sub.get("lang")
    if not isinstance(lang, dict):
        return []
    result: list[str] = []
    langlist = lang.get("langlist")
    if isinstance(langlist, dict):
        for key, code in _LANG_CODES:
            if langlist.get(key) and code not in result:
                result.append(code)
    if not result:
        language = _language_from_text(_text(lang.get("desc")))
        if language is not None:
            result.append(language)
    return result


def _language_from_text(value: str) -> str | None:
    text = value.lower()
    if not text:
        return None
    if "简" in text or "chs" in text or "zh-hans" in text or "zhcn" in text:
        return "zh-Hans"
    if "繁" in text or "cht" in text or "zh-hant" in text or "zhtw" in text:
        return "zh-Hant"
    if "双" in text or "dou" in text or "中英" in text:
        return "zh-Hans"
    if "英" in text or "eng" in text or "english" in text:
        return "en"
    if "日" in text or "jpn" in text or text == "ja":
        return "ja"
    if "韩" in text or "kor" in text or text == "ko":
        return "ko"
    return None


def _choose_language(languages: list[str], requested: str | None) -> str | None:
    if requested:
        if requested in languages:
            return requested
        if requested == "zh" and any(code.startswith("zh") for code in languages):
            return next(code for code in languages if code.startswith("zh"))
        return None
    return languages[0] if languages else None


def _label(language: str | None, sub: Mapping[str, Any]) -> str:
    if language in _LANGUAGE_LABELS:
        label = _LANGUAGE_LABELS[language]
        if language == "zh-Hans" and _is_double(sub):
            label = _DOUBLE_LABEL
        return label
    return _text(sub.get("native_name")) or "在线字幕"


def _is_double(sub: Mapping[str, Any]) -> bool:
    lang = sub.get("lang")
    if not isinstance(lang, dict):
        return False
    langlist = lang.get("langlist")
    if isinstance(langlist, dict) and bool(langlist.get("langdou")):
        return True
    desc = _text(lang.get("desc"))
    return "双" in desc or "中英" in desc


def _episode_matches(name: str, media: MediaContext) -> bool:
    if media.season is None or media.episode is None:
        return True
    found = _EPISODE.search(name)
    return (
        found is not None
        and int(found.group(1)) == media.season
        and int(found.group(2)) == media.episode
    )


def _is_archive(content: bytes, name: str) -> bool:
    lowered = name.lower()
    return content.startswith((b"PK\x03\x04", b"Rar!", b"7z\xbc\xaf\x27\x1c")) or lowered.endswith(
        (".zip", ".rar", ".7z")
    )


def _unsafe_path(name: str) -> bool:
    path = name.replace("\\", "/")
    return path.startswith("/") or ".." in path.split("/")


def _extension(name: str) -> str:
    suffix = name.rpartition(".")[2]
    return suffix.lower() if suffix and suffix != name else ""


def _stem(name: str | None) -> str:
    if not name:
        return ""
    base = name.rpartition("/")[2]
    return base.rpartition(".")[0] or base


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _safe_text(message: str) -> str:
    lowered = message.lower()
    if "token" in lowered or "authorization" in lowered or "bearer" in lowered:
        return "API Token 无效或已过期，请重新填写"
    return message[:200]
