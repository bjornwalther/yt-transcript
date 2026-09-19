"""Opt-in live corpus check against the real YouTube.

A canary, not a spec: it shows whether the native backend still works against
the real service (client versions drift, YouTube changes enforcement). Skipped
unless the run passes --live. Run it rarely; every request counts against your
IP reputation.

    pytest --live -m live test_live_corpus.py -v --log-cli-level=INFO

Requests are paced, the metadata providers are stubbed (only the transcript
backend is exercised), and the cache is isolated. If YouTube blocks every client
the remaining cases are skipped and the result is reported as inconclusive,
because that says something about this IP, not about the code. A required PO
token, however, fails the run: that is the backend gap the roadmap tracks.
"""

import asyncio
import json
import time

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

import mcp_server
import yt_transcript

pytestmark = pytest.mark.live

PAUSE_SECONDS = 5.0
_NO_METADATA = {
    "fields": {"title": "", "channel": "", "published": ""},
    "sources": {"title": "none", "channel": "none", "published": "none"},
    "missing": ["title", "channel", "published"], "complete": False,
}

_state = {"blocked": False, "last_call": 0.0}


async def _call(arguments: dict):
    async with create_connected_server_and_client_session(mcp_server.server) as client:
        return await client.call_tool("fetch_transcript", arguments)


@pytest.fixture
def fetch(monkeypatch, tmp_path):
    monkeypatch.setattr(yt_transcript, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(mcp_server, "fetch_metadata", lambda _url: dict(_NO_METADATA))

    def run(video_id: str, **extra) -> dict:
        if _state["blocked"]:
            pytest.skip("YouTube blocked every client earlier in this run; stopping.")
        wait = _state["last_call"] + PAUSE_SECONDS - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        arguments = {"url": f"https://www.youtube.com/watch?v={video_id}", **extra}
        result = asyncio.run(_call(arguments))
        _state["last_call"] = time.monotonic()
        body = json.loads(result.content[0].text)
        if body.get("error_code") == "YOUTUBE_IP_BLOCKED":
            _state["blocked"] = True
            pytest.skip(f"Inconclusive: every client was bot-checked ({body['error_message']}).")
        if body.get("error_code") == "PO_TOKEN_REQUIRED":
            pytest.fail(f"PO token now required on every client: {body['error_message']}")
        return body

    return run


class TestLiveCorpus:

    def test_manual_captions_in_many_languages(self, fetch):
        body = fetch("dQw4w9WgXcQ")
        assert body["is_error"] is False, body
        assert body["language"] == "en"
        assert body["caption_type"] == "manual"
        assert body["segment_count"] >= 50
        assert body["language_fallback"] is False

    def test_long_video_with_manual_captions(self, fetch):
        body = fetch("aircAruvnKk")
        assert body["is_error"] is False, body
        assert body["language"] == "en"
        assert body["segment_count"] >= 100

    def test_language_fallback_when_requested_language_is_missing(self, fetch):
        body = fetch("jNQXAC9IVRw", languages="fr")
        assert body["is_error"] is False, body
        assert body["language_fallback"] is True
        assert body["fallback_attempted"] is True
        assert "LANGUAGE_FALLBACK" in [w["code"] for w in body["warnings"]]

    def test_nonexistent_video(self, fetch):
        body = fetch("aaaaaaaaaaa")
        assert body["is_error"] is True
        assert body["error_code"] == "VIDEO_UNAVAILABLE"
        assert body["retryable"] is False
