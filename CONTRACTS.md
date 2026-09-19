# yt-transcript-mcp Contract Specification

Single source of truth for the MCP server's behavior. Implementation and tests
are both derived from this document. Any change to behavior starts here.

---

## 1. Error Classification

### Typed errors

Every transcript failure is a `TranscriptError` (module `yt_errors`). The
public error codes below are the contract; the backend that raised the failure
is an implementation detail. A `TranscriptError` carries:

| Attribute            | Meaning                                                    |
|----------------------|------------------------------------------------------------|
| `code`               | Public MCP error code (stable across backends)             |
| `retryable`          | Whether retrying may help                                  |
| `actual_attempts`    | Fetch attempts made before giving up (1 when never retried) |
| `fallback_attempted` | A language fallback track was selected before the failure  |
| `http_status`        | Upstream HTTP status when known, otherwise `null`          |

| Error type              | MCP error code           | Retryable |
|-------------------------|--------------------------|-----------|
| TranscriptNotAvailable  | TRANSCRIPT_NOT_AVAILABLE | no        |
| LanguageNotAvailable    | LANGUAGE_NOT_AVAILABLE   | no        |
| VideoUnavailable        | VIDEO_UNAVAILABLE        | no        |
| InvalidVideoIdError     | INVALID_URL              | no        |
| YouTubeIpBlocked        | YOUTUBE_IP_BLOCKED       | no        |
| PoTokenRequired         | PO_TOKEN_REQUIRED        | no        |
| TransientRequestFailed  | RATE_LIMITED             | yes       |
| UpstreamFailure         | RATE_LIMITED             | no        |

`UpstreamFailure` is the catch-all for anything unclassified.

### Upstream adapter (interim)

Until the native InnerTube backend replaces `youtube-transcript-api`, the
adapter `from_library_exception` converts the library's exceptions into typed
errors. Classification is by exception class name. Message parsing is a
last-resort fallback for unknown generic exceptions only. The adapter and this
table are deleted together with the library dependency; the public codes and
the type table above do not change.

### Retry policy

**Allowlist**: only `TransientRequestFailed` is retried. Everything else
raises immediately on first attempt. Unknown/unrecognized exceptions are
treated as non-retryable.

### Permanent (non-retryable)

Upstream exception class to MCP error code:

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

Adapts to `TransientRequestFailed`. No other exception type is retried,
including unknown/generic exceptions.

### Internal

| Condition                 | MCP error code                            |
|--------------------------|-------------------------------------------|
| URL parse failure         | INVALID_URL                               |
| Non-YouTube host          | INVALID_URL                               |
| All metadata sources fail | METADATA_FETCH_FAILED (warning, not error) |

### Pending: native backend (not in effect)

These rules take effect when the native InnerTube backend replaces
`youtube-transcript-api`. They cannot apply earlier because the library does
not expose HTTP status. Until then, the behavior above is authoritative.

- HTTP 429 maps to `RATE_LIMITED` with `retryable: true` (the library reports
  it as `IpBlocked`, which is `YOUTUBE_IP_BLOCKED`, non-retryable).
- Retry only 5xx, timeouts, and connection errors. Other 4xx statuses (for
  example 403, 404) are not retried.
- Errors expose `http_status` when the failure came from an HTTP response.
- Clients are tried in order `android_vr`, `ios`, `android`. The chain advances
  only on client-specific blocks: a bot check (`YOUTUBE_IP_BLOCKED`) or a
  required PO token (`PO_TOKEN_REQUIRED`). Any other failure ends the fetch.
  When every client is blocked, the last error is raised and its message lists
  the clients tried.
- A caption track whose URL carries `exp=xpe`, or a 200 caption response with an
  empty body (how YouTube answers a request missing a required token), is
  `PO_TOKEN_REQUIRED`.
- `fallback_attempted` is true on any error raised after a language fallback
  track was selected, including when downloading that track fails.
- Caption URLs are followed only if they are `https` on `youtube.com` or a
  subdomain; other tracks are ignored.
- `&fmt=srv3` is removed from caption URLs so YouTube answers in the simple
  `<transcript><text>` format, as `youtube-transcript-api` does. The srv3
  format is still parsed as a fallback.
- Caption segments with no text are dropped, so `segment_count` may differ from
  the library for videos that contain empty caption elements.

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
