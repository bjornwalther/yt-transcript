#!/usr/bin/env python3
"""MCP server: YouTube transcripts as AI context. Cache, retry, metadata.

Install:  uvx --from git+https://github.com/bjornwalther/yt-transcript yt-transcript-mcp
Or run:   uv run mcp_server.py
"""

import asyncio
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

TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "description": "YouTube video URL (any format)"},
        "languages": {"type": "string", "description": "Comma-separated language codes (default: sv,en)", "default": "sv,en"},
        "include_timestamps": {"type": "boolean", "description": "Include HH:MM:SS per line", "default": False},
        "title": {"type": "string", "description": "Manual title override"},
        "channel": {"type": "string", "description": "Manual channel override"},
        "published": {"type": "string", "description": "Manual publish date (YYYY-MM-DD)"},
        "bypass_cache": {"type": "boolean", "description": "Force fresh fetch", "default": False},
    },
    "required": ["url"],
}


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [types.Tool(
        name="fetch_transcript",
        description="Fetch YouTube transcript as clean markdown. Cached locally, retries on rate-limit.",
        inputSchema=TOOL_SCHEMA,
    )]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name != "fetch_transcript":
        raise ValueError(f"Unknown tool: {name}")

    url = arguments["url"]
    languages = [l.strip() for l in arguments.get("languages", "sv,en").split(",")]
    timestamps = arguments.get("include_timestamps", False)
    bypass = arguments.get("bypass_cache", False)
    overrides = {k: arguments.get(k, "") for k in ("title", "channel", "published")}

    try:
        video_id = extract_video_id(url)
    except ValueError:
        return [_err(f"Invalid YouTube URL: {url}")]

    notes = []
    cached = None if bypass else load_from_cache(video_id)

    if cached:
        segments, language, meta = cached["segments"], cached["language"], cached["meta"]
        notes.append(f"Served from local cache (fetched {cached['cached_at']}).")
    else:
        try:
            meta = await asyncio.to_thread(fetch_metadata, url)
        except Exception:
            meta = {"title": "", "channel": "", "published": "", "source": "none"}

        try:
            segments, language, attempts = await asyncio.to_thread(
                fetch_transcript_with_retry, video_id, languages
            )
            if attempts > 1:
                notes.append(f"Retry succeeded (attempt {attempts}/{MAX_RETRIES}).")
        except Exception as e:
            return [_err(_classify_error(e, url, languages))]

        save_to_cache(video_id, segments, language, meta)

    # Apply overrides
    for k, v in overrides.items():
        if v:
            meta[k] = v
            meta["source"] = meta.get("source", "none") + "+manual" if "manual" not in meta.get("source", "") else meta.get("source", "manual")

    missing = [k for k in ("title", "channel", "published") if not meta.get(k)]
    body = raw_text(segments) if timestamps else clean_text(segments)
    notes_str = "".join(f"note: {n}\n" for n in notes)
    missing_str = f"missing: {', '.join(missing)}\n" if missing else ""

    result = (
        f"# {meta.get('title') or 'Untitled'}\n\n"
        f"source: {url}\n"
        f"channel: {meta.get('channel') or '\u2014'}\n"
        f"published: {meta.get('published') or '\u2014'}\n"
        f"language: {language}\n"
        f"segments: {len(segments)}\n"
        f"metadata_source: {meta.get('source', 'unknown')}\n"
        f"{missing_str}{notes_str}"
        f"fetched: {datetime.now().strftime('%Y-%m-%d')}\n\n"
        f"---\n\n## Transcript\n\n{body}\n"
    )
    return [types.TextContent(type="text", text=result)]


def _err(msg: str) -> types.TextContent:
    return types.TextContent(type="text", text=f"Error: {msg}")


def _classify_error(e: Exception, url: str, languages: list) -> str:
    msg = str(e).lower()
    if "disabled" in msg:
        return f"Transcripts are disabled for this video.\nURL: {url}"
    if "no transcript" in msg or "not found" in msg:
        return f"No transcript found for {languages}.\nURL: {url}\nTry different language codes."
    return (
        f"YouTube is rate-limiting your IP.\n"
        f"Tried {MAX_RETRIES}x over ~{MAX_RETRIES * RETRY_DELAY_SECONDS}s.\n"
        f"Wait a few minutes and retry.\nURL: {url}"
    )


async def _serve():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main():
    """Sync entry point for uvx/pip."""
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
