# Changelog

All notable changes to this project will be documented in this file.

---

## [1.1.0] — 2026-08-02

### Added
- MCP server (`mcp_server.py`) for use as a tool in Claude Desktop and ChatGPT Desktop
- `pyproject.toml` with dependencies managed by uv (no manual pip install needed)
- Proper error handling in MCP server: invalid URLs, disabled transcripts, missing subtitles, and network errors now return clear messages
- Hatch build config to include both modules in wheel

### Changed
- Sync YouTube API calls wrapped in `asyncio.to_thread()` to avoid blocking the MCP event loop
- MCP dependency pinned to `>=1.0.0,<2` (2.x has breaking API changes)
- README rewritten with MCP-first setup instructions

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
