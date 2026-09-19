"""Native InnerTube transcript backend. Standard library only.

Contract: CONTRACTS.md section 1 ("Pending: native backend"). Failures surface
as the typed errors in yt_errors. Not yet wired into fetch_transcript; the
public entry point is fetch_transcript_native().

Flow per client: POST /player -> playability check -> caption tracks -> select
track -> GET caption XML -> parse. Clients are tried in CLIENTS order and only
advance on client-specific blocks (bot check, PO token). Permanent failures stop
the chain.
"""

import html
import json
import logging
import math
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlparse
from xml.etree import ElementTree

from yt_errors import (
    InvalidVideoIdError, PoTokenRequired, TranscriptError, TranscriptNotAvailable,
    TransientRequestFailed, UpstreamFailure, VideoUnavailable, YouTubeIpBlocked,
)

log = logging.getLogger("ytfetch.innertube")

PLAYER_URL = "https://www.youtube.com/youtubei/v1/player?prettyPrint=false"
REQUEST_TIMEOUT_SECONDS = 10
CLIENT_COOLDOWN_SECONDS = 600
MAX_BODY_BYTES = 5_000_000

_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
_TAG_PATTERN = re.compile(r"<[^>]*>")
_TRACK_HOST_SUFFIX = ".youtube.com"


# -- Clients ------------------------------------------------------------------

@dataclass(frozen=True)
class ClientConfig:
    """One InnerTube client identity. Values track yt-dlp's client table."""

    name: str
    client_id: int
    context: dict
    user_agent: str


CLIENTS: tuple[ClientConfig, ...] = (
    ClientConfig(
        name="android_vr", client_id=28,
        user_agent="com.google.android.apps.youtube.vr.oculus/1.65.10 "
                   "(Linux; U; Android 12L; eureka-user Build/SQ3A.220605.009.A1) gzip",
        context={"client": {
            "clientName": "ANDROID_VR", "clientVersion": "1.65.10",
            "deviceMake": "Oculus", "deviceModel": "Quest 3", "androidSdkVersion": 32,
            "userAgent": "com.google.android.apps.youtube.vr.oculus/1.65.10 "
                         "(Linux; U; Android 12L; eureka-user Build/SQ3A.220605.009.A1) gzip",
            "osName": "Android", "osVersion": "12L", "hl": "en"}},
    ),
    ClientConfig(
        name="ios", client_id=5,
        user_agent="com.google.ios.youtube/21.26.4 (iPhone16,2; U; CPU iOS 18_3_2 like Mac OS X;)",
        context={"client": {
            "clientName": "IOS", "clientVersion": "21.26.4",
            "deviceMake": "Apple", "deviceModel": "iPhone16,2",
            "userAgent": "com.google.ios.youtube/21.26.4 (iPhone16,2; U; CPU iOS 18_3_2 like Mac OS X;)",
            "osName": "iPhone", "osVersion": "18.3.2.22D82", "hl": "en"}},
    ),
    ClientConfig(
        name="android", client_id=3,
        user_agent="com.google.android.youtube/21.26.364 (Linux; U; Android 11) gzip",
        context={"client": {
            "clientName": "ANDROID", "clientVersion": "21.26.364", "androidSdkVersion": 30,
            "userAgent": "com.google.android.youtube/21.26.364 (Linux; U; Android 11) gzip",
            "osName": "Android", "osVersion": "11", "hl": "en"}},
    ),
)


# -- Transport ----------------------------------------------------------------

@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes


class TransportError(Exception):
    """Connection-level failure: DNS, refused, reset, or timeout."""


# (method, url, headers, body) -> HttpResponse. Non-2xx statuses are returned,
# not raised; only connection-level failures raise TransportError.
Transport = Callable[[str, str, dict, "bytes | None"], HttpResponse]


def _read_capped(stream) -> bytes:
    data = stream.read(MAX_BODY_BYTES + 1)
    if len(data) > MAX_BODY_BYTES:
        raise UpstreamFailure(f"Response body exceeds {MAX_BODY_BYTES} bytes.")
    return data


def urllib_transport(method: str, url: str, headers: dict,
                     body: "bytes | None") -> HttpResponse:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            return HttpResponse(resp.status, _read_capped(resp))
    except urllib.error.HTTPError as e:
        return HttpResponse(e.code, _read_capped(e))
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
        raise TransportError(str(e)) from e


def _send(transport: Transport, method: str, url: str, headers: dict,
          body: "bytes | None", what: str) -> bytes:
    """Perform one request and map failures to typed errors (CONTRACTS.md pending rules).

    429, 5xx, timeouts and connection errors are retryable RATE_LIMITED; any other
    non-2xx status is a non-retryable UpstreamFailure. Both carry http_status.
    """
    try:
        resp = transport(method, url, headers, body)
    except TransportError as e:
        raise TransientRequestFailed(f"{what} failed: {e}") from e
    if 200 <= resp.status < 300:
        return resp.body
    message = f"{what} returned HTTP {resp.status}."
    if resp.status == 429 or resp.status >= 500:
        raise TransientRequestFailed(message, http_status=resp.status)
    raise UpstreamFailure(message, http_status=resp.status)


# -- Player request and playability -------------------------------------------

def fetch_player_response(video_id: str, client: ClientConfig, transport: Transport) -> dict:
    if not _VIDEO_ID_PATTERN.match(video_id):
        raise InvalidVideoIdError(f"Invalid video ID: {video_id!r}")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": client.user_agent,
        "X-YouTube-Client-Name": str(client.client_id),
        "X-YouTube-Client-Version": client.context["client"]["clientVersion"],
    }
    payload = json.dumps({"context": client.context, "videoId": video_id,
                          "contentCheckOk": True, "racyCheckOk": True}).encode("utf-8")
    raw = _send(transport, "POST", PLAYER_URL, headers, payload, f"{client.name} /player")
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise UpstreamFailure(f"{client.name} /player returned invalid JSON.") from e
    if not isinstance(data, dict):
        raise UpstreamFailure(f"{client.name} /player returned a non-object response.")
    return data


def check_playability(player: dict, video_id: str) -> None:
    """Raise a typed error unless the player response is playable."""
    status_data = player.get("playabilityStatus")
    if not isinstance(status_data, dict) or not isinstance(status_data.get("status"), str):
        raise UpstreamFailure("Player response has no playabilityStatus.")
    status = status_data["status"]
    if status == "OK":
        return
    reason = status_data.get("reason") if isinstance(status_data.get("reason"), str) else ""
    detail = f"{status}: {reason}" if reason else status
    if status == "LOGIN_REQUIRED" and "not a bot" in reason:
        raise YouTubeIpBlocked(f"YouTube bot check for {video_id} ({detail}).")
    raise VideoUnavailable(f"Video {video_id} is not playable ({detail}).")


# -- Caption tracks -----------------------------------------------------------

@dataclass(frozen=True)
class CaptionTrack:
    language_code: str
    name: str
    is_generated: bool
    base_url: str
    requires_po_token: bool


def _track_name(raw: object, fallback: str) -> str:
    if isinstance(raw, dict):
        if isinstance(raw.get("simpleText"), str):
            return raw["simpleText"]
        runs = raw.get("runs")
        if isinstance(runs, list) and runs and isinstance(runs[0], dict) \
                and isinstance(runs[0].get("text"), str):
            return runs[0]["text"]
    return fallback


def _is_trusted_track_url(url: str) -> bool:
    """Caption URLs come from a remote response: only follow https youtube.com hosts."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return parsed.scheme == "https" and (host == "youtube.com" or host.endswith(_TRACK_HOST_SUFFIX))


def list_tracks(player: dict) -> list[CaptionTrack]:
    renderer = (player.get("captions") or {}).get("playerCaptionsTracklistRenderer")
    raw_tracks = renderer.get("captionTracks") if isinstance(renderer, dict) else None
    if not raw_tracks:
        raise TranscriptNotAvailable("Video has no caption tracks.")
    tracks = []
    for raw in raw_tracks if isinstance(raw_tracks, list) else []:
        if not isinstance(raw, dict):
            continue
        lang, url = raw.get("languageCode"), raw.get("baseUrl")
        if not isinstance(lang, str) or not isinstance(url, str) or not _is_trusted_track_url(url):
            continue
        tracks.append(CaptionTrack(
            language_code=lang, name=_track_name(raw.get("name"), lang),
            is_generated=raw.get("kind") == "asr",
            # srv3 is the word-run dialect; dropping it yields the simple <text>
            # dialect, which keeps segments identical to youtube-transcript-api.
            base_url=url.replace("&fmt=srv3", ""),
            requires_po_token="exp=xpe" in url))
    if not tracks:
        raise UpstreamFailure("Caption track list is present but unusable.")
    return tracks


@dataclass(frozen=True)
class TrackSelection:
    track: CaptionTrack
    fallback_used: bool


def select_track(tracks: list[CaptionTrack], languages: list[str]) -> TrackSelection:
    """Pick a track: requested languages in priority order, manual before generated
    within a language. If none match, fall back to the first manual track, else the
    first generated one (same semantics as youtube-transcript-api)."""
    if not tracks:
        raise ValueError("tracks must not be empty")
    if not languages:
        raise ValueError("languages must not be empty")
    manual = {t.language_code: t for t in tracks if not t.is_generated}
    generated = {t.language_code: t for t in tracks if t.is_generated}
    for language in languages:
        for pool in (manual, generated):
            if language in pool:
                return TrackSelection(pool[language], False)
    return TrackSelection((list(manual.values()) + list(generated.values()))[0], True)


# -- Caption download and parsing ---------------------------------------------

def _seconds(value: "str | None", scale: float, what: str, default: "float | None" = None) -> float:
    """Parse a caption time attribute. A missing value uses `default`, or fails if None."""
    if value is None:
        if default is None:
            raise UpstreamFailure(f"Caption XML element is missing its {what}.")
        return default
    try:
        seconds = float(value) / scale
    except ValueError as e:
        raise UpstreamFailure(f"Caption XML has a non-numeric {what}: {value!r}.") from e
    if not math.isfinite(seconds) or seconds < 0:
        raise UpstreamFailure(f"Caption XML has an invalid {what}: {value!r}.")
    return seconds


def _clean(element: ElementTree.Element) -> str:
    return _TAG_PATTERN.sub("", html.unescape("".join(element.itertext())))


def parse_timedtext(body: bytes) -> list[dict]:
    """Parse caption XML into [{text, start, duration}] (seconds).

    Two dialects: `<transcript><text start= dur=>` (seconds), which caption URLs
    yield once `&fmt=srv3` is dropped, and the srv3 `<timedtext format="3"><body>
    <p t= d=>` (milliseconds, may hold `<s>` word runs), kept as a fallback in case
    a URL still answers in srv3. Segments with no text are dropped. An empty body is reported as PO_TOKEN_REQUIRED: YouTube
    answers 200 with nothing when a caption request lacks a required token.
    """
    if len(body) > MAX_BODY_BYTES:
        raise UpstreamFailure("Caption body is too large.")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as e:
        raise UpstreamFailure("Caption body is not valid UTF-8.") from e
    if not text.strip():
        raise PoTokenRequired("Caption response was empty (usually a missing PO token).")
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise UpstreamFailure("Caption XML contains a DTD, which is not allowed.")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as e:
        raise UpstreamFailure(f"Caption XML is malformed: {e}") from e

    if root.tag == "timedtext":
        rows = [(el, el.get("t"), el.get("d"), 1000.0) for el in root.iter("p")]
    elif root.tag == "transcript":
        rows = [(el, el.get("start"), el.get("dur"), 1.0) for el in root.iter("text")]
    else:
        raise UpstreamFailure(f"Unrecognized caption XML root <{root.tag}>.")

    segments = []
    for element, start, duration, scale in rows:
        cleaned = _clean(element)
        if not cleaned.strip():
            continue
        segments.append({"text": cleaned, "start": _seconds(start, scale, "start"),
                         "duration": _seconds(duration, scale, "duration", default=0.0)})
    return segments


def fetch_track_segments(track: CaptionTrack, client: ClientConfig,
                         transport: Transport) -> list[dict]:
    if track.requires_po_token:
        raise PoTokenRequired(f"Caption track {track.language_code!r} requires a PO token.")
    raw = _send(transport, "GET", track.base_url, {"User-Agent": client.user_agent}, None,
                f"{client.name} caption download")
    return parse_timedtext(raw)


# -- Client health ------------------------------------------------------------

class ClientHealth:
    """Per-process record of clients that recently answered with a block.

    A blocked client goes on cooldown: `partition()` puts it after every client that
    is not on cooldown, so later calls stop wasting a request on it. Cooled clients
    stay in the list as a last resort. Thread-safe: fetches run in worker threads.
    """

    def __init__(self, cooldown_seconds: float = CLIENT_COOLDOWN_SECONDS,
                 clock: Callable[[], float] = time.monotonic):
        if cooldown_seconds <= 0:
            raise ValueError(f"cooldown_seconds must be > 0, got {cooldown_seconds}")
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._blocked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def partition(self, clients: "tuple[ClientConfig, ...]"
                  ) -> "tuple[list[ClientConfig], list[ClientConfig]]":
        """Split clients into (healthy, cooled), each in the original order."""
        now = self._clock()
        with self._lock:
            cooled_names = {c.name for c in clients
                            if self._blocked_until.get(c.name, 0.0) > now}
        return ([c for c in clients if c.name not in cooled_names],
                [c for c in clients if c.name in cooled_names])

    def mark_blocked(self, name: str) -> None:
        with self._lock:
            self._blocked_until[name] = self._clock() + self._cooldown

    def mark_ok(self, name: str) -> None:
        with self._lock:
            self._blocked_until.pop(name, None)

    def reset(self) -> None:
        with self._lock:
            self._blocked_until.clear()


HEALTH = ClientHealth()


# -- Orchestration ------------------------------------------------------------

@dataclass(frozen=True)
class NativeTranscript:
    segments: list[dict]
    language_code: str
    is_generated: bool
    fallback_used: bool
    client: str
    clients_tried: tuple[str, ...] = field(default_factory=tuple)


# Client-specific blocks: another client may succeed. Everything else is final.
_ADVANCE_ON = (PoTokenRequired, YouTubeIpBlocked)


def fetch_transcript_native(video_id: str, languages: list[str],
                            transport: Transport = urllib_transport,
                            clients: tuple[ClientConfig, ...] = CLIENTS,
                            health: "ClientHealth | None" = None) -> NativeTranscript:
    """Fetch a transcript through the client chain. Raises TranscriptError.

    Clients on cooldown (see ClientHealth; the shared HEALTH unless `health` is
    given) are tried last. No retry happens here: retrying is the caller's job and applies to the whole
    fetch. `fallback_attempted` on a raised error is true when a language fallback
    track had already been selected, even if downloading it failed.
    """
    if not clients:
        raise ValueError("clients must not be empty")
    health = HEALTH if health is None else health
    healthy, cooled = health.partition(clients)
    if cooled:
        log.info("Trying %s last: recently blocked.", ", ".join(c.name for c in cooled))
    ordered = healthy + cooled
    tried: list[str] = []
    last_error: "TranscriptError | None" = None
    for client in ordered:
        tried.append(client.name)
        fallback_used = False
        try:
            player = fetch_player_response(video_id, client, transport)
            check_playability(player, video_id)
            selection = select_track(list_tracks(player), languages)
            fallback_used = selection.fallback_used
            segments = fetch_track_segments(selection.track, client, transport)
        except _ADVANCE_ON as e:
            e.fallback_attempted = e.fallback_attempted or fallback_used
            last_error = e
            health.mark_blocked(client.name)
            log.warning("Client %s blocked for %s (%s: %s); trying next client.",
                        client.name, video_id, type(e).__name__, e)
            continue
        except TranscriptError as e:
            e.fallback_attempted = e.fallback_attempted or fallback_used
            raise
        health.mark_ok(client.name)
        log.info("Fetched %s via %s (language=%s, fallback=%s, clients tried: %s).",
                 video_id, client.name, selection.track.language_code,
                 selection.fallback_used, ", ".join(tried))
        return NativeTranscript(segments=segments, language_code=selection.track.language_code,
                                is_generated=selection.track.is_generated,
                                fallback_used=selection.fallback_used, client=client.name,
                                clients_tried=tuple(tried))
    assert last_error is not None
    raise type(last_error)(
        f"{last_error} (clients tried: {', '.join(tried)})",
        http_status=last_error.http_status,
        fallback_attempted=last_error.fallback_attempted) from last_error
