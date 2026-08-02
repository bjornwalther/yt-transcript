# yt-transcript

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP Compatible](https://img.shields.io/badge/MCP-compatible-green.svg)](https://modelcontextprotocol.io)

**YouTube transcripts as clean markdown, optimised for AI context.**

An MCP server that gives Claude Desktop and ChatGPT Desktop direct access to YouTube transcripts. No browsing, no HTML parsing, no wasted tokens.

---

## Why this tool?

When an AI assistant fetches a YouTube transcript by browsing, it processes the entire page: navigation, ads, recommendations, comments, JavaScript. That's 75,000–150,000 tokens of noise to extract maybe 6,000 tokens of actual content.

This tool cuts straight to the transcript.

| Method | Tokens used | Time | Reliability |
|--------|------------|------|-------------|
| AI browses YouTube | 75,000–150,000 | 20–90 sec | Fragile (blocks, CAPTCHAs) |
| **This MCP** (first fetch) | 6,000–12,000 | 1–3 sec | Retry with backoff |
| **This MCP** (cached) | 6,000–12,000 | instant | Always works |

**10–20x fewer tokens. 20–60x faster. Cached forever.**

Once fetched, a transcript is stored locally and can feed unlimited AI conversations without a single new request to YouTube.

---

## Demo

```
You: Fetch the transcript from https://youtu.be/C5XvwJCGXpo

Tool output:

# Avsnitt 47: Varför AI förändrar allt

source: https://youtu.be/C5XvwJCGXpo
channel: Snacka om AI
published: 2026-05-15
language: sv
segments: 602
metadata_source: oembed+pytubefix
metadata_complete: true
fetched: 2026-08-02

---

## Transcript

Varmt välkomna till ytterligare ett avsnitt...
```

---

## Setup

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended): `curl -LsSf https://astral.sh/uv/install.sh | sh`

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "yt-transcript": {
      "command": "uv",
      "args": ["run", "mcp_server.py"],
      "cwd": "/path/to/yt-transcript"
    }
  }
}
```

> **Tip:** If `uv` isn't found, use the full path (run `which uv` to find it).

### ChatGPT Desktop

In Settings → MCP, add a new server:

| Field | Value |
|-------|-------|
| Name | YouTube Transcript |
| Command | Full path to `uv` (e.g. `~/.local/bin/uv`) |
| Arguments | `run` and `mcp_server.py` (as separate values) |
| Working directory | Path to this repo |

Restart the app. The `fetch_transcript` tool is now available in any conversation.

### CLI (optional)

For saving transcripts as local `.md` files:

```bash
cd /path/to/yt-transcript
uv run yt_transcript.py https://youtu.be/C5XvwJCGXpo
```

Or with a shell alias:

```bash
alias ytt='uv run ~/path/to/yt-transcript/yt_transcript.py --out ~/transcripts'
```

---

## Features

### Local cache

Transcripts are cached in `~/.cache/yt-transcript/` (15–50 KB per video). Once fetched, repeat queries are instant with zero YouTube requests. A year of heavy use stays under 25 MB.

### Retry with backoff

If YouTube rate-limits a request, the tool retries automatically (3 attempts, 3s delay). The output tells you what happened:

```
note: YouTube blocked initial request. Succeeded on attempt 2/3.
```

### Layered metadata

1. **YouTube oEmbed** (primary) — fast, no API key, reliable for title + channel
2. **pytubefix** (fallback) — adds publish date when available
3. **Manual overrides** (always win) — pass `title`, `channel`, or `published` directly

### Transparency

Every response shows where data came from:

```
metadata_source: oembed+pytubefix
metadata_complete: true
note: Served from local cache (originally fetched 2026-07-30).
```

---

## Parameters

### MCP tool

| Parameter | Required | Description |
|-----------|----------|-------------|
| `url` | Yes | YouTube URL (any format) |
| `languages` | No | Comma-separated codes, default `sv,en` |
| `include_timestamps` | No | Timestamped lines instead of clean paragraphs |
| `title` | No | Manual title override |
| `channel` | No | Manual channel name override |
| `published` | No | Manual publish date (YYYY-MM-DD) |
| `bypass_cache` | No | Force fresh fetch from YouTube |

### CLI flags

| Flag | Description |
|------|-------------|
| `--date` | Publish date (YYYY-MM-DD) |
| `--title` | Title override |
| `--channel` | Channel override |
| `--lang` | Language codes (default: `sv,en`) |
| `--out` | Output directory (default: `./transcripts`) |
| `--no-clean` | Keep raw timestamps |
| `--no-cache` | Bypass cache |

---

## Roadmap

- [ ] Summarisation mode (`summary_only`) — return a condensed version for lower token usage
- [ ] Chapter extraction — return only specific chapters/sections
- [ ] Token budget (`max_tokens`) — truncate or summarise to fit a context window
- [ ] Topic filtering (`focus: "topic"`) — return only segments relevant to a query
- [ ] Batch mode — fetch multiple URLs in one call
- [ ] Publish to MCP registry

---

## Project structure

```
yt-transcript/
├── mcp_server.py        ← MCP server (Claude/ChatGPT)
├── yt_transcript.py     ← CLI + shared core logic
├── pyproject.toml       ← dependencies, metadata, entry points
├── README.md
├── CHANGELOG.md
├── LICENSE              ← MIT
└── .github/
    └── FUNDING.yml      ← sponsor links
```

Cache: `~/.cache/yt-transcript/` (auto-created, one JSON file per video)

---

## Troubleshooting

**MCP server doesn't start:**
- Use the full path to `uv` (find it with `which uv`)
- Arguments must be separate values (`run` and `mcp_server.py`), not one string
- No trailing spaces in the command path
- Restart the app completely after config changes

**YouTube rate limiting:**
- Retries happen automatically (3 attempts, 3s between each)
- Previously fetched videos are always served from cache
- If it persists, wait a few minutes

**Metadata missing:**
- Use manual overrides (MCP parameters or CLI flags)
- Publish date is the most common gap (oEmbed doesn't provide it)

---

## Support

If this tool saves you time, consider supporting development:

[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20this%20project-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/bwalther)
[![GitHub Sponsors](https://img.shields.io/badge/GitHub-Sponsor-ea4aaa?logo=github)](https://github.com/sponsors/bjornwalther)

---

## License

MIT © [Björn Walther](https://github.com/bjornwalther)
