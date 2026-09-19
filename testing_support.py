"""Shared test helpers: recorded InnerTube fixtures and a fake transport.

Not part of the wheel. Used by test_innertube.py and test_call_tool_e2e.py.
"""

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from innertube import CLIENTS, HttpResponse

FIXTURES = Path(__file__).parent / "fixtures" / "innertube"
VIDEO_ID = "dQw4w9WgXcQ"
CLIENT_NAMES = {str(c.client_id): c.name for c in CLIENTS}


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def load_player(name: str) -> dict:
    return json.loads(load(name))


class FakeTransport:
    """Serves fixtures and records every request.

    `players` maps client name -> fixture file name, HttpResponse, or Exception; a
    list is consumed one entry per POST for that client.
    `captions` overrides caption GET responses (HttpResponse or Exception); a list is
    consumed one entry per GET, then falls back to the recorded XML. Without an
    override, srv3 URLs get the recorded srv3 XML and all other caption URLs get the
    recorded simple-dialect XML.
    """

    def __init__(self, players: dict, captions=None):
        self.players = players
        self.captions = captions
        self.calls: list[tuple] = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if method == "POST":
            client = CLIENT_NAMES[headers["X-YouTube-Client-Name"]]
            entry = self.players[client]
            if isinstance(entry, list):
                entry = entry.pop(0)
            return self._resolve(entry, lambda n: load(n).encode("utf-8"))
        override = self.captions.pop(0) if isinstance(self.captions, list) and self.captions \
            else None if isinstance(self.captions, list) else self.captions
        if override is not None:
            return self._resolve(override, lambda n: n)
        query = parse_qs(urlparse(url).query)
        if "srv3" in query.get("fmt", []):
            kind = query["kind"][0]
            name = f"timedtext_android_vr_en_{'asr' if kind == 'asr' else 'manual'}.xml"
        else:
            name = "timedtext_ios_en_manual.xml"
        return HttpResponse(200, load(name).encode("utf-8"))

    @staticmethod
    def _resolve(entry, read):
        if isinstance(entry, Exception):
            raise entry
        if isinstance(entry, HttpResponse):
            return entry
        return HttpResponse(200, read(entry))

    def posts(self):
        return [c for c in self.calls if c[0] == "POST"]

    def gets(self):
        return [c for c in self.calls if c[0] == "GET"]


def all_clients(entry) -> dict:
    return {c.name: entry for c in CLIENTS}
