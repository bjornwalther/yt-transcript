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
    from pytubefix import YouTube
except ImportError:
    YouTube = None  # optional: only used for publish date fallback

# ── Config ──────────────────────────────────────────────────────────────────────

CACHE_DIR = Path.home() / ".cache" / "yt-transcript"
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 3

# ── URL parsing ─────────────────────────────────────────────────────────────────

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

# ── Cache ───────────────────────────────────────────────────────────────────────

def load_from_cache(video_id: str) -> dict | None:
    path = CACHE_DIR / f"{video_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

def save_to_cache(video_id: str, segments: list, language: str, meta: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "segments": segments,
        "language": language,
        "meta": {k: meta.get(k, "") for k in ("title", "channel", "published", "source")},
        "cached_at": datetime.now().strftime("%Y-%m-%d"),
    }
    (CACHE_DIR / f"{video_id}.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )

# ── Metadata (layered: oEmbed → pytubefix → manual) ────────────────────────────

def _fetch_oembed(url: str) -> dict:
    try:
        req = Request(f"https://www.youtube.com/oembed?url={url}&format=json",
                      headers={"User-Agent": "yt-transcript/1.1"})
        with urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode())
        return {"title": d.get("title", ""), "channel": d.get("author_name", ""),
                "published": "", "source": "oembed"}
    except (URLError, json.JSONDecodeError, OSError):
        return {"title": "", "channel": "", "published": "", "source": "none"}

def _fetch_pytubefix(url: str) -> dict:
    if YouTube is None:
        return {"title": "", "channel": "", "published": "", "source": "none"}
    try:
        yt = YouTube(url)
        pub = yt.publish_date
        return {"title": yt.title or "", "channel": yt.author or "",
                "published": pub.strftime("%Y-%m-%d") if pub else "", "source": "pytubefix"}
    except Exception:
        return {"title": "", "channel": "", "published": "", "source": "none"}

def fetch_metadata(url: str) -> dict:
    meta = _fetch_oembed(url)
    if not meta["published"]:
        fb = _fetch_pytubefix(url)
        if fb["published"]:
            meta["published"] = fb["published"]
            meta["source"] = "oembed+pytubefix" if meta["source"] == "oembed" else fb["source"]
        if not meta["title"] and fb["title"]:
            meta["title"] = fb["title"]
        if not meta["channel"] and fb["channel"]:
            meta["channel"] = fb["channel"]
    meta["missing"] = [k for k in ("title", "channel", "published") if not meta[k]]
    meta["complete"] = len(meta["missing"]) == 0
    return meta

# ── Transcript ──────────────────────────────────────────────────────────────────

def fetch_transcript(video_id: str, languages: list) -> tuple:
    api = YouTubeTranscriptApi()
    tl = api.list(video_id)
    try:
        t = tl.find_transcript(languages)
    except NoTranscriptFound:
        t = tl.find_transcript([x.language_code for x in tl])
    fetched = t.fetch()
    return [{"text": s.text, "start": s.start, "duration": s.duration} for s in fetched], t.language_code

def fetch_transcript_with_retry(video_id: str, languages: list) -> tuple:
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            segs, lang = fetch_transcript(video_id, languages)
            return segs, lang, attempt
        except (TranscriptsDisabled, NoTranscriptFound):
            raise
        except Exception as e:
            last_err = e
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)
    raise last_err

# ── Formatting ──────────────────────────────────────────────────────────────────

def clean_text(segments: list) -> str:
    lines = []
    for seg in segments:
        t = re.sub(r"\[.*?\]", "", seg["text"]).replace("\n", " ").strip()
        if t:
            lines.append(t)
    return "\n\n".join(" ".join(lines[i:i+10]) for i in range(0, len(lines), 10))

def raw_text(segments: list) -> str:
    return "\n".join(
        f"[{int(s['start'])//3600:02d}:{int(s['start'])%3600//60:02d}:{int(s['start'])%60:02d}]  {s['text']}"
        for s in segments
    )

# ── File output ─────────────────────────────────────────────────────────────────

def safe_filename(text: str, max_len: int = 80) -> str:
    return re.sub(r"\s+", "_", re.sub(r'[<>:"/\\|?*]', "", text.strip()))[:max_len]

def write_md(path: Path, url: str, meta: dict, language: str, body: str, notes: list = None) -> None:
    notes_str = "".join(f"note: {n}\n" for n in (notes or []))
    missing_str = f"missing: {', '.join(meta['missing'])}\n" if meta.get("missing") else ""
    content = (
        f"# {meta.get('title') or 'Untitled'}\n\n"
        f"source: {url}\nchannel: {meta.get('channel') or '\u2014'}\n"
        f"published: {meta.get('published') or '\u2014'}\nlanguage: {language}\n"
        f"metadata_source: {meta.get('source', 'unknown')}\n"
        f"{missing_str}{notes_str}"
        f"fetched: {datetime.now().strftime('%Y-%m-%d')}\n\n---\n\n## Transcript\n\n{body}\n"
    )
    path.write_text(content, encoding="utf-8")

# ── CLI ─────────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="YouTube transcript → markdown")
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

    cached = None if args.no_cache else load_from_cache(video_id)

    if cached:
        segments, language, meta = cached["segments"], cached["language"], cached["meta"]
        meta["missing"] = [k for k in ("title", "channel", "published") if not meta.get(k)]
        meta["complete"] = len(meta["missing"]) == 0
        notes.append(f"From cache (fetched {cached['cached_at']}).")
        print(f"  Cache HIT ({cached['cached_at']})")
    else:
        meta = fetch_metadata(args.url)
        try:
            segments, language, attempts = fetch_transcript_with_retry(video_id, languages)
            if attempts > 1:
                notes.append(f"Retry succeeded (attempt {attempts}/{MAX_RETRIES}).")
        except TranscriptsDisabled:
            sys.exit("Error: Transcripts disabled for this video.")
        except NoTranscriptFound:
            sys.exit(f"Error: No transcript for languages {languages}.")
        except Exception as e:
            sys.exit(f"Error: Rate-limited. Tried {MAX_RETRIES}x. Wait and retry.\n{e}")
        save_to_cache(video_id, segments, language, meta)

    # Manual overrides
    if args.title: meta["title"] = args.title
    if args.channel: meta["channel"] = args.channel
    if args.date:
        try:
            datetime.strptime(args.date, "%Y-%m-%d")
            meta["published"] = args.date
        except ValueError:
            sys.exit(f"Error: --date must be YYYY-MM-DD, got '{args.date}'")
    if args.title or args.channel or args.date:
        meta["source"] = meta.get("source", "none") + "+manual"

    meta["missing"] = [k for k in ("title", "channel", "published") if not meta.get(k)]
    meta["complete"] = len(meta["missing"]) == 0

    body = raw_text(segments) if args.no_clean else clean_text(segments)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = (meta.get("published") or datetime.now().strftime("%Y-%m-%d"))[:10]
    name = safe_filename(meta["title"]) if meta.get("title") else video_id
    out_path = out_dir / f"{prefix}_{name}.md"

    write_md(out_path, args.url, meta, language, body, notes)
    print(f"  Saved: {out_path} ({len(segments)} segments)")
    if meta["missing"]:
        print(f"  Missing: {', '.join(meta['missing'])}")


if __name__ == "__main__":
    main()
