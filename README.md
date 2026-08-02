# yt-transcript

Fetch YouTube transcripts as clean markdown, optimised for AI context.

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

> **Important:** Use the full path to `uv` (find it with `which uv`). Restart the app after adding the config.

### After setup

Restart the app. You now have a `fetch_transcript` tool available. Just say:

> "Fetch the transcript from https://youtu.be/ABC123"

The transcript appears directly in the conversation, ready to be summarised, analysed, or used as context.

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
| `--date` | Set publish date manually | `--date 2026-05-15` |
| `--lang` | Preferred transcript language(s) | `--lang sv,en` |
| `--out` | Output directory | `--out ~/my-notes` |
| `--no-clean` | Keep raw timestamps | `--no-clean` |

---

## MCP tool parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `url` | Yes | YouTube URL (any format) |
| `languages` | No | Comma-separated codes, default `sv,en` |
| `include_timestamps` | No | `true` for timestamped lines instead of clean paragraphs |

---

## Error handling

The MCP server returns clear error messages for:
- Invalid or unrecognised YouTube URLs
- Videos with disabled transcripts
- Videos without subtitles in the requested language
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
fetched: 2026-05-15

---

## Transcript

The actual transcript text...
```

---

## Folder structure

```
yt-transcript/
├── yt_transcript.py     ← CLI script
├── mcp_server.py        ← MCP server (Claude/ChatGPT)
├── pyproject.toml       ← dependencies & entry points
├── CHANGELOG.md
├── README.md
└── transcripts/         ← CLI output (created on first run)
```

---

## Troubleshooting

**MCP server doesn't start:**
1. Make sure `uv` is installed and use its full path (`which uv`)
2. Arguments must be separate values, not one string
3. No trailing spaces or special characters in the command path
4. Restart the app completely (new session required)

**Transcript is empty or metadata missing:**
- YouTube sometimes blocks metadata detection. The transcript itself usually still works.
- Try specifying different languages with `--lang` or `languages` parameter.

---

## Version history

See [CHANGELOG.md](CHANGELOG.md) for details.
