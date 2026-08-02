# yt-transcript

Fetch YouTube transcripts as clean markdown, optimised for AI context.

Built for creating a personal knowledge base from video content in a token-efficient way. Fetch once, use everywhere.

Two interfaces:
- **CLI** — paste a URL in terminal, get a `.md` file
- **MCP server** — use it as a tool directly inside Claude Desktop or ChatGPT Desktop

---

## Quick start (MCP server)

This is the recommended way. No files to move around, transcripts land directly in your AI conversation.

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "yt-transcript": {
      "command": "/Users/YOUR_USER/.local/bin/uv",
      "args": ["run", "mcp_server.py"],
      "cwd": "/path/to/yt-transcript"
    }
  }
}
```

### ChatGPT Desktop (via Codex)

In ChatGPT Desktop MCP settings:

| Field | Value |
|-------|-------|
| Name | YouTube Transcript |
| Command | `/Users/YOUR_USER/.local/bin/uv` |
| Arguments | `run` and `mcp_server.py` (as separate values) |
| Working directory | `/path/to/yt-transcript` |

Or in `~/.codex/config.toml`:

```toml
[mcp_servers.yt-transcript]
command = "/Users/YOUR_USER/.local/bin/uv"
args = ["run", "mcp_server.py"]
cwd = "/path/to/yt-transcript"
```

> **Important:** Use the full path to `uv` (find it with `which uv`). No trailing spaces. Restart the app after config changes (new session required).

### After setup

Restart the app. You now have a `fetch_transcript` tool available. Just say:

> "Fetch the transcript from https://youtu.be/ABC123"

The transcript appears directly in the conversation, ready to be summarised, analysed, or used as context.

---

## How it works

### Metadata (layered)

1. **YouTube oEmbed** (primary) — fast, no API key, reliable for title + channel
2. **pytubefix** (fallback) — slower, can get publish date but sometimes blocked
3. **Manual overrides** (always win) — specify title, channel, or date directly

### Cache

Transcripts are cached locally in `~/.cache/yt-transcript/` (keyed by video ID). A typical 30-60 min video is 15-50 KB. Benefits:
- Instant retrieval on repeat queries (no YouTube request)
- Zero risk of rate limiting on previously fetched videos
- Same transcript can feed multiple AI conversations

Use `--no-cache` (CLI) or `bypass_cache: true` (MCP) to force a fresh fetch.

### Retry with backoff

If YouTube rate-limits a request, the tool automatically retries up to 3 times with 3-second delays. You'll see a note in the output explaining what happened:

```
note: YouTube blocked initial request. Succeeded on attempt 2/3.
```

If all retries fail:

```
Error: YouTube is rate-limiting requests from your IP.
Tried 3 times over ~9 seconds.
Try again in a few minutes.
```

### Transparency

The output always shows where data came from:

```
metadata_source: oembed+pytubefix
metadata_complete: true
note: Served from local cache (originally fetched 2026-07-30).
```

---

## Quick start (CLI)

For when you want local `.md` files:

```bash
# One-time setup
cd /path/to/yt-transcript
uv sync

# Usage
uv run yt_transcript.py https://youtu.be/ABC123
uv run yt_transcript.py https://youtu.be/ABC123 --date 2026-05-15
uv run yt_transcript.py https://youtu.be/ABC123 --title "My Title" --channel "My Channel"
```

Or add a shell alias for convenience:

```bash
alias ytt='uv run /path/to/yt-transcript/yt_transcript.py --out ~/transcripts'
```

Then just: `ytt https://youtu.be/ABC123`

---

## CLI options

| Flag | What it does | Example |
|------|-------------|--------|
| `--date` | Set publish date (overrides auto-detection) | `--date 2026-05-15` |
| `--title` | Set title manually | `--title "Episode 42"` |
| `--channel` | Set channel name manually | `--channel "My Channel"` |
| `--lang` | Preferred transcript language(s) | `--lang sv,en` |
| `--out` | Output directory | `--out ~/my-notes` |
| `--no-clean` | Keep raw timestamps | `--no-clean` |
| `--no-cache` | Bypass cache, fetch fresh | `--no-cache` |

---

## MCP tool parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `url` | Yes | YouTube URL (any format) |
| `languages` | No | Comma-separated codes, default `sv,en` |
| `include_timestamps` | No | `true` for timestamped lines instead of clean paragraphs |
| `title` | No | Manual title override |
| `channel` | No | Manual channel name override |
| `published` | No | Manual publish date (YYYY-MM-DD) |
| `bypass_cache` | No | `true` to force fresh fetch from YouTube |

---

## Error handling

The MCP server returns clear, actionable error messages for:
- Invalid or unrecognised YouTube URLs
- Videos with disabled transcripts
- Videos without subtitles in the requested language
- YouTube rate limiting (with retry count and wait time)
- Network failures

---

## Output format

Both CLI and MCP return the same markdown structure:

```markdown
# Video Title

source: https://youtube.com/watch?v=ABC123
channel: Channel Name
published: 2026-05-15
language: sv
segments: 602
metadata_source: oembed+pytubefix
metadata_complete: true
note: Served from local cache (originally fetched 2026-08-01).
fetched: 2026-08-02

---

## Transcript

The actual transcript text...
```

---

## Folder structure

```
yt-transcript/
├── yt_transcript.py     ← CLI script + shared core functions
├── mcp_server.py        ← MCP server (Claude/ChatGPT)
├── pyproject.toml       ← dependencies & entry points
├── CHANGELOG.md
├── README.md
└── transcripts/         ← CLI output (created on first run)

~/.cache/yt-transcript/  ← local transcript cache (auto-created)
```

---

## Troubleshooting

**MCP server doesn't start:**
1. Use the full path to `uv` (find it with `which uv` in Terminal)
2. Arguments must be separate values (`run` and `mcp_server.py`), not one string
3. No trailing spaces or special characters in the command path
4. Restart the app completely (new session required for tool registration)

**YouTube rate limiting:**
- The tool retries automatically (3 attempts, 3s between each)
- If it still fails, wait a few minutes and try again
- Previously fetched videos are served from cache (never rate-limited)

**Metadata is missing or wrong:**
- Use manual overrides (`--title`, `--channel`, `--date` in CLI, or the corresponding MCP parameters)
- Check `metadata_source` in output to see what worked

**Transcript is empty:**
- Try different languages with `--lang` or `languages` parameter
- Some videos have transcripts disabled by the uploader

---

## Version history

See [CHANGELOG.md](CHANGELOG.md) for details.
