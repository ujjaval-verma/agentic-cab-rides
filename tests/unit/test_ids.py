import re
import uuid
from pathlib import Path

import pytest

from agentic_cab.core.ids import (
    cancel_idempotency_key,
    new_action_id,
    new_draft_id,
    new_idempotency_key,
    new_principal_id,
    new_session_id,
    new_turn_id,
    parse_session_id,
)

_VALID_ULID = "01KS5HMK9QJZ8PCJ92B9VM7BRA"


@pytest.mark.parametrize("raw", [new_session_id(), _VALID_ULID])
def test_session_id_accepts_ulid(raw: str) -> None:
    assert parse_session_id(raw) == raw


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "../etc/passwd",
        "/etc/passwd",
        "..\\x",
        _VALID_ULID.lower(),
        _VALID_ULID[:25],
        _VALID_ULID + "A",
        "8" + _VALID_ULID[1:],
        "I" + _VALID_ULID[1:],
        "L" + _VALID_ULID[1:],
        "O" + _VALID_ULID[1:],
        "U" + _VALID_ULID[1:],
        _VALID_ULID[:-1] + "I",
        _VALID_ULID[:-1] + "L",
        _VALID_ULID[:-1] + "O",
        _VALID_ULID[:-1] + "U",
        _VALID_ULID + "\n",
        str(uuid.uuid4()),
    ],
)
def test_session_id_rejects(raw: str) -> None:
    with pytest.raises(ValueError, match="pattern"):
        parse_session_id(raw)


def test_committed_transcript_session_ids_validate() -> None:
    root = Path(__file__).resolve().parents[2] / "transcripts"
    found = 0
    for md in root.glob("*.md"):
        for sid in re.findall(r'"session_id": "([^"]+)"', md.read_text(encoding="utf-8")):
            parse_session_id(sid)
            found += 1
    assert found > 0


def test_ids_are_ulids():
    for factory in (
        new_session_id,
        new_turn_id,
        new_action_id,
        new_draft_id,
        new_idempotency_key,
        new_principal_id,
    ):
        value = factory()
        assert isinstance(value, str)
        assert len(value) == 26  # ULID canonical length


def test_ids_are_sortable_by_creation():
    a = new_session_id()
    b = new_session_id()
    assert a < b  # ULIDs are time-monotonic at millisecond resolution


# --- cancel_idempotency_key helpers ---


def test_cancel_idempotency_key_is_deterministic() -> None:
    key_a = cancel_idempotency_key("booking-123", 500, "confirmed")
    key_b = cancel_idempotency_key("booking-123", 500, "confirmed")
    assert key_a == key_b
    assert isinstance(key_a, str)
    assert len(key_a) == 64  # sha256 hex digest


def test_cancel_idempotency_key_changes_with_fee() -> None:
    key_a = cancel_idempotency_key("booking-123", 500, "confirmed")
    key_b = cancel_idempotency_key("booking-123", 1000, "confirmed")
    assert key_a != key_b


def test_cancel_idempotency_key_changes_with_status() -> None:
    key_a = cancel_idempotency_key("booking-123", 500, "confirmed")
    key_b = cancel_idempotency_key("booking-123", 500, "in_progress")
    assert key_a != key_b


def test_cancel_idempotency_key_changes_with_booking_id() -> None:
    key_a = cancel_idempotency_key("booking-aaa", 500, "confirmed")
    key_b = cancel_idempotency_key("booking-bbb", 500, "confirmed")
    assert key_a != key_b
