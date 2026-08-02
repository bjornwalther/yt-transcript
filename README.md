# yt-transcript-mcp

[![MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![MCP](https://img.shields.io/badge/MCP-compatible-green.svg)](https://modelcontextprotocol.io)

YouTube transcripts as token-efficient AI context. One fetch, cached forever.

Works with Claude Desktop, ChatGPT Desktop, Cursor, Windsurf, and any MCP client.

---

## Why?

When AI browses YouTube for a transcript, it chews through 75,000–150,000 tokens of page chrome to extract 6,000 tokens of text. This tool fetches just the transcript.

| | Tokens | Speed | Repeat queries |
|-|--------|-------|----------------|
| AI browses YouTube | 75–150k | 20–90s | Same cost every time |
| **yt-transcript-mcp** | 6–12k | 1–3s | Instant (cached) |

---

## Install

One line. No git clone needed.

### Claude Desktop

```json
{
  "mcpServers": {
    "yt-transcript": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/bjornwalther/yt-transcript", "yt-transcript-mcp"]
    }
  }
}
```

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`, restart.

### ChatGPT Desktop

Same config in your Codex MCP settings, or add manually:

| Field | Value |
|-------|-------|
| Command | `uvx` (or full path: `~/.local/bin/uvx`) |
| Arguments | `--from` `git+https://github.com/bjornwalther/yt-transcript` `yt-transcript-mcp` |

### Cursor / Windsurf / VS Code

Paste the same JSON into your MCP server config.

### Requires

[uv](https://docs.astral.sh/uv/) (includes `uvx`): `curl -LsSf https://astral.sh/uv/install.sh | sh`

---

## Usage

> Fetch the transcript from https://youtu.be/C5XvwJCGXpo

```markdown
# Avsnitt 47: Varför AI förändrar allt

source: https://youtu.be/C5XvwJCGXpo
channel: Snacka om AI
published: 2026-05-15
language: sv
segments: 602
metadata_source: oembed+pytubefix
fetched: 2026-08-02

---

## Transcript

Varmt välkomna till ytterligare ett avsnitt...
```

---

## Features

**Cache.** Transcripts stored in `~/.cache/yt-transcript/` (15–50 KB each). Second request for same video: instant, zero YouTube traffic.

**Retry.** Rate-limited? Retries 3x with 3s backoff. Output tells you what happened.

**Metadata.** Title and channel via oEmbed (fast, no API key). Publish date via pytubefix fallback. Manual overrides always win.

**Transparency.** Every response shows `metadata_source`, cache status, and retry info. No silent failures.

---

## Parameters

| Parameter | Description |
|-----------|-------------|
| `url` | YouTube URL (required) |
| `languages` | Language codes, e.g. `sv,en` |
| `include_timestamps` | `true` for `[HH:MM:SS]` per line |
| `title` | Override title |
| `channel` | Override channel |
| `published` | Override date (YYYY-MM-DD) |
| `bypass_cache` | `true` to force fresh fetch |

---

## CLI

Also works as a standalone script:

```bash
uvx --from git+https://github.com/bjornwalther/yt-transcript yt-transcript https://youtu.be/ABC123
```

Or locally: `uv run yt_transcript.py <url> [--date] [--title] [--channel] [--lang] [--out] [--no-clean] [--no-cache]`

---

## Roadmap

- [ ] Summary mode (condensed output, fewer tokens)
- [ ] Chapter/topic filtering
- [ ] Token budget (`max_tokens`)
- [ ] Batch URLs
- [ ] MCP registry listing

---

## Support

[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/bwalther)
[![GitHub Sponsors](https://img.shields.io/badge/Sponsor-ea4aaa?logo=github)](https://github.com/sponsors/bjornwalther)

---

MIT © [Björn Walther](https://github.com/bjornwalther)
