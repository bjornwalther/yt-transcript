# yt-transcript-mcp Contract Specification

Single source of truth for the MCP server's behavior. Implementation and tests
are both derived from this document. Any change to behavior starts here.

---

## 1. Exception Classification

Complete mapping of `youtube_transcript_api._errors` to MCP error codes.
Classification is by exception class, never by message parsing (except as
last-resort fallback for unknown generic exceptions).

### Permanent (non-retryable)

| Exception class         | MCP error code              | Retryable |
|------------------------|-----------------------------|----------|
| TranscriptsDisabled    | TRANSCRIPT_NOT_AVAILABLE    | no        |
| NoTranscriptFound      | LANGUAGE_NOT_AVAILABLE      | no        |
| VideoUnavailable       | VIDEO_UNAVAILABLE           | no        |
| VideoUnplayable        | VIDEO_UNAVAILABLE           | no        |
| InvalidVideoId         | INVALID_URL                 | no        |
| AgeRestricted          | VIDEO_UNAVAILABLE           | no        |
| IpBlocked              | YOUTUBE_IP_BLOCKED          | no        |
| RequestBlocked         | YOUTUBE_IP_BLOCKED          | no        |
| PoTokenRequired        | PO_TOKEN_REQUIRED           | no        |
| NotTranslatable        | LANGUAGE_NOT_AVAILABLE      | no        |
| TranslationLanguageNotAvailable | LANGUAGE_NOT_AVAILABLE | no    |

### Transient (retryable)

Only these are retried. Everything else raises immediately.

| Exception class         | MCP error code   | Retryable |
|------------------------|------------------|----------|
| YouTubeRequestFailed   | RATE_LIMITED     | yes       |
| (generic Exception)    | RATE_LIMITED     | yes       |

### Internal

| Condition              | MCP error code              |
|-----------------------|-----------------------------|
| URL parse failure      | INVALID_URL                 |
| All metadata sources fail | METADATA_FETCH_FAILED (warning, not error) |

---

## 2. Success Response Schema (JSON)

Compact serialization: `json.dumps(response, ensure_ascii=False, separators=(",",":"))`

```json
{
  "is_error": false,
  "video_id": "string",
  "url": "string",
  "title": "string | null",
  "channel": "string | null",
  "published": "string | null",
  "language": "string",
  "language_requested": "string",
  "language_fallback": false,
  "caption_type": "manual | auto-generated",
  "segment_count": 0,
  "transcript_duration_seconds": 0.0,
  "metadata_sources": {"title": "oembed", "channel": "oembed", "published": "pytubefix"},
  "metadata_missing": [],
  "cache_hit": false,
  "cache_age_days": null,
  "fetched_at": "2026-09-05",
  "fetch_duration_seconds": 0.0,
  "retry_count": 0,
  "fallback_attempted": false,
  "content_hash": "sha256hex",
  "warnings": [],
  "segments": [{"text": "string", "start": 0.0, "end": 0.0}],
  "transcript_text": "string"
}
```

### Output modes

| `output` param | `segments` present | `transcript_text` present |
|---------------|-------------------|-------------------------|
| segments (default) | yes | no |
| text | no | yes |
| both | yes | yes |

### Invariants

- Default response contains exactly ONE transcript representation.
- `metadata_missing` is `[]` (not null) when nothing is missing.
- `warnings` is `[]` (not null) when there are no warnings.
- `metadata_sources` is `{}` (not null) when empty.
- `cache_age_days` is `null` only when `cache_hit` is `false`.
- `content_hash` is ALWAYS computed from the canonical `segments` array
  (rounded start/end/text). When `output=text`, the hash is still present
  and documented as an opaque transcript identity (not recomputable from
  that response; recomputable when `output=segments` or `output=both`).
- Markdown format always renders readable text regardless of output mode.
- `caption_type` is `"unknown"` if the information is unavailable.

---

## 3. Error Response Schema (JSON)

```json
{
  "is_error": true,
  "error_code": "RATE_LIMITED",
  "error_message": "human readable",
  "video_id": "string | null",
  "url": "string",
  "retry_count": 0,
  "fallback_attempted": false,
  "fetch_duration_seconds": 0.0,
  "retryable": false
}
```

### Invariants

- `retryable` boolean tells agents whether retrying is appropriate.
- `retry_count` reflects actual retries performed (attempts - 1).
- `fallback_attempted` is true when a language fallback was tried, even if it failed.
- `fetch_duration_seconds` is present in ALL error responses including INVALID_URL.
- Error responses respect `format` param (JSON or markdown).

---

## 4. Cache Contract

### Key generation

Cache filename = `SHA256(json.dumps({"video_id": video_id, "languages": languages}, sort_keys=True))[:16] + ".json"`

Raw language input NEVER appears in the filename. The original request vector
is stored inside the payload for provenance.

### Schema

```json
{
  "cache_version": 2,
  "segments": [...],
  "language": "en",
  "requested_languages": ["sv", "en"],
  "is_generated": false,
  "meta": {"title": "", "channel": "", "published": ""},
  "meta_sources": {"title": "oembed", "channel": "oembed", "published": "none"},
  "cached_at": "2026-09-05"
}
```

### Invariants

- Cache hit requires `cache_version == CACHE_VERSION` (exact match, not >=).
- All required fields validated on load (type check). Missing or wrong type = cache miss.
- Malformed JSON, future versions, truncated files = cache miss, never crash.
- Legacy v1 entries (no version field) are silently skipped.

---

## 5. Tool Schema

Input schema includes `additionalProperties: false`.
Language input validated: max 20 codes, each 1-10 chars, alphanumeric + hyphen only.

---

## 6. Warning Codes

| Code | Trigger | Blocking |
|------|---------|----------|
| METADATA_FETCH_FAILED | All or partial metadata sources returned "none" | no |
| LANGUAGE_FALLBACK | Returned language not in requested list | no |
| AUTO_GENERATED | `is_generated` is true | no |
