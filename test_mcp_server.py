"""Behavioral tests for yt-transcript-mcp, driven by CONTRACTS.md.

Tests mock youtube_transcript_api to avoid network calls.
Run: pytest test_mcp_server.py -v
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

SAMPLE_SEGMENTS = [
    {"text": "Hello world", "start": 0.0, "duration": 2.5},
    {"text": "This is a test", "start": 2.5, "duration": 3.0},
    {"text": "Goodbye", "start": 5.5, "duration": 1.5},
]

SAMPLE_META = {
    "fields": {"title": "Test Video", "channel": "Test Channel", "published": "2026-01-01"},
    "sources": {"title": "oembed", "channel": "oembed", "published": "pytubefix"},
    "missing": [],
    "complete": True,
}


@pytest.fixture
def cache_dir(tmp_path):
    return tmp_path


def _make_cache_entry(**overrides):
    entry = {
        "cache_version": 2,
        "segments": SAMPLE_SEGMENTS,
        "language": "en",
        "requested_languages": ["sv", "en"],
        "is_generated": False,
        "meta": {"title": "Test", "channel": "Test Ch", "published": "2026-01-01"},
        "meta_sources": {"title": "oembed", "channel": "oembed", "published": "pytubefix"},
        "cached_at": "2026-09-01",
    }
    entry.update(overrides)
    return entry


class TestCacheContract:

    def test_cache_key_is_hashed(self, cache_dir):
        from yt_transcript import _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            path = _cache_path("dQw4w9WgXcQ", ["sv", "en"])
            assert "sv" not in path.name and "en" not in path.name
            assert len(path.stem) == 16

    def test_language_order_isolation(self, cache_dir):
        from yt_transcript import _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            assert _cache_path("dQw4w9WgXcQ", ["sv", "en"]) != _cache_path("dQw4w9WgXcQ", ["en", "sv"])

    def test_legacy_cache_rejected(self, cache_dir):
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            _cache_path("dQw4w9WgXcQ", ["en"]).write_text(json.dumps({"segments": [], "language": "en"}))
            assert load_from_cache("dQw4w9WgXcQ", ["en"]) is None

    def test_future_cache_version_rejected(self, cache_dir):
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            _cache_path("dQw4w9WgXcQ", ["en"]).write_text(json.dumps(_make_cache_entry(cache_version=99)))
            assert load_from_cache("dQw4w9WgXcQ", ["en"]) is None

    def test_string_cache_version_rejected(self, cache_dir):
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            _cache_path("dQw4w9WgXcQ", ["en"]).write_text(json.dumps(_make_cache_entry(cache_version="2")))
            assert load_from_cache("dQw4w9WgXcQ", ["en"]) is None

    def test_truncated_cache_is_miss(self, cache_dir):
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            _cache_path("dQw4w9WgXcQ", ["en"]).write_text('{"cache_version": 2, "seg')
            assert load_from_cache("dQw4w9WgXcQ", ["en"]) is None

    def test_missing_required_field_is_miss(self, cache_dir):
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            entry = _make_cache_entry()
            del entry["segments"]
            _cache_path("dQw4w9WgXcQ", ["en"]).write_text(json.dumps(entry))
            assert load_from_cache("dQw4w9WgXcQ", ["en"]) is None

    def test_wrong_type_field_is_miss(self, cache_dir):
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            _cache_path("dQw4w9WgXcQ", ["en"]).write_text(json.dumps(_make_cache_entry(segments="not a list")))
            assert load_from_cache("dQw4w9WgXcQ", ["en"]) is None

    def test_malformed_segment_is_miss(self, cache_dir):
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            _cache_path("dQw4w9WgXcQ", ["en"]).write_text(json.dumps(_make_cache_entry(segments=["string"])))
            assert load_from_cache("dQw4w9WgXcQ", ["en"]) is None

    def test_segment_missing_text_is_miss(self, cache_dir):
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            _cache_path("dQw4w9WgXcQ", ["en"]).write_text(json.dumps(_make_cache_entry(
                segments=[{"start": 0.0, "duration": 1.0}])))
            assert load_from_cache("dQw4w9WgXcQ", ["en"]) is None

    def test_is_generated_wrong_type_is_miss(self, cache_dir):
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            _cache_path("dQw4w9WgXcQ", ["en"]).write_text(json.dumps(_make_cache_entry(is_generated="yes")))
            assert load_from_cache("dQw4w9WgXcQ", ["en"]) is None

    def test_valid_cache_accepted(self, cache_dir):
        from yt_transcript import load_from_cache, save_to_cache
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            save_to_cache("dQw4w9WgXcQ", SAMPLE_SEGMENTS, "en", ["sv", "en"], SAMPLE_META, False)
            result = load_from_cache("dQw4w9WgXcQ", ["sv", "en"])
            assert result is not None
            assert result["cache_version"] == 2

    def test_unsafe_language_in_path(self, cache_dir):
        from yt_transcript import _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            path = _cache_path("dQw4w9WgXcQ", ["x/y", "../etc"])
            assert str(path).startswith(str(cache_dir))
            assert "/" not in path.name


class TestDeepCacheValidation:

    def test_meta_title_wrong_type_is_miss(self, cache_dir):
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            entry = _make_cache_entry()
            entry["meta"]["title"] = 123
            _cache_path("dQw4w9WgXcQ", ["en"]).write_text(json.dumps(entry))
            assert load_from_cache("dQw4w9WgXcQ", ["en"]) is None

    def test_meta_channel_list_is_miss(self, cache_dir):
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            entry = _make_cache_entry()
            entry["meta"]["channel"] = ["not", "a", "string"]
            _cache_path("dQw4w9WgXcQ", ["en"]).write_text(json.dumps(entry))
            assert load_from_cache("dQw4w9WgXcQ", ["en"]) is None

    def test_meta_sources_integer_value_is_miss(self, cache_dir):
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            entry = _make_cache_entry()
            entry["meta_sources"]["title"] = 42
            _cache_path("dQw4w9WgXcQ", ["en"]).write_text(json.dumps(entry))
            assert load_from_cache("dQw4w9WgXcQ", ["en"]) is None

    def test_cached_at_invalid_format_is_miss(self, cache_dir):
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            _cache_path("dQw4w9WgXcQ", ["en"]).write_text(json.dumps(_make_cache_entry(cached_at="not-a-date")))
            assert load_from_cache("dQw4w9WgXcQ", ["en"]) is None

    def test_requested_languages_not_list_of_str(self, cache_dir):
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            _cache_path("dQw4w9WgXcQ", ["en"]).write_text(json.dumps(_make_cache_entry(requested_languages=[1, 2])))
            assert load_from_cache("dQw4w9WgXcQ", ["en"]) is None

    def test_nan_start_is_miss(self, cache_dir):
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            _cache_path("dQw4w9WgXcQ", ["en"]).write_text(json.dumps(
                _make_cache_entry(segments=[{"text": "hi", "start": float("nan"), "duration": 1.0}])))
            assert load_from_cache("dQw4w9WgXcQ", ["en"]) is None

    def test_bool_start_is_miss(self, cache_dir):
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            _cache_path("dQw4w9WgXcQ", ["en"]).write_text(json.dumps(
                _make_cache_entry(segments=[{"text": "hi", "start": True, "duration": 1.0}])))
            assert load_from_cache("dQw4w9WgXcQ", ["en"]) is None


class TestURLValidation:

    def test_valid_youtube_urls(self):
        from yt_transcript import extract_video_id
        assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert extract_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert extract_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert extract_video_id("https://m.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_non_youtube_host_rejected(self):
        from yt_transcript import extract_video_id
        with pytest.raises(ValueError):
            extract_video_id("https://example.com/?v=dQw4w9WgXcQ")

    def test_non_youtube_host_with_shorts(self):
        from yt_transcript import extract_video_id
        with pytest.raises(ValueError):
            extract_video_id("https://evil.com/shorts/dQw4w9WgXcQ")

    def test_invalid_video_id_format(self):
        from yt_transcript import extract_video_id
        with pytest.raises(ValueError):
            extract_video_id("https://youtube.com/watch?v=short")

    def test_completely_invalid_url(self):
        from yt_transcript import extract_video_id
        with pytest.raises(ValueError):
            extract_video_id("not-a-url-at-all")


class TestBareHostname:

    def test_bare_youtube_accepted(self):
        from yt_transcript import extract_video_id
        assert extract_video_id("youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_bare_youtu_be_accepted(self):
        from yt_transcript import extract_video_id
        assert extract_video_id("youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_bare_non_youtube_rejected(self):
        from yt_transcript import extract_video_id
        with pytest.raises(ValueError):
            extract_video_id("example.com/watch?v=dQw4w9WgXcQ")


class TestExceptionClassification:

    @pytest.mark.parametrize("exc_class,expected_code", [
        ("TranscriptsDisabled", "TRANSCRIPT_NOT_AVAILABLE"),
        ("NoTranscriptFound", "LANGUAGE_NOT_AVAILABLE"),
        ("VideoUnavailable", "VIDEO_UNAVAILABLE"),
        ("VideoUnplayable", "VIDEO_UNAVAILABLE"),
        ("InvalidVideoId", "INVALID_URL"),
        ("AgeRestricted", "VIDEO_UNAVAILABLE"),
        ("IpBlocked", "YOUTUBE_IP_BLOCKED"),
        ("RequestBlocked", "YOUTUBE_IP_BLOCKED"),
        ("PoTokenRequired", "PO_TOKEN_REQUIRED"),
        ("NotTranslatable", "LANGUAGE_NOT_AVAILABLE"),
        ("TranslationLanguageNotAvailable", "LANGUAGE_NOT_AVAILABLE"),
        ("YouTubeDataUnparsable", "RATE_LIMITED"),
        ("FailedToCreateConsentCookie", "RATE_LIMITED"),
        ("CookieError", "RATE_LIMITED"),
        ("CookieInvalid", "RATE_LIMITED"),
        ("CookiePathInvalid", "RATE_LIMITED"),
        ("CouldNotRetrieveTranscript", "RATE_LIMITED"),
        ("YouTubeTranscriptApiException", "RATE_LIMITED"),
    ])
    def test_exception_classification(self, exc_class, expected_code):
        from mcp_server import _classify_exception
        exc = type(exc_class, (Exception,), {})("test")
        exc.actual_attempts = 1
        code, msg, retries = _classify_exception(exc)
        assert code == expected_code

    def test_transient_retryable(self):
        from mcp_server import _classify_exception
        exc = type("YouTubeRequestFailed", (Exception,), {})("429")
        exc.actual_attempts = 3
        code, msg, retries = _classify_exception(exc)
        assert code == "RATE_LIMITED" and retries == 2

    def test_non_retryable(self):
        from yt_transcript import _is_retryable
        for name in ["TranscriptsDisabled", "VideoUnavailable", "AgeRestricted",
                     "InvalidVideoId", "IpBlocked", "PoTokenRequired", "CookieError"]:
            assert not _is_retryable(type(name, (Exception,), {})("test"))

    def test_only_transient_retryable(self):
        from yt_transcript import _is_retryable
        assert _is_retryable(type("YouTubeRequestFailed", (Exception,), {})("test"))

    def test_unknown_not_retryable(self):
        from yt_transcript import _is_retryable
        assert not _is_retryable(type("SomeNewException", (Exception,), {})("test"))


class TestContentHash:

    def test_hash_recomputable(self):
        from mcp_server import _content_hash, _build_segments
        s = _build_segments(SAMPLE_SEGMENTS)
        h = _content_hash(s)
        canonical = json.dumps(s, ensure_ascii=False, sort_keys=True)
        assert h == hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def test_hash_deterministic(self):
        from mcp_server import _content_hash, _build_segments
        s = _build_segments(SAMPLE_SEGMENTS)
        assert _content_hash(s) == _content_hash(s)

    def test_hash_changes(self):
        from mcp_server import _content_hash, _build_segments
        s1 = _build_segments(SAMPLE_SEGMENTS)
        s2 = _build_segments([{"text": "X", "start": 0.0, "duration": 1.0}])
        assert _content_hash(s1) != _content_hash(s2)


class TestInputValidation:

    def test_empty_language(self):
        from mcp_server import _validate_languages
        assert _validate_languages("") == ["en"]

    def test_language_filtering(self):
        from mcp_server import _validate_languages
        r = _validate_languages("en,x/y,../../etc,sv")
        assert "en" in r and "sv" in r and "x/y" not in r

    def test_language_max(self):
        from mcp_server import _validate_languages
        assert len(_validate_languages(",".join(f"l{i}" for i in range(30)))) <= 20


class TestOutputModes:

    def test_build_segments(self):
        from mcp_server import _build_segments
        for s in _build_segments(SAMPLE_SEGMENTS):
            assert "start" in s and "end" in s and "duration" not in s

    def test_duration(self):
        from mcp_server import _transcript_duration
        assert _transcript_duration(SAMPLE_SEGMENTS) == 7.0
        assert _transcript_duration([]) is None


class TestErrorResponse:

    def test_retryable_field(self):
        from mcp_server import _error_response
        assert _error_response("RATE_LIMITED", "t", "v", "u", 2, False, 1.5, True)["retryable"] is True

    def test_not_retryable(self):
        from mcp_server import _error_response
        assert _error_response("VIDEO_UNAVAILABLE", "t", "v", "u", 0, False, 0.1, False)["retryable"] is False

    def test_fetch_duration(self):
        from mcp_server import _error_response
        assert _error_response("INVALID_URL", "t", None, "u", 0, False, 0.01, False)["fetch_duration_seconds"] == 0.01

    def test_markdown_format(self):
        from mcp_server import _error_response, _format_error
        md = _format_error(_error_response("RATE_LIMITED", "fast", "v", "u", 2, False, 1.5, True), "markdown")
        assert "# Error: RATE_LIMITED" in md and "retryable: True" in md


class TestMetadata:

    def test_per_field_sources(self):
        from yt_transcript import fetch_metadata
        with patch("yt_transcript._fetch_oembed") as oe, patch("yt_transcript._fetch_pytubefix") as ptf:
            oe.return_value = {"title": "T", "channel": "C", "published": ""}
            ptf.return_value = {"title": "", "channel": "", "published": "2026-01-01"}
            r = fetch_metadata("https://youtube.com/watch?v=test")
            assert r["sources"]["title"] == "oembed" and r["sources"]["published"] == "pytubefix"

    def test_total_failure(self):
        from yt_transcript import fetch_metadata
        with patch("yt_transcript._fetch_oembed") as oe, patch("yt_transcript._fetch_pytubefix") as ptf:
            oe.return_value = {"title": "", "channel": "", "published": ""}
            ptf.return_value = {"title": "", "channel": "", "published": ""}
            r = fetch_metadata("https://youtube.com/watch?v=test")
            assert all(v == "none" for v in r["sources"].values())


class TestMetadataWarnings:

    def test_warnings_after_merge(self):
        from mcp_server import _build_metadata_warnings
        w = _build_metadata_warnings({"title": "T", "channel": "C", "published": ""},
                                     {"title": "oembed", "channel": "oembed", "published": "none"})
        assert any(x["code"] == "METADATA_FETCH_FAILED" for x in w)

    def test_no_warnings_when_complete(self):
        from mcp_server import _build_metadata_warnings
        assert len(_build_metadata_warnings(
            {"title": "T", "channel": "C", "published": "2026-01-01"},
            {"title": "oembed", "channel": "oembed", "published": "pytubefix"})) == 0

    def test_total_failure_warning(self):
        from mcp_server import _build_metadata_warnings
        w = _build_metadata_warnings({"title": "", "channel": "", "published": ""},
                                     {"title": "none", "channel": "none", "published": "none"})
        assert len(w) == 1 and "All metadata" in w[0]["message"]


class TestCaptionType:

    def test_unknown_when_none(self):
        g = None
        ct = "auto-generated" if g is True else ("manual" if g is False else "unknown")
        assert ct == "unknown"
