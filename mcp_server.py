#!/usr/bin/env python3
"""
MCP server that exposes YouTube transcript fetching as a tool.

Designed for use with Claude Desktop, ChatGPT Desktop, or any MCP-compatible client.
Communicates via stdio transport (local process, no hosting needed).

Features:
    - Local cache: previously fetched transcripts served instantly
    - Retry with backoff: handles YouTube rate limiting transparently
    - Layered metadata: oEmbed + pytubefix + manual overrides
    - Clear error messages for all failure modes

Run with:  uv run mcp_server.py
Or via entry point after install:  yt-transcript-mcp
"""

import asyncio
from datetime import datetime

from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

from yt_transcript import (
    extract_video_id,
    fetch_metadata,
    fetch_transcript_with_retry,
    clean_text,
    raw_text,
    load_from_cache,
    save_to_cache,
    MAX_RETRIES,
    RETRY_DELAY_SECONDS,
)

server = Server("yt-transcript")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    """Register the fetch_transcript tool with its input schema."""
    return [
        types.Tool(
            name="fetch_transcript",
            description=(
                "Fetch the transcript of a YouTube video. "
                "Returns clean markdown with metadata (title, channel, date) "
                "and the full transcript text. Optimised for use as AI context. "
                "Supports manual metadata overrides when auto-detection fails. "
                "Uses local cache for instant retrieval of previously fetched videos."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "YouTube video URL (any format: youtube.com, youtu.be, shorts, embed)",
                    },
                    "languages": {
                        "type": "string",
                        "description": "Comma-separated language codes, e.g. 'sv,en'. Default: sv,en",
                        "default": "sv,en",
                    },
                    "include_timestamps": {
                        "type": "boolean",
                        "description": "If true, include timestamps per line instead of clean paragraphs",
                        "default": False,
                    },
                    "title": {
                        "type": "string",
                        "description": "Manual title override (use when auto-detection fails or is wrong)",
                    },
                    "channel": {
                        "type": "string",
                        "description": "Manual channel name override",
                    },
                    "published": {
                        "type": "string",
                        "description": "Manual publish date override (YYYY-MM-DD)",
                    },
                    "bypass_cache": {
                        "type": "boolean",
                        "description": "If true, fetch fresh from YouTube even if cached locally",
                        "default": False,
                    },
                },
                "required": ["url"],
            },
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    """Handle tool invocations.

    Flow:
    1. Check local cache (instant, no YouTube request)
    2. If not cached: fetch with retry (handles rate limiting)
    3. Apply manual overrides
    4. Return formatted markdown with transparency notes
    """
    if name != "fetch_transcript":
        raise ValueError(f"Unknown tool: {name}")

    url = arguments["url"]
    languages = [lang.strip() for lang in arguments.get("languages", "sv,en").split(",")]
    include_timestamps = arguments.get("include_timestamps", False)
    bypass_cache = arguments.get("bypass_cache", False)

    # Manual overrides
    manual_title = arguments.get("title", "")
    manual_channel = arguments.get("channel", "")
    manual_published = arguments.get("published", "")

    # Extract video ID
    try:
        video_id = extract_video_id(url)
    except ValueError:
        return [types.TextContent(
            type="text",
            text=f"Error: Could not extract a video ID from URL: {url}\n\nMake sure it's a valid YouTube URL (youtube.com, youtu.be, shorts, embed)."
        )]

    notes = []

    # Check cache first
    cached = None if bypass_cache else load_from_cache(video_id)

    if cached:
        # Serve from cache (instant, no YouTube request)
        segments = cached["segments"]
        language = cached["language"]
        meta = cached["meta"]
        meta["complete"] = all(meta.get(k) for k in ("title", "channel", "published"))
        meta["missing"] = [k for k in ("title", "channel", "published") if not meta.get(k)]
        notes.append(f"Served from local cache (originally fetched {cached['cached_at']}).")
    else:
        # Fetch metadata (non-blocking, layered: oEmbed -> pytubefix)
        try:
            meta = await asyncio.to_thread(fetch_metadata, url)
        except Exception:
            meta = {"title": "", "channel": "", "published": "", "source": "none", "complete": False, "missing": ["title", "channel", "published"]}

        # Fetch transcript with retry (non-blocking)
        try:
            segments, language, attempts = await asyncio.to_thread(
                fetch_transcript_with_retry, video_id, languages
            )
            if attempts > 1:
                notes.append(f"YouTube blocked initial request. Succeeded on attempt {attempts}/{MAX_RETRIES}.")
        except Exception as e:
            error_msg = str(e).lower()
            if "disabled" in error_msg:
                return [types.TextContent(
                    type="text",
                    text=f"Error: Transcripts are disabled for this video.\n\nURL: {url}"
                )]
            elif "no transcript" in error_msg or "not found" in error_msg:
                return [types.TextContent(
                    type="text",
                    text=f"Error: No transcript found for languages {languages}.\n\nURL: {url}\nTry different language codes or check if the video has subtitles."
                )]
            else:
                return [types.TextContent(
                    type="text",
                    text=(
                        f"Error: YouTube is rate-limiting requests from your IP.\n"
                        f"Tried {MAX_RETRIES} times over ~{MAX_RETRIES * RETRY_DELAY_SECONDS} seconds.\n"
                        f"Try again in a few minutes, or use a different video URL to test.\n\n"
                        f"URL: {url}\nDetails: {e}"
                    )
                )]

        # Save to cache for future instant retrieval
        save_to_cache(video_id, segments, language, meta)

    # Apply manual overrides (always win)
    if manual_title:
        meta["title"] = manual_title
    if manual_channel:
        meta["channel"] = manual_channel
    if manual_published:
        meta["published"] = manual_published

    # Update source and completeness after overrides
    if manual_title or manual_channel or manual_published:
        meta["source"] = meta.get("source", "none") + "+manual" if meta.get("source") != "none" else "manual"
    missing = [k for k in ("title", "channel", "published") if not meta.get(k)]
    meta["complete"] = len(missing) == 0
    meta["missing"] = missing

    # Format transcript body
    body = raw_text(segments) if include_timestamps else clean_text(segments)

    # Build markdown output with transparency
    missing_line = f"\nmissing_metadata: {', '.join(meta['missing'])}" if meta["missing"] else ""
    notes_lines = "\n".join(f"note: {n}" for n in notes) + "\n" if notes else ""

    result = f"""# {meta['title'] or 'Untitled'}

source: {url}
channel: {meta['channel'] or '\u2014'}
published: {meta['published'] or '\u2014'}
language: {language}
segments: {len(segments)}
metadata_source: {meta.get('source', 'unknown')}
metadata_complete: {str(meta['complete']).lower()}{missing_line}
{notes_lines}fetched: {datetime.now().strftime('%Y-%m-%d')}

---

## Transcript

{body}
"""

    return [types.TextContent(type="text", text=result)]


async def main():
    """Start the MCP server using stdio transport."""
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
