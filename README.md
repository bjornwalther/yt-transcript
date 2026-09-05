# yt-transcript-mcp

[![MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![MCP](https://img.shields.io/badge/MCP-compatible-green.svg)](https://modelcontextprotocol.io)
[![PyPI](https://img.shields.io/pypi/v/ytfetch-mcp.svg)](https://pypi.org/project/ytfetch-mcp/)
[![yt-transcript MCP server](https://glama.ai/mcp/servers/bjornwalther/yt-transcript/badges/score.svg)](https://glama.ai/mcp/servers/bjornwalther/yt-transcript)

YouTube transcripts as token-efficient AI context. One fetch, cached forever.

Agent-first: returns structured JSON by default. Zero dependencies on yt-dlp, ffmpeg, or API keys.

Works with Claude Desktop, ChatGPT Desktop, Cursor, Windsurf, and any MCP client.

---

## Why?

When AI browses YouTube for a transcript, it processes the entire page: navigation, ads, recommendations, scripts. That's 75,000-150,000 tokens of noise to extract maybe 6,000 tokens of actual content.

This tool fetches only the transcript.

| | Tokens | Speed | Repeat queries |
|-|--------|-------|----------------|
| AI browses YouTube | 75-150k | 20-90s | Same cost every time |
| **ytfetch-mcp** | 6-12k | 1-3s | Instant (cached) |

~50 KB per video in cache. A year of daily use stays under 120 MB.

---

## Demo

You say:

> Fetch the transcript from https://www.youtube.com/watch?v=dQw4w9WgXcQ

Default response (compact JSON, segments only):

```json
{
  "is_error": false,
  "video_id": "dQw4w9WgXcQ",
  "title": "Never Gonna Give You Up",
  "channel": "Rick Astley",
  "published": "2009-10-25",
  "language": "en",
  "caption_type": "manual",
  "segment_count": 56,
  "transcript_duration_seconds": 213.5,
  "content_hash": "a1b2c3...",
  "cache_hit": false,
  "warnings": [],
  "segments": [
    {"text": "We're no strangers to love", "start": 18.0, "end": 21.4},
    {"text": "You know the rules and so do I", "start": 21.4, "end": 24.8}
  ]
}
```

Structured, machine-readable, one transcript representation. No HTML, no noise, no wasted tokens.

---

## Install

One line. No git clone needed.

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "yt-transcript": {
      "command": "uvx",
      "args": ["ytfetch-mcp"]
    }
  }
}
```

Restart Claude Desktop. Done.

### ChatGPT Desktop

Same config in your Codex MCP settings, or add manually:

| Field | Value |
|-------|-------|
| Command | `uvx` (or full path: `~/.local/bin/uvx`) |
| Arguments | `ytfetch-mcp` |

> **Tip:** Find your uvx path with `which uvx`. Restart the app after config changes.

### Cursor / Windsurf / VS Code

Paste the same JSON block into your MCP server config.

### Requires

[uv](https://docs.astral.sh/uv/) (includes `uvx`): `curl -LsSf https://astral.sh/uv/install.sh | sh`

---

## Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `url` | YouTube URL (required, any format) | |
| `languages` | Language codes in priority order | `sv,en` |
| `output` | `segments`, `text`, or `both` | `segments` |
| `format` | `json` or `markdown` | `json` |
| `include_timestamps` | `true` for `[HH:MM:SS]` per line in text output | `false` |
| `title` | Override title | |
| `channel` | Override channel | |
| `published` | Override date (YYYY-MM-DD) | |
| `bypass_cache` | `true` to force fresh fetch | `false` |

---

## Output modes

| `output` | What you get |
|----------|-------------|
| `segments` (default) | Array of `{text, start, end}` for structured consumption |
| `text` | Single readable string (clean or timestamped) |
| `both` | Both representations |

Markdown format (`format=markdown`) always renders readable text regardless of output mode.

---

## Error handling

Every error returns a structured response with a machine-readable code and a `retryable` flag so agents can branch automatically:

```json
{
  "is_error": true,
  "error_code": "VIDEO_UNAVAILABLE",
  "error_message": "Video is unavailable, private, or removed.",
  "retryable": false,
  "retry_count": 0
}
```

| Error code | Meaning | Retryable |
|-----------|---------|----------|
| `INVALID_URL` | Not a YouTube URL or malformed video ID | No |
| `TRANSCRIPT_NOT_AVAILABLE` | Transcripts disabled for this video | No |
| `LANGUAGE_NOT_AVAILABLE` | No transcript in requested languages | No |
| `VIDEO_UNAVAILABLE` | Video unavailable, private, age-restricted, or unplayable | No |
| `YOUTUBE_IP_BLOCKED` | YouTube is blocking your IP | No |
| `PO_TOKEN_REQUIRED` | Video requires Proof-of-Origin token | No |
| `RATE_LIMITED` | YouTube rate limit (429) | Yes |

---

## Provenance

Every response includes provenance so you know exactly where the data comes from:

- **`caption_type`**: `manual`, `auto-generated`, or `unknown`
- **`metadata_sources`**: per-field tracking (`{"title": "oembed", "published": "pytubefix"}`)
- **`content_hash`**: SHA256 of the segments array for reproducibility
- **`warnings`**: `AUTO_GENERATED` (speech recognition, may contain errors), `LANGUAGE_FALLBACK` (got a different language than requested), `METADATA_FETCH_FAILED` (some metadata unavailable)

---

## Cache

Transcripts cached locally in `~/.cache/yt-transcript/`. Keyed by video ID + language preference. Second fetch: instant, zero network.

- Cache entries validated on load (version, types, segments, metadata)
- Legacy or corrupted entries silently skipped
- Cache write failures never block transcript delivery

---

## CLI

Also works standalone, no MCP client needed:

```bash
uvx ytfetch-mcp  # starts the MCP server
uv run yt_transcript.py https://youtu.be/ABC123  # CLI mode, saves .md file
```

CLI flags: `--date`, `--title`, `--channel`, `--lang`, `--out`, `--no-clean`, `--no-cache`.

---

## Roadmap

- [ ] Summary mode -- condensed output for lower token cost
- [ ] Token budget (`max_tokens`) -- fit any context window
- [ ] Batch URLs -- multiple videos in one call
- [ ] Chapter/topic filtering -- return only relevant sections
- [ ] Remote HTTP transport -- expose as streamable HTTP MCP server
- [ ] Schema.org metadata -- replace pytubefix for publish date
- [ ] MCP outputSchema / structured content

---

## Support

If this saves you time or tokens:

[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/bwalther)
[![GitHub Sponsors](https://img.shields.io/badge/Sponsor-ea4aaa?logo=github)](https://github.com/sponsors/bjornwalther)

---

MIT \u00a9 [Bj\u00f6rn Walther](https://github.com/bjornwalther)
