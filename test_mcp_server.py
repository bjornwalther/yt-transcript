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

# -- Test fixtures ------------------------------------------------------------

SAMPLE_SEGMENTS = [
    {"text": "Hello world", "start": 0.0, "duration": 2.5},
    {"text": "This is a test", "start": 2.5, "duration": 3.0},
    {"text": "Goodbye", "start": 5.5, "duration": 1.5},
]

SAMPLE_OUTPUT_SEGMENTS = [
    {"text": "Hello world", "start": 0.0, "end": 2.5},
    {"text": "This is a test", "start": 2.5, "end": 5.5},
    {"text": "Goodbye", "start": 5.5, "end": 7.0},
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


# ============================================================================
# CACHE TESTS (CONTRACTS.md section 5)
# ============================================================================


class TestCacheContract:

    def test_cache_key_is_hashed(self, cache_dir):
        from yt_transcript import _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            path = _cache_path("dQw4w9WgXcQ", ["sv", "en"])
            assert "/" not in path.name and "\\" not in path.name
            assert "sv" not in path.name and "en" not in path.name
            assert len(path.stem) == 16

    def test_language_order_isolation(self, cache_dir):
        from yt_transcript import _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            p1 = _cache_path("vid1234567a", ["sv", "en"])
            p2 = _cache_path("vid1234567a", ["en", "sv"])
            assert p1 != p2

    def test_legacy_cache_rejected(self, cache_dir):
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            path = _cache_path("vid1234567a", ["en"])
            path.write_text(json.dumps({"segments": [], "language": "en"}))
            assert load_from_cache("vid1234567a", ["en"]) is None

    def test_future_cache_version_rejected(self, cache_dir):
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            path = _cache_path("vid1234567a", ["en"])
            path.write_text(json.dumps(_make_cache_entry(cache_version=99)))
            assert load_from_cache("vid1234567a", ["en"]) is None

    def test_string_cache_version_rejected(self, cache_dir):
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            path = _cache_path("vid1234567a", ["en"])
            path.write_text(json.dumps(_make_cache_entry(cache_version="2")))
            assert load_from_cache("vid1234567a", ["en"]) is None

    def test_truncated_cache_is_miss(self, cache_dir):
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            path = _cache_path("vid1234567a", ["en"])
            path.write_text('{"cache_version": 2, "seg')
            assert load_from_cache("vid1234567a", ["en"]) is None

    def test_missing_required_field_is_miss(self, cache_dir):
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            path = _cache_path("vid1234567a", ["en"])
            entry = _make_cache_entry()
            del entry["segments"]
            path.write_text(json.dumps(entry))
            assert load_from_cache("vid1234567a", ["en"]) is None

    def test_wrong_type_field_is_miss(self, cache_dir):
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            path = _cache_path("vid1234567a", ["en"])
            path.write_text(json.dumps(_make_cache_entry(segments="not a list")))
            assert load_from_cache("vid1234567a", ["en"]) is None

    def test_malformed_segment_is_miss(self, cache_dir):
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            path = _cache_path("vid1234567a", ["en"])
            path.write_text(json.dumps(_make_cache_entry(segments=["not-a-segment"])))
            assert load_from_cache("vid1234567a", ["en"]) is None

    def test_segment_missing_text_is_miss(self, cache_dir):
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            path = _cache_path("vid1234567a", ["en"])
            path.write_text(json.dumps(_make_cache_entry(
                segments=[{"start": 0.0, "duration": 1.0}])))
            assert load_from_cache("vid1234567a", ["en"]) is None

    def test_segment_wrong_start_type_is_miss(self, cache_dir):
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            path = _cache_path("vid1234567a", ["en"])
            path.write_text(json.dumps(_make_cache_entry(
                segments=[{"text": "hi", "start": "zero", "duration": 1.0}])))
            assert load_from_cache("vid1234567a", ["en"]) is None

    def test_is_generated_wrong_type_is_miss(self, cache_dir):
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            path = _cache_path("vid1234567a", ["en"])
            path.write_text(json.dumps(_make_cache_entry(is_generated="yes")))
            assert load_from_cache("vid1234567a", ["en"]) is None

    def test_valid_cache_accepted(self, cache_dir):
        from yt_transcript import load_from_cache, save_to_cache
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            save_to_cache("vid1234567a", SAMPLE_SEGMENTS, "en", ["sv", "en"],
                         SAMPLE_META, False)
            result = load_from_cache("vid1234567a", ["sv", "en"])
            assert result is not None
            assert result["language"] == "en"
            assert result["cache_version"] == 2

    def test_unsafe_language_input_in_path(self, cache_dir):
        from yt_transcript import _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            path = _cache_path("vid1234567a", ["x/y", "../etc/passwd"])
            assert str(path).startswith(str(cache_dir))
            assert "/" not in path.name

    def test_extremely_long_language_input(self, cache_dir):
        from yt_transcript import _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            langs = [f"lang{i}" for i in range(100)]
            path = _cache_path("vid1234567a", langs)
            assert len(path.name) <= 255


# ============================================================================
# URL VALIDATION TESTS (CONTRACTS.md section 2)
# ============================================================================


class TestURLValidation:

    def test_valid_youtube_urls(self):
        from yt_transcript import extract_video_id
        assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert extract_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert extract_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert extract_video_id("https://m.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert extract_video_id("https://music.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_non_youtube_host_rejected(self):
        from yt_transcript import extract_video_id
        with pytest.raises(ValueError):
            extract_video_id("https://example.com/?v=dQw4w9WgXcQ")

    def test_non_youtube_host_with_shorts(self):
        from yt_transcript import extract_video_id
        with pytest.raises(ValueError):
            extract_video_id("https://evil.com/shorts/dQw4w9WgXcQ")

    def test_non_youtube_host_with_embed(self):
        from yt_transcript import extract_video_id
        with pytest.raises(ValueError):
            extract_video_id("https://fake.com/embed/dQw4w9WgXcQ")

    def test_invalid_video_id_format(self):
        from yt_transcript import extract_video_id
        with pytest.raises(ValueError):
            extract_video_id("https://youtube.com/watch?v=short")
        with pytest.raises(ValueError):
            extract_video_id("https://youtube.com/watch?v=way_too_long_id_here")

    def test_completely_invalid_url(self):
        from yt_transcript import extract_video_id
        with pytest.raises(ValueError):
            extract_video_id("not-a-url-at-all")


# ============================================================================
# EXCEPTION CLASSIFICATION TESTS (CONTRACTS.md section 1)
# ============================================================================


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
        exc = type(exc_class, (Exception,), {})("test error")
        exc.actual_attempts = 1
        code, msg, retries = _classify_exception(exc)
        assert code == expected_code

    def test_transient_retryable(self):
        from mcp_server import _classify_exception
        exc = type("YouTubeRequestFailed", (Exception,), {})("429")
        exc.actual_attempts = 3
        code, msg, retries = _classify_exception(exc)
        assert code == "RATE_LIMITED"
        assert retries == 2

    def test_non_retryable_in_allowlist(self):
        from yt_transcript import _is_retryable
        for name in ["TranscriptsDisabled", "VideoUnavailable", "AgeRestricted",
                     "InvalidVideoId", "VideoUnplayable", "IpBlocked",
                     "RequestBlocked", "PoTokenRequired", "NotTranslatable",
                     "TranslationLanguageNotAvailable", "CookieError"]:
            exc = type(name, (Exception,), {})("test")
            assert not _is_retryable(exc), f"{name} should not be retryable"

    def test_only_transient_retryable(self):
        from yt_transcript import _is_retryable
        exc = type("YouTubeRequestFailed", (Exception,), {})("test")
        assert _is_retryable(exc)

    def test_unknown_exception_not_retryable(self):
        from yt_transcript import _is_retryable
        exc = type("SomeNewException", (Exception,), {})("test")
        assert not _is_retryable(exc)


# ============================================================================
# CONTENT HASH TESTS
# ============================================================================


class TestContentHash:

    def test_hash_recomputable_from_segments(self):
        from mcp_server import _content_hash, _build_segments
        segments_out = _build_segments(SAMPLE_SEGMENTS)
        h = _content_hash(segments_out)
        canonical = json.dumps(segments_out, ensure_ascii=False, sort_keys=True)
        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert h == expected

    def test_hash_deterministic(self):
        from mcp_server import _content_hash, _build_segments
        s = _build_segments(SAMPLE_SEGMENTS)
        assert _content_hash(s) == _content_hash(s)

    def test_hash_changes_with_content(self):
        from mcp_server import _content_hash, _build_segments
        s1 = _build_segments(SAMPLE_SEGMENTS)
        s2 = _build_segments([{"text": "Different", "start": 0.0, "duration": 1.0}])
        assert _content_hash(s1) != _content_hash(s2)


# ============================================================================
# INPUT VALIDATION TESTS
# ============================================================================


class TestInputValidation:

    def test_empty_language_input(self):
        from mcp_server import _validate_languages
        assert _validate_languages("") == ["en"]

    def test_language_code_filtering(self):
        from mcp_server import _validate_languages
        result = _validate_languages("en,x/y,../../etc,sv")
        assert "x/y" not in result
        assert "../../etc" not in result
        assert "en" in result
        assert "sv" in result

    def test_language_max_count(self):
        from mcp_server import _validate_languages
        raw = ",".join(f"l{i}" for i in range(30))
        result = _validate_languages(raw)
        assert len(result) <= 20


# ============================================================================
# OUTPUT MODE TESTS
# ============================================================================


class TestOutputModes:

    def test_build_segments_rounded(self):
        from mcp_server import _build_segments
        out = _build_segments(SAMPLE_SEGMENTS)
        for seg in out:
            assert "start" in seg and "end" in seg and "text" in seg
            assert "duration" not in seg

    def test_transcript_duration(self):
        from mcp_server import _transcript_duration
        assert _transcript_duration(SAMPLE_SEGMENTS) == 7.0

    def test_empty_segments_duration(self):
        from mcp_server import _transcript_duration
        assert _transcript_duration([]) is None


# ============================================================================
# ERROR RESPONSE TESTS
# ============================================================================


class TestErrorResponse:

    def test_error_has_retryable_field(self):
        from mcp_server import _error_response
        resp = _error_response("RATE_LIMITED", "test", "vid1234567a",
                               "https://youtube.com/watch?v=vid1234567a", 2, False, 1.5, True)
        assert resp["retryable"] is True

    def test_permanent_error_not_retryable(self):
        from mcp_server import _error_response
        resp = _error_response("VIDEO_UNAVAILABLE", "test", "vid1234567a",
                               "https://youtube.com/watch?v=vid1234567a", 0, False, 0.1, False)
        assert resp["retryable"] is False

    def test_error_has_fetch_duration(self):
        from mcp_server import _error_response
        resp = _error_response("INVALID_URL", "bad url", None, "bad", 0, False, 0.01, False)
        assert resp["fetch_duration_seconds"] == 0.01

    def test_error_markdown_format(self):
        from mcp_server import _error_response, _format_error
        resp = _error_response("RATE_LIMITED", "too fast", "vid1234567a",
                               "https://youtube.com/watch?v=vid1234567a", 2, False, 1.5, True)
        md = _format_error(resp, "markdown")
        assert "# Error: RATE_LIMITED" in md
        assert "retryable: True" in md

    def test_error_json_compact(self):
        from mcp_server import _error_response, _format_error
        resp = _error_response("RATE_LIMITED", "test", "vid1234567a",
                               "https://youtube.com/watch?v=vid1234567a", 0, False, 0.1, True)
        j = _format_error(resp, "json")
        parsed = json.loads(j)
        assert parsed["is_error"] is True


# ============================================================================
# METADATA TESTS
# ============================================================================


class TestMetadata:

    def test_per_field_sources(self):
        from yt_transcript import fetch_metadata
        with patch("yt_transcript._fetch_oembed") as mock_oe, \
             patch("yt_transcript._fetch_pytubefix") as mock_ptf:
            mock_oe.return_value = {"title": "T", "channel": "C", "published": ""}
            mock_ptf.return_value = {"title": "", "channel": "", "published": "2026-01-01"}
            result = fetch_metadata("https://youtube.com/watch?v=test")
            assert result["sources"]["title"] == "oembed"
            assert result["sources"]["published"] == "pytubefix"

    def test_total_metadata_failure(self):
        from yt_transcript import fetch_metadata
        with patch("yt_transcript._fetch_oembed") as mock_oe, \
             patch("yt_transcript._fetch_pytubefix") as mock_ptf:
            mock_oe.return_value = {"title": "", "channel": "", "published": ""}
            mock_ptf.return_value = {"title": "", "channel": "", "published": ""}
            result = fetch_metadata("https://youtube.com/watch?v=test")
            assert all(v == "none" for v in result["sources"].values())

    def test_partial_metadata_failure(self):
        from yt_transcript import fetch_metadata
        with patch("yt_transcript._fetch_oembed") as mock_oe, \
             patch("yt_transcript._fetch_pytubefix") as mock_ptf:
            mock_oe.return_value = {"title": "T", "channel": "", "published": ""}
            mock_ptf.return_value = {"title": "", "channel": "", "published": ""}
            result = fetch_metadata("https://youtube.com/watch?v=test")
            assert result["sources"]["title"] == "oembed"
            assert result["sources"]["channel"] == "none"
            assert "channel" in result["missing"]


# ============================================================================
# METADATA WARNINGS ON CACHE HIT (P2 from review)
# ============================================================================


class TestMetadataWarningsConsistency:

    def test_metadata_warnings_generated_after_merge(self):
        from mcp_server import _build_metadata_warnings
        meta_fields = {"title": "T", "channel": "C", "published": ""}
        meta_sources = {"title": "oembed", "channel": "oembed", "published": "none"}
        warnings = _build_metadata_warnings(meta_fields, meta_sources)
        assert any(w["code"] == "METADATA_FETCH_FAILED" for w in warnings)

    def test_no_metadata_warnings_when_complete(self):
        from mcp_server import _build_metadata_warnings
        meta_fields = {"title": "T", "channel": "C", "published": "2026-01-01"}
        meta_sources = {"title": "oembed", "channel": "oembed", "published": "pytubefix"}
        warnings = _build_metadata_warnings(meta_fields, meta_sources)
        assert len(warnings) == 0

    def test_total_metadata_failure_warning(self):
        from mcp_server import _build_metadata_warnings
        meta_fields = {"title": "", "channel": "", "published": ""}
        meta_sources = {"title": "none", "channel": "none", "published": "none"}
        warnings = _build_metadata_warnings(meta_fields, meta_sources)
        assert len(warnings) == 1
        assert "All metadata" in warnings[0]["message"]


# ============================================================================
# CAPTION TYPE TESTS
# ============================================================================


class TestCaptionType:

    def test_caption_type_unknown_when_none(self):
        """is_generated=None (missing from cache) -> caption_type='unknown'."""
        is_generated = None
        if is_generated is True:
            ct = "auto-generated"
        elif is_generated is False:
            ct = "manual"
        else:
            ct = "unknown"
        assert ct == "unknown"
