#!/usr/bin/env python3
"""YouTube transcript fetcher. CLI + shared logic for MCP server."""

import argparse, json, re, sys, time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from urllib.request import urlopen, Request
from urllib.error import URLError

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import NoTranscriptFound, TranscriptsDisabled

try:
    from youtube_transcript_api._errors import PoTokenRequired
except ImportError:
    PoTokenRequired = None

try:
    from pytubefix import YouTube
except ImportError:
    YouTube = None

# ── Config ──────────────────────────────────────────────────────────────────────────────

CACHE_DIR = Path.home() / ".cache" / "yt-transcript"
CACHE_VERSION = 2
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 3

# Permanent errors: not worth retrying (video state won't change between attempts)
_NON_RETRYABLE = {
    "TranscriptsDisabled", "NoTranscriptFound", "PoTokenRequired",
    "VideoUnavailable", "IpBlocked", "RequestBlocked",
}

# ── URL parsing ─────────────────────────────────────────────────────────────────────────

def extract_video_id(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc in ("youtu.be", "www.youtu.be"):
        return parsed.path.lstrip("/")
    qs = parse_qs(parsed.query)
    if "v" in qs:
        return qs["v"][0]
    m = re.search(r"/(shorts|embed|v)/([^/?&]+)", parsed.path)
    if m:
        return m.group(2)
    raise ValueError(f"Cannot extract video ID from: {url}")

# ── Cache ───────────────────────────────────────────────────────────────────────────────

def _cache_path(video_id: str, languages: list) -> Path:
    """Cache path keyed by video ID and language preference vector.

    Different language priorities produce separate cache entries so that
    a request for sv,en never returns a stale en-only result when Swedish
    might be available. This also means fallback results are correctly
    cached per request: a sv,en request that fell back to English won't
    pollute a pure en request's cache, and vice versa.
    """
    lang_key = "_".join(languages) if languages else "any"
    return CACHE_DIR / f"{video_id}_{lang_key}.json"


def load_from_cache(video_id: str, languages: list) -> dict | None:
    """Load cached transcript for a specific video and language vector.

    Returns None if no matching cache file exists or the entry predates
    the current cache schema (legacy v1 files are silently skipped).
    """
    path = _cache_path(video_id, languages)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if data.get("cache_version", 0) < CACHE_VERSION:
        return None
    return data


def save_to_cache(video_id: str, segments: list, language: str,
                  languages: list, meta: dict, is_generated: bool = False) -> None:
    """Persist transcript with full provenance for later cache hits."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "cache_version": CACHE_VERSION,
        "segments": segments,
        "language": language,
        "requested_languages": languages,
        "is_generated": is_generated,
        "meta": meta.get("fields", meta),
        "meta_sources": meta.get("sources", {}),
        "cached_at": datetime.now().strftime("%Y-%m-%d"),
    }
    _cache_path(video_id, languages).write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )

# ── Metadata (layered: oEmbed then pytubefix then manual) ────────────────────────

def _fetch_oembed(url: str) -> dict:
    try:
        req = Request(f"https://www.youtube.com/oembed?url={url}&format=json",
                      headers={"User-Agent": "yt-transcript/1.2"})
        with urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode())
        return {"title": d.get("title", ""), "channel": d.get("author_name", ""),
                "published": ""}
    except (URLError, json.JSONDecodeError, OSError):
        return {"title": "", "channel": "", "published": ""}


def _fetch_pytubefix(url: str) -> dict:
    if YouTube is None:
        return {"title": "", "channel": "", "published": ""}
    try:
        yt = YouTube(url)
        pub = yt.publish_date
        return {"title": yt.title or "", "channel": yt.author or "",
                "published": pub.strftime("%Y-%m-%d") if pub else ""}
    except Exception:
        return {"title": "", "channel": "", "published": ""}


def fetch_metadata(url: str) -> dict:
    """Fetch metadata with per-field source tracking.

    Returns {\"fields\": {...}, \"sources\": {...}, \"missing\": [...], \"complete\": bool}.
    """
    oembed = _fetch_oembed(url)
    ptf = _fetch_pytubefix(url)

    fields, sources = {}, {}
    for key in ("title", "channel"):
        if oembed[key]:
            fields[key], sources[key] = oembed[key], "oembed"
        elif ptf[key]:
            fields[key], sources[key] = ptf[key], "pytubefix"
        else:
            fields[key], sources[key] = "", "none"

    # Published: only pytubefix provides this (oEmbed does not)
    if ptf["published"]:
        fields["published"], sources["published"] = ptf["published"], "pytubefix"
    else:
        fields["published"], sources["published"] = "", "none"

    missing = [k for k in ("title", "channel", "published") if not fields[k]]
    return {"fields": fields, "sources": sources,
            "missing": missing, "complete": len(missing) == 0}

# ── Transcript ────────────────────────────────────────────────────────────────────────

def fetch_transcript(video_id: str, languages: list) -> tuple:
    """Fetch transcript, falling back to any available language if needed.

    Returns (segments, language_code, is_generated, fallback_used).
    On failure during fallback, sets fallback_attempted=True on the exception
    so upstream callers can report accurate provenance even on errors.
    """
    api = YouTubeTranscriptApi()
    tl = api.list(video_id)
    fallback_used = False
    try:
        t = tl.find_transcript(languages)
    except NoTranscriptFound:
        fallback_used = True
        try:
            t = tl.find_transcript([x.language_code for x in tl])
        except Exception as e:
            e.fallback_attempted = True
            raise
    fetched = t.fetch()
    segments = [{"text": s.text, "start": s.start, "duration": s.duration}
                for s in fetched]
    return segments, t.language_code, t.is_generated, fallback_used


def fetch_transcript_with_retry(video_id: str, languages: list) -> tuple:
    """Fetch with retries on transient errors only.

    Returns (segments, language_code, is_generated, fallback_used, attempts).
    Permanent errors (disabled, unavailable, IP-blocked, PO token) raise
    immediately without burning retries. All raised exceptions carry an
    actual_attempts attribute so error reporters can derive retry_count.
    """
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            segs, lang, is_gen, fallback = fetch_transcript(video_id, languages)
            return segs, lang, is_gen, fallback, attempt
        except Exception as e:
            e.actual_attempts = attempt
            if type(e).__name__ in _NON_RETRYABLE:
                raise
            last_err = e
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)
    last_err.actual_attempts = MAX_RETRIES
    raise last_err

# ── Formatting ────────────────────────────────────────────────────────────────────────

def clean_text(segments: list) -> str:
    lines = []
    for seg in segments:
        t = re.sub(r"\[.*?\]", "", seg["text"]).replace("\n", " ").strip()
        if t:
            lines.append(t)
    return "\n\n".join(" ".join(lines[i:i+10]) for i in range(0, len(lines), 10))


def raw_text(segments: list) -> str:
    return "\n".join(
        f"[{int(s['start'])//3600:02d}:{int(s['start'])%3600//60:02d}:"
        f"{int(s['start'])%60:02d}]  {s['text']}"
        for s in segments
    )

# ── File output ───────────────────────────────────────────────────────────────────────

def safe_filename(text: str, max_len: int = 80) -> str:
    return re.sub(r"\s+", "_", re.sub(r'[<>:"/\\|?*]', "", text.strip()))[:max_len]


def write_md(path: Path, url: str, meta: dict, language: str,
             body: str, notes: list = None) -> None:
    fields = meta.get("fields", meta)
    missing = meta.get("missing", [k for k in ("title", "channel", "published")
                                    if not fields.get(k)])
    notes_str = "".join(f"note: {n}\n" for n in (notes or []))
    missing_str = f"missing: {', '.join(missing)}\n" if missing else ""
    content = (
        f"# {fields.get('title') or 'Untitled'}\n\n"
        f"source: {url}\nchannel: {fields.get('channel') or '\u2014'}\n"
        f"published: {fields.get('published') or '\u2014'}\nlanguage: {language}\n"
        f"{missing_str}{notes_str}"
        f"fetched: {datetime.now().strftime('%Y-%m-%d')}\n\n---\n\n"
        f"## Transcript\n\n{body}\n"
    )
    path.write_text(content, encoding="utf-8")

# ── CLI ─────────────────────────────────────────────────────────────────────────────────

def main():
    """CLI entry point: fetch a YouTube transcript and save as markdown."""
    p = argparse.ArgumentParser(description="YouTube transcript \u2192 markdown")
    p.add_argument("url", help="YouTube video URL")
    p.add_argument("--date", help="Publish date YYYY-MM-DD")
    p.add_argument("--title", help="Title override")
    p.add_argument("--channel", help="Channel override")
    p.add_argument("--lang", default="sv,en", help="Languages (default: sv,en)")
    p.add_argument("--out", default="transcripts", help="Output dir")
    p.add_argument("--no-clean", action="store_true", help="Keep timestamps")
    p.add_argument("--no-cache", action="store_true", help="Bypass cache")
    args = p.parse_args()

    languages = [l.strip() for l in args.lang.split(",")]
    notes = []

    try:
        video_id = extract_video_id(args.url)
    except ValueError as e:
        sys.exit(f"Error: {e}")

    cached = None if args.no_cache else load_from_cache(video_id, languages)

    if cached:
        segments = cached["segments"]
        language = cached["language"]
        meta = {"fields": cached.get("meta", {}),
                "sources": cached.get("meta_sources", {}),
                "missing": [], "complete": True}
        meta["missing"] = [k for k in ("title", "channel", "published")
                           if not meta["fields"].get(k)]
        meta["complete"] = len(meta["missing"]) == 0
        notes.append(f"From cache (fetched {cached['cached_at']}).")
        print(f"  Cache HIT ({cached['cached_at']})")
    else:
        meta = fetch_metadata(args.url)
        try:
            segments, language, is_gen, fallback, attempts = \
                fetch_transcript_with_retry(video_id, languages)
            if attempts > 1:
                notes.append(f"Retry succeeded (attempt {attempts}/{MAX_RETRIES}).")
            if fallback:
                notes.append(f"Language fallback: requested {languages}, got {language}.")
        except TranscriptsDisabled:
            sys.exit("Error: Transcripts disabled for this video.")
        except NoTranscriptFound:
            sys.exit(f"Error: No transcript for languages {languages}.")
        except Exception as e:
            cls = type(e).__name__
            if cls == "PoTokenRequired" or (PoTokenRequired and isinstance(e, PoTokenRequired)):
                sys.exit("Error: Video requires PO token. Cannot fetch without JS runtime.")
            sys.exit(f"Error: Rate-limited. Tried {MAX_RETRIES}x. Wait and retry.\n{e}")
        save_to_cache(video_id, segments, language, languages, meta, is_gen)

    fields = meta.get("fields", meta)
    if args.title:
        fields["title"] = args.title
    if args.channel:
        fields["channel"] = args.channel
    if args.date:
        try:
            datetime.strptime(args.date, "%Y-%m-%d")
            fields["published"] = args.date
        except ValueError:
            sys.exit(f"Error: --date must be YYYY-MM-DD, got '{args.date}'")

    meta["missing"] = [k for k in ("title", "channel", "published")
                       if not fields.get(k)]
    meta["complete"] = len(meta["missing"]) == 0

    body = raw_text(segments) if args.no_clean else clean_text(segments)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = (fields.get("published") or datetime.now().strftime("%Y-%m-%d"))[:10]
    name = safe_filename(fields["title"]) if fields.get("title") else video_id
    out_path = out_dir / f"{prefix}_{name}.md"

    write_md(out_path, args.url, meta, language, body, notes)
    print(f"  Saved: {out_path} ({len(segments)} segments)")
    if meta["missing"]:
        print(f"  Missing: {', '.join(meta['missing'])}")


if __name__ == "__main__":
    main()
