# yt-transcript-mcp

[![MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![MCP](https://img.shields.io/badge/MCP-compatible-green.svg)](https://modelcontextprotocol.io)

YouTube transcripts as token-efficient AI context. One fetch, cached forever.

Works with Claude Desktop, ChatGPT Desktop, Cursor, Windsurf, and any MCP client.

---

## Why?

When AI browses YouTube for a transcript, it processes the entire page: navigation, ads, recommendations, scripts. That's 75,000–150,000 tokens of noise to extract maybe 6,000 tokens of actual content.

This tool fetches only the transcript.

| | Tokens | Speed | Repeat queries |
|-|--------|-------|----------------|
| AI browses YouTube | 75–150k | 20–90s | Same cost every time |
| **yt-transcript-mcp** | 6–12k | 1–3s | Instant (cached) |

Once cached, a transcript costs zero tokens to retrieve again. Same content can feed 10 different conversations without a single YouTube request. Less compute, less energy, more output.

---

## Demo

You say:

> Fetch the transcript from https://www.youtube.com/watch?v=dQw4w9WgXcQ

The tool returns:

```markdown
# Never Gonna Give You Up

source: https://www.youtube.com/watch?v=dQw4w9WgXcQ
channel: Rick Astley
published: 2009-10-25
language: en
segments: 56
metadata_source: oembed+pytubefix
fetched: 2026-08-02

---

## Transcript

We're no strangers to love You know the rules and so do I
A full commitment's what I'm thinking of You wouldn't get
this from any other guy...
```

Clean markdown. Metadata header for context. No HTML, no noise, no wasted tokens.

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
      "args": ["--from", "git+https://github.com/bjornwalther/yt-transcript", "yt-transcript-mcp"]
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
| Arguments | `--from` `git+https://github.com/bjornwalther/yt-transcript` `yt-transcript-mcp` |

> **Tip:** Find your uvx path with `which uvx`. Arguments must be separate values, not one string.

### Cursor / Windsurf / VS Code

Paste the same JSON block into your MCP server config.

### Requires

[uv](https://docs.astral.sh/uv/) (includes `uvx`): `curl -LsSf https://astral.sh/uv/install.sh | sh`

---

## Features

**Local cache.** Transcripts stored in `~/.cache/yt-transcript/` (15–50 KB per video). A year of heavy use stays under 25 MB. Second fetch: instant, zero network, zero energy.

**Retry with backoff.** YouTube rate-limits sometimes. Retries 3x with 3s delays. Output tells you what happened:

```
note: Retry succeeded (attempt 2/3).
```

**Layered metadata.** Title and channel via YouTube oEmbed (fast, no API key). Publish date via pytubefix fallback. Manual overrides always win: pass `title`, `channel`, or `published` directly.

**Transparency.** Every response shows metadata source, cache status, retry info. No guessing, no silent failures.

**Lean code.** Minimal dependencies, ~100 lines for the MCP server. Less code = less to break, less energy to run.

---

## Parameters

| Parameter | Description |
|-----------|-------------|
| `url` | YouTube URL (required, any format) |
| `languages` | Language codes, e.g. `sv,en` (default: `sv,en`) |
| `include_timestamps` | `true` for `[HH:MM:SS]` per line |
| `title` | Override title |
| `channel` | Override channel |
| `published` | Override date (YYYY-MM-DD) |
| `bypass_cache` | `true` to force fresh fetch |

---

## CLI

Also works standalone, no MCP client needed:

```bash
uvx --from git+https://github.com/bjornwalther/yt-transcript yt-transcript https://youtu.be/ABC123
```

Saves a `.md` file to `./transcripts/`. Flags: `--date`, `--title`, `--channel`, `--lang`, `--out`, `--no-clean`, `--no-cache`.

---

## Roadmap

- [ ] Summary mode — condensed output for lower token cost
- [ ] Chapter/topic filtering — return only relevant sections
- [ ] Token budget (`max_tokens`) — fit any context window
- [ ] Batch URLs — multiple videos in one call
- [ ] MCP registry listing

---

## Support

If this saves you time or tokens:

[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/bwalther)
[![GitHub Sponsors](https://img.shields.io/badge/Sponsor-ea4aaa?logo=github)](https://github.com/sponsors/bjornwalther)

---

MIT © [Björn Walther](https://github.com/bjornwalther)
