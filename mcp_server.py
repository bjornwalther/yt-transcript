#!/usr/bin/env python3
"""
MCP server that exposes YouTube transcript fetching as a tool.

Designed for use with Claude Desktop, ChatGPT Desktop, or any MCP-compatible client.
Communicates via stdio transport (local process, no hosting needed).

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
    fetch_transcript,
    clean_text,
    raw_text,
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
                "Supports manual metadata overrides when auto-detection fails."
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
                },
                "required": ["url"],
            },
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    """Handle tool invocations.

    Fetches transcript and metadata, applies manual overrides,
    and returns formatted markdown with metadata transparency.
    """
    if name != "fetch_transcript":
        raise ValueError(f"Unknown tool: {name}")

    url = arguments["url"]
    languages = [lang.strip() for lang in arguments.get("languages", "sv,en").split(",")]
    include_timestamps = arguments.get("include_timestamps", False)

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

    # Fetch metadata (non-blocking, layered: oEmbed -> pytubefix)
    try:
        meta = await asyncio.to_thread(fetch_metadata, url)
    except Exception:
        meta = {"title": "", "channel": "", "published": "", "source": "none", "complete": False, "missing": ["title", "channel", "published"]}

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

    # Fetch transcript (non-blocking)
    try:
        segments, language = await asyncio.to_thread(fetch_transcript, video_id, languages)
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
                text=f"Error fetching transcript: {e}\n\nURL: {url}"
            )]

    # Format transcript body
    body = raw_text(segments) if include_timestamps else clean_text(segments)

    # Build markdown output with metadata transparency
    missing_line = f"\nmissing_metadata: {', '.join(meta['missing'])}" if meta["missing"] else ""

    result = f"""# {meta['title'] or 'Untitled'}

source: {url}
channel: {meta['channel'] or '\u2014'}
published: {meta['published'] or '\u2014'}
language: {language}
segments: {len(segments)}
metadata_source: {meta.get('source', 'unknown')}
metadata_complete: {str(meta['complete']).lower()}{missing_line}
fetched: {datetime.now().strftime('%Y-%m-%d')}

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
