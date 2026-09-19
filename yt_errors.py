"""Typed transcript errors and the interim youtube-transcript-api adapter.

Contract: CONTRACTS.md section 1. Every transcript failure surfaces as a
TranscriptError carrying a stable public `code`, a `retryable` flag, the number
of attempts made, and whether a language fallback was tried. The backend that
raised the failure is an implementation detail: when the library dependency is
replaced, `from_library_exception` and LIBRARY_EXCEPTION_MAP are deleted and the
public error types and codes stay as they are.
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


# -- Interim adapter: youtube-transcript-api ----------------------------------

# Classification is by exception class name so the library stays an optional
# import. Deleted together with the dependency.
LIBRARY_EXCEPTION_MAP: dict[str, type[TranscriptError]] = {
    "TranscriptsDisabled": TranscriptNotAvailable,
    "NoTranscriptFound": LanguageNotAvailable,
    "VideoUnavailable": VideoUnavailable,
    "VideoUnplayable": VideoUnavailable,
    "InvalidVideoId": InvalidVideoIdError,
    "AgeRestricted": VideoUnavailable,
    "IpBlocked": YouTubeIpBlocked,
    "RequestBlocked": YouTubeIpBlocked,
    "PoTokenRequired": PoTokenRequired,
    "NotTranslatable": LanguageNotAvailable,
    "TranslationLanguageNotAvailable": LanguageNotAvailable,
    "YouTubeRequestFailed": TransientRequestFailed,
    "YouTubeDataUnparsable": UpstreamFailure,
    "FailedToCreateConsentCookie": UpstreamFailure,
    "CookieError": UpstreamFailure,
    "CookieInvalid": UpstreamFailure,
    "CookiePathInvalid": UpstreamFailure,
    "CouldNotRetrieveTranscript": UpstreamFailure,
    "YouTubeTranscriptApiException": UpstreamFailure,
}


def _classify_by_message(message: str) -> type[TranscriptError] | None:
    """Last resort for exceptions whose class is not in LIBRARY_EXCEPTION_MAP."""
    msg = message.lower()
    if "disabled" in msg:
        return TranscriptNotAvailable
    if "unavailable" in msg or "private" in msg or "no longer available" in msg:
        return VideoUnavailable
    if "po token" in msg:
        return PoTokenRequired
    if "blocked" in msg:
        return YouTubeIpBlocked
    if "429" in msg:
        return UpstreamFailure
    return None


def from_library_exception(exc: Exception) -> TranscriptError:
    """Convert any exception into a TranscriptError.

    A TranscriptError passes through unchanged. Otherwise the class name selects
    the type, then message parsing, then UpstreamFailure. `actual_attempts` and
    `fallback_attempted` set on the source exception are carried over, and the
    source is chained as `__cause__`.
    """
    if isinstance(exc, TranscriptError):
        return exc
    attempts = max(1, getattr(exc, "actual_attempts", 1))
    fallback = bool(getattr(exc, "fallback_attempted", False))
    cls_name = type(exc).__name__
    error_cls = LIBRARY_EXCEPTION_MAP.get(cls_name)
    if error_cls is not None:
        message = str(exc) or cls_name
    else:
        error_cls = _classify_by_message(str(exc))
        if error_cls is not None:
            message = str(exc)
        else:
            error_cls = UpstreamFailure
            message = f"Request failed after {attempts} attempts: {exc}"
    err = error_cls(message, fallback_attempted=fallback, actual_attempts=attempts)
    err.__cause__ = exc
    return err
