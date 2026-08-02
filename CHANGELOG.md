# Changelog

All notable changes to this project will be documented in this file.

---

## [1.1.0] — 2026-08-02

### Added
- MCP server (`mcp_server.py`) for use as a tool in Claude Desktop and ChatGPT Desktop
- `pyproject.toml` with full package metadata, managed by uv
- Layered metadata fetching: oEmbed (primary) + pytubefix (fallback)
- Manual metadata overrides: `title`, `channel`, `published` parameters
- Metadata transparency: `metadata_source`, `metadata_complete`, `missing_metadata`
- Local transcript cache (`~/.cache/yt-transcript/`): instant retrieval of previously fetched videos
- Retry with backoff: automatic retry (3 attempts, 3s delay) on YouTube rate limiting
- Transparent status notes: cache hits, retry attempts, error details
- `--no-cache` CLI flag and `bypass_cache` MCP parameter
- `--title` and `--channel` CLI flags
- Proper error handling: invalid URLs, disabled transcripts, rate limiting, network failures
- MIT license
- GitHub Sponsors and Ko-fi funding configuration

### Changed
- Metadata uses YouTube oEmbed as primary source (fast, no API key, reliable)
- pytubefix demoted to optional fallback (mainly for publish date)
- Sync calls wrapped in `asyncio.to_thread()` in MCP server
- MCP dependency pinned to `>=1.0.0,<2`

### Fixed
- Entry point `yt-transcript-mcp` packaging (module now included in wheel)

---

## [1.0.0] — 2026-07-01

### Added
- Initial CLI script (`yt_transcript.py`)
- Fetches transcripts from any YouTube URL format
- Clean markdown output with metadata header
- Language priority (default: sv, en)
- Raw timestamps mode (`--no-clean`)
- Auto-detection of title, channel, publish date via pytubefix
- Manual date override (`--date`)
- Configurable output directory (`--out`)
