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
    assert got is not None  # noqa: S101 pytest assertion
    assert got.draft_id == "D1"  # noqa: S101 pytest assertion
    assert got.option_snapshot.price.amount_cents == 1500  # noqa: S101 pytest assertion


def test_consume_draft_is_atomic_single_use(store: SqliteStateStore) -> None:
    store.put_draft(_draft())
    now = datetime(2026, 5, 19, 12, 0, 30, tzinfo=UTC)
    assert store.consume_draft("D1", at=now) is True  # noqa: S101 pytest assertion
    assert store.consume_draft("D1", at=now) is False  # noqa: S101 pytest assertion


def test_claim_or_recall_new_then_recalled(store: SqliteStateStore) -> None:
    # set up a booking row first (FK)
    store.put_draft(_draft())
    booking = _booking()
    store.put_booking(booking)
    key = "K1"
    first = store.claim_or_recall(key, scope="confirm")
    assert first.status == "new"  # noqa: S101 pytest assertion
    assert first.booking_id is None  # noqa: S101 pytest assertion
    store.finalize_idempotency(key, booking=booking)
    second = store.claim_or_recall(key, scope="confirm")
    assert second.status == "recalled"  # noqa: S101 pytest assertion
    assert second.booking_id == "B1"  # noqa: S101 pytest assertion


def test_claim_or_recall_in_progress_returns_in_progress(store: SqliteStateStore) -> None:
    """Claimed but never finalised → on re-call with same key, return status='in_progress'.

    P3 restart-survival semantic (T30): a claimed-but-not-finalised row signals
    that a prior call is in-flight (or crashed). The caller must NOT re-issue a
    provider call; instead it surfaces 'in_progress' to the agent.
    """
    key = "K-CRASH"
    first = store.claim_or_recall(key, scope="confirm")
    assert first.status == "new"  # noqa: S101 pytest assertion
    # simulate crash: no finalize_idempotency call
    second = store.claim_or_recall(key, scope="confirm")
    assert second.status == "in_progress"  # noqa: S101 pytest assertion
    assert second.booking_id is None  # noqa: S101 pytest assertion


def test_schema_apply_is_idempotent(store: SqliteStateStore) -> None:
    store.apply_schema()
    store.apply_schema()  # second apply must not raise


def test_get_draft_consumed_at_returns_none_until_consumed(store: SqliteStateStore) -> None:
    store.put_draft(_draft())
    result = store.get_draft_consumed_at("D1")
    assert result is None  # noqa: S101 pytest assertion


def test_get_draft_consumed_at_returns_timestamp_after_consume(store: SqliteStateStore) -> None:
    store.put_draft(_draft())
    consumed_time = datetime(2026, 5, 19, 12, 1, 0, tzinfo=UTC)
    store.consume_draft("D1", at=consumed_time)
    result = store.get_draft_consumed_at("D1")
    assert result is not None  # noqa: S101 pytest assertion
    assert result == consumed_time  # noqa: S101 pytest assertion


def test_get_draft_consumed_at_returns_utc_aware_even_for_naive_stored_value(
    store: SqliteStateStore,
) -> None:
    """A naive ISO string in the DB (no UTC offset) must still yield a UTC-aware datetime."""
    store.put_draft(_draft())
    # Directly insert a naive ISO string to simulate a pre-fix or foreign-written row.
    store._conn.execute(
        "UPDATE drafts SET consumed_at = ? WHERE draft_id = ?",
        ("2026-05-19T12:01:00", "D1"),
    )
    store._conn.commit()
    result = store.get_draft_consumed_at("D1")
    assert result is not None  # noqa: S101 pytest assertion
    assert result.tzinfo is not None  # noqa: S101 pytest assertion
    assert result.tzinfo == UTC  # noqa: S101 pytest assertion
    assert result == datetime(2026, 5, 19, 12, 1, 0, tzinfo=UTC)  # noqa: S101 pytest assertion
