#!/usr/bin/env python3
"""
yt_transcript.py  —  YouTube → local knowledge bank

Fetches YouTube video transcripts and saves them as clean markdown files,
optimised for use as AI context. Includes layered metadata fetching with
oEmbed as primary source and pytubefix as fallback.

Usage:
    python3 yt_transcript.py <youtube_url> [options]

Options:
    --date      Publish date YYYY-MM-DD    (e.g. --date 2026-05-15)
    --title     Manual title override
    --channel   Manual channel override
    --lang      Language codes             (default: sv,en)
    --out       Output directory           (default: ./transcripts)
    --no-clean  Keep raw timestamps

Examples:
    python3 yt_transcript.py https://youtu.be/C5XvwJCGXpo
    python3 yt_transcript.py https://youtu.be/C5XvwJCGXpo --date 2026-05-15
    python3 yt_transcript.py https://youtu.be/C5XvwJCGXpo --title "My Title" --channel "My Channel"
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from urllib.request import urlopen, Request
from urllib.error import URLError


# ── Dependency checks ──────────────────────────────────────────────────────────────────

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import NoTranscriptFound, TranscriptsDisabled
except ImportError:
    sys.exit("Missing dependency. Run:  pip3 install youtube-transcript-api")

try:
    from pytubefix import YouTube
except ImportError:
    # pytubefix is optional (used as fallback for publish date)
    YouTube = None


# ── Metadata fetching (layered approach) ─────────────────────────────────────────
#
# Layer 1: YouTube oEmbed (no API key, fast, reliable for title + channel)
# Layer 2: pytubefix (slower, fragile, but can get publish date)
# Layer 3: Manual overrides (always win)
#
# This order ensures we get the best data available without hard dependencies
# on any single source.


def fetch_metadata_oembed(url: str) -> dict:
    """Fetch title and channel via YouTube's oEmbed endpoint.

    Fast, no authentication needed, and stable. Does not provide
    publish date (that's what pytubefix or manual override is for).

    Returns:
        dict with keys: title, channel, published, source
    """
    oembed_url = f"https://www.youtube.com/oembed?url={url}&format=json"
    try:
        req = Request(oembed_url, headers={"User-Agent": "yt-transcript/1.1"})
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        return {
            "title": data.get("title", ""),
            "channel": data.get("author_name", ""),
            "published": "",  # oEmbed doesn't provide this
            "source": "oembed",
        }
    except (URLError, json.JSONDecodeError, OSError):
        return {"title": "", "channel": "", "published": "", "source": "none"}


def fetch_metadata_pytubefix(url: str) -> dict:
    """Fetch metadata via pytubefix (fallback, mainly for publish date).

    Slower and more fragile than oEmbed. YouTube sometimes blocks it.
    Used primarily to supplement oEmbed with publish date.

    Returns:
        dict with keys: title, channel, published, source
    """
    if YouTube is None:
        return {"title": "", "channel": "", "published": "", "source": "none"}
    try:
        yt = YouTube(url)
        published = yt.publish_date
        return {
            "title": yt.title or "",
            "channel": yt.author or "",
            "published": published.strftime("%Y-%m-%d") if published else "",
            "source": "pytubefix",
        }
    except Exception:
        return {"title": "", "channel": "", "published": "", "source": "none"}


def fetch_metadata(url: str) -> dict:
    """Fetch metadata using layered approach: oEmbed first, pytubefix as fallback.

    Returns the best available metadata merged from both sources.
    oEmbed is preferred for title/channel (faster, more reliable).
    pytubefix is used to fill in publish date when available.

    Returns:
        dict with keys: title, channel, published, source, complete
    """
    # Layer 1: oEmbed (primary)
    meta = fetch_metadata_oembed(url)

    # Layer 2: pytubefix (supplement, mainly for publish date)
    if not meta["published"]:
        fallback = fetch_metadata_pytubefix(url)
        if fallback["published"]:
            meta["published"] = fallback["published"]
            meta["source"] = f"oembed+pytubefix" if meta["source"] == "oembed" else fallback["source"]
        # Also fill in title/channel if oEmbed failed
        if not meta["title"] and fallback["title"]:
            meta["title"] = fallback["title"]
            meta["source"] = fallback["source"]
        if not meta["channel"] and fallback["channel"]:
            meta["channel"] = fallback["channel"]

    # Determine completeness
    missing = [k for k in ("title", "channel", "published") if not meta[k]]
    meta["complete"] = len(missing) == 0
    meta["missing"] = missing

    return meta


# ── Transcript fetching ───────────────────────────────────────────────────────────


def extract_video_id(url: str) -> str:
    """Extract the video ID from any YouTube URL format.

    Supports: youtube.com/watch, youtu.be, /shorts/, /embed/, /v/

    Raises:
        ValueError: If the URL format is not recognised.
    """
    parsed = urlparse(url)
    if parsed.netloc in ("youtu.be", "www.youtu.be"):
        return parsed.path.lstrip("/")
    qs = parse_qs(parsed.query)
    if "v" in qs:
        return qs["v"][0]
    match = re.search(r"/(shorts|embed|v)/([^/?&]+)", parsed.path)
    if match:
        return match.group(2)
    raise ValueError(f"Could not extract video ID from URL: {url}")


def fetch_transcript(video_id: str, languages: list) -> tuple:
    """Fetch transcript segments for a video.

    Tries the specified languages first, falls back to any available transcript.

    Args:
        video_id: YouTube video ID
        languages: List of preferred language codes (e.g. ['sv', 'en'])

    Returns:
        Tuple of (segments_list, language_code)

    Raises:
        TranscriptsDisabled: If the video has transcripts disabled
        NoTranscriptFound: If no transcript is available
    """
    api = YouTubeTranscriptApi()
    transcript_list = api.list(video_id)
    try:
        transcript = transcript_list.find_transcript(languages)
    except NoTranscriptFound:
        transcript = transcript_list.find_transcript(
            [t.language_code for t in transcript_list]
        )
    fetched = transcript.fetch()
    segments = [{"text": s.text, "start": s.start, "duration": s.duration}
                for s in fetched]
    return segments, transcript.language_code


# ── Text formatting ──────────────────────────────────────────────────────────────


def clean_text(segments: list) -> str:
    """Format segments into clean paragraphs (10 lines per paragraph).

    Removes bracketed annotations (e.g. [Music]) and joins lines into
    readable paragraphs optimised for AI consumption.
    """
    lines = []
    for seg in segments:
        text = re.sub(r"\[.*?\]", "", seg["text"])
        text = text.replace("\n", " ").strip()
        if text:
            lines.append(text)
    paragraphs, chunk = [], []
    for i, line in enumerate(lines):
        chunk.append(line)
        if (i + 1) % 10 == 0:
            paragraphs.append(" ".join(chunk))
            chunk = []
    if chunk:
        paragraphs.append(" ".join(chunk))
    return "\n\n".join(paragraphs)


def raw_text(segments: list) -> str:
    """Format segments with timestamps (HH:MM:SS per line)."""
    lines = []
    for seg in segments:
        ts = int(seg["start"])
        h, m, s = ts // 3600, (ts % 3600) // 60, ts % 60
        lines.append(f"[{h:02d}:{m:02d}:{s:02d}]  {seg['text']}")
    return "\n".join(lines)


# ── File utilities ────────────────────────────────────────────────────────────────


def safe_filename(text: str, max_len: int = 80) -> str:
    """Sanitise a string for use as a filename."""
    text = re.sub(r'[<>:"/\\|?*]', "", text)
    text = re.sub(r"\s+", "_", text.strip())
    return text[:max_len]


def validate_date(date_str: str) -> str:
    """Validate a date string is YYYY-MM-DD format."""
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return date_str
    except ValueError:
        sys.exit(f"Error: --date must be YYYY-MM-DD, got '{date_str}'")


def write_md(path: Path, url: str, meta: dict, language: str, body: str) -> None:
    """Write transcript to a markdown file with metadata header."""
    content = f"""# {meta['title'] or 'Untitled'}

source: {url}
channel: {meta['channel'] or '\u2014'}
published: {meta['published'] or '\u2014'}
language: {language}
metadata_source: {meta.get('source', 'unknown')}
metadata_complete: {str(meta.get('complete', False)).lower()}
fetched: {datetime.now().strftime('%Y-%m-%d')}

---

## Transcript

{body}
"""
    path.write_text(content, encoding="utf-8")


# ── Main ───────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Save a YouTube transcript to your local knowledge bank."
    )
    parser.add_argument("url",          help="YouTube video URL")
    parser.add_argument("--date",       help="Publish date YYYY-MM-DD (overrides auto-detection)")
    parser.add_argument("--title",      help="Manual title override")
    parser.add_argument("--channel",    help="Manual channel override")
    parser.add_argument("--lang",       default="sv,en",
                        help="Preferred languages, comma-separated (default: sv,en)")
    parser.add_argument("--out",        default="transcripts",
                        help="Output directory (default: ./transcripts)")
    parser.add_argument("--no-clean",   action="store_true",
                        help="Keep raw timestamps instead of clean paragraphs")
    args = parser.parse_args()

    languages = [l.strip() for l in args.lang.split(",")]

    print(f"\n  URL \u2192 {args.url}")

    # 1. Video ID
    try:
        video_id = extract_video_id(args.url)
    except ValueError as e:
        sys.exit(f"Error: {e}")

    # 2. Metadata (layered: oEmbed -> pytubefix -> manual overrides)
    print("  Fetching metadata \u2026")
    meta = fetch_metadata(args.url)

    # 3. Manual overrides always win
    if args.title:
        meta["title"] = args.title
    if args.channel:
        meta["channel"] = args.channel
    if args.date:
        meta["published"] = validate_date(args.date)

    # Recalculate completeness after overrides
    missing = [k for k in ("title", "channel", "published") if not meta[k]]
    meta["complete"] = len(missing) == 0
    meta["missing"] = missing
    if args.title or args.channel or args.date:
        meta["source"] = meta["source"] + "+manual" if meta["source"] != "none" else "manual"

    print(f"  Title     \u2192 {meta['title'] or '(not detected)'}")
    print(f"  Channel   \u2192 {meta['channel'] or '(not detected)'}")
    print(f"  Published \u2192 {meta['published'] or '(not detected)'}")
    print(f"  Source    \u2192 {meta['source']}")

    # 4. Transcript
    print(f"  Fetching transcript (langs: {languages}) \u2026")
    try:
        segments, language = fetch_transcript(video_id, languages)
    except TranscriptsDisabled:
        sys.exit("Error: Transcripts are disabled for this video.")
    except NoTranscriptFound:
        sys.exit(f"Error: No transcript found for languages {languages}.")
    print(f"  Language  \u2192 {language}  ({len(segments)} segments)")

    # 5. Format
    body = raw_text(segments) if args.no_clean else clean_text(segments)

    # 6. Save
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    date_prefix = (meta["published"] or datetime.now().strftime("%Y-%m-%d"))[:10]
    name        = safe_filename(meta["title"]) if meta["title"] else video_id
    out_path    = out_dir / f"{date_prefix}_{name}.md"

    write_md(out_path, args.url, meta, language, body)

    print(f"\n  Saved \u2192 {out_path}")
    if not meta["complete"]:
        print(f"  Note: missing metadata: {', '.join(meta['missing'])}")
    print()


if __name__ == "__main__":
    main()
