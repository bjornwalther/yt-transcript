#!/usr/bin/env python3
"""YouTube transcript fetcher. CLI + shared logic for MCP server."""

import argparse, hashlib, json, math, re, sys, time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from urllib.request import urlopen, Request
from urllib.error import URLError

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import NoTranscriptFound, TranscriptsDisabled

_OPTIONAL_ERRORS = {}
for _name in ("PoTokenRequired", "VideoUnavailable", "VideoUnplayable",
              "InvalidVideoId", "AgeRestricted", "IpBlocked", "RequestBlocked",
              "NotTranslatable", "TranslationLanguageNotAvailable",
              "YouTubeRequestFailed", "YouTubeDataUnparsable",
              "FailedToCreateConsentCookie", "CookieError", "CookieInvalid",
              "CookiePathInvalid", "CouldNotRetrieveTranscript",
              "YouTubeTranscriptApiException"):
    try:
        _mod = __import__("youtube_transcript_api._errors", fromlist=[_name])
        _OPTIONAL_ERRORS[_name] = getattr(_mod, _name)
    except (ImportError, AttributeError):
        pass

try:
    from pytubefix import YouTube
except ImportError:
    YouTube = None

_EM_DASH = "\u2014"

CACHE_DIR = Path.home() / ".cache" / "yt-transcript"
CACHE_VERSION = 2
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 3
_RETRYABLE_CLASS_NAMES = {"YouTubeRequestFailed"}
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com",
                  "youtu.be", "www.youtu.be", "music.youtube.com"}
_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_retryable(exc: Exception) -> bool:
    return type(exc).__name__ in _RETRYABLE_CLASS_NAMES


def extract_video_id(url: str) -> str:
    if "://" not in url and not url.startswith("//"):
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Cannot extract video ID from: {url}")
    host = parsed.netloc.lower()
    if host and host not in _YOUTUBE_HOSTS:
        raise ValueError(f"Cannot extract video ID from: {url}")
    vid = None
    if host in ("youtu.be", "www.youtu.be"):
        vid = parsed.path.lstrip("/").split("/")[0] or None
    if vid is None:
        qs = parse_qs(parsed.query)
        if "v" in qs:
            vid = qs["v"][0]
    if vid is None:
        m = re.search(r"/(shorts|embed|v)/([^/?&]+)", parsed.path)
        if m:
            vid = m.group(2)
    if vid is None:
        raise ValueError(f"Cannot extract video ID from: {url}")
    if not _VIDEO_ID_PATTERN.match(vid):
        raise ValueError(f"Cannot extract video ID from: {url}")
    return vid


_CACHE_REQUIRED_FIELDS = {"cache_version": int, "segments": list,
                          "language": str, "cached_at": str}


def _validate_segment(seg: object) -> bool:
    if not isinstance(seg, dict):
        return False
    if not isinstance(seg.get("text"), str):
        return False
    start = seg.get("start")
    if isinstance(start, bool) or not isinstance(start, (int, float)):
        return False
    if not math.isfinite(start) or start < 0:
        return False
    dur = seg.get("duration")
    if isinstance(dur, bool) or not isinstance(dur, (int, float)):
        return False
    if not math.isfinite(dur) or dur < 0:
        return False
    return True


def _cache_path(video_id: str, languages: list) -> Path:
    canonical = json.dumps({"video_id": video_id, "languages": languages}, sort_keys=True)
    h = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / f"{h}.json"


def load_from_cache(video_id: str, languages: list) -> dict | None:
    path = _cache_path(video_id, languages)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("cache_version") != CACHE_VERSION:
        return None
    for field, expected_type in _CACHE_REQUIRED_FIELDS.items():
        if not isinstance(data.get(field), expected_type):
            return None
    for seg in data["segments"]:
        if not _validate_segment(seg):
            return None
    if "is_generated" in data and not isinstance(data["is_generated"], bool):
        return None
    if "meta" in data and not isinstance(data["meta"], dict):
        return None
    if "meta_sources" in data and not isinstance(data["meta_sources"], dict):
        return None
    meta = data.get("meta", {})
    if isinstance(meta, dict):
        for k in ("title", "channel", "published"):
            if k in meta and not isinstance(meta[k], str):
                return None
    ms = data.get("meta_sources", {})
    if isinstance(ms, dict):
        for v in ms.values():
            if not isinstance(v, str):
                return None
    if not _DATE_PATTERN.match(data.get("cached_at", "")):
        return None
    rl = data.get("requested_languages")
    if rl is not None:
        if not isinstance(rl, list) or not all(isinstance(x, str) for x in rl):
            return None
    return data


def save_to_cache(video_id: str, segments: list, language: str,
                  languages: list, meta: dict, is_generated: bool = False) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data = {"cache_version": CACHE_VERSION, "segments": segments,
            "language": language, "requested_languages": languages,
            "is_generated": is_generated,
            "meta": meta.get("fields", meta),
            "meta_sources": meta.get("sources", {}),
            "cached_at": datetime.now().strftime("%Y-%m-%d")}
    _cache_path(video_id, languages).write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _fetch_oembed(url: str) -> dict:
    try:
        req = Request(f"https://www.youtube.com/oembed?url={url}&format=json",
                      headers={"User-Agent": "yt-transcript/1.2"})
        with urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode())
        return {"title": d.get("title", ""), "channel": d.get("author_name", ""), "published": ""}
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
    if ptf["published"]:
        fields["published"], sources["published"] = ptf["published"], "pytubefix"
    else:
        fields["published"], sources["published"] = "", "none"
    missing = [k for k in ("title", "channel", "published") if not fields[k]]
    return {"fields": fields, "sources": sources, "missing": missing, "complete": len(missing) == 0}


def fetch_transcript(video_id: str, languages: list) -> tuple:
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
    segments = [{"text": s.text, "start": s.start, "duration": s.duration} for s in fetched]
    return segments, t.language_code, t.is_generated, fallback_used


def fetch_transcript_with_retry(video_id: str, languages: list) -> tuple:
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            segs, lang, is_gen, fallback = fetch_transcript(video_id, languages)
            return segs, lang, is_gen, fallback, attempt
        except Exception as e:
            e.actual_attempts = attempt
            if not _is_retryable(e):
                raise
            last_err = e
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)
    last_err.actual_attempts = MAX_RETRIES
    raise last_err


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
        for s in segments)


def safe_filename(text: str, max_len: int = 80) -> str:
    return re.sub(r"\s+", "_", re.sub(r'[<>:"/\\|?*]', "", text.strip()))[:max_len]


def write_md(path: Path, url: str, meta: dict, language: str, body: str, notes: list = None) -> None:
    fields = meta.get("fields", meta)
    missing = meta.get("missing", [k for k in ("title", "channel", "published") if not fields.get(k)])
    notes_str = "".join(f"note: {n}\n" for n in (notes or []))
    missing_str = f"missing: {', '.join(missing)}\n" if missing else ""
    content = (f"# {fields.get('title') or 'Untitled'}\n\n"
               f"source: {url}\nchannel: {fields.get('channel') or _EM_DASH}\n"
               f"published: {fields.get('published') or _EM_DASH}\nlanguage: {language}\n"
               f"{missing_str}{notes_str}"
               f"fetched: {datetime.now().strftime('%Y-%m-%d')}\n\n---\n\n## Transcript\n\n{body}\n")
    path.write_text(content, encoding="utf-8")


def main():
    p = argparse.ArgumentParser(description="YouTube transcript -> markdown")
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
        meta = {"fields": cached.get("meta", {}), "sources": cached.get("meta_sources", {}),
                "missing": [], "complete": True}
        meta["missing"] = [k for k in ("title", "channel", "published") if not meta["fields"].get(k)]
        meta["complete"] = len(meta["missing"]) == 0
        notes.append(f"From cache (fetched {cached['cached_at']}).")
        print(f"  Cache HIT ({cached['cached_at']})")
    else:
        meta = fetch_metadata(args.url)
        try:
            segments, language, is_gen, fallback, attempts = fetch_transcript_with_retry(video_id, languages)
            if attempts > 1:
                notes.append(f"Retry succeeded (attempt {attempts}/{MAX_RETRIES}).")
            if fallback:
                notes.append(f"Language fallback: requested {languages}, got {language}.")
        except Exception as e:
            cls = type(e).__name__
            if cls == "TranscriptsDisabled":
                sys.exit("Error: Transcripts disabled for this video.")
            if cls == "NoTranscriptFound":
                sys.exit(f"Error: No transcript for languages {languages}.")
            if cls == "PoTokenRequired":
                sys.exit("Error: Video requires PO token. Cannot fetch without JS runtime.")
            if cls == "AgeRestricted":
                sys.exit("Error: Video is age-restricted. Cookie auth not supported.")
            if cls in ("VideoUnavailable", "VideoUnplayable", "InvalidVideoId"):
                sys.exit(f"Error: Video unavailable ({cls}).")
            sys.exit(f"Error: {cls}: {e}")
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
    body = raw_text(segments) if args.no_clean else clean_text(segments)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = (fields.get("published") or datetime.now().strftime("%Y-%m-%d"))[:10]
    name = safe_filename(fields["title"]) if fields.get("title") else video_id
    out_path = out_dir / f"{prefix}_{name}.md"
    write_md(out_path, args.url, meta, language, body, notes)
    print(f"  Saved: {out_path} ({len(segments)} segments)")


if __name__ == "__main__":
    main()
