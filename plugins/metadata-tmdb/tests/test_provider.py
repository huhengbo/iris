"""TMDB provider：候选、详情、图片、优先级和校验状态。"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from pathlib import Path

import pytest
from iris_plugin_sdk import (
    ImageLookupRequest,
    MetadataPriority,
    MetadataSearchRequest,
    MetadataUnavailable,
    RateLimiter,
    SettingsEndpoint,
    current_priority,
    priority_scope,
)

from iris_plugin_tmdb.admin import tmdb_settings
from iris_plugin_tmdb.errors import TmdbAuthFailed, TmdbNotConfigured, TmdbUnavailable
from iris_plugin_tmdb.gateway import TmdbPerson
from iris_plugin_tmdb.provider import TmdbMetadataProvider
from iris_plugin_tmdb.service import TmdbService
from iris_plugin_tmdb.validation import ValidationStore, validation_problem
from tests.fake_gateway import VALID_KEY, FakeTmdb


class RecordingLimiter(RateLimiter):
    def __init__(self) -> None:
        super().__init__(0)
        self.priorities: list[MetadataPriority] = []

    def acquire(self) -> None:
        self.priorities.append(current_priority())


def make_settings(tmp_path: Path, key: str | None = VALID_KEY) -> SettingsEndpoint:
    from iris_plugin_sdk import JsonSettingsStore

    store = JsonSettingsStore(tmp_path)
    if key is not None:
        store.put("api_key", key)
    return tmdb_settings(store)


def make_service(
    gateway: FakeTmdb,
    tmp_path: Path,
    *,
    key: str | None = VALID_KEY,
    limiter: RateLimiter | None = None,
) -> TmdbService:
    return TmdbService(
        gateway,
        limiter or RateLimiter(0),
        make_settings(tmp_path, key),
        ValidationStore(tmp_path),
    )


def make_provider(
    gateway: FakeTmdb,
    tmp_path: Path,
    *,
    key: str | None = VALID_KEY,
    limiter: RateLimiter | None = None,
) -> tuple[TmdbMetadataProvider, TmdbService]:
    service = make_service(gateway, tmp_path, key=key, limiter=limiter)
    return TmdbMetadataProvider(service, "builtin.tmdb"), service


def test_movie_search_returns_candidates(tmp_path: Path) -> None:
    gateway = FakeTmdb()
    gateway.add_movie(157336, "星际穿越", 2014, original_title="Interstellar")
    provider, _ = make_provider(gateway, tmp_path)

    candidates = provider.search(MetadataSearchRequest(title="星际穿越"))

    assert [(item.remote_id, item.title, item.year) for item in candidates] == [
        ("157336", "星际穿越", 2014)
    ]
    assert candidates[0].provider_id == "builtin.tmdb"


def test_provider_images_use_tmdb_id_directly(tmp_path: Path) -> None:
    gateway = FakeTmdb()
    gateway.add_movie(157336, "星际穿越", 2014, imdb_id="tt0816692")
    provider, _ = make_provider(gateway, tmp_path)

    images = provider.images(
        ImageLookupRequest(kind="movie", external_ids={"tmdb": "157336", "imdb": "tt0816692"})
    )

    assert [image.source for image in images] == [
        "/poster-157336.jpg",
        "/backdrop-157336.jpg",
        "/thumb-157336.jpg",
        "/logo-157336.png",
    ]
    assert "find:tt0816692" not in gateway.calls


def test_provider_images_find_tmdb_by_imdb(tmp_path: Path) -> None:
    gateway = FakeTmdb()
    gateway.add_movie(157336, "星际穿越", 2014, imdb_id="tt0816692")
    provider, _ = make_provider(gateway, tmp_path)

    images = provider.images(ImageLookupRequest(kind="movie", external_ids={"imdb": "tt0816692"}))

    assert len(images) == 4
    assert "find:tt0816692" in gateway.calls
    assert "images:movie:157336" in gateway.calls


def test_provider_images_without_ids_do_not_request(tmp_path: Path) -> None:
    gateway = FakeTmdb()
    provider, _ = make_provider(gateway, tmp_path)

    assert provider.images(ImageLookupRequest(kind="movie", external_ids={})) == []
    assert gateway.calls == []


def test_kind_is_forwarded_to_find(tmp_path: Path) -> None:
    gateway = FakeTmdb()
    gateway.add_series(108545, "三体", 2023, {1: 1}, imdb_id="tt20244034")
    provider, _ = make_provider(gateway, tmp_path)

    images = provider.images(ImageLookupRequest(kind="series", external_ids={"imdb": "tt20244034"}))

    assert len(images) == 4
    assert "find:tt20244034" in gateway.calls
    assert "images:series:108545" in gateway.calls


def test_record_contains_tmdb_id_and_fallback(tmp_path: Path) -> None:
    gateway = FakeTmdb()
    gateway.add_movie(157336, "Interstellar", 2014, imdb_id="tt0816692")
    details = gateway.titles[("movie", 157336)].details
    gateway.titles[("movie", 157336)].details = replace(
        details, fallback_fields=frozenset({"title"})
    )
    provider, _ = make_provider(gateway, tmp_path)

    record = provider.details("157336", kind="movie")

    assert record.external_ids == {"imdb": "tt0816692", "tmdb": "157336"}
    assert record.fallback_fields == frozenset({"title"})


def test_all_requests_read_priority_from_context(tmp_path: Path) -> None:
    gateway = FakeTmdb()
    gateway.add_movie(157336, "星际穿越", 2014, imdb_id="tt0816692")
    gateway.add_series(108545, "三体", 2023, {1: 1})
    gateway.people[42] = TmdbPerson(42, "演员", "Actor", None, None, None, None)
    limiter = RecordingLimiter()
    provider, _ = make_provider(gateway, tmp_path, limiter=limiter)

    with priority_scope("background"):
        provider.search(MetadataSearchRequest(title="星际穿越"))
        provider.images(ImageLookupRequest(kind="movie", external_ids={"tmdb": "157336"}))
        provider.details("157336", kind="movie")
        provider.season("108545", 1)
        provider.person("42")
        provider.image("/poster-157336.jpg", 780)

    assert limiter.priorities == ["background"] * 6

    provider.details("157336", kind="movie")
    assert limiter.priorities[-1] == "interactive"


def test_missing_key_reports_reason_without_request(tmp_path: Path) -> None:
    gateway = FakeTmdb()
    provider, service = make_provider(gateway, tmp_path, key=None)

    assert provider.unavailable_reason() == "还没有配置 TMDB Key，在插件 › TMDB 元数据里填写"
    with pytest.raises(TmdbNotConfigured):
        provider.details("157336", kind="movie")
    assert gateway.calls == []
    assert service.validation() is None


def test_auth_failure_marks_validation_invalid(tmp_path: Path) -> None:
    gateway = FakeTmdb()
    gateway.add_movie(157336, "星际穿越", 2014)
    provider, service = make_provider(gateway, tmp_path)
    gateway.valid_keys.clear()

    with pytest.raises(TmdbAuthFailed):
        provider.details("157336", kind="movie")

    validation = service.validation()
    assert validation is not None
    assert validation.status == "invalid"
    assert validation.checked_at
    assert validation_problem(service.validation(), blocking_only=True) is not None
    assert provider.unavailable_reason() is not None


def test_unreachable_is_recorded_but_not_blocking(tmp_path: Path) -> None:
    gateway = FakeTmdb()
    gateway.add_movie(157336, "星际穿越", 2014)
    provider, service = make_provider(gateway, tmp_path)
    gateway.error = TmdbUnavailable("network down")

    with pytest.raises(TmdbUnavailable):
        provider.details("157336", kind="movie")

    validation = service.validation()
    assert validation is not None
    assert validation.status == "unreachable"
    assert validation_problem(service.validation()) is not None
    assert validation_problem(service.validation(), blocking_only=True) is None
    assert provider.unavailable_reason() is None


def test_successful_request_marks_validation_ok(tmp_path: Path) -> None:
    gateway = FakeTmdb()
    gateway.add_movie(157336, "星际穿越", 2014)
    provider, service = make_provider(gateway, tmp_path)

    provider.details("157336", kind="movie")

    validation = service.validation()
    assert validation is not None
    assert validation.status == "ok"
    assert validation_problem(service.validation()) is None


def test_validation_is_cleared_when_key_changes(tmp_path: Path) -> None:
    gateway = FakeTmdb()
    gateway.add_movie(157336, "星际穿越", 2014)
    provider, service = make_provider(gateway, tmp_path)
    provider.details("157336", kind="movie")
    assert service.validation() is not None

    service.clear_validation()

    assert service.validation() is None


def test_corrupt_validation_file_is_ignored(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "validation.json").write_text("{not json", encoding="utf-8")
    gateway = FakeTmdb()
    gateway.add_movie(157336, "星际穿越", 2014)
    provider, service = make_provider(gateway, tmp_path)

    with caplog.at_level(logging.WARNING):
        assert provider.unavailable_reason() is None

    assert "校验状态文件已损坏" in caplog.text
    assert service.validation() is None

    provider.details("157336", kind="movie")

    validation = service.validation()
    assert validation is not None
    assert validation.status == "ok"
    assert json.loads((state / "validation.json").read_text(encoding="utf-8"))["status"] == "ok"


def test_person_and_season_details(tmp_path: Path) -> None:
    from iris_plugin_tmdb.gateway import TmdbPerson

    gateway = FakeTmdb()
    gateway.add_series(108545, "三体", 2023, {1: 2})
    gateway.people[42] = TmdbPerson(42, "演员", "Actor", "简介", None, None, None)
    provider, _ = make_provider(gateway, tmp_path)

    season = provider.season("108545", 1)
    person = provider.person("42")

    assert [episode.number for episode in season.episodes] == [1, 2]
    assert person.name == "演员"


def test_image_failure_is_metadata_not_found(tmp_path: Path) -> None:
    from iris_plugin_sdk import MetadataNotFound

    gateway = FakeTmdb()
    gateway.missing_images.add("/missing.jpg")
    provider, _ = make_provider(gateway, tmp_path)

    with pytest.raises(MetadataNotFound):
        provider.image("/missing.jpg", 780)


def test_metadata_error_is_metadata_unavailable(tmp_path: Path) -> None:
    gateway = FakeTmdb()
    gateway.error = MetadataUnavailable("down")
    provider, _ = make_provider(gateway, tmp_path)

    with pytest.raises(MetadataUnavailable):
        provider.search(MetadataSearchRequest(title="x"))


def test_unreachable_then_recovery_updates_validation(tmp_path: Path) -> None:
    gateway = FakeTmdb()
    gateway.add_movie(157336, "星际穿越", 2014)
    provider, service = make_provider(gateway, tmp_path)
    gateway.error = TmdbUnavailable("network down")

    with pytest.raises(TmdbUnavailable):
        provider.details("157336", kind="movie")
    validation = service.validation()
    assert validation is not None
    assert validation.status == "unreachable"

    gateway.error = None
    provider.details("157336", kind="movie")
    validation = service.validation()
    assert validation is not None
    assert validation.status == "ok"
