from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.adapters.uber_sim.provider import UberSimAdapter
from agentic_cab.core.types import AddressRef, RideIntent


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
    assert len(options) == 1
    assert options[0].details.vehicle_class == "uberx"
    assert options[0].price.amount_cents > 0


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
    assert booking.status == "confirmed"
    assert booking.draft_id == draft.draft_id
