"""本地外挂字幕插件：匹配规则、同步里的接入，以及插件停用后的行为。不访问网络。"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient
from iris_plugin_sdk import MediaContext

from app.config import AppConfig
from app.library.subtitle_plugin import LOCAL_SUBTITLES, LocalSubtitleRequest
from tests.conftest import save_tmdb_key
from tests.fake_cloud115 import FakeCloud115
from tests.fake_tmdb import FakeTmdb
from tests.test_catalog import movie_library
from tests.test_sync import sync

FOLDER = "流浪地球 (2019)"
V2160 = f"{FOLDER}/流浪地球.2019.2160p.mkv"
V1080 = f"{FOLDER}/流浪地球.2019.1080p.mkv"


def search(video: str, *files: str) -> dict[str, tuple[str, str, bool, bool, float | None]]:
    request = LocalSubtitleRequest(MediaContext(title="", filename=video), files=files)
    return {
        c.remote_id: (c.language, c.label, c.forced, c.default, c.score)
        for c in LOCAL_SUBTITLES.search(request)
    }


def test_same_name_and_language_suffix_match() -> None:
    result = search(
        V2160,
        f"{FOLDER}/流浪地球.2019.2160p.srt",
        f"{FOLDER}/流浪地球.2019.2160p.zh.srt",
        f"{FOLDER}/流浪地球.2019.2160p.chs.ass",
        f"{FOLDER}/流浪地球.2019.2160p.eng.forced.vtt",
        f"{FOLDER}/流浪地球.2019.2160p.zh.default.srt",
    )
    score = float(len("流浪地球.2019.2160p"))
    assert result == {
        f"{FOLDER}/流浪地球.2019.2160p.srt": ("", "外挂字幕", False, False, score),
        f"{FOLDER}/流浪地球.2019.2160p.zh.srt": ("zh", "中文", False, False, score),
        f"{FOLDER}/流浪地球.2019.2160p.chs.ass": ("zh-Hans", "简体中文", False, False, score),
        f"{FOLDER}/流浪地球.2019.2160p.eng.forced.vtt": ("en", "英文（强制）", True, False, score),
        f"{FOLDER}/流浪地球.2019.2160p.zh.default.srt": ("zh", "中文", False, True, score),
    }


def test_other_video_other_folder_and_non_prefix_do_not_match() -> None:
    assert (
        search(
            V2160,
            f"{FOLDER}/流浪地球.2019.1080p.zh.srt",
            f"{FOLDER}/其他.srt",
            f"{FOLDER}/流浪地球.2019.2160pX.srt",
            "别的目录/流浪地球.2019.2160p.srt",
        )
        == {}
    )


def test_request_without_video_has_no_candidates() -> None:
    request = LocalSubtitleRequest(MediaContext(title=""), files=(f"{FOLDER}/a.srt",))
    assert LOCAL_SUBTITLES.search(request) == []


@pytest.fixture
def library(
    client: TestClient, admin: dict[str, str], cloud115: FakeCloud115, tmdb: FakeTmdb
) -> None:
    save_tmdb_key(client, admin)
    tmdb.add_movie(535167, "流浪地球", 2019, original_title="The Wandering Earth")
    movie_library(
        client,
        admin,
        cloud115,
        [
            V2160,
            V1080,
            # 两个版本都是它的前缀时，归到更具体的 2160p
            f"{FOLDER}/流浪地球.2019.2160p.zh.srt",
            f"{FOLDER}/流浪地球.2019.1080p.chs.ass",
            f"{FOLDER}/流浪地球.2019.eng.srt",
            f"{FOLDER}/无关.srt",
        ],
    )


def subtitles(config: AppConfig) -> dict[str, tuple[str, str | None, str]]:
    """字幕文件 → (所属版本文件, 语言, 标签)。"""
    with sqlite3.connect(config.database_path) as connection:
        rows = connection.execute(
            """
            SELECT c.rel_path, vf.rel_path, s.language, s.label
            FROM subtitle s JOIN media_file c ON c.id = s.media_file_id
            JOIN version v ON v.id = s.version_id JOIN media_file vf ON vf.id = v.media_file_id
            """
        ).fetchall()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


@pytest.mark.usefixtures("library")
def test_sync_attaches_subtitles_to_matching_versions(
    client: TestClient, admin: dict[str, str], config: AppConfig
) -> None:
    assert sync(client, admin)["status"] == "succeeded"

    assert subtitles(config) == {
        f"{FOLDER}/流浪地球.2019.2160p.zh.srt": (V2160, "zh", "中文"),
        f"{FOLDER}/流浪地球.2019.1080p.chs.ass": (V1080, "zh-Hans", "简体中文"),
    }
