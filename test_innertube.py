"""Tests for the native InnerTube backend (innertube.py).

Fixtures in fixtures/innertube/ are real responses recorded by
tools/record_innertube_fixtures.py, with signed URLs replaced and caption words
redacted. Everything is served through a fake transport: no network.
Run: pytest test_innertube.py -v
"""

import http.server
import json
import threading
import time

import pytest

import innertube
from testing_support import (
    FIXTURES, VIDEO_ID, FakeTransport, all_clients as _all_clients, load, load_player,
)
from innertube import (
    CLIENTS, CaptionTrack, HttpResponse, TransportError, check_playability,
    fetch_player_response, fetch_transcript_native, list_tracks, parse_timedtext,
    select_track, urllib_transport,
)
from yt_errors import (
    InvalidVideoIdError, PoTokenRequired, TranscriptNotAvailable, TransientRequestFailed,
    UpstreamFailure, VideoUnavailable, YouTubeIpBlocked,
)
import yt_transcript

def _track(lang: str, generated: bool = False, url: str = "https://www.youtube.com/t") -> CaptionTrack:
    return CaptionTrack(lang, lang, generated, url, False)


# -- Player request -----------------------------------------------------------

class TestPlayerRequest:

    def test_request_shape(self):
        transport = FakeTransport(_all_clients("player_android_vr_captioned.json"))
        fetch_player_response(VIDEO_ID, CLIENTS[0], transport)
        (method, url, headers, body), = transport.calls
        assert (method, url) == ("POST", innertube.PLAYER_URL)
        assert headers["X-YouTube-Client-Name"] == "28"
        assert headers["X-YouTube-Client-Version"] == "1.65.10"
        assert headers["User-Agent"] == CLIENTS[0].user_agent
        payload = json.loads(body)
        assert payload["videoId"] == VIDEO_ID
        assert payload["context"]["client"]["clientName"] == "ANDROID_VR"

    def test_invalid_video_id_makes_no_request(self):
        transport = FakeTransport({})
        with pytest.raises(InvalidVideoIdError):
            fetch_player_response("../etc/passwd", CLIENTS[0], transport)
        assert transport.calls == []

    @pytest.mark.parametrize("raw", [b"not json", b"[]", b'"str"', b""])
    def test_unparsable_body_is_upstream_failure(self, raw):
        transport = FakeTransport({"android_vr": HttpResponse(200, raw)})
        with pytest.raises(UpstreamFailure):
            fetch_player_response(VIDEO_ID, CLIENTS[0], transport)


# -- HTTP status mapping ------------------------------------------------------

class TestStatusMapping:

    def _fetch(self, response):
        transport = FakeTransport({"android_vr": response})
        return fetch_player_response(VIDEO_ID, CLIENTS[0], transport)

    @pytest.mark.parametrize("status", [429, 500, 502, 503, 599])
    def test_transient_statuses_are_retryable_rate_limited(self, status):
        with pytest.raises(TransientRequestFailed) as info:
            self._fetch(HttpResponse(status, b""))
        assert info.value.retryable is True
        assert info.value.code == "RATE_LIMITED"
        assert info.value.http_status == status

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 410])
    def test_other_client_errors_are_not_retryable(self, status):
        with pytest.raises(UpstreamFailure) as info:
            self._fetch(HttpResponse(status, b""))
        assert info.value.retryable is False
        assert info.value.http_status == status

    def test_connection_failure_is_retryable_without_status(self):
        with pytest.raises(TransientRequestFailed) as info:
            self._fetch(TransportError("connection reset"))
        assert info.value.retryable is True
        assert info.value.http_status is None
        assert "connection reset" in str(info.value)


# -- Playability --------------------------------------------------------------

class TestPlayability:

    def test_ok(self):
        check_playability(load_player("player_android_vr_captioned.json"), VIDEO_ID)

    def test_bot_check_is_ip_blocked(self):
        with pytest.raises(YouTubeIpBlocked) as info:
            check_playability(load_player("player_bot_check.json"), VIDEO_ID)
        assert info.value.code == "YOUTUBE_IP_BLOCKED"
        assert VIDEO_ID in str(info.value)

    def test_unavailable_video(self):
        with pytest.raises(VideoUnavailable) as info:
            check_playability(load_player("player_unavailable.json"), "aaaaaaaaaaa")
        assert "This video is unavailable" in str(info.value)

    @pytest.mark.parametrize("status_data", [
        {"status": "LOGIN_REQUIRED", "reason": "This video may be inappropriate for some users."},
        {"status": "LOGIN_REQUIRED"},
        {"status": "UNPLAYABLE", "reason": "Video unavailable in your country"},
        {"status": "LIVE_STREAM_OFFLINE"},
    ])
    def test_other_unplayable_states(self, status_data):
        with pytest.raises(VideoUnavailable):
            check_playability({"playabilityStatus": status_data}, VIDEO_ID)

    @pytest.mark.parametrize("player", [
        {}, {"playabilityStatus": None}, {"playabilityStatus": {}},
        {"playabilityStatus": {"status": 7}}, {"playabilityStatus": "OK"},
    ])
    def test_missing_or_malformed_status(self, player):
        with pytest.raises(UpstreamFailure):
            check_playability(player, VIDEO_ID)


# -- Tracks -------------------------------------------------------------------

class TestTracks:

    def test_recorded_track_list(self):
        tracks = list_tracks(load_player("player_android_vr_captioned.json"))
        assert [(t.language_code, t.is_generated) for t in tracks] == [
            ("en", False), ("en", True), ("de-DE", False),
            ("ja", False), ("pt-BR", False), ("es-419", False)]
        assert tracks[1].name == "English (auto-generated)"
        assert not any(t.requires_po_token for t in tracks)

    def test_srv3_is_stripped_from_urls(self):
        raw = load_player("player_android_vr_captioned.json")
        assert "&fmt=srv3" in json.dumps(raw)
        assert all("fmt=srv3" not in t.base_url for t in list_tracks(raw))

    def test_po_token_marker_is_detected(self):
        raw = load_player("player_android_vr_captioned.json")
        raw["captions"]["playerCaptionsTracklistRenderer"]["captionTracks"][0]["baseUrl"] += "&exp=xpe"
        tracks = list_tracks(raw)
        assert tracks[0].requires_po_token is True
        assert tracks[1].requires_po_token is False

    @pytest.mark.parametrize("player", [
        {}, {"captions": {}}, {"captions": None},
        {"captions": {"playerCaptionsTracklistRenderer": {}}},
        {"captions": {"playerCaptionsTracklistRenderer": {"captionTracks": []}}},
    ])
    def test_no_tracks_is_transcript_not_available(self, player):
        with pytest.raises(TranscriptNotAvailable):
            list_tracks(player)

    @pytest.mark.parametrize("url", [
        "http://www.youtube.com/api/timedtext?v=x",
        "https://evil.example/api/timedtext?v=x",
        "https://youtube.com.evil.example/api/timedtext",
        "https://notyoutube.com/api/timedtext",
        "file:///etc/passwd",
        "//www.youtube.com/api/timedtext",
    ])
    def test_untrusted_caption_hosts_are_ignored(self, url):
        raw = load_player("player_android_vr_captioned.json")
        raw["captions"]["playerCaptionsTracklistRenderer"]["captionTracks"][0]["baseUrl"] = url
        tracks = list_tracks(raw)
        assert len(tracks) == 5
        assert (tracks[0].language_code, tracks[0].is_generated) == ("en", True)

    def test_all_tracks_untrusted_is_upstream_failure(self):
        raw = load_player("player_android_vr_captioned.json")
        for track in raw["captions"]["playerCaptionsTracklistRenderer"]["captionTracks"]:
            track["baseUrl"] = "https://evil.example/x"
        with pytest.raises(UpstreamFailure):
            list_tracks(raw)

    def test_malformed_entries_are_skipped(self):
        raw = load_player("player_android_vr_captioned.json")
        tracks = raw["captions"]["playerCaptionsTracklistRenderer"]["captionTracks"]
        raw["captions"]["playerCaptionsTracklistRenderer"]["captionTracks"] = [
            "junk", {"languageCode": 5, "baseUrl": tracks[0]["baseUrl"]},
            {"languageCode": "en"}, tracks[0]]
        assert len(list_tracks(raw)) == 1

    def test_name_falls_back_to_language_code(self):
        raw = load_player("player_android_vr_captioned.json")
        track = raw["captions"]["playerCaptionsTracklistRenderer"]["captionTracks"][0]
        track["name"] = {"simpleText": "Simple"}
        assert list_tracks(raw)[0].name == "Simple"
        track["name"] = 42
        assert list_tracks(raw)[0].name == "en"


# -- Track selection ----------------------------------------------------------

class TestSelection:

    def test_requested_language_wins(self):
        tracks = list_tracks(load_player("player_android_vr_captioned.json"))
        selection = select_track(tracks, ["ja", "en"])
        assert selection.track.language_code == "ja"
        assert selection.fallback_used is False

    def test_manual_beats_generated_within_a_language(self):
        tracks = list_tracks(load_player("player_android_vr_captioned.json"))
        selection = select_track(tracks, ["en"])
        assert selection.track.is_generated is False

    def test_language_priority_beats_caption_kind(self):
        tracks = [_track("en"), _track("sv", generated=True)]
        selection = select_track(tracks, ["sv", "en"])
        assert (selection.track.language_code, selection.track.is_generated) == ("sv", True)

    def test_fallback_prefers_first_manual_track(self):
        tracks = [_track("de", generated=True), _track("ja"), _track("fr")]
        selection = select_track(tracks, ["sv"])
        assert selection.track.language_code == "ja"
        assert selection.fallback_used is True

    def test_fallback_uses_generated_when_no_manual_exists(self):
        selection = select_track([_track("de", generated=True)], ["sv"])
        assert selection.track.language_code == "de"
        assert selection.fallback_used is True

    def test_exact_language_code_match_only(self):
        selection = select_track([_track("en-US")], ["en"])
        assert selection.fallback_used is True

    @pytest.mark.parametrize("tracks,languages", [([], ["en"]), ([_track("en")], [])])
    def test_empty_inputs_are_contract_violations(self, tracks, languages):
        with pytest.raises(ValueError):
            select_track(tracks, languages)


# -- Caption parsing ----------------------------------------------------------

def _simple(*elements: str) -> bytes:
    return f'<?xml version="1.0" encoding="utf-8" ?><transcript>{"".join(elements)}</transcript>'.encode()


class TestParsing:

    def test_simple_dialect_recorded(self):
        segments = parse_timedtext(load("timedtext_ios_en_manual.xml").encode("utf-8"))
        assert len(segments) == 8
        assert segments[0] == {"text": "[♪♪♪]", "start": 1.36, "duration": 1.68}
        assert segments[-1]["start"] == 43.0
        assert segments[-1]["duration"] == 2.12

    def test_double_encoded_apostrophes_are_decoded(self):
        segments = parse_timedtext(load("timedtext_ios_en_manual.xml").encode("utf-8"))
        assert segments[1]["text"] == "♪ word1'word2 word3 word4 word5 word6 ♪"
        assert "&" not in "".join(s["text"] for s in segments)

    def test_newlines_inside_text_are_preserved(self):
        segments = parse_timedtext(load("timedtext_ios_en_manual.xml").encode("utf-8"))
        assert "\n" in segments[2]["text"]

    def test_srv3_manual_dialect_recorded(self):
        segments = parse_timedtext(load("timedtext_android_vr_en_manual.xml").encode("utf-8"))
        assert len(segments) == 8
        assert segments[0] == {"text": "[♪♪♪]", "start": 1.36, "duration": 1.68}
        assert segments[1]["start"] == 18.64
        assert segments[1]["text"] == "♪ word1'word2 word3 word4 word5 word6 ♪"

    def test_srv3_and_simple_dialects_agree(self):
        srv3 = parse_timedtext(load("timedtext_android_vr_en_manual.xml").encode("utf-8"))
        simple = parse_timedtext(load("timedtext_ios_en_manual.xml").encode("utf-8"))
        assert srv3 == simple

    def test_srv3_word_runs_are_joined_and_empty_rows_dropped(self):
        segments = parse_timedtext(load("timedtext_android_vr_en_asr.xml").encode("utf-8"))
        assert [s["start"] for s in segments] == [0.32, 18.8, 21.8, 25.96]
        assert segments[0]["text"] == "[word1]"
        assert segments[1]["text"] == "word2'word3 word4 word5 word6"
        assert segments[1]["duration"] == 7.16
        assert all(s["text"].strip() for s in segments)

    def test_html_tags_are_stripped(self):
        (segment,) = parse_timedtext(_simple('<text start="1" dur="2">&lt;i&gt;hi&lt;/i&gt; there</text>'))
        assert segment["text"] == "hi there"

    def test_missing_duration_defaults_to_zero(self):
        (segment,) = parse_timedtext(_simple('<text start="1">hi</text>'))
        assert segment["duration"] == 0.0

    def test_empty_text_elements_are_dropped(self):
        segments = parse_timedtext(_simple(
            '<text start="1" dur="1"></text>', '<text start="2" dur="1">  </text>',
            '<text start="3" dur="1">kept</text>'))
        assert [s["text"] for s in segments] == ["kept"]

    def test_order_is_preserved(self):
        segments = parse_timedtext(_simple(
            '<text start="5" dur="1">b</text>', '<text start="1" dur="1">a</text>'))
        assert [s["text"] for s in segments] == ["b", "a"]

    def test_valid_document_with_no_segments_is_empty_list(self):
        assert parse_timedtext(_simple()) == []

    @pytest.mark.parametrize("body", [b"", b"   \n  "])
    def test_empty_body_means_po_token_required(self, body):
        with pytest.raises(PoTokenRequired):
            parse_timedtext(body)

    @pytest.mark.parametrize("body", [
        b"<transcript><text start='1'>unterminated",
        b"not xml at all",
        b"<html><body>blocked</body></html>",
        b"\xff\xfe\x00bad utf8",
    ])
    def test_malformed_documents_are_upstream_failures(self, body):
        with pytest.raises(UpstreamFailure):
            parse_timedtext(body)

    @pytest.mark.parametrize("body", [
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaa">]><transcript><text start="1">&a;</text></transcript>',
        b'<!DOCTYPE transcript><transcript/>',
    ])
    def test_dtd_and_entity_declarations_are_rejected(self, body):
        with pytest.raises(UpstreamFailure, match="DTD"):
            parse_timedtext(body)

    @pytest.mark.parametrize("start", ["-1", "nan", "inf", "-inf", "abc", ""])
    def test_invalid_start_is_upstream_failure(self, start):
        with pytest.raises(UpstreamFailure):
            parse_timedtext(_simple(f'<text start="{start}" dur="1">x</text>'))

    def test_missing_start_is_upstream_failure_not_time_zero(self):
        with pytest.raises(UpstreamFailure, match="missing its start"):
            parse_timedtext(_simple('<text dur="1">x</text>'))

    @pytest.mark.parametrize("duration", ["-2", "nan", "inf", "x"])
    def test_invalid_duration_is_upstream_failure(self, duration):
        with pytest.raises(UpstreamFailure):
            parse_timedtext(_simple(f'<text start="1" dur="{duration}">x</text>'))

    def test_oversized_body_is_rejected(self, monkeypatch):
        monkeypatch.setattr(innertube, "MAX_BODY_BYTES", 100)
        with pytest.raises(UpstreamFailure, match="too large"):
            parse_timedtext(_simple(*(f'<text start="{i}" dur="1">x</text>' for i in range(20))))

    @pytest.mark.parametrize("fixture", [
        "timedtext_ios_en_manual.xml", "timedtext_android_vr_en_manual.xml",
        "timedtext_android_vr_en_asr.xml"])
    def test_output_satisfies_the_cache_segment_contract(self, fixture):
        for segment in parse_timedtext(load(fixture).encode("utf-8")):
            assert yt_transcript._validate_segment(segment)


# -- Orchestration ------------------------------------------------------------

class TestOrchestration:

    def test_happy_path(self):
        transport = FakeTransport(_all_clients("player_android_vr_captioned.json"))
        result = fetch_transcript_native(VIDEO_ID, ["sv", "en"], transport)
        assert result.client == "android_vr"
        assert result.clients_tried == ("android_vr",)
        assert result.language_code == "en"
        assert result.is_generated is False
        assert result.fallback_used is False
        assert len(result.segments) == 8
        assert len(transport.posts()) == 1 and len(transport.gets()) == 1
        assert "fmt=srv3" not in transport.gets()[0][1]

    def test_caption_request_uses_client_user_agent(self):
        transport = FakeTransport(_all_clients("player_android_vr_captioned.json"))
        fetch_transcript_native(VIDEO_ID, ["en"], transport)
        assert transport.gets()[0][2] == {"User-Agent": CLIENTS[0].user_agent}

    def test_bot_check_advances_to_next_client(self):
        transport = FakeTransport({"android_vr": "player_bot_check.json",
                                   "ios": "player_ios_captioned.json"})
        result = fetch_transcript_native(VIDEO_ID, ["en"], transport)
        assert result.client == "ios"
        assert result.clients_tried == ("android_vr", "ios")

    def test_empty_caption_body_advances_to_next_client(self):
        transport = FakeTransport(_all_clients("player_ios_captioned.json"),
                                  captions=[HttpResponse(200, b"")])
        result = fetch_transcript_native(VIDEO_ID, ["en"], transport)
        assert result.client == "ios"
        assert result.clients_tried == ("android_vr", "ios")

    def test_po_token_on_every_client_is_po_token_required(self):
        raw = load_player("player_ios_captioned.json")
        for track in raw["captions"]["playerCaptionsTracklistRenderer"]["captionTracks"]:
            track["baseUrl"] += "&exp=xpe"
        transport = FakeTransport(_all_clients(HttpResponse(200, json.dumps(raw).encode())))
        with pytest.raises(PoTokenRequired) as info:
            fetch_transcript_native(VIDEO_ID, ["en"], transport)
        assert "clients tried: android_vr, ios, android" in str(info.value)
        assert len(transport.posts()) == 3
        assert transport.gets() == []

    def test_every_client_bot_checked_is_ip_blocked(self):
        transport = FakeTransport(_all_clients("player_bot_check.json"))
        with pytest.raises(YouTubeIpBlocked) as info:
            fetch_transcript_native(VIDEO_ID, ["en"], transport)
        assert "clients tried: android_vr, ios, android" in str(info.value)
        assert info.value.fallback_attempted is False
        assert isinstance(info.value.__cause__, YouTubeIpBlocked)

    def test_permanent_failure_stops_the_chain(self):
        transport = FakeTransport(_all_clients("player_unavailable.json"))
        with pytest.raises(VideoUnavailable):
            fetch_transcript_native(VIDEO_ID, ["en"], transport)
        assert len(transport.calls) == 1

    def test_transient_failure_is_raised_not_chained(self):
        transport = FakeTransport(_all_clients(HttpResponse(503, b"")))
        with pytest.raises(TransientRequestFailed) as info:
            fetch_transcript_native(VIDEO_ID, ["en"], transport)
        assert info.value.retryable is True
        assert info.value.http_status == 503
        assert len(transport.calls) == 1

    def test_no_captions_is_transcript_not_available(self):
        player = {"playabilityStatus": {"status": "OK"}}
        transport = FakeTransport(_all_clients(HttpResponse(200, json.dumps(player).encode())))
        with pytest.raises(TranscriptNotAvailable):
            fetch_transcript_native(VIDEO_ID, ["en"], transport)
        assert len(transport.calls) == 1

    def test_language_fallback_is_reported(self):
        transport = FakeTransport(_all_clients("player_android_vr_captioned.json"))
        result = fetch_transcript_native(VIDEO_ID, ["fr"], transport)
        assert result.fallback_used is True
        assert result.language_code == "en"

    def test_failed_fallback_download_reports_fallback_attempted(self):
        transport = FakeTransport(_all_clients("player_android_vr_captioned.json"),
                                  captions=HttpResponse(503, b""))
        with pytest.raises(TransientRequestFailed) as info:
            fetch_transcript_native(VIDEO_ID, ["fr"], transport)
        assert info.value.fallback_attempted is True

    def test_failed_download_without_fallback_does_not_claim_one(self):
        transport = FakeTransport(_all_clients("player_android_vr_captioned.json"),
                                  captions=HttpResponse(503, b""))
        with pytest.raises(TransientRequestFailed) as info:
            fetch_transcript_native(VIDEO_ID, ["en"], transport)
        assert info.value.fallback_attempted is False

    def test_fallback_provenance_survives_an_exhausted_chain(self):
        raw = load_player("player_ios_captioned.json")
        for track in raw["captions"]["playerCaptionsTracklistRenderer"]["captionTracks"]:
            track["baseUrl"] += "&exp=xpe"
        transport = FakeTransport(_all_clients(HttpResponse(200, json.dumps(raw).encode())))
        with pytest.raises(PoTokenRequired) as info:
            fetch_transcript_native(VIDEO_ID, ["fr"], transport)
        assert info.value.fallback_attempted is True

    def test_empty_client_list_is_a_contract_violation(self):
        with pytest.raises(ValueError):
            fetch_transcript_native(VIDEO_ID, ["en"], FakeTransport({}), clients=())


# -- Real transport (local HTTP server) ---------------------------------------

class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, status: int, body: bytes = b""):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/ok":
            self._reply(200, b"hello")
        elif self.path == "/big":
            self._reply(200, b"x" * 500)
        elif self.path == "/slow":
            time.sleep(1.0)
            self._reply(200, b"late")
        else:
            self._reply(404, b"nope")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self._reply(200, self.rfile.read(length))


@pytest.fixture(scope="module")
def local_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


class TestUrllibTransport:

    def test_get_success(self, local_server):
        assert urllib_transport("GET", f"{local_server}/ok", {}, None) == HttpResponse(200, b"hello")

    def test_post_sends_body(self, local_server):
        resp = urllib_transport("POST", f"{local_server}/echo", {}, b'{"a":1}')
        assert resp == HttpResponse(200, b'{"a":1}')

    def test_error_status_is_returned_not_raised(self, local_server):
        assert urllib_transport("GET", f"{local_server}/missing", {}, None) == HttpResponse(404, b"nope")

    def test_oversized_body_is_rejected(self, local_server, monkeypatch):
        monkeypatch.setattr(innertube, "MAX_BODY_BYTES", 100)
        with pytest.raises(UpstreamFailure, match="exceeds"):
            urllib_transport("GET", f"{local_server}/big", {}, None)

    def test_timeout_is_a_transport_error(self, local_server, monkeypatch):
        monkeypatch.setattr(innertube, "REQUEST_TIMEOUT_SECONDS", 0.2)
        with pytest.raises(TransportError):
            urllib_transport("GET", f"{local_server}/slow", {}, None)

    def test_connection_refused_is_a_transport_error(self):
        with pytest.raises(TransportError):
            urllib_transport("GET", "http://127.0.0.1:1/", {}, None)
