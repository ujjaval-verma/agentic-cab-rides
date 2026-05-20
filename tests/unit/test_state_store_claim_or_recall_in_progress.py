"""Tests for claim_or_recall 'in_progress' status — spec §10a + CLAUDE.md inv. 5.

A row that was claimed (INSERT succeeded) but never finalised returns
status='in_progress' to prevent a concurrent or restarted caller from
issuing a duplicate provider call.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.core.types import Booking, Draft, Money, Option, RideOptionDetails


def _opt() -> Option:
    return Option(
        option_id="opt-1",
        provider="uber-sim",
        title="UberX",
        price=Money(amount_cents=1500, currency="USD"),
        eta_seconds=180,
        when=None,
        details=RideOptionDetails(vehicle_class="uberx", capacity=4),
        expires_at=datetime(2026, 5, 20, 13, 0, tzinfo=UTC),
    )


def _draft(draft_id: str = "D-ip-1") -> Draft:
    return Draft(
        draft_id=draft_id,
        option_snapshot=_opt(),
        fingerprint="f" * 64,
        created_at=datetime(2026, 5, 20, 12, 0, tzinfo=UTC),
        expires_at=datetime(2026, 5, 20, 12, 0, tzinfo=UTC) + timedelta(minutes=5),
    )


def _booking(booking_id: str = "B-ip-1", draft_id: str = "D-ip-1") -> Booking:
    return Booking(
        booking_id=booking_id,
        draft_id=draft_id,
        status="confirmed",
        provider="uber-sim",
        summary=_opt(),
    )


@pytest.fixture
def store(tmp_path: Path) -> SqliteStateStore:
    s = SqliteStateStore(db_path=tmp_path / "state.db")
    s.apply_schema()
    return s


def test_claim_returns_new_for_fresh_key(store: SqliteStateStore) -> None:
    """A key never seen before must return status='new'."""
    result = store.claim_or_recall("fresh-key-001", scope="confirm")
    assert result.status == "new"
    assert result.booking_id is None


def test_finalized_claim_returns_recalled(store: SqliteStateStore) -> None:
    """A key that was claimed AND finalised must return status='recalled'."""
    store.put_draft(_draft())
    booking = _booking()
    store.put_booking(booking)

    key = "finalized-key-001"
    first = store.claim_or_recall(key, scope="confirm")
    assert first.status == "new"

    store.finalize_idempotency(key, booking=booking)

    second = store.claim_or_recall(key, scope="confirm")
    assert second.status == "recalled"
    assert second.booking_id == "B-ip-1"


def test_claimed_but_not_finalized_returns_in_progress(store: SqliteStateStore) -> None:
    """A key that was claimed but never finalised (crash scenario) must return 'in_progress'."""
    key = "inflight-key-001"

    # Claim once — simulates the first caller inserting the row before provider.confirm.
    first = store.claim_or_recall(key, scope="confirm")
    assert first.status == "new"

    # Do NOT call finalize_idempotency — simulate a crash between claim and finalize.

    # Second caller (same process or restarted process) hits the same key.
    second = store.claim_or_recall(key, scope="confirm")
    assert second.status == "in_progress"
    assert second.booking_id is None
