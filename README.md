# yt-transcript

Fetch YouTube transcripts as clean markdown, optimised for AI context.

Two interfaces:
- **CLI** — paste a URL in terminal, get a `.md` file
- **MCP server** — use it as a tool directly inside Claude Desktop or ChatGPT

---

## Quick start (MCP server)

This is the recommended way. No files to move around, transcripts land directly in your AI conversation.

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "yt-transcript": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/yt-transcript", "mcp_server.py"]
    }
  }
}
```

### ChatGPT Desktop

Add to your Codex MCP config (same format as above). ChatGPT desktop shares MCP configuration with Codex.

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

## Output format

Both CLI and MCP return the same markdown structure:

```markdown
# Video Title

source: https://youtube.com/watch?v=ABC123
channel: Channel Name
published: 2026-05-15
language: sv
fetched: 2026-05-15

---

## Transcript

The actual transcript text...
```

---

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

With uv, dependencies are handled automatically. No manual pip install needed.

---

## Folder structure

```
yt-transcript/
├── yt_transcript.py     ← CLI script
├── mcp_server.py        ← MCP server (Claude/ChatGPT)
├── pyproject.toml       ← dependencies & entry points
├── README.md
└── transcripts/         ← CLI output (created on first run)
```
