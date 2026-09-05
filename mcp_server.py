#!/usr/bin/env python3
"""MCP server: YouTube transcripts as AI context. Cache, retry, metadata.

Install:  uvx --from git+https://github.com/bjornwalther/yt-transcript yt-transcript-mcp
Or run:   uv run mcp_server.py
"""

import asyncio
import hashlib
import json
import re
import time
from datetime import datetime

from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

from yt_transcript import (
    extract_video_id, fetch_metadata, fetch_transcript_with_retry,
    clean_text, raw_text, load_from_cache, save_to_cache,
    MAX_RETRIES, RETRY_DELAY_SECONDS, _is_retryable,
)

server = Server("yt-transcript")

_EM_DASH = "\u2014"

# -- Error codes (CONTRACTS.md section 1) -------------------------------------

INVALID_URL = "INVALID_URL"
TRANSCRIPT_NOT_AVAILABLE = "TRANSCRIPT_NOT_AVAILABLE"
LANGUAGE_NOT_AVAILABLE = "LANGUAGE_NOT_AVAILABLE"
RATE_LIMITED = "RATE_LIMITED"
YOUTUBE_IP_BLOCKED = "YOUTUBE_IP_BLOCKED"
VIDEO_UNAVAILABLE = "VIDEO_UNAVAILABLE"
PO_TOKEN_REQUIRED = "PO_TOKEN_REQUIRED"
METADATA_FETCH_FAILED = "METADATA_FETCH_FAILED"
CACHE_WRITE_FAILED = "CACHE_WRITE_FAILED"

_EXCEPTION_CODE_MAP = {
    "TranscriptsDisabled": TRANSCRIPT_NOT_AVAILABLE,
    "NoTranscriptFound": LANGUAGE_NOT_AVAILABLE,
    "VideoUnavailable": VIDEO_UNAVAILABLE,
    "VideoUnplayable": VIDEO_UNAVAILABLE,
    "InvalidVideoId": INVALID_URL,
    "AgeRestricted": VIDEO_UNAVAILABLE,
    "IpBlocked": YOUTUBE_IP_BLOCKED,
    "RequestBlocked": YOUTUBE_IP_BLOCKED,
    "PoTokenRequired": PO_TOKEN_REQUIRED,
    "NotTranslatable": LANGUAGE_NOT_AVAILABLE,
    "TranslationLanguageNotAvailable": LANGUAGE_NOT_AVAILABLE,
    "YouTubeRequestFailed": RATE_LIMITED,
    "YouTubeDataUnparsable": RATE_LIMITED,
    "FailedToCreateConsentCookie": RATE_LIMITED,
    "CookieError": RATE_LIMITED,
    "CookieInvalid": RATE_LIMITED,
    "CookiePathInvalid": RATE_LIMITED,
    "CouldNotRetrieveTranscript": RATE_LIMITED,
    "YouTubeTranscriptApiException": RATE_LIMITED,
}

# -- Language validation ------------------------------------------------------

_LANG_PATTERN = re.compile(r"^[a-zA-Z0-9-]{1,10}$")
_MAX_LANGUAGES = 20


def _validate_languages(raw: str) -> list[str]:
    if not raw or not raw.strip():
        return ["en"]
    codes = [c.strip() for c in raw.split(",") if c.strip()]
    valid = [c for c in codes if _LANG_PATTERN.match(c)][:_MAX_LANGUAGES]
    return valid if valid else ["en"]

# -- Tool schema --------------------------------------------------------------

TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "description": "YouTube video URL (watch, youtu.be, shorts, embed)"},
        "languages": {"type": "string", "description": "Comma-separated language codes in priority order (default: sv,en)", "default": "sv,en"},
        "output": {"type": "string", "enum": ["segments", "text", "both"], "description": "Transcript representation. 'segments' (default): array of {text,start,end}. 'text': readable string. 'both': both. Markdown always renders text.", "default": "segments"},
        "include_timestamps": {"type": "boolean", "description": "Include HH:MM:SS per line in text output", "default": False},
        "format": {"type": "string", "enum": ["json", "markdown"], "description": "Response format. JSON is compact (no indent).", "default": "json"},
        "title": {"type": "string", "description": "Manual title override"},
        "channel": {"type": "string", "description": "Manual channel override"},
        "published": {"type": "string", "description": "Manual date (YYYY-MM-DD)"},
        "bypass_cache": {"type": "boolean", "description": "Force fresh fetch", "default": False},
    },
    "required": ["url"],
    "additionalProperties": False,
}

# -- Helpers ------------------------------------------------------------------

def _content_hash(segments: list) -> str:
    canonical = json.dumps(segments, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

def _cache_age_days(cached_at: str) -> int:
    try:
        return (datetime.now() - datetime.strptime(cached_at, "%Y-%m-%d")).days
    except (ValueError, TypeError):
        return -1

def _transcript_duration(segments_raw: list) -> float | None:
    if not segments_raw:
        return None
    last = segments_raw[-1]
    return round(last["start"] + last["duration"], 2)

def _build_metadata_warnings(meta_fields: dict, meta_sources: dict) -> list[dict]:
    """Generate metadata warnings from final state (AFTER cache/fetch merge)."""
    warnings = []
    if not meta_sources:
        return warnings
    if all(v == "none" for v in meta_sources.values()):
        warnings.append({"code": METADATA_FETCH_FAILED, "message": "All metadata sources failed."})
    else:
        failed = [k for k, v in meta_sources.items() if v == "none" and not meta_fields.get(k)]
        if failed:
            warnings.append({"code": METADATA_FETCH_FAILED, "message": f"Metadata unavailable for: {', '.join(failed)}."})
    return warnings

def _classify_exception(e: Exception) -> tuple[str, str, int]:
    cls = type(e).__name__
    attempts = getattr(e, "actual_attempts", 1)
    retries = max(0, attempts - 1)
    code = _EXCEPTION_CODE_MAP.get(cls)
    if code:
        is_transient = _is_retryable(e)
        return code, str(e) or cls, retries if is_transient else 0
    msg = str(e).lower()
    if "disabled" in msg:
        return TRANSCRIPT_NOT_AVAILABLE, str(e), 0
    if "unavailable" in msg or "private" in msg or "no longer available" in msg:
        return VIDEO_UNAVAILABLE, str(e), 0
    if "po token" in msg:
        return PO_TOKEN_REQUIRED, str(e), 0
    if "blocked" in msg:
        return YOUTUBE_IP_BLOCKED, str(e), 0
    if "429" in msg:
        return RATE_LIMITED, str(e), retries
    return RATE_LIMITED, f"Request failed after {attempts} attempts: {e}", 0

def _error_response(error_code: str, message: str, video_id: str | None,
                     url: str, retry_count: int, fallback_attempted: bool,
                     fetch_duration: float, retryable: bool) -> dict:
    return {"is_error": True, "error_code": error_code, "error_message": message,
            "video_id": video_id, "url": url, "retry_count": retry_count,
            "fallback_attempted": fallback_attempted,
            "fetch_duration_seconds": fetch_duration, "retryable": retryable}

def _format_error(resp: dict, fmt: str) -> str:
    if fmt == "markdown":
        return (f"# Error: {resp['error_code']}\n\n{resp['error_message']}\n\n"
                f"url: {resp['url']}\nvideo_id: {resp.get('video_id') or _EM_DASH}\n"
                f"retry_count: {resp['retry_count']}\nretryable: {resp['retryable']}\n"
                f"fetch_duration_seconds: {resp['fetch_duration_seconds']}\n")
    return json.dumps(resp, ensure_ascii=False, separators=(",", ":"))

def _build_segments(raw_segments: list) -> list[dict]:
    return [{"text": s["text"], "start": round(s["start"], 2),
             "end": round(s["start"] + s["duration"], 2)} for s in raw_segments]

# -- Tool ---------------------------------------------------------------------

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [types.Tool(
        name="fetch_transcript",
        description=("Fetch a YouTube video transcript. Returns compact JSON (default) "
                     "or markdown. Defaults to segments-only for token efficiency. "
                     "Cached by language preference. Retries only transient errors. "
                     "Includes provenance, caption type, and verifiable content hash."),
        inputSchema=TOOL_SCHEMA,
    )]

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name != "fetch_transcript":
        raise ValueError(f"Unknown tool: {name}")

    url = arguments["url"]
    languages = _validate_languages(arguments.get("languages", "sv,en"))
    output_mode = arguments.get("output", "segments")
    timestamps = arguments.get("include_timestamps", False)
    fmt = arguments.get("format", "json")
    bypass = arguments.get("bypass_cache", False)
    overrides = {k: arguments.get(k, "") for k in ("title", "channel", "published")}
    fetch_start = time.monotonic()

    try:
        video_id = extract_video_id(url)
    except ValueError:
        dur = round(time.monotonic() - fetch_start, 2)
        resp = _error_response(INVALID_URL, f"Cannot extract video ID from: {url}",
                               None, url, 0, False, dur, False)
        return [types.TextContent(type="text", text=_format_error(resp, fmt))]

    warnings = []
    retry_count = 0
    is_generated = None
    fallback_attempted = False

    cached = None if bypass else load_from_cache(video_id, languages)

    if cached:
        segments_raw = cached["segments"]
        language = cached["language"]
        is_generated = cached.get("is_generated")
        meta_fields = cached.get("meta", {})
        meta_sources = cached.get("meta_sources", {})
        cache_hit = True
        cache_age = _cache_age_days(cached.get("cached_at", ""))
        fetched_at = cached.get("cached_at", "")
    else:
        cache_hit = False
        cache_age = 0
        fetched_at = datetime.now().strftime("%Y-%m-%d")

        try:
            meta = await asyncio.to_thread(fetch_metadata, url)
            meta_fields = meta["fields"]
            meta_sources = meta["sources"]
        except Exception:
            meta_fields = {"title": "", "channel": "", "published": ""}
            meta_sources = {"title": "none", "channel": "none", "published": "none"}

        try:
            segments_raw, language, is_generated, fallback_attempted, attempts = (
                await asyncio.to_thread(fetch_transcript_with_retry, video_id, languages))
            retry_count = attempts - 1
        except Exception as e:
            dur = round(time.monotonic() - fetch_start, 2)
            fallback_attempted = getattr(e, "fallback_attempted", False)
            error_code, error_msg, err_retries = _classify_exception(e)
            retryable = _is_retryable(e)
            resp = _error_response(error_code, error_msg, video_id, url,
                                   err_retries, fallback_attempted, dur, retryable)
            return [types.TextContent(type="text", text=_format_error(resp, fmt))]

        try:
            save_to_cache(video_id, segments_raw, language, languages,
                          {"fields": meta_fields, "sources": meta_sources}, is_generated)
        except OSError:
            warnings.append({"code": CACHE_WRITE_FAILED,
                             "message": "Cache write failed. Transcript returned without caching."})

    fetch_duration = round(time.monotonic() - fetch_start, 2)

    for k, v in overrides.items():
        if v:
            meta_fields[k] = v
            meta_sources[k] = "manual"

    # Warnings AFTER cache/fetch merge
    warnings.extend(_build_metadata_warnings(meta_fields, meta_sources))

    language_fallback = language not in languages
    if language_fallback:
        fallback_attempted = True
        warnings.append({"code": "LANGUAGE_FALLBACK",
                         "message": f"Requested {','.join(languages)}, got {language}."})

    if is_generated is True:
        warnings.append({"code": "AUTO_GENERATED",
                         "message": "Auto-generated by YouTube speech recognition. May contain errors; verify against audio before quoting."})

    if is_generated is True:
        caption_type = "auto-generated"
    elif is_generated is False:
        caption_type = "manual"
    else:
        caption_type = "unknown"

    segments_out = _build_segments(segments_raw)
    missing = [k for k in ("title", "channel", "published") if not meta_fields.get(k)]
    duration = _transcript_duration(segments_raw)
    content_hash = _content_hash(segments_out)

    response = {
        "is_error": False, "video_id": video_id, "url": url,
        "title": meta_fields.get("title") or None,
        "channel": meta_fields.get("channel") or None,
        "published": meta_fields.get("published") or None,
        "language": language, "language_requested": ",".join(languages),
        "language_fallback": language_fallback, "caption_type": caption_type,
        "segment_count": len(segments_out),
        "transcript_duration_seconds": duration,
        "metadata_sources": meta_sources or {},
        "metadata_missing": missing or [],
        "cache_hit": cache_hit,
        "cache_age_days": cache_age if cache_hit else None,
        "fetched_at": fetched_at,
        "fetch_duration_seconds": fetch_duration,
        "retry_count": retry_count,
        "fallback_attempted": fallback_attempted,
        "content_hash": content_hash,
        "warnings": warnings or [],
    }

    if output_mode in ("text", "both"):
        response["transcript_text"] = raw_text(segments_raw) if timestamps else clean_text(segments_raw)
    if output_mode in ("segments", "both"):
        response["segments"] = segments_out

    if fmt == "markdown":
        text_body = raw_text(segments_raw) if timestamps else clean_text(segments_raw)
        notes = []
        if cache_hit:
            notes.append(f"Served from local cache (fetched {fetched_at}).")
        if retry_count > 0:
            notes.append(f"Retry succeeded (attempt {retry_count + 1}/{MAX_RETRIES}).")
        for w in warnings:
            notes.append(f"Warning [{w['code']}]: {w['message']}")
        notes_str = "".join(f"note: {n}\n" for n in notes)
        missing_str = f"missing: {', '.join(missing)}\n" if missing else ""
        md = (f"# {meta_fields.get('title') or 'Untitled'}\n\n"
              f"source: {url}\nchannel: {meta_fields.get('channel') or _EM_DASH}\n"
              f"published: {meta_fields.get('published') or _EM_DASH}\n"
              f"language: {language}\ncaption_type: {caption_type}\n"
              f"segments: {len(segments_out)}\nduration: {duration}s\n"
              f"content_hash: {content_hash}\n{missing_str}{notes_str}"
              f"fetched: {fetched_at}\n\n---\n\n## Transcript\n\n{text_body}\n")
        return [types.TextContent(type="text", text=md)]

    return [types.TextContent(type="text",
            text=json.dumps(response, ensure_ascii=False, separators=(",", ":")))]

# -- Serve --------------------------------------------------------------------

async def _serve():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())

def main():
    asyncio.run(_serve())

if __name__ == "__main__":
    main()
