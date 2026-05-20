from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.adapters.uber_sim.provider import UberSimAdapter
from agentic_cab.core.fingerprint import fingerprint
from agentic_cab.core.ids import new_draft_id
from agentic_cab.core.types import (
    AddressRef,
    Booking,
    Draft,
    Money,
    Option,
    RideIntent,
    RideOptionDetails,
)


@pytest.fixture
def sim(tmp_path: Path) -> UberSimAdapter:
    store = SqliteStateStore(db_path=tmp_path / "s.db")
    store.apply_schema()
    clock = FrozenClock(start=datetime(2026, 5, 19, 12, 0, tzinfo=UTC))
    return UberSimAdapter(store=store, clock=clock, rng_seed=42)


@pytest.mark.asyncio
async def test_discover_returns_uberx_option(sim: UberSimAdapter) -> None:
    intent = RideIntent(
        pickup=AddressRef(raw="A", formatted="A", lat=37.7749, lng=-122.4194, confidence=1.0),
        dropoff=AddressRef(raw="B", formatted="B", lat=37.7849, lng=-122.4094, confidence=1.0),
        when="now",
    )
    options = await sim.discover(intent)
    # P3: sim now returns all 4 vehicle classes
    assert len(options) == 4
    vehicle_classes = {
        o.details.vehicle_class for o in options if isinstance(o.details, RideOptionDetails)
    }
    assert "uberx" in vehicle_classes
    assert all(o.price.amount_cents > 0 for o in options)


@pytest.mark.asyncio
async def test_prepare_then_confirm_returns_booking(sim: UberSimAdapter) -> None:
    intent = RideIntent(
        pickup=AddressRef(raw="A", formatted="A", lat=37.7749, lng=-122.4194, confidence=1.0),
        dropoff=AddressRef(raw="B", formatted="B", lat=37.7849, lng=-122.4094, confidence=1.0),
        when="now",
    )
    options = await sim.discover(intent)
    opt = options[0]
    draft = await sim.prepare(opt.option_id)
    booking = await sim.confirm(draft.draft_id, idempotency_key="K1")
    # P3: sim starts booking at "requested" (state machine initial state)
    assert booking.status == "requested"
    assert booking.draft_id == draft.draft_id


# ---------------------------------------------------------------------------
# quote_cancel_fee — all status branches
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)
_OPT = Option(
    option_id="opt-test",
    provider="uber-sim",
    title="UberX — test",
    price=Money(amount_cents=500, currency="USD"),
    eta_seconds=120,
    when=None,
    details=RideOptionDetails(vehicle_class="uberx", capacity=4),
    expires_at=_NOW,
)


def _make_booking(store: SqliteStateStore, status: str) -> Booking:
    """Persist a Draft + Booking with the given status, bypassing discover/prepare."""
    draft_id = new_draft_id()
    draft = Draft(
        draft_id=draft_id,
        option_snapshot=_OPT,
        fingerprint=fingerprint(_OPT),
        created_at=_NOW,
        expires_at=_NOW,
    )
    store.put_draft(draft)
    booking = Booking(
        booking_id="bk-test-" + status,
        draft_id=draft_id,
        status=status,  # type: ignore[arg-type]
        provider="uber-sim",
        summary=_OPT,
    )
    store.put_booking(booking)
    return booking


def _make_sim(tmp_path: Path, db_name: str) -> tuple[SqliteStateStore, UberSimAdapter]:
    store = SqliteStateStore(db_path=tmp_path / db_name)
    store.apply_schema()
    clock = FrozenClock(start=_NOW)
    return store, UberSimAdapter(store=store, clock=clock, rng_seed=0)


@pytest.mark.parametrize(
    ("status", "expected_fee_cents"),
    [
        ("confirmed", 0),
        ("driver_assigned", 500),
        ("en_route", 500),
        ("arrived", 500),
        ("cancelled", 0),
        ("failed", 0),
    ],
)
@pytest.mark.asyncio
async def test_quote_cancel_fee_by_status(
    tmp_path: Path, status: str, expected_fee_cents: int
) -> None:
    store, sim = _make_sim(tmp_path, "s2.db")
    booking = _make_booking(store, status)
    fee = await sim.quote_cancel_fee(booking.booking_id)
    assert fee == Money(amount_cents=expected_fee_cents, currency="USD")


@pytest.mark.parametrize("status", ["in_progress", "completed"])
@pytest.mark.asyncio
async def test_quote_cancel_fee_raises_for_active_ride(tmp_path: Path, status: str) -> None:
    store, sim = _make_sim(tmp_path, "s3.db")
    booking = _make_booking(store, status)
    with pytest.raises(ValueError, match="cannot cancel"):
        await sim.quote_cancel_fee(booking.booking_id)
