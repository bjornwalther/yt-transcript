#!/usr/bin/env python3
"""MCP server that exposes YouTube transcript fetching as a tool.

Run with:  uv run mcp_server.py
Or via entry point after install:  yt-transcript-mcp
"""

import asyncio
import json
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
    return [
        types.Tool(
            name="fetch_transcript",
            description=(
                "Fetch the transcript of a YouTube video. "
                "Returns clean markdown with metadata (title, channel, date) "
                "and the full transcript text. Optimised for use as AI context."
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
                },
                "required": ["url"],
            },
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name != "fetch_transcript":
        raise ValueError(f"Unknown tool: {name}")

    url = arguments["url"]
    languages = [l.strip() for l in arguments.get("languages", "sv,en").split(",")]
    include_timestamps = arguments.get("include_timestamps", False)

    # Extract video ID
    video_id = extract_video_id(url)

    # Fetch metadata (best effort)
    meta = fetch_metadata(url)

    # Fetch transcript
    segments, language = fetch_transcript(video_id, languages)

    # Format
    body = raw_text(segments) if include_timestamps else clean_text(segments)

    # Build markdown output
    result = f"""# {meta['title'] or 'Untitled'}

source: {url}
channel: {meta['channel'] or '\u2014'}
published: {meta['published'] or '\u2014'}
language: {language}
segments: {len(segments)}
fetched: {datetime.now().strftime('%Y-%m-%d')}

---

## Transcript

{body}
"""

    return [types.TextContent(type="text", text=result)]


async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
