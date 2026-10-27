"""识别器：从资料库目录下的相对路径解析片名、年份、季集、版本属性和附加内容（ADR 0012、0016）。

纯函数，不访问数据库和网络。规则参考 MoviePilot 的命名习惯：资料库多数已整理成
「片名 (年份)/片名 (年份) - 2160p.mkv」和「剧名 (年份)/Season 1/剧名 - S01E01 - 第 1 集.mkv」，
同时兼容发布组式命名（点分隔）、目录名里的 {tmdb-ID} 标签和蓝光原盘目录。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

LibraryKind = Literal["movie", "series"]

# 附加内容目录（任意一级命中即忽略）。不含 Emby 的 Other、Shorts：它们常被用作分类目录名
_EXTRA_DIRS = frozenset(
    {
        "extras",
        "extra",
        "featurettes",
        "featurette",
        "sample",
        "samples",
        "trailers",
        "trailer",
        "behind the scenes",
        "deleted scenes",
        "interviews",
        "bonus",
        "花絮",
        "预告",
        "预告片",
        "特典",
    }
)
_EXTRA_WORD = re.compile(r"(?:^|[\s._\-\[(])(sample|trailer|预告片?|花絮)(?:[\s._\-\])]|$)", re.I)

_SPACES = re.compile(r"[\s 　]+")
_TMDB_TAG = re.compile(r"[\[{(]\s*tmdb(?:id)?\s*[-=:]\s*(\d+)\s*[\]})]", re.I)
_PAREN_YEAR = re.compile(r"[(（]\s*((?:19|20)\d{2})\s*[)）]")
_BARE_YEAR = re.compile(r"(?<![0-9a-z])((?:19|20)\d{2})(?![0-9a-z])", re.I)
_EPISODE = re.compile(r"(?<![a-z])S(\d{1,3})\s*E(\d{1,4})(?!\d)", re.I)
_SEASON_TOKEN = re.compile(r"(?<![a-z0-9])S(\d{1,3})(?![0-9e])", re.I)
_EPISODE_ONLY = re.compile(r"(?<![a-z0-9])EP?(\d{1,4})(?![0-9])", re.I)
_CHINESE_EPISODE = re.compile(r"第\s*(\d{1,4})\s*[集话話期]")
_LEADING_NUMBER = re.compile(r"^(\d{1,4})(?=$|[\s._\-\])])")
_PART = re.compile(r"(?<![a-z])part\s*(\d{1,2})(?!\d)", re.I)
_SEASON_DIR = re.compile(
    r"^(?:season\s*(\d{1,3})|s(\d{1,3})|specials?|sp|第\s*([0-9一二三四五六七八九十]+)\s*季)$", re.I
)
# 不限定具体数值：资料库里有 2016p、1008p 这样的笔误，照原样显示比丢掉更容易被发现
_RESOLUTION = re.compile(r"(?<![0-9])(\d{3,4})[pi](?![a-z])", re.I)
_UHD = re.compile(r"(?<![a-z0-9])(4k|uhd)(?![a-z0-9])", re.I)
# 片名到这里为止：年份、季集、清晰度等技术信息从这些标记开始
_TECH = re.compile(
    r"(?<![a-z0-9])(?:(?:2160|1440|1080|720|576|480)[pi]|4k|uhd|web-?dl|webrip|blu-?ray|"
    r"bdrip|remux|hdtv|dvdrip|h\.?26[45]|x26[45]|hevc|avc)(?![a-z0-9])",
    re.I,
)
_CJK = re.compile(r"[㐀-鿿豈-﫿]")
_LATIN_WORD = re.compile(r"[A-Za-z]")
_CHINESE_NUMERALS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}

BDMV_MARKER = "/BDMV/STREAM/"


@dataclass(frozen=True)
class ParsedName:
    title: str | None
    # 中英混排时的另一个名字（通常是英文名），用于搜索回退
    alt_title: str | None
    year: int | None
    tmdb_id: int | None


@dataclass(frozen=True)
class VersionInfo:
    resolution: str | None = None
    source: str | None = None
    hdr: str | None = None
    video_codec: str | None = None
    audio: str | None = None
    part: int | None = None

    def label(self, size: int | None) -> str:
        """版本显示名，例如「2160p · WEB-DL · DV · 58 GB」。"""
        parts = [self.resolution, self.source, self.hdr]
        if self.part is not None:
            parts.append(f"Part {self.part}")
        if size:
            parts.append(format_size(size))
        return " · ".join(p for p in parts if p) or "默认版本"

    def to_json(self) -> dict[str, str | int | None]:
        return {
            "resolution": self.resolution,
            "source": self.source,
            "hdr": self.hdr,
            "video_codec": self.video_codec,
            "audio": self.audio,
            "part": self.part,
        }


@dataclass(frozen=True)
class Recognized:
    # 作品目录（电影目录或剧集目录）相对资料库目录的路径；作品就是资料库目录本身时为空字符串，
    # 一个文件单独成为作品时是它自己的路径。同一作品下的文件一起匹配 TMDB
    work_path: str
    name: ParsedName
    season: int | None
    episode: int | None
    version: VersionInfo
    # 蓝光原盘的根目录（BDMV 的上级），同一张盘只保留最大的流文件
    disc: str | None
    # 附加内容的原因；不是附加内容时为 None
    extra: str | None


def recognize(kind: LibraryKind, rel_path: str, root_name: str = "") -> Recognized:
    """识别一个视频文件。root_name 是资料库目录自己的名字，作品目录就是资料库目录时用它解析片名。"""
    rel_path = normalize(rel_path)
    dirs = rel_path.split("/")[:-1]
    filename = rel_path.rpartition("/")[2]
    stem = filename.rpartition(".")[0] or filename
    extra = _extra_reason(dirs, stem)
    disc = None
    if f"/{rel_path}".find(BDMV_MARKER) >= 0:
        disc = f"/{rel_path}".partition(BDMV_MARKER)[0].lstrip("/")
        dirs = disc.split("/") if disc else []
    version = parse_version(stem if disc is None else "", rel_path)
    if kind == "movie":
        work_path, name = _movie_work(rel_path, dirs, stem, root_name, disc is not None)
        return Recognized(work_path, name, None, None, version, disc, extra)
    work_path, name, season = _series_work(dirs, stem, root_name)
    episode_season, episode = parse_episode(stem)
    return Recognized(
        work_path,
        name,
        episode_season if episode_season is not None else season,
        episode,
        version,
        disc,
        extra,
    )


def normalize(text: str) -> str:
    return _SPACES.sub(" ", text).strip()


def parse_name(raw: str) -> ParsedName:
    """从目录名或文件名（不含扩展名）解析片名、年份和 TMDB 标签。"""
    text = normalize(raw)
    tmdb_id = None
    if match := _TMDB_TAG.search(text):
        tmdb_id = int(match.group(1))
        text = normalize(text[: match.start()] + " " + text[match.end() :])
    year = None
    if match := _PAREN_YEAR.search(text):
        year = int(match.group(1))
    # 「S01E38 - 火影忍者 - Naruto - [2160p]」这类以集号开头的名字，片名在后面的段里；
    # 整体能取到片名时不拆段，保留「电锯人 - 剧场版：蕾塞篇」这样带分隔符的片名
    segments = [text] + [s for s in re.split(r"\s+-\s+", text) if s]
    for segment in segments:
        title, segment_year = _title_of(segment)
        if year is None:
            year = segment_year
        if title:
            zh, alt = _split_languages(title)
            return ParsedName(zh, alt, year, tmdb_id)
    return ParsedName(None, None, year, tmdb_id)


def parse_episode(stem: str) -> tuple[int | None, int | None]:
    """从文件名解析（季，集）。只有集号时季为 None，由季目录决定。"""
    text = normalize(stem)
    if match := _EPISODE.search(text):
        return int(match.group(1)), int(match.group(2))
    if match := _CHINESE_EPISODE.search(text):
        return None, int(match.group(1))
    if match := _EPISODE_ONLY.search(text):
        return None, int(match.group(1))
    if match := _LEADING_NUMBER.match(text):
        return None, int(match.group(1))
    return None, None


def season_of_dir(name: str) -> int | None:
    """季目录（Season 1、S01、Specials、第一季）的季号，不是季目录时返回 None。"""
    text = normalize(name)
    match = _SEASON_DIR.match(text)
    if match is None:
        # 季目录后面跟着年份或清晰度等标签，例如 Season 01 REMUX、第一季 (2011) 4K
        tagless = _BARE_YEAR.sub(" ", _TECH.sub(" ", _PAREN_YEAR.sub(" ", text)))
        tagless = normalize(re.sub(r"[\[\]()（）【】._\-]", " ", tagless))
        if tagless != text:
            match = _SEASON_DIR.match(tagless)
    if match is None:
        # 发布组式的季包目录，例如 Show.2023.S01.2160p.WEB-DL
        if (token := _SEASON_TOKEN.search(text)) and not _EPISODE.search(text):
            return int(token.group(1))
        return None
    number = match.group(1) or match.group(2)
    if number is not None:
        return int(number)
    if match.group(3) is not None:
        return _chinese_number(match.group(3))
    return 0


def parse_version(stem: str, rel_path: str) -> VersionInfo:
    text = normalize(stem)
    resolution = None
    if match := _RESOLUTION.search(text):
        resolution = f"{match.group(1)}{match.group(0)[-1].lower()}"
    elif _UHD.search(text):
        resolution = "2160p"
    lower = text.lower()
    source = None
    if f"/{rel_path}".find(BDMV_MARKER) >= 0:
        source = "原盘"
    elif rel_path.lower().endswith(".iso"):
        source = "ISO"
    elif "remux" in lower:
        source = "REMUX"
    elif re.search(r"blu-?ray|bdrip|(?<![a-z])bd(?=\d|[\s.\-]|$)", lower):
        source = "BluRay"
    elif re.search(r"web-?dl", lower):
        source = "WEB-DL"
    elif "webrip" in lower:
        source = "WEBRip"
    elif "hdtv" in lower:
        source = "HDTV"
    elif re.search(r"dvd", lower):
        source = "DVD"
    hdr = None
    if re.search(r"dovi|dolby[\s.]?vision|(?<![a-z])dv(?![a-z])", lower):
        hdr = "DV"
    elif re.search(r"hdr10\+|hdr10plus", lower):
        hdr = "HDR10+"
    elif re.search(r"(?<![a-z])hdr", lower):
        hdr = "HDR"
    video_codec = None
    if re.search(r"h\.?265|x265|hevc", lower):
        video_codec = "HEVC"
    elif re.search(r"h\.?264|x264|(?<![a-z])avc", lower):
        video_codec = "H.264"
    elif re.search(r"(?<![a-z])av1(?![0-9])", lower):
        video_codec = "AV1"
    elif re.search(r"mpeg-?2", lower):
        video_codec = "MPEG-2"
    audio = None
    for pattern, label in (
        (r"atmos", "Atmos"),
        (r"truehd", "TrueHD"),
        (r"dts-?hd|dts-?x", "DTS-HD"),
        (r"(?<![a-z])dts(?![a-z])", "DTS"),
        (r"ddp|eac3|e-ac-3|dd\+|dolby[\s.]?digital[\s.]?plus", "DDP"),
        (r"ac3|dolby[\s.]?digital|(?<![a-z])dd(?=[\s.]?\d)", "DD"),
        (r"flac", "FLAC"),
        (r"aac", "AAC"),
    ):
        if re.search(pattern, lower):
            audio = label
            break
    part = int(match.group(1)) if (match := _PART.search(text)) else None
    return VersionInfo(resolution, source, hdr, video_codec, audio, part)


def format_size(size: int) -> str:
    gb = size / 1024**3
    if gb >= 1:
        return f"{gb:.1f}".removesuffix(".0") + " GB"
    return f"{max(1, round(size / 1024**2))} MB"


# ---- 字幕 ----

_SUBTITLE_LANGUAGES = (
    # （标记，语言代码，显示名）。按顺序匹配，繁体放在简体前面，避免 zh-tw 先命中 zh
    (r"cht|tc|big5|zh-?tw|zh-?hk|zh-?hant|繁体|繁中|繁", "zh-Hant", "繁体中文"),
    (r"chs|sc|gb|zh-?cn|zh-?hans|zh-?sg|简体|简中|简", "zh-Hans", "简体中文"),
    (r"chi|zho|zh|chinese|中文|中字", "zh", "中文"),
    (r"eng|en|english|英文|英", "en", "英文"),
    (r"jpn|ja|jp|japanese|日文|日", "ja", "日文"),
    (r"kor|ko|kr|korean|韩文|韩", "ko", "韩文"),
)


@dataclass(frozen=True)
class SubtitleInfo:
    language: str | None
    label: str
    forced: bool
    default: bool


def parse_language_token(token: str) -> str | None:
    """单个语言标记的规范码：文件名里的 chs、播放器上报的 chi / zh-Hans，整段匹配，不区分大小写。"""
    text = normalize(token).lower()
    return None if not text else _language_of([text])[0]


def subtitle_match_length(subtitle_path: str, video_path: str) -> int | None:
    """字幕是否属于这个视频：同一目录下，字幕文件名以视频文件名（去掉扩展名）开头。

    返回匹配的视频名长度，越长越具体；不属于返回 None。
    """
    folder, _, name = subtitle_path.rpartition("/")
    video_folder, _, video_name = video_path.rpartition("/")
    if video_folder != folder:
        return None
    name = normalize(name).lower()
    stem = normalize(video_name.rpartition(".")[0] or video_name).lower()
    separated = name[len(stem) : len(stem) + 1] in (".", " ", "_", "-", "[")
    return len(stem) if name.startswith(stem) and separated else None


def match_subtitle(subtitle_path: str, video_paths: list[str]) -> str | None:
    """找出字幕对应的视频，同一目录下取文件名最长的匹配。"""
    best: tuple[int, str] | None = None
    for video in video_paths:
        length = subtitle_match_length(subtitle_path, video)
        if length is not None and (best is None or length > best[0]):
            best = (length, video)
    return best[1] if best else None


def subtitle_info(subtitle_path: str, video_path: str) -> SubtitleInfo:
    """用字幕文件名在视频文件名之后的部分解析语言和标记，例如 .chs.forced.srt。"""
    name = normalize(subtitle_path.rpartition("/")[2])
    video_stem = normalize(video_path.rpartition("/")[2].rpartition(".")[0])
    rest = name[len(video_stem) :].rpartition(".")[0].lower()
    # 整段（zh-cn）和按连字符拆开的部分都参与匹配，整段优先命中更具体的语言
    whole = [t for t in re.split(r"[\s._\[\]()]+", rest) if t]
    tokens = whole + [p for t in whole if "-" in t for p in t.split("-") if p]
    language, matched_label = _language_of(tokens)
    label = matched_label or "外挂字幕"
    forced = "forced" in tokens
    if forced:
        label += "（强制）"
    return SubtitleInfo(language, label, forced, "default" in tokens)


# ---- 内部 ----


def _language_of(tokens: list[str]) -> tuple[str | None, str | None]:
    """在若干标记里找语言，返回（规范码，显示名）。按表顺序匹配，越具体的语言排在越前面。"""
    for pattern, code, display in _SUBTITLE_LANGUAGES:
        full = re.compile(f"^(?:{pattern})$")
        if any(full.match(token) for token in tokens):
            return code, display
    return None, None


def _extra_reason(dirs: list[str], stem: str) -> str | None:
    for directory in dirs:
        if normalize(directory).lower() in _EXTRA_DIRS:
            return f"附加内容目录「{directory}」"
    if match := _EXTRA_WORD.search(stem):
        return f"文件名含「{match.group(1)}」"
    return None


def _movie_work(
    rel_path: str, dirs: list[str], stem: str, root_name: str, is_disc: bool
) -> tuple[str, ParsedName]:
    from_file = parse_name(stem) if not is_disc else ParsedName(None, None, None, None)
    if not dirs:
        if not is_disc and (from_file.year or from_file.tmdb_id or not root_name):
            return rel_path, from_file
        return "", _prefer(parse_name(root_name), from_file)
    from_dir = parse_name(dirs[-1])
    # 上级目录看起来不像电影目录（没有年份和标签），而文件名有年份时，说明文件散放在分类目录里，
    # 每个文件单独成为一部作品，避免同一分类下的电影被并成一部
    if not (from_dir.year or from_dir.tmdb_id) and from_file.year and not is_disc:
        return rel_path, from_file
    return "/".join(dirs), _prefer(from_dir, from_file)


def _series_work(dirs: list[str], stem: str, root_name: str) -> tuple[str, ParsedName, int | None]:
    season = None
    if dirs and (season := season_of_dir(dirs[-1])) is not None:
        dirs = dirs[:-1]
    from_file = parse_name(stem)
    if not dirs:
        root = parse_name(root_name) if root_name else ParsedName(None, None, None, None)
        root_season = season_of_dir(root_name) if root_name else None
        if root_season is not None and season is None:
            season = root_season
        return "", _prefer(from_file if root_season is not None else root, from_file), season
    return "/".join(dirs), _prefer(parse_name(dirs[-1]), from_file), season


def _prefer(primary: ParsedName, fallback: ParsedName) -> ParsedName:
    """目录名优先，缺的字段用文件名补。"""
    if primary.title is None:
        return ParsedName(
            fallback.title,
            fallback.alt_title,
            primary.year or fallback.year,
            primary.tmdb_id or fallback.tmdb_id,
        )
    return ParsedName(
        primary.title,
        primary.alt_title or (fallback.alt_title if fallback.title == primary.title else None),
        primary.year or fallback.year,
        primary.tmdb_id or fallback.tmdb_id,
    )


def _title_of(segment: str) -> tuple[str | None, int | None]:
    """取一段名字里技术标记之前的部分作为片名，同时返回其中的年份。"""
    text = re.sub(r"^\[[^\]]*\]\s*", "", segment)  # 开头的发布组标签 [ANi]
    cut = len(text)
    year = None
    for pattern in (_PAREN_YEAR, _EPISODE, _SEASON_TOKEN, _TECH, _CHINESE_EPISODE):
        if (match := pattern.search(text)) and match.start() < cut:
            cut = match.start()
    if match := _PAREN_YEAR.search(text):
        # 有括号年份时，它前面的数字都属于片名，例如「乘风2025 (2025)」「1917 (2019)」
        year = int(match.group(1))
    else:
        # 发布组式命名的年份不带括号，取技术标记前最后一个；开头的年份是片名本身（1917.2019.1080p）
        bare = [m for m in _BARE_YEAR.finditer(text) if 0 < m.start() < cut]
        if bare:
            cut = bare[-1].start()
            year = int(bare[-1].group(1))
    # 发布组式命名用点分隔单词（心花路放.Breakup.Buddies）；两侧都是单个字符的点保留，
    # 例如缩写 G.O.A.T 和数字 2.0
    title = re.sub(r"(?<=[^\s.]{2})\.|\.(?=[^\s.]{2})", " ", text[:cut])
    title = normalize(title).strip(" -_.[(（")
    title = re.sub(r"\s*[\[(（][^\])）]*$", "", title).strip(" -_.")
    # 「龙之家族 第三季」：季号由季目录决定，搜索时只用剧名
    title = re.sub(r"\s*第\s*[0-9一二三四五六七八九十]+\s*季$", "", title).strip(" -_.")
    return (title or None), year


def _split_languages(title: str) -> tuple[str, str | None]:
    """「心花路放 Breakup Buddies」拆成中文名和英文名。"""
    if not (_CJK.search(title) and _LATIN_WORD.search(title)):
        return title, None
    match = re.match(r"^(.*[㐀-鿿豈-﫿0-9：:·！!？?，,]+)[\s._\-]+([A-Za-z].*)$", title)
    if match is None:
        return title, None
    zh, alt = match.group(1).strip(), match.group(2).strip()
    if _CJK.search(alt):
        return title, None
    return zh, alt


def _chinese_number(text: str) -> int:
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if text.startswith("十"):
        return 10 + _CHINESE_NUMERALS.get(text[1:], 0)
    if "十" in text:
        tens, _, ones = text.partition("十")
        return _CHINESE_NUMERALS.get(tens, 0) * 10 + _CHINESE_NUMERALS.get(ones, 0)
    return _CHINESE_NUMERALS.get(text, 0)
