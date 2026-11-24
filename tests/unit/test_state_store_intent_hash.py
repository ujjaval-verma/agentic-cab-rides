"""Tests for the three intent_hash methods on SqliteStateStore."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.core.types import (
    Booking,
    Money,
    Option,
    RideOptionDetails,
)

_BookingStatusLiteral = Literal[
    "confirmed",
    "driver_assigned",
    "en_route",
    "arrived",
    "in_progress",
    "completed",
    "cancelled",
    "failed",
    "requested",
    "matched",
]


def _store(tmp_path: Path) -> SqliteStateStore:
    s = SqliteStateStore(tmp_path / "state.db")
    s.apply_schema()
    return s


def _opt(option_id: str = "opt-1") -> Option:
    return Option(
        option_id=option_id,
        provider="uber-sim",
        title="UberX",
        price=Money(amount_cents=1000, currency="USD"),
        eta_seconds=300,
        when=None,
        details=RideOptionDetails(vehicle_class="uberx", capacity=4),
        expires_at=datetime(2026, 5, 20, 13, 0, tzinfo=UTC),
    )


def _draft_row(store: SqliteStateStore, draft_id: str) -> None:
    # Bookings reference drafts via FK; insert a minimal draft row directly.
    with store._write() as conn:
        conn.execute(
            "INSERT INTO drafts(draft_id, option_snapshot, fingerprint, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                draft_id,
                _opt().model_dump_json(),
                "fp",
                datetime(2026, 5, 20, 12, 0, tzinfo=UTC).isoformat(),
                datetime(2026, 5, 20, 13, 0, tzinfo=UTC).isoformat(),
            ),
        )


def _booking(
    booking_id: str, draft_id: str, status: _BookingStatusLiteral = "confirmed"
) -> Booking:
    return Booking(
        booking_id=booking_id,
        draft_id=draft_id,
        provider="uber-sim",
        status=status,
        summary=_opt(),
    )


def test_find_active_returns_none_when_no_match(tmp_path: Path) -> None:
    s = _store(tmp_path)
    assert s.find_active_booking_by_intent_hash("ih-abc") is None  # noqa: S101 pytest assertion


def test_find_active_returns_match_after_set(tmp_path: Path) -> None:
    s = _store(tmp_path)
    _draft_row(s, "draft-1")
    s.put_booking(_booking("bk-1", "draft-1", status="confirmed"))
    s.set_booking_intent_hash("bk-1", "ih-abc")
    found = s.find_active_booking_by_intent_hash("ih-abc")
    assert found is not None  # noqa: S101 pytest assertion
    assert found.booking_id == "bk-1"  # noqa: S101 pytest assertion


def test_find_active_skips_terminal(tmp_path: Path) -> None:
    s = _store(tmp_path)
    _draft_row(s, "draft-1")
    s.put_booking(_booking("bk-1", "draft-1", status="completed"))
    s.set_booking_intent_hash("bk-1", "ih-abc")
    assert s.find_active_booking_by_intent_hash("ih-abc") is None  # noqa: S101 pytest assertion


def test_find_active_skips_null_intent_hash(tmp_path: Path) -> None:
    s = _store(tmp_path)
    _draft_row(s, "draft-1")
    s.put_booking(_booking("bk-1", "draft-1", status="confirmed"))
    # intent_hash never set
    assert s.find_active_booking_by_intent_hash("ih-abc") is None  # noqa: S101 pytest assertion


def test_count_terminal_returns_zero_when_no_match(tmp_path: Path) -> None:
    s = _store(tmp_path)
    assert s.count_terminal_bookings_by_intent_hash("ih-abc") == 0  # noqa: S101 pytest assertion


def test_count_terminal_counts_only_terminal(tmp_path: Path) -> None:
    s = _store(tmp_path)
    _draft_row(s, "draft-1")
    _draft_row(s, "draft-2")
    _draft_row(s, "draft-3")
    s.put_booking(_booking("bk-1", "draft-1", status="completed"))
    s.put_booking(_booking("bk-2", "draft-2", status="cancelled"))
    s.put_booking(_booking("bk-3", "draft-3", status="confirmed"))
    for b in ("bk-1", "bk-2", "bk-3"):
        s.set_booking_intent_hash(b, "ih-abc")
    assert s.count_terminal_bookings_by_intent_hash("ih-abc") == 2  # noqa: S101 pytest assertion


def test_set_booking_intent_hash_is_idempotent_overwrite(tmp_path: Path) -> None:
    s = _store(tmp_path)
    _draft_row(s, "draft-1")
    s.put_booking(_booking("bk-1", "draft-1"))
    s.set_booking_intent_hash("bk-1", "ih-aaa")
    s.set_booking_intent_hash("bk-1", "ih-bbb")
    assert s.find_active_booking_by_intent_hash("ih-aaa") is None  # noqa: S101 pytest assertion
    found = s.find_active_booking_by_intent_hash("ih-bbb")
    assert found is not None  # noqa: S101 pytest assertion
    assert found.booking_id == "bk-1"  # noqa: S101 pytest assertion
