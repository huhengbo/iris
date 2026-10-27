"""NFO 解析、搜索结果的取舍和图片缓存的单元测试。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from iris_plugin_sdk import MetadataCandidate

from app.clock import SystemClock
from app.db import Database
from app.library.images import ImageError, ImageStore, image_sizes
from app.library.matcher import choose
from app.library.nfo import nfo_for_work, parse_nfo
from tests.fake_tmdb import image_bytes

# ---- NFO ----


@pytest.mark.parametrize(
    ("data", "kind", "expected"),
    [
        # Emby、Jellyfin、MoviePilot 的写法
        (
            '<movie><uniqueid type="tmdb" default="true">535167</uniqueid>'
            '<uniqueid type="imdb">tt7605074</uniqueid></movie>',
            "movie",
            (535167, "tt7605074"),
        ),
        # Kodi 旧写法和 tinyMediaManager
        (
            "<movie><tmdbid>157336</tmdbid><id>tt0816692</id></movie>",
            "movie",
            (157336, "tt0816692"),
        ),
        ("<movie><imdbid>tt0816692</imdbid></movie>", "movie", (None, "tt0816692")),
        # 只有一行链接
        ("https://www.themoviedb.org/tv/108545-three-body", "series", (108545, None)),
        # 剧集资料库里不要电影的链接
        ("https://www.themoviedb.org/movie/535167", "series", (None, None)),
        ("<uniqueid type='tmdb'>abc</uniqueid>", "movie", (None, None)),
        (b"\xff\xfe broken".decode("latin-1"), "movie", (None, None)),
    ],
)
def test_parse_nfo(data: str, kind: Any, expected: tuple[int | None, str | None]) -> None:
    ids = parse_nfo(data.encode(), kind)
    assert (ids.tmdb_id, ids.imdb_id) == expected


def test_nfo_for_work() -> None:
    nfos = ["流浪地球 (2019)/movie.nfo", "流浪地球 (2019)/other.nfo", "单片.2020.nfo"]
    assert (
        nfo_for_work("movie", "流浪地球 (2019)", ["流浪地球 (2019)/a.mkv"], nfos)
        == "流浪地球 (2019)/movie.nfo"
    )
    assert nfo_for_work("movie", "单片.2020.mkv", ["单片.2020.mkv"], nfos) == "单片.2020.nfo"
    assert nfo_for_work("movie", "B", ["B/b.mkv"], ["B/b.nfo", "B/extra.nfo"]) == "B/b.nfo"
    assert nfo_for_work("movie", "C", ["C/c.mkv"], ["C/x.nfo", "C/y.nfo"]) is None
    series_nfos = ["三体/tvshow.nfo", "三体/S01/E01.nfo"]
    assert nfo_for_work("series", "三体", ["三体/S01/E01.mkv"], series_nfos) == "三体/tvshow.nfo"


# ---- 搜索结果取舍 ----


def result(remote_id: int, title: str, year: int | None, original: str | None = None) -> Any:
    return MetadataCandidate(
        provider_id="test",
        remote_id=str(remote_id),
        title=title,
        year=year,
        kind="movie",
        original_title=original,
    )


def test_choose_prefers_the_single_result_of_the_same_year() -> None:
    results = [result(1, "流浪地球2", 2023), result(2, "流浪地球", 2019)]
    assert choose(results, {"流浪地球"}, 2019) == "2"


def test_choose_allows_one_year_off() -> None:
    assert choose([result(1, "某片", 2020)], {"某片"}, 2021) == "1"
    assert choose([result(1, "某片", 2018)], {"某片"}, 2021) is None


def test_choose_uses_titles_to_break_ties() -> None:
    results = [result(1, "Heat", 1995, "Heat"), result(2, "Heat 2", 1995)]
    assert choose(results, {"heat"}, 1995) == "1"
    assert choose([result(1, "甲", 1995), result(2, "乙", 1995)], {"丙"}, 1995) is None


def test_choose_without_year_needs_a_unique_title_match() -> None:
    assert choose([result(1, "Interstellar", 2014)], {"interstellar"}, None) == "1"
    assert choose([result(1, "Dune", 1984), result(2, "Dune", 2021)], {"dune"}, None) is None
    assert choose([result(1, "Other", 2014)], {"interstellar"}, None) is None


# ---- 图片缓存 ----


@pytest.fixture
def store(tmp_path: Path) -> ImageStore:
    return ImageStore(tmp_path / "images", SystemClock())


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[Any]:
    database = Database(tmp_path / "test.db")
    database.migrate(SystemClock())
    with database.session() as connection:
        yield connection


def files(store: ImageStore) -> list[str]:
    return sorted(p.name for p in store.root.rglob("*") if p.is_file())


def test_saved_images_have_every_width(store: ImageStore, connection: Any) -> None:
    poster = store.save(connection, "poster", "/p.jpg", image_bytes())
    logo = store.save(connection, "logo", "/l.png", image_bytes(png=True))

    assert store.save(connection, "poster", "/p.jpg", b"ignored") == poster
    names = files(store)
    assert len(names) == 3
    assert {n.rsplit("_", 1)[1] for n in names} == {"342.jpg", "780.jpg", "500.png"}
    digest = connection.execute("SELECT hash FROM image WHERE id = ?", (poster,)).fetchone()[0]
    assert [s.width for s in image_sizes(digest, "poster")] == [342, 780]
    for size in image_sizes(digest, "poster"):
        assert store.path(size.url.rsplit("/", 1)[1]) is not None
    assert logo != poster


def test_rejects_non_images(store: ImageStore, connection: Any) -> None:
    with pytest.raises(ImageError):
        store.save(connection, "poster", "/x.jpg", b"<html>not an image</html>")


@pytest.mark.parametrize(
    "name", ["../../etc/passwd", "abc_342.jpg", "0" * 32 + "_342.gif", "0" * 32 + "_x.jpg"]
)
def test_path_rejects_invalid_names(store: ImageStore, name: str) -> None:
    assert store.path(name) is None


def test_cleanup_keeps_files_shared_by_another_kind(store: ImageStore, connection: Any) -> None:
    data = image_bytes()
    poster = store.save(connection, "poster", "/same-as-poster.jpg", data)
    store.save(connection, "thumb", "/same-as-thumb.jpg", data)
    connection.execute(
        "INSERT INTO library (name, kind, sort_order, created_at, updated_at) "
        "VALUES ('电影', 'movie', 0, 'x', 'x')"
    )
    connection.execute(
        "INSERT INTO media_item (library_id, kind, provider_id, remote_id, title, thumb_image_id, "
        "scraped_at, added_at) VALUES (1, 'movie', 'builtin.tmdb', '1', '片', "
        "(SELECT id FROM image WHERE kind = 'thumb'), 'x', 'x')"
    )

    assert store.cleanup(connection) == 1

    # 海报的 342 档只有海报用，删除；780 档和横版图共用，保留
    assert [n.rsplit("_", 1)[1] for n in files(store)] == ["780.jpg"]
    assert connection.execute("SELECT id FROM image WHERE id = ?", (poster,)).fetchone() is None
