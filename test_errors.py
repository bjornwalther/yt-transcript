"""Unit tests for the typed error contract (CONTRACTS.md section 1)."""

from pathlib import Path

import pytest

import yt_errors
from yt_errors import (
    InvalidVideoIdError, LanguageNotAvailable, PoTokenRequired, TranscriptError,
    TranscriptNotAvailable, TransientRequestFailed, UpstreamFailure, VideoUnavailable,
    YouTubeIpBlocked, as_transcript_error,
)

CONTRACTS = Path(__file__).parent / "CONTRACTS.md"


def _contract_table_rows(section_start: str, section_end: str) -> list[tuple[str, ...]]:
    """Rows of the markdown tables between two markers in CONTRACTS.md."""
    text = CONTRACTS.read_text(encoding="utf-8")
    body = text[text.index(section_start):text.index(section_end)]
    rows = []
    for line in body.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and not set(line) <= set("|- "):
            rows.append(tuple(cells))
    return rows


class TestTypedErrors:

    def test_only_transient_is_retryable(self):
        retryable = {cls for cls in TranscriptError.__subclasses__() if cls.retryable}
        assert retryable == {TransientRequestFailed}
        assert not UpstreamFailure.retryable

    def test_defaults(self):
        err = TranscriptNotAvailable("nope")
        assert str(err) == "nope"
        assert err.http_status is None
        assert err.fallback_attempted is False
        assert err.actual_attempts == 1

    def test_attributes_are_carried(self):
        err = TransientRequestFailed("x", http_status=503, fallback_attempted=True,
                                     actual_attempts=3)
        assert (err.http_status, err.fallback_attempted, err.actual_attempts) == (503, True, 3)

    @pytest.mark.parametrize("attempts", [0, -1])
    def test_rejects_invalid_attempt_count(self, attempts):
        with pytest.raises(ValueError):
            TranscriptError("x", actual_attempts=attempts)

    @pytest.mark.parametrize("cls,code", [
        (TranscriptNotAvailable, "TRANSCRIPT_NOT_AVAILABLE"),
        (LanguageNotAvailable, "LANGUAGE_NOT_AVAILABLE"),
        (VideoUnavailable, "VIDEO_UNAVAILABLE"),
        (InvalidVideoIdError, "INVALID_URL"),
        (YouTubeIpBlocked, "YOUTUBE_IP_BLOCKED"),
        (PoTokenRequired, "PO_TOKEN_REQUIRED"),
        (TransientRequestFailed, "RATE_LIMITED"),
        (UpstreamFailure, "RATE_LIMITED"),
    ])
    def test_public_codes(self, cls, code):
        assert cls.code == code


class TestBoundary:

    def test_typed_error_passes_through_unchanged(self):
        original = VideoUnavailable("gone", actual_attempts=2)
        assert as_transcript_error(original) is original

    def test_unexpected_exception_becomes_non_retryable_upstream_failure(self):
        source = RuntimeError("weird")
        err = as_transcript_error(source)
        assert type(err) is UpstreamFailure
        assert err.code == yt_errors.RATE_LIMITED
        assert err.retryable is False
        assert err.__cause__ is source
        assert str(err) == "Unexpected RuntimeError: weird"


class TestContractDocument:
    """CONTRACTS.md section 1 must describe exactly what the code does."""

    def test_type_table_matches_code(self):
        rows = _contract_table_rows("| Error type", "### Backend")
        documented = {name: (code, retry) for name, code, retry, _ in rows if name != "Error type"}
        actual = {cls.__name__: (cls.code, "yes" if cls.retryable else "no")
                  for cls in TranscriptError.__subclasses__()}
        assert documented == actual
