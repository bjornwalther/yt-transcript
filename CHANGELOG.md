# Changelog

All notable changes to this project will be documented in this file.

---

## [1.1.0] — 2026-08-02

### Added
- MCP server (`mcp_server.py`) for use as a tool in Claude Desktop and ChatGPT Desktop
- `pyproject.toml` with dependencies managed by uv (no manual pip install needed)
- Layered metadata fetching: oEmbed (primary) + pytubefix (fallback)
- Manual metadata overrides: `title`, `channel`, `published` parameters in both CLI and MCP
- Metadata transparency in output: `metadata_source`, `metadata_complete`, `missing_metadata`
- Local transcript cache (`~/.cache/yt-transcript/`): instant retrieval of previously fetched videos
- Retry with backoff: automatic retry (3 attempts, 3s delay) on YouTube rate limiting
- Transparent status notes in output: cache hits, retry attempts, error details
- `--no-cache` CLI flag and `bypass_cache` MCP parameter to force fresh fetch
- `--title` and `--channel` CLI flags for manual overrides
- Proper error handling: invalid URLs, disabled transcripts, rate limiting, network failures
- Hatch build config to include both modules in wheel

### Changed
- Metadata now uses YouTube oEmbed as primary source (fast, no API key, reliable)
- pytubefix demoted to optional fallback (mainly for publish date)
- Sync YouTube API calls wrapped in `asyncio.to_thread()` in MCP server
- MCP dependency pinned to `>=1.0.0,<2` (2.x has breaking API changes)
- README rewritten with full documentation

### Fixed
- Entry point `yt-transcript-mcp` now works correctly (module included in package)

---

## [1.0.0] — 2026-07-01

### Added
- Initial CLI script (`yt_transcript.py`)
- Fetches transcripts from any YouTube URL format
- Outputs clean markdown with metadata header
- Language priority (default: sv, en)
- Optional raw timestamps mode (`--no-clean`)
- Auto-detects title, channel, and publish date via pytubefix
- Manual date override (`--date`)
- Configurable output directory (`--out`)
