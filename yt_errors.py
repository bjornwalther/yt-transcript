"""Typed transcript errors.

Contract: CONTRACTS.md section 1. Every transcript failure surfaces as a
TranscriptError carrying a stable public `code`, a `retryable` flag, the number
of attempts made, and whether a language fallback was tried.
"""

# -- Public error codes (CONTRACTS.md section 1) ------------------------------

INVALID_URL = "INVALID_URL"
TRANSCRIPT_NOT_AVAILABLE = "TRANSCRIPT_NOT_AVAILABLE"
LANGUAGE_NOT_AVAILABLE = "LANGUAGE_NOT_AVAILABLE"
RATE_LIMITED = "RATE_LIMITED"
YOUTUBE_IP_BLOCKED = "YOUTUBE_IP_BLOCKED"
VIDEO_UNAVAILABLE = "VIDEO_UNAVAILABLE"
PO_TOKEN_REQUIRED = "PO_TOKEN_REQUIRED"


# -- Typed errors -------------------------------------------------------------

class TranscriptError(Exception):
    """Base class. Subclasses fix `code` and the default `retryable`."""

    code: str = RATE_LIMITED
    retryable: bool = False

    def __init__(self, message: str, *, http_status: int | None = None,
                 fallback_attempted: bool = False, actual_attempts: int = 1):
        if actual_attempts < 1:
            raise ValueError(f"actual_attempts must be >= 1, got {actual_attempts}")
        super().__init__(message)
        self.http_status = http_status
        self.fallback_attempted = fallback_attempted
        self.actual_attempts = actual_attempts


class TranscriptNotAvailable(TranscriptError):
    code = TRANSCRIPT_NOT_AVAILABLE


class LanguageNotAvailable(TranscriptError):
    code = LANGUAGE_NOT_AVAILABLE


class VideoUnavailable(TranscriptError):
    code = VIDEO_UNAVAILABLE


class InvalidVideoIdError(TranscriptError):
    code = INVALID_URL


class YouTubeIpBlocked(TranscriptError):
    code = YOUTUBE_IP_BLOCKED


class PoTokenRequired(TranscriptError):
    code = PO_TOKEN_REQUIRED


class TransientRequestFailed(TranscriptError):
    """The only retryable error type."""

    code = RATE_LIMITED
    retryable = True


class UpstreamFailure(TranscriptError):
    """Catch-all for unclassified failures. Never retried."""

    code = RATE_LIMITED


# -- Boundary ----------------------------------------------------------------

def as_transcript_error(exc: Exception) -> TranscriptError:
    """Return `exc` if it is a TranscriptError, else wrap it as a non-retryable
    UpstreamFailure chained to `exc`. Safety net for unexpected exceptions."""
    if isinstance(exc, TranscriptError):
        return exc
    err = UpstreamFailure(f"Unexpected {type(exc).__name__}: {exc}")
    err.__cause__ = exc
    return err
