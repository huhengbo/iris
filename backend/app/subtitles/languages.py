"""字幕语言：把外挂字幕、内封字幕和音轨上报的语言归一到规范码，并判断已有语言是否够用。

规范码与字幕接口一致：zh-Hans、zh-Hant、zh（笼统中文）、en、ja、ko 等。外挂字幕文件名按
`app/library/recognize.py` 的规则解析；内封字幕和音轨由播放器上报 ISO 639-2 代码或标题，
所以这里再处理一遍 ISO 639-2 和标题里的中文名。
"""

from __future__ import annotations

import re

from app.library.recognize import normalize, parse_language_token

# 播放器和容器常上报 ISO 639-2/B、639-2/T，与字幕的两位码对应
_ISO_639_2 = {
    "chi": "zh",
    "zho": "zh",
    "eng": "en",
    "jpn": "ja",
    "kor": "ko",
    "fre": "fr",
    "fra": "fr",
    "ger": "de",
    "deu": "de",
    "spa": "es",
    "rus": "ru",
    "por": "pt",
    "ita": "it",
    "tha": "th",
    "vie": "vi",
    "hin": "hi",
}

# 内封字幕和音轨的标题是给人看的（「简体中文」「English SDH」），整段匹配不上，按关键词找。
# 英文只认完整单词，避免把普通的单词片段当成语言代码。
_TITLE_HINTS = (
    (re.compile(r"繁体|繁體|繁中|正体|正體", re.IGNORECASE), "zh-Hant"),
    (re.compile(r"简体|簡体|简中|簡中", re.IGNORECASE), "zh-Hans"),
    (re.compile(r"中文|中字|chinese|mandarin|cantonese|粤语|粵語|国语|國語", re.IGNORECASE), "zh"),
    (re.compile(r"english|英文|英语|英語", re.IGNORECASE), "en"),
    (re.compile(r"japanese|日文|日语|日語|日本語", re.IGNORECASE), "ja"),
    (re.compile(r"korean|韩文|韓文|韩语|韩語|韓語", re.IGNORECASE), "ko"),
)


def track_language(language: str | None, title: str | None) -> str | None:
    """播放器上报的字幕或音轨语言：语言代码优先，代码不认识时再看标题里的语言名。"""
    code = _normalize(language)
    if code is not None:
        return code
    text = normalize(title or "")
    if not text:
        return None
    for pattern, hint in _TITLE_HINTS:
        if pattern.search(text):
            return hint
    return None


def covers(have: str, wanted: str) -> bool:
    """已有语言 have 是否已经满足想要的 wanted。

    笼统的 zh 同时覆盖简繁两种中文；zh-Hans 不覆盖 zh-Hant，反过来也一样。
    """
    if have == wanted:
        return True
    if have == "zh":
        return wanted.startswith("zh")
    if wanted == "zh":
        return have.startswith("zh")
    return False


def _normalize(value: str | None) -> str | None:
    if value is None:
        return None
    text = normalize(value).lower().replace("_", "-")
    if not text:
        return None
    if text in _ISO_639_2:
        return _ISO_639_2[text]
    # chs、cht、zh-Hans、zh-CN 这类标记由识别器的表统一处理
    parsed = parse_language_token(text)
    if parsed is not None:
        return parsed
    # 其他两位码（fr、de…）原样保留，用于判断「已有这种语言」
    if len(text) == 2 and text.isalpha():
        return text
    return None
