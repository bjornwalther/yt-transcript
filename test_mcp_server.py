"""Behavioral tests for yt-transcript-mcp, driven by CONTRACTS.md.

Tests mock youtube_transcript_api to avoid network calls.
Run: pytest test_mcp_server.py -v
"""

import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

# ── Test fixtures ──────────────────────────────────────────────────────────────────

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
    """Provide a temp cache directory."""
    return tmp_path


def _make_cache_entry(**overrides):
    """Build a valid v2 cache entry."""
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


# ══════════════════════════════════════════════════════════════════════════════
# CACHE TESTS
# ══════════════════════════════════════════════════════════════════════════════


class TestCacheContract:
    """Cache behavior per CONTRACTS.md section 4."""

    def test_cache_key_is_hashed(self, cache_dir):
        """Cache filename is SHA256 hash, never contains raw language input."""
        from yt_transcript import _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            path = _cache_path("dQw4w9WgXcQ", ["sv", "en"])
            assert "/" not in path.name and "\\" not in path.name
            assert "sv" not in path.name
            assert "en" not in path.name
            assert len(path.stem) == 16  # 16 hex chars

    def test_language_order_isolation(self, cache_dir):
        """sv,en and en,sv produce different cache files."""
        from yt_transcript import _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            p1 = _cache_path("vid1", ["sv", "en"])
            p2 = _cache_path("vid1", ["en", "sv"])
            assert p1 != p2

    def test_legacy_cache_rejected(self, cache_dir):
        """Cache entry without cache_version is a miss."""
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            path = _cache_path("vid1", ["en"])
            entry = {"segments": [], "language": "en"}  # no cache_version
            path.write_text(json.dumps(entry))
            result = load_from_cache("vid1", ["en"])
            assert result is None

    def test_future_cache_version_rejected(self, cache_dir):
        """Cache version 99 is a miss (exact match required)."""
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            path = _cache_path("vid1", ["en"])
            entry = _make_cache_entry(cache_version=99)
            path.write_text(json.dumps(entry))
            result = load_from_cache("vid1", ["en"])
            assert result is None

    def test_string_cache_version_rejected(self, cache_dir):
        """cache_version: \"2\" (string) is a miss, not a crash."""
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            path = _cache_path("vid1", ["en"])
            entry = _make_cache_entry(cache_version="2")
            path.write_text(json.dumps(entry))
            result = load_from_cache("vid1", ["en"])
            assert result is None

    def test_truncated_cache_is_miss(self, cache_dir):
        """Truncated JSON is a miss, not a crash."""
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            path = _cache_path("vid1", ["en"])
            path.write_text('{"cache_version": 2, "seg')
            result = load_from_cache("vid1", ["en"])
            assert result is None

    def test_missing_required_field_is_miss(self, cache_dir):
        """Cache entry missing 'segments' is a miss."""
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            path = _cache_path("vid1", ["en"])
            entry = _make_cache_entry()
            del entry["segments"]
            path.write_text(json.dumps(entry))
            result = load_from_cache("vid1", ["en"])
            assert result is None

    def test_wrong_type_field_is_miss(self, cache_dir):
        """Cache entry with segments as string is a miss."""
        from yt_transcript import load_from_cache, _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            path = _cache_path("vid1", ["en"])
            entry = _make_cache_entry(segments="not a list")
            path.write_text(json.dumps(entry))
            result = load_from_cache("vid1", ["en"])
            assert result is None

    def test_valid_cache_accepted(self, cache_dir):
        """Valid v2 cache entry is returned."""
        from yt_transcript import load_from_cache, save_to_cache
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            save_to_cache("vid1", SAMPLE_SEGMENTS, "en", ["sv", "en"],
                         SAMPLE_META, False)
            result = load_from_cache("vid1", ["sv", "en"])
            assert result is not None
            assert result["language"] == "en"
            assert result["cache_version"] == 2

    def test_unsafe_language_input_in_path(self, cache_dir):
        """Language codes with path separators don't escape cache dir."""
        from yt_transcript import _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            path = _cache_path("vid1", ["x/y", "../etc/passwd"])
            assert str(path).startswith(str(cache_dir))
            assert "/" not in path.name

    def test_extremely_long_language_input(self, cache_dir):
        """Very long language list produces valid filename."""
        from yt_transcript import _cache_path
        with patch("yt_transcript.CACHE_DIR", cache_dir):
            langs = [f"lang{i}" for i in range(100)]
            path = _cache_path("vid1", langs)
            assert len(path.name) <= 255


# ══════════════════════════════════════════════════════════════════════════════
# EXCEPTION CLASSIFICATION TESTS
# ══════════════════════════════════════════════════════════════════════════════


class TestExceptionClassification:
    """Every youtube_transcript_api exception maps to correct error code."""

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
    ])
    def test_permanent_exception_classification(self, exc_class, expected_code):
        """Permanent exceptions classified correctly."""
        from mcp_server import _classify_exception
        exc = type(exc_class, (Exception,), {})("test error")
        exc.actual_attempts = 1
        code, msg, retries = _classify_exception(exc)
        assert code == expected_code

    def test_transient_exception_retryable(self):
        """YouTubeRequestFailed is classified as RATE_LIMITED."""
        from mcp_server import _classify_exception
        exc = type("YouTubeRequestFailed", (Exception,), {})("429 error")
        exc.actual_attempts = 3
        code, msg, retries = _classify_exception(exc)
        assert code == "RATE_LIMITED"
        assert retries == 2  # 3 attempts - 1

    def test_non_retryable_not_retried(self):
        """Permanent exceptions identified as non-retryable."""
        from yt_transcript import _is_retryable
        for exc_name in ["TranscriptsDisabled", "VideoUnavailable", "AgeRestricted",
                         "InvalidVideoId", "VideoUnplayable", "IpBlocked",
                         "RequestBlocked", "PoTokenRequired", "NotTranslatable",
                         "TranslationLanguageNotAvailable"]:
            exc = type(exc_name, (Exception,), {})("test")
            assert not _is_retryable(exc), f"{exc_name} should not be retryable"

    def test_transient_is_retryable(self):
        """YouTubeRequestFailed is retryable."""
        from yt_transcript import _is_retryable
        exc = type("YouTubeRequestFailed", (Exception,), {})("test")
        assert _is_retryable(exc)


# ══════════════════════════════════════════════════════════════════════════════
# CONTENT HASH TESTS
# ══════════════════════════════════════════════════════════════════════════════


class TestContentHash:

    def test_hash_recomputable_from_segments(self):
        """Hash can be recomputed from the segments array."""
        from mcp_server import _content_hash, _build_segments
        segments_out = _build_segments(SAMPLE_SEGMENTS)
        h = _content_hash(segments_out)
        canonical = json.dumps(segments_out, ensure_ascii=False, sort_keys=True)
        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert h == expected

    def test_hash_deterministic(self):
        """Same segments always produce same hash."""
        from mcp_server import _content_hash, _build_segments
        s = _build_segments(SAMPLE_SEGMENTS)
        assert _content_hash(s) == _content_hash(s)

    def test_hash_changes_with_content(self):
        """Different content produces different hash."""
        from mcp_server import _content_hash, _build_segments
        s1 = _build_segments(SAMPLE_SEGMENTS)
        s2 = _build_segments([{"text": "Different", "start": 0.0, "duration": 1.0}])
        assert _content_hash(s1) != _content_hash(s2)


# ══════════════════════════════════════════════════════════════════════════════
# INPUT VALIDATION TESTS
# ══════════════════════════════════════════════════════════════════════════════


class TestInputValidation:

    def test_invalid_url(self):
        """Non-YouTube URL raises ValueError."""
        from yt_transcript import extract_video_id
        with pytest.raises(ValueError):
            extract_video_id("https://example.com/not-youtube")

    def test_valid_url_formats(self):
        """All standard YouTube URL formats parse correctly."""
        from yt_transcript import extract_video_id
        assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert extract_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert extract_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_empty_language_input(self):
        """Empty language string falls back to default."""
        from mcp_server import _validate_languages
        result = _validate_languages("")
        assert result == ["en"]

    def test_language_code_validation(self):
        """Invalid language codes are filtered out."""
        from mcp_server import _validate_languages
        result = _validate_languages("en,x/y,../../etc,sv")
        assert "x/y" not in result
        assert "../../etc" not in result
        assert "en" in result
        assert "sv" in result

    def test_language_max_count(self):
        """More than 20 language codes are truncated."""
        from mcp_server import _validate_languages
        raw = ",".join(f"l{i}" for i in range(30))
        result = _validate_languages(raw)
        assert len(result) <= 20


# ══════════════════════════════════════════════════════════════════════════════
# OUTPUT MODE TESTS
# ══════════════════════════════════════════════════════════════════════════════


class TestOutputModes:

    def test_build_segments_rounded(self):
        """Output segments have rounded start/end, no duration."""
        from mcp_server import _build_segments
        out = _build_segments(SAMPLE_SEGMENTS)
        for seg in out:
            assert "start" in seg
            assert "end" in seg
            assert "text" in seg
            assert "duration" not in seg

    def test_transcript_duration(self):
        """Duration is last segment end time."""
        from mcp_server import _transcript_duration
        d = _transcript_duration(SAMPLE_SEGMENTS)
        assert d == 7.0  # 5.5 + 1.5

    def test_empty_segments_duration(self):
        """Empty segments returns None duration."""
        from mcp_server import _transcript_duration
        assert _transcript_duration([]) is None


# ══════════════════════════════════════════════════════════════════════════════
# ERROR RESPONSE TESTS
# ══════════════════════════════════════════════════════════════════════════════


class TestErrorResponse:

    def test_error_has_retryable_field(self):
        """Error responses include retryable boolean."""
        from mcp_server import _error_response
        resp = _error_response("RATE_LIMITED", "test", "vid1",
                               "https://youtube.com/watch?v=vid1", 2, False, 1.5, True)
        assert "retryable" in resp
        assert resp["retryable"] is True

    def test_permanent_error_not_retryable(self):
        """Permanent error has retryable=False."""
        from mcp_server import _error_response
        resp = _error_response("VIDEO_UNAVAILABLE", "test", "vid1",
                               "https://youtube.com/watch?v=vid1", 0, False, 0.1, False)
        assert resp["retryable"] is False

    def test_error_has_fetch_duration(self):
        """All errors include fetch_duration_seconds."""
        from mcp_server import _error_response
        resp = _error_response("INVALID_URL", "bad url", None, "bad", 0, False, 0.01, False)
        assert "fetch_duration_seconds" in resp
        assert resp["fetch_duration_seconds"] == 0.01

    def test_error_markdown_format(self):
        """Error formatted as markdown includes key fields."""
        from mcp_server import _error_response, _format_error
        resp = _error_response("RATE_LIMITED", "too fast", "vid1",
                               "https://youtube.com/watch?v=vid1", 2, False, 1.5, True)
        md = _format_error(resp, "markdown")
        assert "# Error: RATE_LIMITED" in md
        assert "retryable: True" in md

    def test_error_json_compact(self):
        """Error JSON uses compact separators."""
        from mcp_server import _error_response, _format_error
        resp = _error_response("RATE_LIMITED", "test", "vid1",
                               "https://youtube.com/watch?v=vid1", 0, False, 0.1, True)
        j = _format_error(resp, "json")
        assert ": " not in j  # no spaces after colons
        assert ", " not in j  # no spaces after commas


# ══════════════════════════════════════════════════════════════════════════════
# METADATA TESTS
# ══════════════════════════════════════════════════════════════════════════════


class TestMetadata:

    def test_per_field_sources(self):
        """fetch_metadata returns per-field source tracking."""
        from yt_transcript import fetch_metadata
        with patch("yt_transcript._fetch_oembed") as mock_oe, \
             patch("yt_transcript._fetch_pytubefix") as mock_ptf:
            mock_oe.return_value = {"title": "T", "channel": "C", "published": ""}
            mock_ptf.return_value = {"title": "", "channel": "", "published": "2026-01-01"}
            result = fetch_metadata("https://youtube.com/watch?v=test")
            assert result["sources"]["title"] == "oembed"
            assert result["sources"]["published"] == "pytubefix"

    def test_total_metadata_failure(self):
        """Both providers failing returns all sources as 'none'."""
        from yt_transcript import fetch_metadata
        with patch("yt_transcript._fetch_oembed") as mock_oe, \
             patch("yt_transcript._fetch_pytubefix") as mock_ptf:
            mock_oe.return_value = {"title": "", "channel": "", "published": ""}
            mock_ptf.return_value = {"title": "", "channel": "", "published": ""}
            result = fetch_metadata("https://youtube.com/watch?v=test")
            assert all(v == "none" for v in result["sources"].values())
            assert result["missing"] == ["title", "channel", "published"]

    def test_partial_metadata_failure(self):
        """One field missing, others present."""
        from yt_transcript import fetch_metadata
        with patch("yt_transcript._fetch_oembed") as mock_oe, \
             patch("yt_transcript._fetch_pytubefix") as mock_ptf:
            mock_oe.return_value = {"title": "T", "channel": "", "published": ""}
            mock_ptf.return_value = {"title": "", "channel": "", "published": ""}
            result = fetch_metadata("https://youtube.com/watch?v=test")
            assert result["sources"]["title"] == "oembed"
            assert result["sources"]["channel"] == "none"
            assert "channel" in result["missing"]
