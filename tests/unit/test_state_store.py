from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.core.types import (
    Booking,
    Draft,
    Money,
    Option,
    RideOptionDetails,
)


def _opt() -> Option:
    return Option(
        option_id="opt-1",
        provider="uber-sim",
        title="UberX",
        price=Money(amount_cents=1500, currency="USD"),
        eta_seconds=180,
        when=None,
        details=RideOptionDetails(vehicle_class="uberx", capacity=4),
        expires_at=datetime(2026, 5, 19, 13, 0, tzinfo=UTC),
    )


def _draft(draft_id: str = "D1", expires_in_seconds: int = 60) -> Draft:
    return Draft(
        draft_id=draft_id,
        option_snapshot=_opt(),
        fingerprint="f" * 64,
        created_at=datetime(2026, 5, 19, 12, 0, tzinfo=UTC),
        expires_at=datetime(2026, 5, 19, 12, 0, tzinfo=UTC) + timedelta(seconds=expires_in_seconds),
    )


def _booking(booking_id: str = "B1") -> Booking:
    return Booking(
        booking_id=booking_id,
        draft_id="D1",
        status="confirmed",
        provider="uber-sim",
        summary=_opt(),
    )


@pytest.fixture
def store(tmp_path: Path) -> SqliteStateStore:
    s = SqliteStateStore(db_path=tmp_path / "state.db")
    s.apply_schema()
    return s


def test_put_and_get_draft(store: SqliteStateStore) -> None:
    d = _draft()
    store.put_draft(d)
    got = store.get_draft("D1")
    assert got is not None
    assert got.draft_id == "D1"
    assert got.option_snapshot.price.amount_cents == 1500


def test_consume_draft_is_atomic_single_use(store: SqliteStateStore) -> None:
    store.put_draft(_draft())
    now = datetime(2026, 5, 19, 12, 0, 30, tzinfo=UTC)
    assert store.consume_draft("D1", at=now) is True
    assert store.consume_draft("D1", at=now) is False


def test_claim_or_recall_new_then_recalled(store: SqliteStateStore) -> None:
    # set up a booking row first (FK)
    store.put_draft(_draft())
    booking = _booking()
    store.put_booking(booking)
    key = "K1"
    first = store.claim_or_recall(key, scope="confirm")
    assert first.status == "new"
    assert first.booking is None
    store.finalize_idempotency(key, booking=booking)
    second = store.claim_or_recall(key, scope="confirm")
    assert second.status == "recalled"
    assert second.booking is not None
    assert second.booking.booking_id == "B1"


def test_schema_apply_is_idempotent(store: SqliteStateStore) -> None:
    store.apply_schema()
    store.apply_schema()  # second apply must not raise
