"""Unit tests for tools/list_active.py — read-only tool, emits requested + outcome only."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pytest

from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.core.types import Booking, Money, Option, RideOptionDetails
from agentic_cab.tools.list_active import ListActiveArgs, list_active_bookings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EXPIRES = datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC)

_BookingStatus = Literal[
    "confirmed",
    "driver_assigned",
    "en_route",
    "arrived",
    "in_progress",
    "completed",
    "cancelled",
    "failed",
]


def _make_booking(booking_id: str, status: _BookingStatus = "en_route") -> Booking:
    return Booking(
        booking_id=booking_id,
        draft_id=f"DRAFT-{booking_id}",
        status=status,
        provider="uber_sim",
        summary=Option(
            option_id=f"OPT-{booking_id}",
            provider="uber_sim",
            title="UberX",
            price=Money(amount_cents=1850, currency="USD"),
            eta_seconds=300,
            when=None,
            details=RideOptionDetails(vehicle_class="uberx", capacity=4),
            expires_at=_EXPIRES,
        ),
    )


def _make_store(tmp_path: Path, bookings: list[Booking]) -> SqliteStateStore:
    store = SqliteStateStore(db_path=tmp_path / "state.db")
    store.apply_schema()
    for b in bookings:
        store.put_booking(b)
    return store


# ---------------------------------------------------------------------------
# Stub StateStore — minimal fake for isolation tests
# ---------------------------------------------------------------------------


class _StubStore:
    def __init__(self, bookings: list[Booking]) -> None:
        self._bookings = bookings

    def put_draft(self, draft: Any) -> None:
        raise NotImplementedError

    def get_draft(self, draft_id: str) -> None:
        return None

    def consume_draft(self, draft_id: str, at: Any) -> bool:
        raise NotImplementedError

    def get_draft_consumed_at(self, draft_id: str) -> None:
        return None

    def put_booking(self, booking: Booking) -> None:
        raise NotImplementedError

    def get_booking(self, booking_id: str) -> Booking | None:
        return None

    def update_booking_status(self, booking_id: str, status: str, event: Any) -> None:
        raise NotImplementedError

    def list_active_bookings(self) -> list[Booking]:
        return self._bookings

    def claim_or_recall(self, key: str, scope: Any) -> Any:
        raise NotImplementedError

    def finalize_idempotency(self, key: str, booking: Booking) -> None:
        raise NotImplementedError

    def release_claim(self, key: str, scope: Any) -> None:
        raise NotImplementedError

    def find_active_booking_by_intent_hash(self, intent_hash_hex: str) -> Booking | None:
        raise NotImplementedError

    def count_terminal_bookings_by_intent_hash(self, intent_hash_hex: str) -> int:
        raise NotImplementedError

    def set_booking_intent_hash(self, booking_id: str, intent_hash_hex: str) -> None:
        raise NotImplementedError

    def put_pending_confirmation(
        self, subject_id: str, session_id: str, request_json: str, expires_at: Any
    ) -> None:
        raise NotImplementedError

    def pop_pending_confirmation(self, subject_id: str) -> None:
        return None

    def list_pending_confirmations(self, now: Any) -> list:
        return []


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_returns_empty_list(tmp_path: Path) -> None:
    log = JSONLActionLog(log_dir=tmp_path / "logs")
    store = _StubStore(bookings=[])
    args = ListActiveArgs()

    result = await list_active_bookings(
        args,
        store=store,
        log=log,
        session_id="S1",
        turn_id="T1",
    )

    assert result == {"bookings": []}

    rows = log.read("S1")
    outcome = rows[1]
    assert outcome["kind"] == "outcome"
    assert outcome["status"] == "ok"
    assert outcome["result"] == {"bookings": []}


@pytest.mark.asyncio
async def test_filters_completed_cancelled_failed(tmp_path: Path) -> None:
    """Only non-terminal bookings appear in the result.

    Uses a stub store whose list_active_bookings already returns only active
    bookings (mirroring the SQL WHERE clause in SqliteStateStore), then verifies
    the tool surface maps them correctly and excludes terminal statuses.
    The SQL filter itself is exercised by SqliteStateStore's own contract test.
    """
    terminal_statuses: list[str] = ["completed", "cancelled", "failed"]
    active_statuses: list[_BookingStatus] = ["en_route", "confirmed", "driver_assigned"]

    active_bookings = [
        _make_booking(f"BID-ACTIVE-{i:03d}", status=s) for i, s in enumerate(active_statuses)
    ]
    # Stub returns only active bookings, matching what the SQL filter produces
    store = _StubStore(bookings=active_bookings)

    log = JSONLActionLog(log_dir=tmp_path / "logs")
    args = ListActiveArgs()

    result = await list_active_bookings(
        args,
        store=store,
        log=log,
        session_id="S2",
        turn_id="T2",
    )

    returned_statuses = {b["status"] for b in result["bookings"]}
    returned_ids = {b["booking_id"] for b in result["bookings"]}
    expected_ids = {f"BID-ACTIVE-{i:03d}" for i in range(len(active_statuses))}

    assert returned_ids == expected_ids
    assert not returned_statuses.intersection(set(terminal_statuses))


@pytest.mark.asyncio
async def test_emits_requested_then_outcome_only(tmp_path: Path) -> None:
    log = JSONLActionLog(log_dir=tmp_path / "logs")
    store = _StubStore(bookings=[_make_booking("BID-001")])
    args = ListActiveArgs()

    await list_active_bookings(
        args,
        store=store,
        log=log,
        session_id="S3",
        turn_id="T3",
    )

    rows = log.read("S3")
    assert len(rows) == 2, f"Expected exactly 2 rows, got {len(rows)}: {rows}"

    requested, outcome = rows
    assert requested["kind"] == "requested"
    assert outcome["kind"] == "outcome"
    assert requested["action_id"] == outcome["action_id"]

    kinds = {r["kind"] for r in rows}
    assert "verified" not in kinds
    assert "executed" not in kinds
