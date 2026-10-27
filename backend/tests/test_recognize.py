"""识别器的表驱动测试。

样例来自真实资料库（/emby/电影、/emby/电视剧 的导出目录树，路径相对资料库目录，第一层是分类目录）；
标注「常见命名」的是资料库里暂时没有、但按 Emby/MoviePilot 习惯会出现的写法。
"""

from __future__ import annotations

import pytest

from app.library.recognize import (
    LibraryKind,
    VersionInfo,
    format_size,
    match_subtitle,
    parse_episode,
    recognize,
    season_of_dir,
    subtitle_info,
)

# (相对路径, 作品目录, 片名, 别名, 年份, tmdb_id)
MOVIES = [
    (
        "动画电影/三国的星空第一部 (2025)/三国的星空第一部 (2025) - 1080p.mkv",
        "动画电影/三国的星空第一部 (2025)",
        "三国的星空第一部",
        None,
        2025,
        None,
    ),
    (
        "动画电影/电锯人 - 剧场版：蕾塞篇 (2025)/电锯人 - 剧场版：蕾塞篇 (2025) - 1080p.mkv",
        "动画电影/电锯人 - 剧场版：蕾塞篇 (2025)",
        "电锯人 - 剧场版：蕾塞篇",
        None,
        2025,
        None,
    ),
    (
        "外语电影/变形金刚(2007)/变形金刚.2007.BD1080P.H265.AC3.双语-双字.mp4",
        "外语电影/变形金刚(2007)",
        "变形金刚",
        None,
        2007,
        None,
    ),
    (
        "华语电影/心花路放 (2014) {tmdb-284298}/心花路放.Breakup Buddies (2014).mp4",
        "华语电影/心花路放 (2014) {tmdb-284298}",
        "心花路放",
        "Breakup Buddies",
        2014,
        284298,
    ),
    (
        "外语电影/美国队长4 (2025)/美国队长4 (2025).2160p.WEB-DL.H.264.Dolby Digital 5.1.mkv",
        "外语电影/美国队长4 (2025)",
        "美国队长4",
        None,
        2025,
        None,
    ),
    (
        "华语电影/破·地狱 (2024) {tmdb-1226141}/"
        "破·地狱 (2024)\xa0-\xa01080p.BD\xa0Blu-ray\xa0Remux.h264.TrueHD\xa07.1-AY.mkv",
        "华语电影/破·地狱 (2024) {tmdb-1226141}",
        "破·地狱",
        None,
        2024,
        1226141,
    ),
    (
        "外语电影/G.O.A.T (2026)/BDMV/STREAM/00080.m2ts",
        "外语电影/G.O.A.T (2026)",
        "G.O.A.T",
        None,
        2026,
        None,
    ),
    (
        "华语电影/寒战1994 (2026)/寒战1994 (2026) - 2160p.mkv",
        "华语电影/寒战1994 (2026)",
        "寒战1994",
        None,
        2026,
        None,
    ),
    (
        "外语电影/距安德烈夫卡2000米 (2025)/距安德烈夫卡2000米 (2025) - 1080p.mkv",
        "外语电影/距安德烈夫卡2000米 (2025)",
        "距安德烈夫卡2000米",
        None,
        2025,
        None,
    ),
    (
        "外语电影/EPiC： Elvis Presley in Concert (2026)/BDMV/STREAM/00000.m2ts",
        "外语电影/EPiC： Elvis Presley in Concert (2026)",
        "EPiC： Elvis Presley in Concert",
        None,
        2026,
        None,
    ),
    (
        "动画电影/新神榜：哪吒重生 (2021)/新神榜：哪吒重生 (2021).mp4",
        "动画电影/新神榜：哪吒重生 (2021)",
        "新神榜：哪吒重生",
        None,
        2021,
        None,
    ),
    # 常见命名：发布组式文件名，片名本身是年份
    (
        "1917.2019.1080p.BluRay.x264.mkv",
        "1917.2019.1080p.BluRay.x264.mkv",
        "1917",
        None,
        2019,
        None,
    ),
    # 常见命名：电影文件散放在分类目录里，每个文件单独成为作品
    (
        "华语电影/让子弹飞 (2010) - 1080p.mkv",
        "华语电影/让子弹飞 (2010) - 1080p.mkv",
        "让子弹飞",
        None,
        2010,
        None,
    ),
    # 常见命名：英文发布名，中英文目录
    (
        "Blade.Runner.2049.2017.2160p.UHD.BluRay.REMUX.HDR.HEVC.Atmos-EPSiLON/"
        "Blade.Runner.2049.2017.2160p.UHD.BluRay.REMUX.HDR.HEVC.Atmos-EPSiLON.mkv",
        "Blade.Runner.2049.2017.2160p.UHD.BluRay.REMUX.HDR.HEVC.Atmos-EPSiLON",
        "Blade Runner 2049",
        None,
        2017,
        None,
    ),
]


@pytest.mark.parametrize(("path", "work", "title", "alt", "year", "tmdb_id"), MOVIES)
def test_movie_names(
    path: str, work: str, title: str, alt: str | None, year: int, tmdb_id: int | None
) -> None:
    result = recognize("movie", path, "电影")
    assert result.extra is None
    assert result.work_path == work.replace("\xa0", " ")
    assert (result.name.title, result.name.alt_title) == (title, alt)
    assert (result.name.year, result.name.tmdb_id) == (year, tmdb_id)
    assert (result.season, result.episode) == (None, None)


def test_movie_versions_in_one_folder_share_the_work() -> None:
    folder = "动画电影/驯龙高手2 (2014)"
    hd = recognize("movie", f"{folder}/驯龙高手2 (2014) - 1080p.mkv")
    uhd = recognize("movie", f"{folder}/驯龙高手2 (2014) - 2160p.mkv")
    assert hd.work_path == uhd.work_path == folder
    assert (hd.version.resolution, uhd.version.resolution) == ("1080p", "2160p")


def test_movie_at_library_root_uses_folder_name() -> None:
    # 资料库目录本身就是一部电影的目录
    result = recognize("movie", "BDMV/STREAM/00001.m2ts", "哥斯拉 (1954)")
    assert result.work_path == ""
    assert (result.name.title, result.name.year) == ("哥斯拉", 1954)
    assert result.disc == ""


def test_bluray_disc_streams() -> None:
    result = recognize("movie", "华语电影/鹿鼎记II神龙教 (1992)/BDMV/STREAM/00000.m2ts")
    assert result.work_path == "华语电影/鹿鼎记II神龙教 (1992)"
    assert result.disc == "华语电影/鹿鼎记II神龙教 (1992)"
    assert result.version.source == "原盘"
    assert (result.name.title, result.name.year) == ("鹿鼎记II神龙教", 1992)


# (相对路径, 作品目录, 剧名, 年份, tmdb_id, 季, 集)
SERIES = [
    (
        "儿童/超级飞侠 (2014)/Season 1/超级飞侠 - S01E01 - 第 1 集.mp4",
        "儿童/超级飞侠 (2014)",
        "超级飞侠",
        2014,
        None,
        1,
        1,
    ),
    (
        "国产剧/罚罪 (2022)/Season 2/罚罪 - S02E37 - 第 37 集.mp4",
        "国产剧/罚罪 (2022)",
        "罚罪",
        2022,
        None,
        2,
        37,
    ),
    (
        "国产剧/滤镜(2025)/滤镜.S01E01.mp4",
        "国产剧/滤镜(2025)",
        "滤镜",
        2025,
        None,
        1,
        1,
    ),
    (
        "国产剧/第二次拥抱 (2022)/Season 1/My.Way.2022.S01E01.V2.WEB-DL.4k.H265.AAC-Xiaomi.mp4",
        "国产剧/第二次拥抱 (2022)",
        "第二次拥抱",
        2022,
        None,
        1,
        1,
    ),
    (
        "国漫/中国奇谭 (2023)/YaoChinese.Folktales.2023.S01.2160p.WEB-DL.H265.AAC-HHWEB/"
        "YaoChinese.Folktales.2023.S01E01.2160p.WEB-DL.H265.AAC-HHWEB.mp4",
        "国漫/中国奇谭 (2023)",
        "中国奇谭",
        2023,
        None,
        1,
        1,
    ),
    (
        "日番/火影忍者/Season 1/S01E01 - 火影忍者 - Naruto - [2160p] - WEB-DL.H265.AAC-HDCTV.mkv",
        "日番/火影忍者",
        "火影忍者",
        None,
        None,
        1,
        1,
    ),
    (
        "综艺/乘风2025 (2025)/Season 6/乘风2025 - S06E04-Part1 - 第 4 集.mkv",
        "综艺/乘风2025 (2025)",
        "乘风2025",
        2025,
        None,
        6,
        4,
    ),
    (
        "综艺/我是歌手 (2013)/Season 1/S01E01 - 1080i.HDTV.MPEG-2.ts",
        "综艺/我是歌手 (2013)",
        "我是歌手",
        2013,
        None,
        1,
        1,
    ),
    (
        "综艺/我是歌手 (2013)/Season 2/S02E01 - 我是歌手20140103期：总决赛.mp4",
        "综艺/我是歌手 (2013)",
        "我是歌手",
        2013,
        None,
        2,
        1,
    ),
    (
        "综艺/我是歌手 (2013)/Season 10/我是歌手.S10E01.第1集.2013.WEB-DL.2160p.H265.AAC.mp4",
        "综艺/我是歌手 (2013)",
        "我是歌手",
        2013,
        None,
        10,
        1,
    ),
    (
        "综艺/奔跑吧！兄弟 (2014) {tmdb-98031}/Season 0/"
        "奔跑吧！兄弟 - S00E01 - 第 1 集 - 1080p.H.264.AAC 2.0.mp4",
        "综艺/奔跑吧！兄弟 (2014) {tmdb-98031}",
        "奔跑吧！兄弟",
        2014,
        98031,
        0,
        1,
    ),
    (
        "综艺/快乐大本营 (1997)/Season 2/快乐大本营 - S02E224 - 第 224 集.mp4",
        "综艺/快乐大本营 (1997)",
        "快乐大本营",
        1997,
        None,
        2,
        224,
    ),
    (
        "欧美剧/龙之家族 第三季 (2026)/Season 3/龙之家族 第三季 - S03E01 - 第 1 集.mkv",
        "欧美剧/龙之家族 第三季 (2026)",
        "龙之家族",
        2026,
        None,
        3,
        1,
    ),
    (
        "未分类/通用/蝉 (2026)/Season 1/蝉 - S01E01 - 第 1 集.mkv",
        "未分类/通用/蝉 (2026)",
        "蝉",
        2026,
        None,
        1,
        1,
    ),
    (
        "国产剧/你好1983 (2026)/Season 1/你好1983 - S01E36 - 第 36 集.mkv",
        "国产剧/你好1983 (2026)",
        "你好1983",
        2026,
        None,
        1,
        36,
    ),
    # 常见命名：中文季目录，文件名只有集号
    (
        "国漫/斗罗大陆 (2018)/第二季/第05集.mp4",
        "国漫/斗罗大陆 (2018)",
        "斗罗大陆",
        2018,
        None,
        2,
        5,
    ),
    (
        "日番/孤独摇滚 (2022)/Specials/EP01.mkv",
        "日番/孤独摇滚 (2022)",
        "孤独摇滚",
        2022,
        None,
        0,
        1,
    ),
    (
        "日番/葬送的芙莉莲 (2023)/S01/03.mkv",
        "日番/葬送的芙莉莲 (2023)",
        "葬送的芙莉莲",
        2023,
        None,
        1,
        3,
    ),
    # 季目录带上片源标签
    (
        "欧美剧/权力的游戏 (2011)/Season 08 REMUX/权利的游戏S08E01 - 2160p.UHD Blu-ray Remux.mkv",
        "欧美剧/权力的游戏 (2011)",
        "权力的游戏",
        2011,
        None,
        8,
        1,
    ),
]


@pytest.mark.parametrize(("path", "work", "title", "year", "tmdb_id", "season", "episode"), SERIES)
def test_series_names(
    path: str,
    work: str,
    title: str,
    year: int | None,
    tmdb_id: int | None,
    season: int,
    episode: int,
) -> None:
    result = recognize("series", path, "电视剧")
    assert result.extra is None
    assert result.work_path == work
    assert (result.name.title, result.name.year, result.name.tmdb_id) == (title, year, tmdb_id)
    assert (result.season, result.episode) == (season, episode)


def test_series_library_folder_is_the_show() -> None:
    # 资料库目录直接指向一部剧
    result = recognize("series", "Season 1/巅峰对决 - S01E01 - 第 1 集.mkv", "巅峰对决 (2025)")
    assert result.work_path == ""
    assert (result.name.title, result.name.year) == ("巅峰对决", 2025)
    assert (result.season, result.episode) == (1, 1)


def test_series_library_folder_is_a_season() -> None:
    result = recognize("series", "巅峰对决 - S01E02 - 第 2 集.mkv", "Season 1")
    assert result.work_path == ""
    assert result.name.title == "巅峰对决"
    assert (result.season, result.episode) == (1, 2)


@pytest.mark.parametrize(
    ("name", "season"),
    [
        ("Season 1", 1),
        ("season 12", 12),
        ("S02", 2),
        ("Specials", 0),
        ("SP", 0),
        ("第一季", 1),
        ("第十二季", 12),
        ("第 3 季", 3),
        ("Season 01 REMUX", 1),
        ("Season 2 1080p", 2),
        ("Season.03.2160p.BluRay", 3),
        ("第一季 (2011)", 1),
        ("第二季 4K", 2),
        ("[Season 4] [WEB-DL]", 4),
        ("Season 1 Extras", None),
        ("YaoChinese.Folktales.2023.S01.2160p.WEB-DL.H265.AAC-HHWEB", 1),
        ("巅峰对决 (2025)", None),
        ("My.Way.2022.S01E01.V2", None),
        ("未分类", None),
    ],
)
def test_season_dirs(name: str, season: int | None) -> None:
    assert season_of_dir(name) == season


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        ("巅峰对决 - S01E01 - 第 1 集", (1, 1)),
        ("s01e05", (1, 5)),
        ("Show S1E2", (1, 2)),
        ("乘风2025 - S06E04-Part2 - 第 4 集", (6, 4)),
        ("第12集", (None, 12)),
        ("第 3 话", (None, 3)),
        ("EP07", (None, 7)),
        ("E12", (None, 12)),
        ("05", (None, 5)),
        ("预告", (None, None)),
    ],
)
def test_parse_episode(stem: str, expected: tuple[int | None, int | None]) -> None:
    assert parse_episode(stem) == expected


# (路径, 清晰度, 来源, HDR, 视频编码, 音频, 分段)
VERSIONS = [
    (
        "动画电影/疯狂动物城2 (2025)/疯狂动物城2 (2025) - 1440p.mkv",
        "1440p",
        None,
        None,
        None,
        None,
        None,
    ),
    (
        "外语电影/美国队长4 (2025)/美国队长4 (2025).2160p.WEB-DL.H.264.Dolby Digital 5.1.mkv",
        "2160p",
        "WEB-DL",
        None,
        "H.264",
        "DD",
        None,
    ),
    (
        "华语电影/破·地狱 (2024)/"
        "破·地狱 (2024)\xa0-\xa01080p.BD\xa0Blu-ray\xa0Remux.h264.TrueHD\xa07.1-AY.mkv",
        "1080p",
        "REMUX",
        None,
        "H.264",
        "TrueHD",
        None,
    ),
    (
        "外语电影/变形金刚(2007)/变形金刚.2007.BD1080P.H265.AC3.双语-双字.mp4",
        "1080p",
        "BluRay",
        None,
        "HEVC",
        "DD",
        None,
    ),
    (
        "日韩剧/鱿鱼游戏 (2021)/Season 1/"
        "鱿鱼游戏 - S01E01 - 第 1 集 - 2160p.WEB-DL.DoVi.HDR.H.265.DDP 5.1.Atmos.mkv",
        "2160p",
        "WEB-DL",
        "DV",
        "HEVC",
        "Atmos",
        None,
    ),
    (
        "国产剧/第二次拥抱 (2022)/Season 1/My.Way.2022.S01E01.V2.WEB-DL.4k.H265.AAC-Xiaomi.mp4",
        "2160p",
        "WEB-DL",
        None,
        "HEVC",
        "AAC",
        None,
    ),
    (
        "综艺/我是歌手 (2013)/Season 1/S01E01 - 1080i.HDTV.MPEG-2.ts",
        "1080i",
        "HDTV",
        None,
        "MPEG-2",
        None,
        None,
    ),
    (
        "综艺/乘风2025 (2025)/Season 6/乘风2025 - S06E04-Part2 - 第 4 集.mkv",
        None,
        None,
        None,
        None,
        None,
        2,
    ),
    ("外语电影/金童 (2025)/金童 (2025) - 1080p.iso", "1080p", "ISO", None, None, None, None),
    ("外语电影/Dracula (2025)/BDMV/STREAM/00000.m2ts", None, "原盘", None, None, None, None),
    # 资料库里的笔误照原样保留
    ("华语电影/日掛中天 (2025)/日掛中天 (2025) - 2016p.mkv", "2016p", None, None, None, None, None),
]


@pytest.mark.parametrize(
    ("path", "resolution", "source", "hdr", "codec", "audio", "part"), VERSIONS
)
def test_version_attributes(
    path: str,
    resolution: str | None,
    source: str | None,
    hdr: str | None,
    codec: str | None,
    audio: str | None,
    part: int | None,
) -> None:
    kind: LibraryKind = "series" if "/Season " in path else "movie"
    version = recognize(kind, path).version
    assert version == VersionInfo(resolution, source, hdr, codec, audio, part)


def test_version_label() -> None:
    remux = VersionInfo(resolution="2160p", source="REMUX", hdr="DV")
    assert remux.label(58 * 1024**3) == "2160p · REMUX · DV · 58 GB"
    assert VersionInfo().label(None) == "默认版本"
    assert VersionInfo(part=1).label(700 * 1024**2) == "Part 1 · 700 MB"
    assert format_size(int(1.5 * 1024**3)) == "1.5 GB"


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("外语电影/沙丘 (2021)/Extras/沙丘幕后.mkv", "附加内容目录「Extras」"),
        ("外语电影/沙丘 (2021)/Featurettes/Making Of.mkv", "附加内容目录「Featurettes」"),
        ("外语电影/沙丘 (2021)/Trailers/沙丘 预告.mp4", "附加内容目录「Trailers」"),
        (
            "外语电影/沙丘 (2021)/Behind The Scenes/拍摄花絮.mp4",
            "附加内容目录「Behind The Scenes」",
        ),
        ("华语电影/满江红 (2023)/花絮/片场.mp4", "附加内容目录「花絮」"),
        ("外语电影/沙丘 (2021)/沙丘 (2021) - sample.mkv", "文件名含「sample」"),
        ("外语电影/沙丘 (2021)/Dune.2021.Trailer.mkv", "文件名含「Trailer」"),
        ("华语电影/满江红 (2023)/满江红-预告片.mp4", "文件名含「预告片」"),
        ("国产剧/繁花 (2023)/Season 1/繁花 - 花絮 - 01.mp4", "文件名含「花絮」"),
    ],
)
def test_extras(path: str, reason: str) -> None:
    kind: LibraryKind = "series" if "/Season " in path else "movie"
    assert recognize(kind, path).extra == reason


@pytest.mark.parametrize(
    "path",
    [
        # 片名里含关键词但不是附加内容
        "外语电影/Samples of Love (2020)/Samples of Love (2020) - 1080p.mkv",
        "华语电影/花絮人生 (2019)/花絮人生 (2019) - 1080p.mkv",
        "动画电影/Other (2020)/Other (2020) - 1080p.mkv",
    ],
)
def test_not_extras(path: str) -> None:
    assert recognize("movie", path).extra is None


VIDEOS = [
    "动画电影/驯龙高手2 (2014)/驯龙高手2 (2014) - 1080p.mkv",
    "动画电影/驯龙高手2 (2014)/驯龙高手2 (2014) - 2160p.mkv",
    "外语电影/阿凡达：火与烬 (2025)/阿凡达：火与烬 (2025) - 1080p.mkv",
    "外语电影/美国队长4 (2025)/美国队长4 (2025).2160p.WEB-DL.H.264.Dolby Digital 5.1.mkv",
    "外语电影/怪奇收割 (2025)/怪奇收割 (2025) - 2160p.mkv",
]


@pytest.mark.parametrize(
    ("subtitle", "video", "language", "label", "forced", "default"),
    [
        (
            "动画电影/驯龙高手2 (2014)/驯龙高手2 (2014) - 1080p.srt",
            VIDEOS[0],
            None,
            "外挂字幕",
            False,
            False,
        ),
        (
            "外语电影/阿凡达：火与烬 (2025)/阿凡达：火与烬 (2025) - 1080p.default.chi.zh-cn.srt",
            VIDEOS[2],
            "zh-Hans",
            "简体中文",
            False,
            True,
        ),
        (
            "外语电影/美国队长4 (2025)/"
            "美国队长4 (2025).2160p.WEB-DL.H.264.Dolby Digital 5.1.chs.srt",
            VIDEOS[3],
            "zh-Hans",
            "简体中文",
            False,
            False,
        ),
        (
            "外语电影/怪奇收割 (2025)/怪奇收割 (2025) - 2160p.ass",
            VIDEOS[4],
            None,
            "外挂字幕",
            False,
            False,
        ),
        (
            "动画电影/驯龙高手2 (2014)/驯龙高手2 (2014) - 2160p.cht.forced.ass",
            VIDEOS[1],
            "zh-Hant",
            "繁体中文（强制）",
            True,
            False,
        ),
        (
            "动画电影/驯龙高手2 (2014)/驯龙高手2 (2014) - 2160p.eng.srt",
            VIDEOS[1],
            "en",
            "英文",
            False,
            False,
        ),
    ],
)
def test_subtitles(
    subtitle: str, video: str, language: str | None, label: str, forced: bool, default: bool
) -> None:
    assert match_subtitle(subtitle, VIDEOS) == video
    info = subtitle_info(subtitle, video)
    assert (info.language, info.label, info.forced, info.default) == (
        language,
        label,
        forced,
        default,
    )


def test_subtitle_without_matching_video() -> None:
    assert match_subtitle("外语电影/阿凡达：火与烬 (2025)/其他.srt", VIDEOS) is None
    # 不同目录的同名文件不算
    assert match_subtitle("动画电影/驯龙高手2 (2014) - 1080p.srt", VIDEOS) is None
