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

# ── Error codes ─────────────────────────────────────────────────────────────────────────────

INVALID_URL = "INVALID_URL"
TRANSCRIPT_NOT_AVAILABLE = "TRANSCRIPT_NOT_AVAILABLE"
LANGUAGE_NOT_AVAILABLE = "LANGUAGE_NOT_AVAILABLE"
RATE_LIMITED = "RATE_LIMITED"
VIDEO_UNAVAILABLE = "VIDEO_UNAVAILABLE"
PO_TOKEN_REQUIRED = "PO_TOKEN_REQUIRED"
METADATA_FETCH_FAILED = "METADATA_FETCH_FAILED"

# ── Tool schema ─────────────────────────────────────────────────────────────────────────────

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


# ── Helpers ────────────────────────────────────────────────────────────────────────────────

def _content_hash(segments: list) -> str:
    """SHA256 of the segment array for reproducibility."""
    canonical = json.dumps(segments, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _cache_age_days(cached_at: str) -> int:
    """Days since cache entry was created."""
    try:
        cached_date = datetime.strptime(cached_at, "%Y-%m-%d")
        return (datetime.now() - cached_date).days
    except (ValueError, TypeError):
        return -1


def _classify_exception(e: Exception) -> tuple[str, str]:
    """Map exception to (error_code, human message)."""
    name = type(e).__name__
    msg = str(e).lower()

    if name == "PoTokenRequired" or "po token" in msg or "potokenrequired" in name.lower():
        return PO_TOKEN_REQUIRED, (
            "This video requires a Proof-of-Origin token (PO token) that cannot be "
            "generated without a JavaScript runtime. This is a YouTube restriction, "
            "not an IP or rate-limit issue. Retry will not help."
        )
    if "disabled" in msg:
        return TRANSCRIPT_NOT_AVAILABLE, "Transcripts are disabled for this video."
    if "no transcript" in msg or "notranscriptfound" in name.lower():
        return LANGUAGE_NOT_AVAILABLE, "No transcript found for the requested languages."
    if "429" in msg or "rate" in msg or "blocked" in msg or "ipblocked" in name.lower():
        return RATE_LIMITED, (
            f"YouTube is rate-limiting your IP. "
            f"Tried {MAX_RETRIES}x over ~{MAX_RETRIES * RETRY_DELAY_SECONDS}s. "
            f"Wait a few minutes and retry."
        )
    if "unavailable" in msg or "private" in msg or "removed" in msg:
        return VIDEO_UNAVAILABLE, "Video is unavailable, private, or removed."
    return RATE_LIMITED, (
        f"Request failed after {MAX_RETRIES} attempts. "
        f"This is likely a rate-limit or network issue."
    )


def _error_response(error_code: str, message: str, video_id: str | None,
                     url: str, retry_count: int, fallback_attempted: bool) -> dict:
    """Build structured error response."""
    return {
        "is_error": True,
        "error_code": error_code,
        "error_message": message,
        "video_id": video_id,
        "url": url,
        "retry_count": retry_count,
        "fallback_attempted": fallback_attempted,
    }


def _build_segments(raw_segments: list) -> list[dict]:
    """Add end time to each segment."""
    return [
        {
            "text": s["text"],
            "start": round(s["start"], 2),
            "end": round(s["start"] + s["duration"], 2),
        }
        for s in raw_segments
    ]


# ── Tool ───────────────────────────────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [types.Tool(
        name="fetch_transcript",
        description=(
            "Fetch a YouTube video transcript. Returns structured JSON (default) "
            "or markdown. Cached locally, retries on rate-limit. "
            "JSON includes metadata, segments with timestamps, provenance flags, "
            "and a content hash for reproducibility."
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

    # ── Parse video ID ──────────────────────────────────────────────────────────
    try:
        video_id = extract_video_id(url)
    except ValueError:
        resp = _error_response(INVALID_URL, f"Cannot extract video ID from: {url}",
                               None, url, 0, False)
        return [types.TextContent(type="text", text=json.dumps(resp, ensure_ascii=False))]

    warnings = []
    retry_count = 0
    fallback_attempted = False
    fetch_start = time.monotonic()

    # ── Cache check ─────────────────────────────────────────────────────────────
    cached = None if bypass else load_from_cache(video_id)

    if cached:
        segments_raw = cached["segments"]
        language = cached["language"]
        meta = cached["meta"]
        cache_hit = True
        cache_age = _cache_age_days(cached.get("cached_at", ""))
        fetched_at = cached.get("cached_at", "")
    else:
        cache_hit = False
        cache_age = 0
        fetched_at = datetime.now().strftime("%Y-%m-%d")

        # ── Metadata ────────────────────────────────────────────────────────────────
        try:
            meta = await asyncio.to_thread(fetch_metadata, url)
        except Exception:
            meta = {"title": "", "channel": "", "published": "", "source": "none"}
            warnings.append({
                "code": METADATA_FETCH_FAILED,
                "message": "Metadata fetch failed. Transcript may still be available.",
            })

        # ── Transcript ──────────────────────────────────────────────────────────────
        try:
            segments_raw, language, attempts = await asyncio.to_thread(
                fetch_transcript_with_retry, video_id, languages
            )
            retry_count = attempts - 1
        except Exception as e:
            fetch_duration = round(time.monotonic() - fetch_start, 2)
            error_code, error_msg = _classify_exception(e)
            resp = _error_response(error_code, error_msg, video_id, url,
                                   MAX_RETRIES if error_code == RATE_LIMITED else 0,
                                   fallback_attempted)
            resp["fetch_duration_seconds"] = fetch_duration
            return [types.TextContent(type="text", text=json.dumps(resp, ensure_ascii=False))]

        save_to_cache(video_id, segments_raw, language, meta)

    fetch_duration = round(time.monotonic() - fetch_start, 2)

    # ── Apply overrides ───────────────────────────────────────────────────────
    for k, v in overrides.items():
        if v:
            meta[k] = v
            if "manual" not in meta.get("source", ""):
                meta["source"] = meta.get("source", "none") + "+manual"

    # ── Check language fallback ─────────────────────────────────────────────────
    language_fallback = language not in languages
    if language_fallback:
        warnings.append({
            "code": "LANGUAGE_FALLBACK",
            "message": f"Requested {','.join(languages)}, got {language}.",
        })

    # ── Build response ─────────────────────────────────────────────────────────
    segments_out = _build_segments(segments_raw)
    missing = [k for k in ("title", "channel", "published") if not meta.get(k)]

    response = {
        "is_error": False,
        "video_id": video_id,
        "url": url,
        "title": meta.get("title") or None,
        "channel": meta.get("channel") or None,
        "published": meta.get("published") or None,
        "language": language,
        "language_requested": ",".join(languages),
        "language_fallback": language_fallback,
        "segment_count": len(segments_out),
        "metadata_source": meta.get("source", "unknown"),
        "metadata_missing": missing if missing else None,
        "cache_hit": cache_hit,
        "cache_age_days": cache_age if cache_hit else None,
        "fetched_at": fetched_at,
        "fetch_duration_seconds": fetch_duration,
        "retry_count": retry_count,
        "content_hash": _content_hash(segments_raw),
        "warnings": warnings if warnings else None,
        "transcript_text": raw_text(segments_raw) if timestamps else clean_text(segments_raw),
        "segments": segments_out,
    }

    # ── Format output ───────────────────────────────────────────────────────────
    if fmt == "markdown":
        body = response["transcript_text"]
        notes = []
        if cache_hit:
            notes.append(f"Served from local cache (fetched {fetched_at}).")
        if retry_count > 0:
            notes.append(f"Retry succeeded (attempt {retry_count + 1}/{MAX_RETRIES}).")
        for w in (warnings or []):
            notes.append(f"Warning: {w['message']}")

        notes_str = "".join(f"note: {n}\n" for n in notes)
        missing_str = f"missing: {', '.join(missing)}\n" if missing else ""

        md = (
            f"# {meta.get('title') or 'Untitled'}\n\n"
            f"source: {url}\n"
            f"channel: {meta.get('channel') or '\u2014'}\n"
            f"published: {meta.get('published') or '\u2014'}\n"
            f"language: {language}\n"
            f"segments: {len(segments_out)}\n"
            f"content_hash: {response['content_hash']}\n"
            f"metadata_source: {meta.get('source', 'unknown')}\n"
            f"{missing_str}{notes_str}"
            f"fetched: {fetched_at}\n\n"
            f"---\n\n## Transcript\n\n{body}\n"
        )
        return [types.TextContent(type="text", text=md)]

    return [types.TextContent(type="text", text=json.dumps(response, ensure_ascii=False, indent=2))]


# ── Serve ──────────────────────────────────────────────────────────────────────────────────

async def _serve():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main():
    """Sync entry point for uvx/pip."""
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
