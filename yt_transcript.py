#!/usr/bin/env python3
"""
yt_transcript.py  —  YouTube → local knowledge bank

Usage:
    python3 yt_transcript.py <youtube_url> [options]

Options:
    --date      Publish date YYYY-MM-DD    (e.g. --date 2026-05-15)
    --lang      Language codes             (default: sv,en)
    --out       Output directory           (default: ./transcripts)
    --no-clean  Keep raw timestamps

Examples:
    python3 yt_transcript.py https://youtu.be/C5XvwJCGXpo
    python3 yt_transcript.py https://youtu.be/C5XvwJCGXpo --date 2026-05-15
    python3 yt_transcript.py https://youtu.be/C5XvwJCGXpo --lang en
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs


# ── Dependency checks ──────────────────────────────────────────────────────────

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import NoTranscriptFound, TranscriptsDisabled
except ImportError:
    sys.exit("Missing dependency. Run:  pip3 install youtube-transcript-api")

try:
    from pytubefix import YouTube
except ImportError:
    sys.exit("Missing dependency. Run:  pip3 install pytubefix")


# ── Helpers ────────────────────────────────────────────────────────────────────

def extract_video_id(url: str) -> str:
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


def fetch_metadata(url: str) -> dict:
    try:
        yt = YouTube(url)
        published = yt.publish_date
        return {
            "title":     yt.title or "",
            "channel":   yt.author or "",
            "published": published.strftime("%Y-%m-%d") if published else "",
        }
    except Exception:
        return {"title": "", "channel": "", "published": ""}


def fetch_transcript(video_id: str, languages: list) -> tuple:
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


def clean_text(segments: list) -> str:
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
    lines = []
    for seg in segments:
        ts = int(seg["start"])
        h, m, s = ts // 3600, (ts % 3600) // 60, ts % 60
        lines.append(f"[{h:02d}:{m:02d}:{s:02d}]  {seg['text']}")
    return "\n".join(lines)


def safe_filename(text: str, max_len: int = 80) -> str:
    text = re.sub(r'[<>:"/\\|?*]', "", text)
    text = re.sub(r"\s+", "_", text.strip())
    return text[:max_len]


def validate_date(date_str: str) -> str:
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return date_str
    except ValueError:
        sys.exit(f"Error: --date must be YYYY-MM-DD, got '{date_str}'")


def write_md(path: Path, url: str, meta: dict, language: str, body: str) -> None:
    content = f"""# {meta['title'] or 'Untitled'}

source: {url}
channel: {meta['channel'] or '—'}
published: {meta['published'] or '—'}
language: {language}
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
    parser.add_argument("url",        help="YouTube video URL")
    parser.add_argument("--date",     help="Publish date YYYY-MM-DD")
    parser.add_argument("--lang",     default="sv,en",
                        help="Preferred languages, comma-separated (default: sv,en)")
    parser.add_argument("--out",      default="transcripts",
                        help="Output directory (default: ./transcripts)")
    parser.add_argument("--no-clean", action="store_true",
                        help="Keep raw timestamps instead of clean paragraphs")
    args = parser.parse_args()

    languages = [l.strip() for l in args.lang.split(",")]

    print(f"\n  URL → {args.url}")

    # 1. Video ID
    try:
        video_id = extract_video_id(args.url)
    except ValueError as e:
        sys.exit(f"Error: {e}")

    # 2. Metadata — best effort, no hard failures
    print("  Fetching metadata …")
    meta = fetch_metadata(args.url)

    # 3. Date: manual override wins, then auto, then today
    if args.date:
        meta["published"] = validate_date(args.date)

    print(f"  Title     → {meta['title'] or '(not detected)'}")
    print(f"  Channel   → {meta['channel'] or '(not detected)'}")
    print(f"  Published → {meta['published'] or '(not detected)'}")

    # 4. Transcript
    print(f"  Fetching transcript (langs: {languages}) …")
    try:
        segments, language = fetch_transcript(video_id, languages)
    except TranscriptsDisabled:
        sys.exit("Error: Transcripts are disabled for this video.")
    except NoTranscriptFound:
        sys.exit(f"Error: No transcript found for languages {languages}.")
    print(f"  Language  → {language}  ({len(segments)} segments)")

    # 5. Format
    body = raw_text(segments) if args.no_clean else clean_text(segments)

    # 6. Save — use title if available, fall back to video ID
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    date_prefix = (meta["published"] or datetime.now().strftime("%Y-%m-%d"))[:10]
    name        = safe_filename(meta["title"]) if meta["title"] else video_id
    out_path    = out_dir / f"{date_prefix}_{name}.md"

    write_md(out_path, args.url, meta, language, body)

    print(f"\n  Saved → {out_path}")

    # Remind to rename only if title was missing
    if not meta["title"]:
        print("  Note: title not detected — you may want to rename this file.")

    print()


if __name__ == "__main__":
    main()
