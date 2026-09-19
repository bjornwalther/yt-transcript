#!/usr/bin/env python3
"""Record redacted InnerTube fixtures for test_innertube.py.

Live network. Paced, and stops at the first bot check. Run rarely: every request
counts against the caller's IP reputation.

Redaction: signed caption URLs (they embed IP and signature parameters) are
replaced by a stable fake URL, and caption words are replaced by `wordN` so no
transcript text is stored. XML structure, attributes, whitespace, punctuation and
entity encoding are preserved, since those are what the parsers depend on.

Usage: uv run python tools/record_innertube_fixtures.py
"""

import json
import re
import sys
import time
from pathlib import Path

import innertube
from yt_errors import TranscriptError

OUT = Path(__file__).resolve().parent.parent / "fixtures" / "innertube"
PAUSE_SECONDS = 2.0
CAPTIONED_VIDEO = "dQw4w9WgXcQ"
BOT_CHECK_VIDEO = "jNQXAC9IVRw"
UNAVAILABLE_VIDEO = "aaaaaaaaaaa"
MAX_ELEMENTS = 8

_ENTITY = re.compile(r"(&#?\w+;)")
_WORD = re.compile(r"[^\W\d_]+")
_TEXT_NODE = re.compile(r">([^<]+)<")


def _fake_track_url(video_id: str, track: dict, client: str) -> str:
    """Stable stand-in for a signed URL. Keeps `&fmt=srv3` when the original had it."""
    kind = "asr" if track.get("kind") == "asr" else "manual"
    srv3 = "&fmt=srv3" if "&fmt=srv3" in track["baseUrl"] else ""
    return (f"https://www.youtube.com/api/timedtext?v={video_id}"
            f"&lang={track['languageCode']}&kind={kind}&fixture={client}{srv3}")


def _redact_text(chunk: str, counter: list) -> str:
    def word(_match):
        counter[0] += 1
        return f"word{counter[0]}"
    return "".join(part if _ENTITY.fullmatch(part) else _WORD.sub(word, part)
                   for part in _ENTITY.split(chunk))


def redact_caption_xml(xml: str) -> str:
    """Keep the first MAX_ELEMENTS caption elements, replace words with wordN."""
    counter = [0]
    dialect_a = "<timedtext" in xml
    closer = "</p>" if dialect_a else "</text>"
    tail = "</body></timedtext>" if dialect_a else "</transcript>"
    elements = xml.split(closer)[:-1][:MAX_ELEMENTS]
    body = closer.join(elements) + closer
    body = _TEXT_NODE.sub(lambda m: ">" + _redact_text(m.group(1), counter) + "<", body)
    return body + tail


def trim_player(player: dict, video_id: str, client: str) -> dict:
    trimmed = {"playabilityStatus": player.get("playabilityStatus")}
    renderer = (player.get("captions") or {}).get("playerCaptionsTracklistRenderer")
    if renderer:
        tracks = []
        for track in renderer.get("captionTracks", []):
            tracks.append({**{k: v for k, v in track.items() if k != "baseUrl"},
                           "baseUrl": _fake_track_url(video_id, track, client)})
        trimmed["captions"] = {"playerCaptionsTracklistRenderer": {"captionTracks": tracks}}
    return trimmed


def _write(name: str, content: str) -> None:
    (OUT / name).write_text(content, encoding="utf-8")
    print(f"  wrote {name} ({len(content)} bytes)")


def _pace() -> None:
    time.sleep(PAUSE_SECONDS)


def record_status_only(video_id: str, label: str) -> dict:
    client = innertube.CLIENTS[0]
    player = innertube.fetch_player_response(video_id, client, innertube.urllib_transport)
    _write(f"player_{label}.json", json.dumps(trim_player(player, video_id, client.name), indent=2))
    return player


def record_captioned(client: innertube.ClientConfig, want: list) -> bool:
    player = innertube.fetch_player_response(CAPTIONED_VIDEO, client, innertube.urllib_transport)
    status = player.get("playabilityStatus", {}).get("status")
    if status != "OK":
        print(f"  {client.name}: playability {status}; stopping.")
        return False
    _write(f"player_{client.name}_captioned.json",
           json.dumps(trim_player(player, CAPTIONED_VIDEO, client.name), indent=2))
    for track in innertube.list_tracks(player):
        if (track.language_code, track.is_generated) not in want:
            continue
        _pace()
        raw = innertube._send(innertube.urllib_transport, "GET", track.base_url,
                              {"User-Agent": client.user_agent}, None, "caption download")
        kind = "asr" if track.is_generated else "manual"
        _write(f"timedtext_{client.name}_{track.language_code}_{kind}.xml",
               redact_caption_xml(raw.decode("utf-8")))
    return True


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        print("unavailable video"); record_status_only(UNAVAILABLE_VIDEO, "unavailable"); _pace()
        print("bot-check video"); record_status_only(BOT_CHECK_VIDEO, "bot_check"); _pace()
        android_vr, ios = innertube.CLIENTS[0], innertube.CLIENTS[1]
        print("android_vr captioned")
        if not record_captioned(android_vr, [("en", False), ("en", True)]):
            return 2
        _pace()
        print("ios captioned")
        record_captioned(ios, [("en", False)])
    except TranscriptError as e:
        print(f"stopped: {type(e).__name__}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
