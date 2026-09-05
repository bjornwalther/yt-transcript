# yt-transcript-mcp Contract Specification

Single source of truth for the MCP server's behavior. Implementation and tests
are both derived from this document. Any change to behavior starts here.

---

## 1. Exception Classification

Complete mapping of `youtube_transcript_api._errors` to MCP error codes.
Classification is by exception class name. Message parsing is a last-resort
fallback for unknown generic exceptions only.

### Retry policy

**Allowlist**: only explicitly listed transient errors are retried.
Everything else raises immediately on first attempt. Unknown/unrecognized
exceptions are treated as non-retryable.

### Permanent (non-retryable)

| Exception class                   | MCP error code           |
|-----------------------------------|--------------------------|
| TranscriptsDisabled               | TRANSCRIPT_NOT_AVAILABLE |
| NoTranscriptFound                 | LANGUAGE_NOT_AVAILABLE   |
| VideoUnavailable                  | VIDEO_UNAVAILABLE        |
| VideoUnplayable                   | VIDEO_UNAVAILABLE        |
| InvalidVideoId                    | INVALID_URL              |
| AgeRestricted                     | VIDEO_UNAVAILABLE        |
| IpBlocked                         | YOUTUBE_IP_BLOCKED       |
| RequestBlocked                    | YOUTUBE_IP_BLOCKED       |
| PoTokenRequired                   | PO_TOKEN_REQUIRED        |
| NotTranslatable                   | LANGUAGE_NOT_AVAILABLE   |
| TranslationLanguageNotAvailable   | LANGUAGE_NOT_AVAILABLE   |
| YouTubeDataUnparsable             | RATE_LIMITED             |
| FailedToCreateConsentCookie       | RATE_LIMITED             |
| CookieError                       | RATE_LIMITED             |
| CookieInvalid                     | RATE_LIMITED             |
| CookiePathInvalid                 | RATE_LIMITED             |
| CouldNotRetrieveTranscript        | RATE_LIMITED             |
| YouTubeTranscriptApiException     | RATE_LIMITED             |
| (unknown Exception)               | RATE_LIMITED             |

All of the above are non-retryable. Only the transient list below is retried.

### Transient (retryable)

| Exception class         | MCP error code | Max retries |
|------------------------|----------------|-------------|
| YouTubeRequestFailed   | RATE_LIMITED   | 2 (3 total) |

No other exception type is retried, including unknown/generic exceptions.

### Internal

| Condition                 | MCP error code                            |
|--------------------------|-------------------------------------------|
| URL parse failure         | INVALID_URL                               |
| Non-YouTube host          | INVALID_URL                               |
| All metadata sources fail | METADATA_FETCH_FAILED (warning, not error) |

---

## 2. URL Validation

Accepted hosts: `youtube.com`, `www.youtube.com`, `m.youtube.com`,
`youtu.be`, `www.youtu.be`, `music.youtube.com`.

Accepted schemes: `http`, `https` (bare hostnames without scheme also accepted).

Video ID format: 11 characters, `[A-Za-z0-9_-]` only.

Any URL with a non-YouTube host or an extracted ID that does not match the
format is rejected with INVALID_URL.

---

## 3. Success Response Schema (JSON)

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
  "caption_type": "manual | auto-generated | unknown",
  "segment_count": 0,
  "transcript_duration_seconds": "number | null",
  "metadata_sources": {},
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

| `output` param      | `segments` present | `transcript_text` present |
|---------------------|-------------------|---------------------------|
| segments (default)  | yes               | no                        |
| text                | no                | yes                       |
| both                | yes               | yes                       |

### Invariants

- Default response contains exactly ONE transcript representation.
- `metadata_missing` is always `[]` (never null).
- `warnings` is always `[]` (never null).
- `metadata_sources` is always `{}` (never null).
- `cache_age_days` is `null` only when `cache_hit` is `false`.
- `content_hash` is ALWAYS computed from the canonical `segments` array
  (rounded start/end/text). When `output=text`, the hash is an opaque
  transcript identity (not recomputable from that response alone).
- Markdown format always renders readable text regardless of output mode.
- `caption_type` is `"unknown"` when the information is unavailable
  (e.g., legacy cache entries that predate this field).
- Metadata warnings are generated from the final meta_sources/meta_fields
  AFTER the cache/fetch merge, so they appear identically whether the
  response is cached or fresh.

---

## 4. Error Response Schema (JSON)

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

- `retryable` tells agents whether retrying may help.
- `retry_count` = actual retries performed (attempts - 1).
- `fallback_attempted` is true when a language fallback was tried, even if it failed.
- `fetch_duration_seconds` present in ALL error responses including INVALID_URL.
- Error responses respect `format` param (JSON or markdown).

---

## 5. Cache Contract

### Key generation

`SHA256(json.dumps({"video_id": vid, "languages": langs}, sort_keys=True))[:16] + ".json"`

Raw language input NEVER appears in the filename.

### Schema

```json
{
  "cache_version": 2,
  "segments": [{"text": "str", "start": 0.0, "duration": 0.0}],
  "language": "en",
  "requested_languages": ["sv", "en"],
  "is_generated": false,
  "meta": {"title": "", "channel": "", "published": ""},
  "meta_sources": {"title": "oembed", "channel": "oembed", "published": "none"},
  "cached_at": "2026-09-05"
}
```

### Validation on load

- `cache_version` must be `int` and `== CACHE_VERSION` (exact, not >=).
- `segments` must be `list`. Each element must be a `dict` with keys
  `text` (str), `start` (finite non-negative int or float, not bool),
  `duration` (finite non-negative int or float, not bool).
- `language` must be `str`.
- `cached_at` must be `str`.
- `is_generated` must be `bool` if present; maps to `caption_type: "unknown"` if missing.
- `meta` must be `dict` if present; default to `{}`.
- `meta_sources` must be `dict` if present; default to `{}`.
- Any type mismatch, missing required field, or malformed segment = cache miss.
- Malformed JSON, future versions, truncated files = cache miss, never crash.

---

## 6. Tool Schema

Input schema includes `additionalProperties: false`.
Language codes: max 20, each 1-10 chars, `[a-zA-Z0-9-]` only.

---

## 7. Warning Codes

| Code                  | Trigger                                          |
|-----------------------|--------------------------------------------------|
| METADATA_FETCH_FAILED | All or partial metadata sources returned "none"  |
| LANGUAGE_FALLBACK     | Returned language not in requested list           |
| AUTO_GENERATED        | `is_generated` is true                            |
| CACHE_WRITE_FAILED    | Cache directory unwritable or disk full            |

Warnings are generated AFTER cache/fetch merge from the final state.
A cached response with missing metadata produces the same warnings as
the original fresh response.
