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
- Proper error handling in MCP server: invalid URLs, disabled transcripts, missing subtitles, and network errors return clear messages
- CLI flags `--title` and `--channel` for manual overrides
- Hatch build config to include both modules in wheel

### Changed
- Metadata now uses YouTube oEmbed as primary source (fast, no API key, reliable)
- pytubefix demoted to fallback (mainly for publish date)
- pytubefix is now optional (CLI still works without it, just loses publish date auto-detection)
- Sync YouTube API calls wrapped in `asyncio.to_thread()` in MCP server to avoid blocking event loop
- MCP dependency pinned to `>=1.0.0,<2` (2.x has breaking API changes)
- README rewritten with MCP-first setup, metadata docs, and troubleshooting

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
