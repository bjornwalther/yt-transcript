#!/usr/bin/env python3
"""MCP server: YouTube transcripts as AI context. Cache, retry, metadata.

Install:  uvx --from git+https://github.com/bjornwalther/yt-transcript yt-transcript-mcp
Or run:   uv run mcp_server.py
"""

import asyncio
import hashlib
import json
import time
from datetime import datetime

from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

from yt_transcript import (
    extract_video_id, fetch_metadata, fetch_transcript_with_retry,
    clean_text, raw_text, load_from_cache, save_to_cache,
    MAX_RETRIES, RETRY_DELAY_SECONDS,
)

server = Server("yt-transcript")

# ── Error codes ─────────────────────────────────────────────────────────────────────

INVALID_URL = "INVALID_URL"
TRANSCRIPT_NOT_AVAILABLE = "TRANSCRIPT_NOT_AVAILABLE"
LANGUAGE_NOT_AVAILABLE = "LANGUAGE_NOT_AVAILABLE"
RATE_LIMITED = "RATE_LIMITED"
YOUTUBE_IP_BLOCKED = "YOUTUBE_IP_BLOCKED"
VIDEO_UNAVAILABLE = "VIDEO_UNAVAILABLE"
PO_TOKEN_REQUIRED = "PO_TOKEN_REQUIRED"
METADATA_FETCH_FAILED = "METADATA_FETCH_FAILED"

# ── Tool schema ─────────────────────────────────────────────────────────────────────

TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "description": "YouTube video URL (any format)"},
        "languages": {"type": "string", "description": "Comma-separated language codes (default: sv,en)", "default": "sv,en"},
        "include_timestamps": {"type": "boolean", "description": "Include HH:MM:SS per line in transcript_text", "default": False},
        "format": {"type": "string", "enum": ["json", "markdown"], "description": "Response format (default: json)", "default": "json"},
        "title": {"type": "string", "description": "Manual title override"},
        "channel": {"type": "string", "description": "Manual channel override"},
        "published": {"type": "string", "description": "Manual publish date (YYYY-MM-DD)"},
        "bypass_cache": {"type": "boolean", "description": "Force fresh fetch", "default": False},
    },
    "required": ["url"],
}


# ── Helpers ─────────────────────────────────────────────────────────────────────────

def _content_hash(segments: list) -> str:
    """SHA256 of the canonical output segments (start/end/text) for reproducibility.
    Consumers can recompute this from the returned segments array."""
    canonical = json.dumps(segments, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _cache_age_days(cached_at: str) -> int:
    """Days since cache entry was created."""
    try:
        cached_date = datetime.strptime(cached_at, "%Y-%m-%d")
        return (datetime.now() - cached_date).days
    except (ValueError, TypeError):
        return -1


def _transcript_duration(segments_raw: list) -> float | None:
    """Total transcript duration in seconds from first to last segment end."""
    if not segments_raw:
        return None
    last = segments_raw[-1]
    return round(last["start"] + last["duration"], 2)


def _classify_exception(e: Exception, actual_attempts: int) -> tuple[str, str, int]:
    """Map exception to (error_code, human message, retry_count).
    retry_count = actual_attempts - 1 (first attempt is not a retry)."""
    cls = type(e).__name__
    msg = str(e).lower()

    # Classify by exception class name first (reliable), message second (fallback)
    if cls in ("PoTokenRequired",):
        return PO_TOKEN_REQUIRED, (
            "This video requires a Proof-of-Origin token (PO token) that cannot be "
            "generated without a JavaScript runtime. This is a YouTube restriction, "
            "not an IP or rate-limit issue. Retry will not help."
        ), 0

    if cls in ("TranscriptsDisabled",):
        return TRANSCRIPT_NOT_AVAILABLE, "Transcripts are disabled for this video.", 0

    if cls in ("NoTranscriptFound",):
        return LANGUAGE_NOT_AVAILABLE, "No transcript found for the requested languages.", 0

    if cls in ("VideoUnavailable",):
        return VIDEO_UNAVAILABLE, "Video is unavailable, private, or removed.", 0

    if cls in ("IpBlocked",):
        return YOUTUBE_IP_BLOCKED, (
            "YouTube has blocked your IP address. Use residential proxies or wait. "
            "This is distinct from rate-limiting."
        ), actual_attempts - 1

    if cls in ("RequestBlocked",):
        return YOUTUBE_IP_BLOCKED, (
            "YouTube is blocking requests from your IP. This is typically caused by "
            "cloud provider IPs or excessive request volume."
        ), actual_attempts - 1

    # Fallback: parse message for cases where exception class is generic
    if "disabled" in msg:
        return TRANSCRIPT_NOT_AVAILABLE, "Transcripts are disabled for this video.", 0
    if "no longer available" in msg or "unavailable" in msg or "private" in msg:
        return VIDEO_UNAVAILABLE, "Video is unavailable, private, or removed.", 0
    if "po token" in msg:
        return PO_TOKEN_REQUIRED, (
            "This video requires a Proof-of-Origin token. Retry will not help."
        ), 0
    if "429" in msg:
        return RATE_LIMITED, (
            f"YouTube returned 429 Too Many Requests. "
            f"Tried {actual_attempts}x over ~{(actual_attempts - 1) * RETRY_DELAY_SECONDS}s. "
            f"Wait a few minutes and retry."
        ), actual_attempts - 1
    if "blocked" in msg or "ipblocked" in msg:
        return YOUTUBE_IP_BLOCKED, (
            "YouTube is blocking requests from your IP."
        ), actual_attempts - 1

    return RATE_LIMITED, (
        f"Request failed after {actual_attempts} attempts. "
        f"This is likely a rate-limit or network issue."
    ), actual_attempts - 1


def _error_response(error_code: str, message: str, video_id: str | None,
                     url: str, retry_count: int, fallback_attempted: bool,
                     fetch_duration: float) -> dict:
    """Build structured error response."""
    return {
        "is_error": True,
        "error_code": error_code,
        "error_message": message,
        "video_id": video_id,
        "url": url,
        "retry_count": retry_count,
        "fallback_attempted": fallback_attempted,
        "fetch_duration_seconds": fetch_duration,
    }


def _format_error(resp: dict, fmt: str) -> str:
    """Format error response as JSON or markdown."""
    if fmt == "markdown":
        return (
            f"# Error: {resp['error_code']}\n\n"
            f"{resp['error_message']}\n\n"
            f"url: {resp['url']}\n"
            f"video_id: {resp.get('video_id') or '\u2014'}\n"
            f"retry_count: {resp['retry_count']}\n"
            f"fallback_attempted: {resp['fallback_attempted']}\n"
            f"fetch_duration_seconds: {resp['fetch_duration_seconds']}\n"
        )
    return json.dumps(resp, ensure_ascii=False, indent=2)


def _build_segments(raw_segments: list) -> list[dict]:
    """Build canonical output segments with rounded start/end."""
    return [
        {
            "text": s["text"],
            "start": round(s["start"], 2),
            "end": round(s["start"] + s["duration"], 2),
        }
        for s in raw_segments
    ]


# ── Tool ─────────────────────────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [types.Tool(
        name="fetch_transcript",
        description=(
            "Fetch a YouTube video transcript. Returns structured JSON (default) "
            "or markdown. Cached locally, retries on rate-limit. "
            "JSON includes metadata, segments with timestamps, provenance flags, "
            "caption type (auto-generated vs manual), and a content hash for "
            "reproducibility."
        ),
        inputSchema=TOOL_SCHEMA,
    )]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name != "fetch_transcript":
        raise ValueError(f"Unknown tool: {name}")

    url = arguments["url"]
    languages = [l.strip() for l in arguments.get("languages", "sv,en").split(",")]
    timestamps = arguments.get("include_timestamps", False)
    fmt = arguments.get("format", "json")
    bypass = arguments.get("bypass_cache", False)
    overrides = {k: arguments.get(k, "") for k in ("title", "channel", "published")}

    fetch_start = time.monotonic()

    # ── Parse video ID ──────────────────────────────────────────────────────
    try:
        video_id = extract_video_id(url)
    except ValueError:
        fetch_duration = round(time.monotonic() - fetch_start, 2)
        resp = _error_response(INVALID_URL, f"Cannot extract video ID from: {url}",
                               None, url, 0, False, fetch_duration)
        return [types.TextContent(type="text", text=_format_error(resp, fmt))]

    warnings = []
    retry_count = 0
    is_generated = False
    fallback_attempted = False

    # ── Cache check (validates language match) ──────────────────────────────
    cached = None if bypass else load_from_cache(video_id, languages)

    if cached:
        segments_raw = cached["segments"]
        language = cached["language"]
        is_generated = cached.get("is_generated", False)
        meta_fields = cached.get("meta", {})
        meta_sources = cached.get("meta_sources", {})
        cache_hit = True
        cache_age = _cache_age_days(cached.get("cached_at", ""))
        fetched_at = cached.get("cached_at", "")
    else:
        cache_hit = False
        cache_age = 0
        fetched_at = datetime.now().strftime("%Y-%m-%d")

        # ── Metadata ────────────────────────────────────────────────────────────
        try:
            meta = await asyncio.to_thread(fetch_metadata, url)
            meta_fields = meta["fields"]
            meta_sources = meta["sources"]
        except Exception:
            meta_fields = {"title": "", "channel": "", "published": ""}
            meta_sources = {"title": "none", "channel": "none", "published": "none"}

        # Detect metadata failure: all sources are "none"
        if all(v == "none" for v in meta_sources.values()):
            warnings.append({
                "code": METADATA_FETCH_FAILED,
                "message": "All metadata sources failed. Title, channel, and date may be missing.",
            })
        elif any(v == "none" for v in meta_sources.values()):
            failed_fields = [k for k, v in meta_sources.items() if v == "none" and not meta_fields.get(k)]
            if failed_fields:
                warnings.append({
                    "code": METADATA_FETCH_FAILED,
                    "message": f"Metadata unavailable for: {', '.join(failed_fields)}.",
                })

        # ── Transcript ──────────────────────────────────────────────────────────
        try:
            segments_raw, language, is_generated, fallback_attempted, attempts = (
                await asyncio.to_thread(fetch_transcript_with_retry, video_id, languages)
            )
            retry_count = attempts - 1
        except Exception as e:
            fetch_duration = round(time.monotonic() - fetch_start, 2)
            error_code, error_msg, err_retries = _classify_exception(e, MAX_RETRIES)
            resp = _error_response(error_code, error_msg, video_id, url,
                                   err_retries, fallback_attempted, fetch_duration)
            return [types.TextContent(type="text", text=_format_error(resp, fmt))]

        save_to_cache(video_id, segments_raw, language,
                      {"fields": meta_fields, "sources": meta_sources}, is_generated)

    fetch_duration = round(time.monotonic() - fetch_start, 2)

    # ── Apply overrides ─────────────────────────────────────────────────────
    for k, v in overrides.items():
        if v:
            meta_fields[k] = v
            meta_sources[k] = "manual"

    # ── Check language fallback ─────────────────────────────────────────────
    language_fallback = language not in languages
    if language_fallback:
        fallback_attempted = True
        warnings.append({
            "code": "LANGUAGE_FALLBACK",
            "message": f"Requested {','.join(languages)}, got {language}.",
        })

    # ── Provenance warning for auto-generated captions ──────────────────────
    if is_generated:
        warnings.append({
            "code": "AUTO_GENERATED",
            "message": (
                "This transcript is auto-generated by YouTube speech recognition. "
                "It may contain errors and should not be used as a verbatim quote "
                "without verification against the audio."
            ),
        })

    # ── Build response ──────────────────────────────────────────────────────
    segments_out = _build_segments(segments_raw)
    missing = [k for k in ("title", "channel", "published") if not meta_fields.get(k)]
    duration = _transcript_duration(segments_raw)

    response = {
        "is_error": False,
        "video_id": video_id,
        "url": url,
        "title": meta_fields.get("title") or None,
        "channel": meta_fields.get("channel") or None,
        "published": meta_fields.get("published") or None,
        "language": language,
        "language_requested": ",".join(languages),
        "language_fallback": language_fallback,
        "caption_type": "auto-generated" if is_generated else "manual",
        "segment_count": len(segments_out),
        "transcript_duration_seconds": duration,
        "metadata_sources": meta_sources if meta_sources else None,
        "metadata_missing": missing if missing else None,
        "cache_hit": cache_hit,
        "cache_age_days": cache_age if cache_hit else None,
        "fetched_at": fetched_at,
        "fetch_duration_seconds": fetch_duration,
        "retry_count": retry_count,
        "fallback_attempted": fallback_attempted,
        "content_hash": _content_hash(segments_out),
        "warnings": warnings if warnings else None,
        "transcript_text": raw_text(segments_raw) if timestamps else clean_text(segments_raw),
        "segments": segments_out,
    }

    # ── Format output ───────────────────────────────────────────────────────
    if fmt == "markdown":
        body = response["transcript_text"]
        notes = []
        if cache_hit:
            notes.append(f"Served from local cache (fetched {fetched_at}).")
        if retry_count > 0:
            notes.append(f"Retry succeeded (attempt {retry_count + 1}/{MAX_RETRIES}).")
        for w in (warnings or []):
            notes.append(f"Warning [{w['code']}]: {w['message']}")

        notes_str = "".join(f"note: {n}\n" for n in notes)
        missing_str = f"missing: {', '.join(missing)}\n" if missing else ""

        md = (
            f"# {meta_fields.get('title') or 'Untitled'}\n\n"
            f"source: {url}\n"
            f"channel: {meta_fields.get('channel') or '\u2014'}\n"
            f"published: {meta_fields.get('published') or '\u2014'}\n"
            f"language: {language}\n"
            f"caption_type: {response['caption_type']}\n"
            f"segments: {len(segments_out)}\n"
            f"duration: {duration}s\n"
            f"content_hash: {response['content_hash']}\n"
            f"{missing_str}{notes_str}"
            f"fetched: {fetched_at}\n\n"
            f"---\n\n## Transcript\n\n{body}\n"
        )
        return [types.TextContent(type="text", text=md)]

    return [types.TextContent(type="text", text=json.dumps(response, ensure_ascii=False, indent=2))]


# ── Serve ────────────────────────────────────────────────────────────────────────────

async def _serve():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main():
    """Sync entry point for uvx/pip."""
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
