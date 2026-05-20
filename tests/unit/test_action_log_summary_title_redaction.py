"""Test that summary_title fields are redacted at the action-log boundary.

Spec §8 + CLAUDE.md invariant 8: PII must not enter logs. A real provider
could embed driver names or plate numbers in booking.summary.title; we
strip them in the emit() chokepoint via _redact_summary_title().
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_cab.adapters.action_log import JSONLActionLog, redact


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
    assert len(rows) == 1
    stored_title = rows[0]["summary_title"]
    assert "Sarah Smith" not in stored_title
    assert "[redacted-name]" in stored_title


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
    assert rows[0]["summary_title"] == "UberX — ~5 min"


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
    assert "+1 555-123-4567" not in rows[0]["summary_title"]
    assert "[redacted-phone]" in rows[0]["summary_title"]


def test_redact_function_handles_summary_title_directly() -> None:
    """redact() standalone: summary_title key triggers name stripping."""
    payload = {
        "session_id": "S",
        "summary_title": "Driver John Van Der Berg - XYZ999",
    }
    result = redact(payload)
    assert "John Van" not in result["summary_title"]
    assert "[redacted-name]" in result["summary_title"]


def test_non_summary_title_string_unaffected() -> None:
    """Non-summary_title string fields only get phone redaction, not name redaction."""
    payload = {
        "session_id": "S",
        "provider": "uber-sim",
        "note": "This is a note from John Smith",
    }
    result = redact(payload)
    # 'note' is not summary_title — name regex not applied; only phone regex
    assert result["note"] == "This is a note from John Smith"
