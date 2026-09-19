"""Unit tests for the typed error contract (CONTRACTS.md section 1)."""

import re
from pathlib import Path

import pytest

import yt_errors
from yt_errors import (
    LIBRARY_EXCEPTION_MAP, InvalidVideoIdError, LanguageNotAvailable, PoTokenRequired,
    TranscriptError, TranscriptNotAvailable, TransientRequestFailed, UpstreamFailure,
    VideoUnavailable, YouTubeIpBlocked, from_library_exception,
)

CONTRACTS = Path(__file__).parent / "CONTRACTS.md"


def _library_exc(name: str, message: str = "boom", **attrs) -> Exception:
    exc = type(name, (Exception,), {})(message)
    for key, value in attrs.items():
        setattr(exc, key, value)
    return exc


def _contract_table_rows(section_start: str, section_end: str) -> list[tuple[str, ...]]:
    """Rows of the markdown tables between two headings in CONTRACTS.md."""
    text = CONTRACTS.read_text(encoding="utf-8")
    body = text[text.index(section_start):text.index(section_end)]
    rows = []
    for line in body.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and not set(line) <= set("|- "):
            rows.append(tuple(cells))
    return rows


class TestTypedErrors:

    def test_every_error_type_is_a_transcript_error(self):
        for cls in (TranscriptNotAvailable, LanguageNotAvailable, VideoUnavailable,
                    InvalidVideoIdError, YouTubeIpBlocked, PoTokenRequired,
                    TransientRequestFailed, UpstreamFailure):
            assert issubclass(cls, TranscriptError)

    def test_only_transient_is_retryable(self):
        retryable = {cls for cls in LIBRARY_EXCEPTION_MAP.values() if cls.retryable}
        assert retryable == {TransientRequestFailed}
        assert not UpstreamFailure.retryable

    def test_defaults(self):
        err = TranscriptNotAvailable("nope")
        assert str(err) == "nope"
        assert err.http_status is None
        assert err.fallback_attempted is False
        assert err.actual_attempts == 1

    @pytest.mark.parametrize("attempts", [0, -1])
    def test_rejects_invalid_attempt_count(self, attempts):
        with pytest.raises(ValueError):
            TranscriptError("x", actual_attempts=attempts)


class TestLibraryAdapter:

    @pytest.mark.parametrize("name,error_cls", sorted(LIBRARY_EXCEPTION_MAP.items()))
    def test_class_name_selects_type(self, name, error_cls):
        err = from_library_exception(_library_exc(name))
        assert type(err) is error_cls

    def test_typed_error_passes_through_unchanged(self):
        original = VideoUnavailable("gone", actual_attempts=2)
        assert from_library_exception(original) is original

    def test_source_exception_is_chained(self):
        source = _library_exc("TranscriptsDisabled")
        assert from_library_exception(source).__cause__ is source

    def test_attempts_and_fallback_carried_over(self):
        source = _library_exc("YouTubeRequestFailed", actual_attempts=3,
                              fallback_attempted=True)
        err = from_library_exception(source)
        assert err.actual_attempts == 3
        assert err.fallback_attempted is True

    def test_empty_message_falls_back_to_class_name(self):
        assert str(from_library_exception(_library_exc("TranscriptsDisabled", ""))) \
            == "TranscriptsDisabled"

    @pytest.mark.parametrize("message,error_cls", [
        ("Subtitles are disabled for this video", TranscriptNotAvailable),
        ("This video is private", VideoUnavailable),
        ("The video is no longer available", VideoUnavailable),
        ("A PO token is required", PoTokenRequired),
        ("Request blocked by YouTube", YouTubeIpBlocked),
        ("HTTP 429 too many requests", UpstreamFailure),
    ])
    def test_message_parsing_is_last_resort_for_unknown_classes(self, message, error_cls):
        err = from_library_exception(_library_exc("SomeNewException", message))
        assert type(err) is error_cls
        assert str(err) == message

    def test_unknown_exception_is_non_retryable_upstream_failure(self):
        err = from_library_exception(_library_exc("SomeNewException", "weird"))
        assert type(err) is UpstreamFailure
        assert err.code == yt_errors.RATE_LIMITED
        assert err.retryable is False
        assert str(err) == "Request failed after 1 attempts: weird"

    def test_known_class_name_wins_over_message(self):
        err = from_library_exception(_library_exc("TranscriptsDisabled", "video is private"))
        assert type(err) is TranscriptNotAvailable


class TestContractDocument:
    """CONTRACTS.md section 1 must describe exactly what the code does."""

    def test_type_table_matches_code(self):
        rows = _contract_table_rows("| Error type", "### Upstream adapter")
        documented = {name: (code, retry) for name, code, retry in rows if name != "Error type"}
        actual = {
            cls.__name__: (cls.code, "yes" if cls.retryable else "no")
            for cls in {*LIBRARY_EXCEPTION_MAP.values(), InvalidVideoIdError}
        }
        assert documented == actual

    def test_library_mapping_table_matches_code(self):
        rows = _contract_table_rows("### Permanent (non-retryable)", "### Internal")
        documented = {r[0]: r[1] for r in rows
                      if re.fullmatch(r"[A-Za-z]+", r[0]) and r[0] != "Exception"}
        actual = {name: cls.code for name, cls in LIBRARY_EXCEPTION_MAP.items()}
        assert documented == actual
