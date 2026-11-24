"""Test that summary_title fields are redacted at the action-log boundary.

Spec §8 + CLAUDE.md invariant 8: PII must not enter logs. A real provider
could embed driver names or plate numbers in booking.summary.title; we
strip them in the emit() chokepoint via _redact_summary_title().
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_cab.adapters.action_log import _PHONE, JSONLActionLog, redact


@pytest.fixture
def log(tmp_path: Path) -> JSONLActionLog:
    return JSONLActionLog(log_dir=tmp_path / "logs")


def test_summary_title_name_redacted_on_emit(log: JSONLActionLog, tmp_path: Path) -> None:
    """A summary_title containing a driver name must have the name stripped."""
    log.emit(
        {
            "session_id": "S-redact-1",
            "action_id": "A1",
            "kind": "outcome",
            "tool": "track_booking",
            "summary_title": "Driver Sarah Smith - ABC123",
        }
    )
    rows = log.read("S-redact-1")
    assert len(rows) == 1  # noqa: S101 pytest assertion
    stored_title = rows[0]["summary_title"]
    assert "Sarah Smith" not in stored_title  # noqa: S101 pytest assertion
    assert "[redacted-name]" in stored_title  # noqa: S101 pytest assertion


def test_summary_title_benign_unchanged(log: JSONLActionLog) -> None:
    """Benign summary_title (no names) must pass through unchanged."""
    log.emit(
        {
            "session_id": "S-redact-2",
            "action_id": "A2",
            "kind": "outcome",
            "tool": "track_booking",
            "summary_title": "UberX — ~5 min",
        }
    )
    rows = log.read("S-redact-2")
    assert rows[0]["summary_title"] == "UberX — ~5 min"  # noqa: S101 pytest assertion


def test_summary_title_phone_redacted(log: JSONLActionLog) -> None:
    """A summary_title containing a phone number must have it stripped."""
    log.emit(
        {
            "session_id": "S-redact-3",
            "action_id": "A3",
            "kind": "outcome",
            "tool": "track_booking",
            "summary_title": "Driver +1 555-123-4567",
        }
    )
    rows = log.read("S-redact-3")
    assert "+1 555-123-4567" not in rows[0]["summary_title"]  # noqa: S101 pytest assertion
    assert "[redacted-phone]" in rows[0]["summary_title"]  # noqa: S101 pytest assertion


def test_redact_function_handles_summary_title_directly() -> None:
    """redact() standalone: summary_title key triggers name stripping."""
    payload = {
        "session_id": "S",
        "summary_title": "Driver John Van Der Berg - XYZ999",
    }
    result = redact(payload)
    assert "John Van" not in result["summary_title"]  # noqa: S101 pytest assertion
    assert "[redacted-name]" in result["summary_title"]  # noqa: S101 pytest assertion


def test_non_summary_title_string_unaffected() -> None:
    """Non-summary_title string fields only get phone redaction, not name redaction."""
    payload = {
        "session_id": "S",
        "provider": "uber-sim",
        "note": "This is a note from John Smith",
    }
    result = redact(payload)
    # 'note' is not summary_title — name regex not applied; only phone regex
    assert result["note"] == "This is a note from John Smith"  # noqa: S101 pytest assertion


# ---------------------------------------------------------------------------
# Phone regex contract — pin the redaction boundary mechanically.
#
# The regex must:
#   (a) redact realistic phone shapes that a real provider could embed in
#       summary_title or freeform strings, AND
#   (b) leave alone every shape the action log actually emits today —
#       ISO timestamps, ULIDs, hex idempotency keys, lat/lng floats,
#       option_ids, integer ids.
# Both directions matter: a regex that over-matches corrupts every
# transcript (the bug this PR fixes); a regex that under-matches lets PII
# leak. Pin both.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phone",
    [
        "+1 555-123-4567",  # E.164 with separators
        "+44 20 7946 0958",  # E.164 international
        "(555) 123-4567",  # NANP parenthesised
        "(555)123-4567",  # NANP no space
        "555-123-4567",  # plain 3-3-4 hyphen
        "555.123.4567",  # plain 3-3-4 dot
        "555 123 4567",  # plain 3-3-4 space
    ],
)
def test_phone_regex_redacts_realistic_shapes(phone: str) -> None:
    assert _PHONE.search(phone) is not None, f"expected match for {phone!r}"  # noqa: S101 pytest assertion


@pytest.mark.parametrize(
    "benign",
    [
        "2026-05-19",  # ISO date (the bug this fix addresses)
        "2026-05-19T12:00:00+00:00",  # ISO timestamp
        "01KS5A7M9RS8N2RJTN8MG3S6QZ",  # ULID
        "fd8f1fc601c2f0c249513b98f0f8e6c1650",  # hex idempotency-key prefix
        "37.7749",  # lat/lng float
        "-122.4194",  # lat/lng float negative
        "opt-uberx-123005401501",  # option_id with 12-digit suffix
        "opt-comfort-811856239313",  # option_id with 12-digit suffix
        "1850",  # amount_cents
        "180",  # eta_seconds
    ],
)
def test_phone_regex_does_not_match_log_payload_shapes(benign: str) -> None:
    assert _PHONE.search(benign) is None, f"unexpected match for {benign!r}"  # noqa: S101 pytest assertion
