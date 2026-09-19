# Changelog

All notable changes to this project will be documented in this file.

---

## [1.3.0] — 2026-09-19

### Changed
- **Native InnerTube backend** replaces `youtube-transcript-api`. Transcripts are fetched directly from YouTube's `/player` and caption endpoints through a client chain (`android_vr`, `ios`, `android`) that advances only on a bot check or a required PO token. A cold call is about 1.3s (was about 3s).
- **Contract errors are MCP tool errors.** They now arrive with `isError: true`; the JSON (or markdown) error payload is unchanged and `is_error` stays in it. Clients that treated every tool result as a success will now see these calls as failures.
- **Requires `mcp>=1.10.0`.** Older SDKs do not validate arguments against the tool schema, so unknown or wrongly typed arguments were accepted as successes.
- **Status-aware retry.** Only 429, 5xx, timeouts and connection errors are retried; other HTTP errors are not (previously every non-429 failure was retried). `RATE_LIMITED` now also covers 5xx and timeouts; check `retryable`.
- Metadata and transcript are fetched concurrently, and a failed transcript returns without waiting for metadata.
- `LANGUAGE_NOT_AVAILABLE` is no longer returned: when none of the requested languages exist, another available track is used and `LANGUAGE_FALLBACK` is warned.
- Caption segments with no text are dropped, and a caption element with a missing or invalid time fails the fetch instead of being silently placed at zero.
- The MCP `initialize` response reports the package version instead of the SDK's.

### Added
- `CLIENT_FALLBACK` warning when an earlier client was blocked and a later one served the request.
- Client cooldown: a client that answers with a bot check or a required PO token is tried last for 10 minutes, so later calls skip the wasted request.
- Logging to stderr through the `ytfetch` logger; `YTFETCH_LOG_LEVEL=INFO` shows which client served each fetch.
- Typed errors (`yt_errors`) with `code`, `retryable`, `actual_attempts`, `fallback_attempted` and `http_status`.
- End-to-end tests through the real MCP protocol, recorded InnerTube fixtures (`tools/record_innertube_fixtures.py`), an opt-in live corpus test (`pytest --live -m live`), and a CI job against the lowest allowed dependency versions.

### Fixed
- `fallback_attempted` is now reported truthfully when a fallback track was selected but its download failed.
- Registry metadata: `server.json` uses `pypi` with the real package name, and `smithery.yaml` starts `ytfetch-mcp`.

### Removed
- The `youtube-transcript-api` dependency (and the `requests` chain it pulled in).

### Known limitations
- No PO-token fallback yet: if YouTube requires tokens on every client, affected videos return `PO_TOKEN_REQUIRED`.
- Client identities are hardcoded and need periodic updates when YouTube changes. Verified against a small set of public videos on one network.
- Manual `published` overrides are not yet validated as `YYYY-MM-DD`.

---

## [1.2.0] — 2026-09-05

### Added
- Structured JSON responses by default (compact), with `output=segments|text|both`; markdown is opt-in via `format=markdown`.
- Typed error codes with a `retryable` flag; only known transient failures are retried.
- Provenance: `caption_type` (manual, auto-generated, unknown), language fallback, per-field metadata sources, and a SHA-256 `content_hash` of the segments.
- Warnings: `AUTO_GENERATED`, `LANGUAGE_FALLBACK`, `METADATA_FETCH_FAILED`, `CACHE_WRITE_FAILED`.
- Cache v2: hashed filenames, keyed by video ID and ordered language preference, deep validation on load.
- Input validation: YouTube host allowlist, video ID format, language codes.
- `CONTRACTS.md` as the single source of truth for behavior; behavioral test suite; CI matrix for Python 3.11 to 3.14.
- Published to PyPI as `ytfetch-mcp`.

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
