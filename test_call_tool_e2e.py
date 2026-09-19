"""End-to-end tests for the fetch_transcript MCP tool, driven through the real
MCP protocol (in-memory client <-> server session), not by calling call_tool()
directly. Only the network boundary is mocked. Most tests patch the single
transcript-fetch seam (yt_transcript.fetch_transcript); TestNativeBackend drives
the real InnerTube backend against recorded fixtures through a fake transport.
Cache, retry, classification, and serialization always run for real.

Run: pytest test_call_tool_e2e.py -v
"""

import asyncio
import copy
import json
import logging
import threading
import time
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

import innertube
import mcp_server
import yt_transcript
from innertube import HttpResponse, NativeTranscript
from testing_support import FakeTransport, all_clients
from yt_errors import (
    LanguageNotAvailable, PoTokenRequired, TranscriptNotAvailable, TransientRequestFailed,
    UpstreamFailure, VideoUnavailable, YouTubeIpBlocked,
)

VIDEO_ID = "dQw4w9WgXcQ"
URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"

SEGMENTS = [
    {"text": "Hello world", "start": 0.0, "duration": 2.5},
    {"text": "This is a test", "start": 2.5, "duration": 3.0},
    {"text": "Goodbye", "start": 5.5, "duration": 1.5},
]
OUTPUT_SEGMENTS = [
    {"text": "Hello world", "start": 0.0, "end": 2.5},
    {"text": "This is a test", "start": 2.5, "end": 5.5},
    {"text": "Goodbye", "start": 5.5, "end": 7.0},
]
META_OK = {
    "fields": {"title": "Test Video", "channel": "Test Channel", "published": "2026-01-01"},
    "sources": {"title": "oembed", "channel": "oembed", "published": "pytubefix"},
    "missing": [],
    "complete": True,
}
META_TOTAL_FAILURE = {
    "fields": {"title": "", "channel": "", "published": ""},
    "sources": {"title": "none", "channel": "none", "published": "none"},
    "missing": ["title", "channel", "published"],
    "complete": False,
}


def _native(result) -> NativeTranscript:
    """Build a fetch result from a (segments, language, is_generated, fallback) tuple."""
    if isinstance(result, NativeTranscript):
        return result
    segments, language, is_generated, fallback_used = result
    return NativeTranscript(segments=segments, language_code=language,
                            is_generated=is_generated, fallback_used=fallback_used,
                            client="android_vr", clients_tried=("android_vr",))


class Harness:
    """Mock handles for one test: transcript fetch seam, metadata, native transport."""

    def __init__(self, fetch, metadata, transport=None):
        self.fetch = fetch
        self.metadata = metadata
        self.transport = transport


@contextmanager
def _env(tmp_path, *, fetch_result=None, fetch_side_effect=None, meta=META_OK,
         meta_side_effect=None, transport=None):
    """Isolated cache dir, mocked network, no real sleeping between retries.

    By default the transcript-fetch seam is patched. With `transport`, the real
    InnerTube backend runs against that fake transport instead.
    """
    # Fresh copy per call, like the real provider: the server mutates the returned dict.
    meta_kwargs = {"side_effect": meta_side_effect} if meta_side_effect is not None \
        else {"side_effect": lambda _url: copy.deepcopy(meta)}
    if isinstance(fetch_side_effect, list):
        fetch_side_effect = [x if isinstance(x, Exception) else _native(x)
                             for x in fetch_side_effect]
    fetch_kwargs = {"side_effect": fetch_side_effect} if fetch_side_effect is not None \
        else {"return_value": _native(fetch_result or (SEGMENTS, "en", False, False))}
    with patch.object(yt_transcript, "CACHE_DIR", tmp_path), \
         patch.object(mcp_server, "fetch_metadata", **meta_kwargs) as metadata, \
         patch.object(yt_transcript.time, "sleep"):
        if transport is not None:
            with patch.object(innertube, "urllib_transport", transport):
                yield Harness(None, metadata, transport)
        else:
            with patch.object(yt_transcript, "fetch_transcript", **fetch_kwargs) as fetch:
                yield Harness(fetch, metadata)


async def _call_async(arguments: dict, name: str = "fetch_transcript"):
    async with create_connected_server_and_client_session(mcp_server.server) as client:
        return await client.call_tool(name, arguments)


def call(arguments: dict, name: str = "fetch_transcript"):
    """Invoke the tool through the MCP protocol and return the CallToolResult."""
    return asyncio.run(_call_async(arguments, name))


def call_json(arguments: dict) -> dict:
    result = call(arguments)
    assert len(result.content) == 1 and result.content[0].type == "text"
    return json.loads(result.content[0].text)


def call_text(arguments: dict) -> str:
    result = call(arguments)
    assert len(result.content) == 1 and result.content[0].type == "text"
    return result.content[0].text


# -- Discovery ----------------------------------------------------------------

class TestToolDiscovery:

    def test_list_tools_advertises_fetch_transcript(self):
        async def run():
            async with create_connected_server_and_client_session(mcp_server.server) as c:
                return await c.list_tools()
        tools = asyncio.run(run()).tools
        assert [t.name for t in tools] == ["fetch_transcript"]
        schema = tools[0].inputSchema
        assert schema["required"] == ["url"]
        assert schema["additionalProperties"] is False
        assert schema == mcp_server.TOOL_SCHEMA


# -- Success envelope ---------------------------------------------------------

class TestSuccessEnvelope:

    def test_url_only_defaults(self, tmp_path):
        with _env(tmp_path) as h:
            result = call({"url": URL})
        assert result.isError is False
        body = json.loads(result.content[0].text)
        assert body["is_error"] is False
        assert body["video_id"] == VIDEO_ID
        assert body["segments"] == OUTPUT_SEGMENTS
        assert "transcript_text" not in body
        assert body["language"] == "en"
        assert body["language_requested"] == "sv,en"
        assert body["caption_type"] == "manual"
        assert body["cache_hit"] is False
        assert body["cache_age_days"] is None
        assert body["title"] == "Test Video"
        assert body["warnings"] == []
        assert body["metadata_missing"] == []
        h.fetch.assert_called_once_with(VIDEO_ID, ["sv", "en"])

    def test_default_json_is_compact(self, tmp_path):
        with _env(tmp_path):
            text = call_text({"url": URL})
        assert "\n" not in text
        assert ": " not in text.split('"segments"')[0]
        assert ", " not in text.split('"segments"')[0]

    def test_output_text_only(self, tmp_path):
        with _env(tmp_path):
            body = call_json({"url": URL, "output": "text"})
        assert "segments" not in body
        assert body["transcript_text"] == "Hello world This is a test Goodbye"

    def test_output_both(self, tmp_path):
        with _env(tmp_path):
            body = call_json({"url": URL, "output": "both"})
        assert body["segments"] == OUTPUT_SEGMENTS
        assert body["transcript_text"]

    def test_timestamps_in_text_output(self, tmp_path):
        with _env(tmp_path):
            body = call_json({"url": URL, "output": "text", "include_timestamps": True})
        assert body["transcript_text"].startswith("[00:00:00]")

    @pytest.mark.parametrize("output", ["segments", "text", "both"])
    def test_markdown_renders_text_under_every_selector(self, tmp_path, output):
        with _env(tmp_path):
            text = call_text({"url": URL, "format": "markdown", "output": output})
        assert text.startswith("# Test Video")
        assert "## Transcript" in text
        assert "Hello world This is a test Goodbye" in text

    def test_manual_overrides_recorded_as_manual_source(self, tmp_path):
        with _env(tmp_path, meta=META_TOTAL_FAILURE):
            body = call_json({"url": URL, "title": "Mine", "channel": "Me",
                              "published": "2025-05-05"})
        assert body["title"] == "Mine"
        assert body["metadata_sources"] == {"title": "manual", "channel": "manual",
                                            "published": "manual"}
        assert body["warnings"] == []

    def test_auto_generated_captions_flagged(self, tmp_path):
        with _env(tmp_path, fetch_result=(SEGMENTS, "en", True, False)):
            body = call_json({"url": URL})
        assert body["caption_type"] == "auto-generated"
        assert [w["code"] for w in body["warnings"]] == ["AUTO_GENERATED"]

    def test_language_fallback_reported(self, tmp_path):
        with _env(tmp_path, fetch_result=(SEGMENTS, "de", False, True)):
            body = call_json({"url": URL, "languages": "sv,en"})
        assert body["language"] == "de"
        assert body["language_fallback"] is True
        assert body["fallback_attempted"] is True
        assert [w["code"] for w in body["warnings"]] == ["LANGUAGE_FALLBACK"]

    def test_content_hash_recomputable_from_returned_segments(self, tmp_path):
        with _env(tmp_path):
            body = call_json({"url": URL})
        assert body["content_hash"] == mcp_server._content_hash(body["segments"])

    def test_content_hash_identical_across_output_modes(self, tmp_path):
        with _env(tmp_path):
            by_mode = {m: call_json({"url": URL, "output": m, "bypass_cache": True})["content_hash"]
                       for m in ("segments", "text", "both")}
        assert len(set(by_mode.values())) == 1

    def test_empty_transcript_keeps_stable_schema(self, tmp_path):
        with _env(tmp_path, fetch_result=([], "en", False, False)):
            body = call_json({"url": URL})
        assert body["segments"] == []
        assert body["segment_count"] == 0
        assert body["transcript_duration_seconds"] is None


# -- Cache through the protocol -----------------------------------------------

class TestCacheEndToEnd:

    def test_second_call_is_served_from_cache(self, tmp_path):
        with _env(tmp_path) as h:
            first = call_json({"url": URL})
            second = call_json({"url": URL})
        assert first["cache_hit"] is False
        assert second["cache_hit"] is True
        assert second["cache_age_days"] == 0
        assert second["segments"] == first["segments"]
        assert second["content_hash"] == first["content_hash"]
        assert h.fetch.call_count == 1

    def test_bypass_cache_forces_fresh_fetch(self, tmp_path):
        with _env(tmp_path) as h:
            call_json({"url": URL})
            body = call_json({"url": URL, "bypass_cache": True})
        assert body["cache_hit"] is False
        assert h.fetch.call_count == 2

    def test_language_order_isolates_cache_entries(self, tmp_path):
        with _env(tmp_path) as h:
            call_json({"url": URL, "languages": "sv,en"})
            reversed_order = call_json({"url": URL, "languages": "en,sv"})
        assert reversed_order["cache_hit"] is False
        assert h.fetch.call_count == 2

    def test_fallback_result_reused_under_originating_request(self, tmp_path):
        with _env(tmp_path, fetch_result=(SEGMENTS, "de", False, True)) as h:
            call_json({"url": URL, "languages": "sv,en"})
            second = call_json({"url": URL, "languages": "sv,en"})
        assert second["cache_hit"] is True
        assert second["language"] == "de"
        assert second["language_fallback"] is True
        assert h.fetch.call_count == 1

    def test_metadata_warning_survives_cache_hit(self, tmp_path):
        with _env(tmp_path, meta=META_TOTAL_FAILURE):
            fresh = call_json({"url": URL})
            cached = call_json({"url": URL})
        assert cached["cache_hit"] is True
        assert [w["code"] for w in fresh["warnings"]] == ["METADATA_FETCH_FAILED"]
        assert cached["warnings"] == fresh["warnings"]
        assert cached["metadata_missing"] == ["title", "channel", "published"]

    def test_cache_write_failure_still_returns_transcript(self, tmp_path):
        with _env(tmp_path), \
             patch.object(mcp_server, "save_to_cache", side_effect=OSError("disk full")):
            result = call({"url": URL})
        assert result.isError is False
        body = json.loads(result.content[0].text)
        assert body["segments"] == OUTPUT_SEGMENTS
        assert [w["code"] for w in body["warnings"]] == ["CACHE_WRITE_FAILED"]

    def test_corrupt_cache_entry_becomes_miss(self, tmp_path):
        with _env(tmp_path) as h:
            call_json({"url": URL})
            (entry,) = list(tmp_path.glob("*.json"))
            entry.write_text("{not json")
            body = call_json({"url": URL})
        assert body["cache_hit"] is False
        assert h.fetch.call_count == 2

    @pytest.mark.parametrize("bad_value", [True, -1.0, float("nan"), float("inf")])
    def test_invalid_segment_numbers_in_cache_become_miss(self, tmp_path, bad_value):
        with _env(tmp_path) as h:
            call_json({"url": URL})
            (entry,) = list(tmp_path.glob("*.json"))
            data = json.loads(entry.read_text())
            data["segments"][0]["start"] = bad_value
            entry.write_text(json.dumps(data))
            body = call_json({"url": URL})
        assert body["cache_hit"] is False
        assert h.fetch.call_count == 2


# -- Error envelope -----------------------------------------------------------

class TestErrorEnvelope:
    """Contract errors are JSON payloads inside a normal tool result."""

    def test_invalid_url(self, tmp_path):
        with _env(tmp_path) as h:
            result = call({"url": "https://example.com/watch?v=dQw4w9WgXcQ"})
        body = json.loads(result.content[0].text)
        assert body["is_error"] is True
        assert body["error_code"] == "INVALID_URL"
        assert body["video_id"] is None
        assert body["retryable"] is False
        assert body["retry_count"] == 0
        assert isinstance(body["fetch_duration_seconds"], float)
        h.fetch.assert_not_called()

    def test_invalid_url_respects_markdown_format(self, tmp_path):
        with _env(tmp_path):
            text = call_text({"url": "not a url", "format": "markdown"})
        assert text.startswith("# Error: INVALID_URL")
        assert "retryable: False" in text

    @pytest.mark.parametrize("error_cls,code", [
        (TranscriptNotAvailable, "TRANSCRIPT_NOT_AVAILABLE"),
        (VideoUnavailable, "VIDEO_UNAVAILABLE"),
        (YouTubeIpBlocked, "YOUTUBE_IP_BLOCKED"),
        (PoTokenRequired, "PO_TOKEN_REQUIRED"),
        (LanguageNotAvailable, "LANGUAGE_NOT_AVAILABLE"),
        (UpstreamFailure, "RATE_LIMITED"),
    ])
    def test_permanent_failures_are_not_retried(self, tmp_path, error_cls, code):
        with _env(tmp_path, fetch_side_effect=error_cls("boom")) as h:
            body = call_json({"url": URL})
        assert body["is_error"] is True
        assert body["error_code"] == code
        assert body["retryable"] is False
        assert body["retry_count"] == 0
        assert body["video_id"] == VIDEO_ID
        assert h.fetch.call_count == 1

    def test_unknown_exception_is_not_retried(self, tmp_path):
        with _env(tmp_path, fetch_side_effect=RuntimeError("boom")) as h:
            body = call_json({"url": URL})
        assert body["is_error"] is True
        assert body["retryable"] is False
        assert h.fetch.call_count == 1

    def test_transient_failure_exhausts_retries(self, tmp_path):
        with _env(tmp_path, fetch_side_effect=TransientRequestFailed("HTTP 429", http_status=429)) as h:
            body = call_json({"url": URL})
        assert body["is_error"] is True
        assert body["error_code"] == "RATE_LIMITED"
        assert body["retryable"] is True
        assert body["retry_count"] == yt_transcript.MAX_RETRIES - 1
        assert h.fetch.call_count == yt_transcript.MAX_RETRIES

    def test_transient_failure_then_success(self, tmp_path):
        effects = [TransientRequestFailed("HTTP 503", http_status=503), (SEGMENTS, "en", False, False)]
        with _env(tmp_path, fetch_side_effect=effects) as h:
            body = call_json({"url": URL})
        assert body["is_error"] is False
        assert body["retry_count"] == 1
        assert h.fetch.call_count == 2

    def test_transient_then_success_markdown_notes_retry(self, tmp_path):
        effects = [TransientRequestFailed("HTTP 503", http_status=503), (SEGMENTS, "en", False, False)]
        with _env(tmp_path, fetch_side_effect=effects):
            text = call_text({"url": URL, "format": "markdown"})
        assert "Retry succeeded (attempt 2/" in text

    def test_failed_fallback_reports_fallback_attempted(self, tmp_path):
        exc = LanguageNotAvailable("boom", fallback_attempted=True)
        with _env(tmp_path, fetch_side_effect=exc):
            body = call_json({"url": URL})
        assert body["error_code"] == "LANGUAGE_NOT_AVAILABLE"
        assert body["fallback_attempted"] is True

    def test_upstream_failure_is_not_cached(self, tmp_path):
        with _env(tmp_path, fetch_side_effect=TranscriptNotAvailable("no captions")):
            call_json({"url": URL})
        assert list(tmp_path.glob("*.json")) == []

    def test_error_payload_matches_contract_keys(self, tmp_path):
        with _env(tmp_path, fetch_side_effect=TranscriptNotAvailable("no captions")):
            body = call_json({"url": URL})
        assert set(body) == {"is_error", "error_code", "error_message", "video_id", "url",
                             "retry_count", "fallback_attempted", "fetch_duration_seconds",
                             "retryable"}


# -- Metadata provider failures -----------------------------------------------

class TestMetadataFailures:

    def test_total_metadata_failure_keeps_transcript(self, tmp_path):
        with _env(tmp_path, meta=META_TOTAL_FAILURE):
            body = call_json({"url": URL})
        assert body["is_error"] is False
        assert body["segments"] == OUTPUT_SEGMENTS
        assert body["title"] is None
        assert body["metadata_missing"] == ["title", "channel", "published"]
        assert [w["code"] for w in body["warnings"]] == ["METADATA_FETCH_FAILED"]

    def test_metadata_provider_exception_keeps_transcript(self, tmp_path):
        with _env(tmp_path, meta_side_effect=RuntimeError("oembed down")):
            body = call_json({"url": URL})
        assert body["is_error"] is False
        assert body["segments"] == OUTPUT_SEGMENTS
        assert body["metadata_sources"] == {"title": "none", "channel": "none",
                                            "published": "none"}
        assert [w["code"] for w in body["warnings"]] == ["METADATA_FETCH_FAILED"]

    def test_partial_metadata_failure_names_missing_fields(self, tmp_path):
        partial = {
            "fields": {"title": "T", "channel": "C", "published": ""},
            "sources": {"title": "oembed", "channel": "oembed", "published": "none"},
            "missing": ["published"], "complete": False,
        }
        with _env(tmp_path, meta=partial):
            body = call_json({"url": URL})
        assert body["metadata_missing"] == ["published"]
        (warning,) = body["warnings"]
        assert warning["code"] == "METADATA_FETCH_FAILED"
        assert "published" in warning["message"]


# -- Language input validation ------------------------------------------------

class TestLanguageInputs:

    @pytest.mark.parametrize("raw,expected", [
        ("", ["en"]),
        ("   ", ["en"]),
        ("../../etc/passwd", ["en"]),
        ("sv,../x,en", ["sv", "en"]),
        ("a" * 11, ["en"]),
        ("sv,,en", ["sv", "en"]),
    ])
    def test_unsafe_or_empty_languages_are_sanitized(self, tmp_path, raw, expected):
        with _env(tmp_path) as h:
            body = call_json({"url": URL, "languages": raw})
        assert body["is_error"] is False
        h.fetch.assert_called_once_with(VIDEO_ID, expected)

    def test_language_list_is_capped(self, tmp_path):
        many = ",".join(f"l{i}" for i in range(50))
        with _env(tmp_path) as h:
            call_json({"url": URL, "languages": many})
        (_, langs), _ = h.fetch.call_args
        assert len(langs) == 20

    def test_cache_paths_never_contain_raw_input(self, tmp_path):
        with _env(tmp_path):
            call_json({"url": URL, "languages": "sv,en"})
        (entry,) = list(tmp_path.glob("*.json"))
        assert VIDEO_ID not in entry.name


# -- Protocol-level rejection -------------------------------------------------

class TestProtocolValidation:
    """The SDK validates arguments against inputSchema before our handler runs."""

    def test_missing_url_is_protocol_error(self, tmp_path):
        with _env(tmp_path) as h:
            result = call({})
        assert result.isError is True
        assert "url" in result.content[0].text
        h.fetch.assert_not_called()

    def test_unknown_argument_is_rejected(self, tmp_path):
        with _env(tmp_path) as h:
            result = call({"url": URL, "surprise": 1})
        assert result.isError is True
        h.fetch.assert_not_called()

    @pytest.mark.parametrize("arguments", [
        {"url": URL, "output": "everything"},
        {"url": URL, "format": "xml"},
        {"url": URL, "include_timestamps": "yes"},
        {"url": URL, "bypass_cache": "no"},
        {"url": 123},
    ])
    def test_wrongly_typed_arguments_are_rejected(self, tmp_path, arguments):
        with _env(tmp_path) as h:
            result = call(arguments)
        assert result.isError is True
        h.fetch.assert_not_called()

    def test_unknown_tool_is_protocol_error(self, tmp_path):
        with _env(tmp_path):
            result = call({"url": URL}, name="nope")
        assert result.isError is True
        assert "nope" in result.content[0].text


# -- Native backend, end to end ------------------------------------------------

def _players(**by_client):
    """Player fixtures per client; unnamed clients answer with a bot check."""
    return {c.name: by_client.get(c.name, "player_bot_check.json") for c in innertube.CLIENTS}


class TestNativeBackend:
    """Real InnerTube backend + real retry loop + real cache, recorded fixtures."""

    def test_happy_path(self, tmp_path):
        transport = FakeTransport(all_clients("player_android_vr_captioned.json"))
        with _env(tmp_path, transport=transport):
            body = call_json({"url": URL})
        assert body["is_error"] is False
        assert body["language"] == "en"
        assert body["caption_type"] == "manual"
        assert body["segment_count"] == 8
        assert body["retry_count"] == 0
        assert body["warnings"] == []
        assert len(transport.posts()) == 1 and len(transport.gets()) == 1

    def test_client_is_reported_only_in_warnings_and_logs(self, tmp_path, caplog):
        transport = FakeTransport(_players(ios="player_ios_captioned.json"))
        with caplog.at_level(logging.WARNING, logger="ytfetch"), _env(tmp_path, transport=transport):
            body = call_json({"url": URL})
        assert body["is_error"] is False
        assert "client" not in body
        (warning,) = body["warnings"]
        assert warning["code"] == "CLIENT_FALLBACK"
        assert "ios" in warning["message"] and "android_vr" in warning["message"]
        assert any("android_vr" in r.getMessage() and r.levelno == logging.WARNING
                   for r in caplog.records)

    def test_blocked_client_is_skipped_on_later_calls_without_a_warning(self, tmp_path, caplog):
        transport = FakeTransport(_players(ios=["player_ios_captioned.json"] * 2))
        with caplog.at_level(logging.INFO, logger="ytfetch"), _env(tmp_path, transport=transport):
            first = call_json({"url": URL})
            caplog.clear()
            second = call_json({"url": URL, "bypass_cache": True})
        assert [w["code"] for w in first["warnings"]] == ["CLIENT_FALLBACK"]
        assert second["warnings"] == []
        assert len(transport.posts()) == 3
        assert any("Trying android_vr last" in r.getMessage() for r in caplog.records)

    def test_client_fallback_warning_is_absent_on_cache_hit(self, tmp_path):
        transport = FakeTransport(_players(ios="player_ios_captioned.json"))
        with _env(tmp_path, transport=transport):
            call_json({"url": URL})
            cached = call_json({"url": URL})
        assert cached["cache_hit"] is True
        assert cached["warnings"] == []
        assert len(transport.posts()) == 2

    def test_normal_fetch_logs_client_at_info_only(self, tmp_path, caplog):
        transport = FakeTransport(all_clients("player_android_vr_captioned.json"))
        with caplog.at_level(logging.WARNING, logger="ytfetch"), _env(tmp_path, transport=transport):
            call_json({"url": URL})
        assert caplog.records == []
        with caplog.at_level(logging.INFO, logger="ytfetch"), _env(tmp_path, transport=transport):
            call_json({"url": URL, "bypass_cache": True})
        assert any("via android_vr" in r.getMessage() for r in caplog.records)

    def test_transient_status_is_retried_then_succeeds(self, tmp_path):
        transport = FakeTransport(all_clients("player_android_vr_captioned.json"))
        transport.players["android_vr"] = [HttpResponse(503, b""), "player_android_vr_captioned.json"]
        with _env(tmp_path, transport=transport):
            body = call_json({"url": URL})
        assert body["is_error"] is False
        assert body["retry_count"] == 1
        assert len(transport.posts()) == 2

    def test_429_is_retried_and_reported_retryable(self, tmp_path):
        transport = FakeTransport(all_clients(HttpResponse(429, b"")))
        with _env(tmp_path, transport=transport):
            body = call_json({"url": URL})
        assert body["error_code"] == "RATE_LIMITED"
        assert body["retryable"] is True
        assert body["retry_count"] == yt_transcript.MAX_RETRIES - 1
        assert "429" in body["error_message"]
        assert len(transport.posts()) == yt_transcript.MAX_RETRIES

    @pytest.mark.parametrize("status", [400, 403, 404])
    def test_client_error_statuses_are_not_retried(self, tmp_path, status):
        transport = FakeTransport(all_clients(HttpResponse(status, b"")))
        with _env(tmp_path, transport=transport):
            body = call_json({"url": URL})
        assert body["is_error"] is True
        assert body["error_code"] == "RATE_LIMITED"
        assert body["retryable"] is False
        assert body["retry_count"] == 0
        assert len(transport.posts()) == 1

    def test_failed_fallback_download_reports_fallback_attempted(self, tmp_path):
        transport = FakeTransport(all_clients("player_android_vr_captioned.json"),
                                  captions=HttpResponse(503, b""))
        with _env(tmp_path, transport=transport):
            body = call_json({"url": URL, "languages": "fr"})
        assert body["is_error"] is True
        assert body["fallback_attempted"] is True
        assert body["retry_count"] == yt_transcript.MAX_RETRIES - 1

    def test_every_client_bot_checked(self, tmp_path):
        transport = FakeTransport(all_clients("player_bot_check.json"))
        with _env(tmp_path, transport=transport):
            body = call_json({"url": URL})
        assert body["error_code"] == "YOUTUBE_IP_BLOCKED"
        assert body["retryable"] is False
        assert "clients tried: android_vr, ios, android" in body["error_message"]
        assert len(transport.posts()) == 3

    def test_unavailable_video(self, tmp_path):
        transport = FakeTransport(all_clients("player_unavailable.json"))
        with _env(tmp_path, transport=transport):
            body = call_json({"url": URL})
        assert body["error_code"] == "VIDEO_UNAVAILABLE"
        assert len(transport.calls) == 1

    def test_unexpected_exception_is_not_retried(self, tmp_path):
        with _env(tmp_path, fetch_side_effect=RuntimeError("bug")) as h:
            body = call_json({"url": URL})
        assert body["error_code"] == "RATE_LIMITED"
        assert body["retryable"] is False
        assert "RuntimeError" in body["error_message"]
        assert h.fetch.call_count == 1


class TestConcurrency:

    def test_metadata_and_transcript_are_fetched_concurrently(self, tmp_path):
        """Both fetches wait on a 2-party barrier: sequential execution would deadlock
        the first one until the barrier times out and fail the call."""
        barrier = threading.Barrier(2, timeout=5)

        def metadata(_url):
            barrier.wait()
            return copy.deepcopy(META_OK)

        def transcript(_video_id, _languages):
            barrier.wait()
            return _native((SEGMENTS, "en", False, False))

        with _env(tmp_path, meta_side_effect=metadata, fetch_side_effect=transcript):
            body = call_json({"url": URL})
        assert body["is_error"] is False
        assert body["title"] == "Test Video"

    def test_transcript_failure_returns_without_waiting_for_metadata(self, tmp_path):
        release = threading.Event()

        def slow_metadata(_url):
            release.wait(timeout=5)
            return copy.deepcopy(META_OK)

        async def timed_call():
            async with create_connected_server_and_client_session(mcp_server.server) as client:
                started = time.monotonic()
                result = await client.call_tool("fetch_transcript", {"url": URL})
                elapsed = time.monotonic() - started
                release.set()  # let the abandoned metadata thread finish before shutdown
                return elapsed, json.loads(result.content[0].text)

        with _env(tmp_path, meta_side_effect=slow_metadata,
                  fetch_side_effect=TranscriptNotAvailable("none")):
            elapsed, body = asyncio.run(timed_call())
        assert body["error_code"] == "TRANSCRIPT_NOT_AVAILABLE"
        assert elapsed < 3, "error path waited for the metadata fetch"


# -- Known gaps (tracked in ClickUp 869ecu188, Sprint 2) ----------------------

class TestKnownGaps:
    """Strict xfails: each flips to a hard failure the moment the gap is fixed,
    forcing the marker to be removed and the gap closed in the ticket."""

    @pytest.mark.xfail(strict=True, reason=(
        "Roadmap says errors return isError; server currently returns contract errors "
        "with isError=False and only flags is_error inside the JSON payload."))
    def test_contract_errors_set_mcp_is_error(self, tmp_path):
        with _env(tmp_path, fetch_side_effect=TranscriptNotAvailable("no captions")):
            result = call({"url": URL})
        assert result.isError is True

    @pytest.mark.xfail(strict=True, reason=(
        "Deferred: manual `published` override is not validated against YYYY-MM-DD."))
    def test_invalid_manual_date_is_rejected(self, tmp_path):
        with _env(tmp_path):
            body = call_json({"url": URL, "published": "yesterday"})
        assert body["is_error"] is True
