"""Tests for UberSimAdapter wiring with pricing, surge, and state machine. T28 Commit 4."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.adapters.uber_sim.provider import UberSimAdapter
from agentic_cab.adapters.uber_sim.state_machine import DriverAvailability
from agentic_cab.adapters.uber_sim.surge import SurgeCurve
from agentic_cab.core.types import AddressRef, RideIntent

_START = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)
_INTENT = RideIntent(
    pickup=AddressRef(raw="A", formatted="A", lat=37.7749, lng=-122.4194, confidence=1.0),
    dropoff=AddressRef(raw="B", formatted="B", lat=37.7849, lng=-122.4094, confidence=1.0),
    when="now",
)
_SURGE_PEAK_START = datetime(2026, 5, 19, 11, 0, tzinfo=UTC)  # 1 hour before _START


def _sim(
    tmp_path: Path,
    surge_curve: SurgeCurve | None = None,
    availability: DriverAvailability | None = None,
) -> tuple[FrozenClock, UberSimAdapter]:
    store = SqliteStateStore(db_path=tmp_path / "s.db")
    store.apply_schema()
    clock = FrozenClock(start=_START)
    sim = UberSimAdapter(
        store=store,
        clock=clock,
        rng_seed=42,
        surge_curve=surge_curve,
        availability=availability,
    )
    return clock, sim


@pytest.mark.asyncio
async def test_discover_returns_all_four_vehicle_classes(tmp_path: Path) -> None:
    """With multi-class pricing, discover() returns uberx/comfort/uberxl/black."""
    _, sim = _sim(tmp_path)
    options = await sim.discover(_INTENT)
    vehicle_classes = {o.details.vehicle_class for o in options}
    assert vehicle_classes == {"uberx", "comfort", "uberxl", "black"}


@pytest.mark.asyncio
async def test_discover_prices_ordered_uberx_cheapest(tmp_path: Path) -> None:
    """uberx must be the cheapest option."""
    _, sim = _sim(tmp_path)
    options = sorted(await sim.discover(_INTENT), key=lambda o: o.price.amount_cents)
    assert options[0].details.vehicle_class == "uberx"
    assert options[-1].details.vehicle_class == "black"


@pytest.mark.asyncio
async def test_surge_curve_applied_on_discover(tmp_path: Path) -> None:
    """discover() without surge vs with surge → higher prices."""
    store = SqliteStateStore(db_path=tmp_path / "no_surge.db")
    store.apply_schema()
    clock_no = FrozenClock(start=_START)
    sim_no = UberSimAdapter(store=store, clock=clock_no, rng_seed=42)

    store2 = SqliteStateStore(db_path=tmp_path / "surge.db")
    store2.apply_schema()
    clock_surge = FrozenClock(start=_START)
    # Peak window covers _START so surge fires on discover
    peak_curve = SurgeCurve(
        base_multiplier=Decimal("1.0"),
        peak_multiplier=Decimal("2.0"),
        peak_start=_START - timedelta(minutes=30),
        peak_duration_min=60,
        force_surge_on_confirm=False,
    )
    sim_surge = UberSimAdapter(store=store2, clock=clock_surge, rng_seed=42, surge_curve=peak_curve)

    opts_no = {
        o.details.vehicle_class: o.price.amount_cents for o in await sim_no.discover(_INTENT)
    }
    opts_surge = {
        o.details.vehicle_class: o.price.amount_cents for o in await sim_surge.discover(_INTENT)
    }

    for vc in ("uberx", "comfort", "uberxl", "black"):
        assert opts_surge[vc] > opts_no[vc], f"Expected surge price for {vc} to be higher"


@pytest.mark.asyncio
async def test_confirm_final_price_uses_surge_on_confirm(tmp_path: Path) -> None:
    """With force_surge_on_confirm=True, confirm() re-prices at peak_multiplier."""
    curve = SurgeCurve(
        base_multiplier=Decimal("1.0"),
        peak_multiplier=Decimal("3.0"),
        peak_start=_START - timedelta(hours=2),  # off-peak at discover time
        peak_duration_min=10,  # already ended
        force_surge_on_confirm=True,
    )
    _, sim = _sim(tmp_path, surge_curve=curve)
    opts = await sim.discover(_INTENT)
    uberx = next(o for o in opts if o.details.vehicle_class == "uberx")
    price_at_discover = uberx.price.amount_cents

    draft = await sim.prepare(uberx.option_id)
    await sim.confirm(draft.draft_id, idempotency_key="K-surge")
    # last_confirm_price_cents is set by confirm() after re-pricing with on_confirm=True
    confirm_price_cents = sim.last_confirm_price_cents
    assert confirm_price_cents > price_at_discover


@pytest.mark.asyncio
async def test_track_uses_state_machine(tmp_path: Path) -> None:
    """track() advances booking status via the state machine."""
    clock, sim = _sim(tmp_path)
    opts = await sim.discover(_INTENT)
    uberx = next(o for o in opts if o.details.vehicle_class == "uberx")
    draft = await sim.prepare(uberx.option_id)
    booking = await sim.confirm(draft.draft_id, idempotency_key="K1")

    # At t=0 booking is newly confirmed; state machine should yield 'requested'
    tracked0 = await sim.track(booking.booking_id)
    assert tracked0.status == "requested"

    # Advance clock past matched threshold
    clock.tick(timedelta(seconds=35))
    tracked1 = await sim.track(booking.booking_id)
    assert tracked1.status == "matched"


@pytest.mark.asyncio
async def test_force_no_availability_keeps_tracking_in_requested(tmp_path: Path) -> None:
    """With force_no_availability=True, track() always returns requested."""
    avail = DriverAvailability(available=True, force_no_availability=True)
    clock, sim = _sim(tmp_path, availability=avail)
    opts = await sim.discover(_INTENT)
    uberx = next(o for o in opts if o.details.vehicle_class == "uberx")
    draft = await sim.prepare(uberx.option_id)
    booking = await sim.confirm(draft.draft_id, idempotency_key="K2")

    clock.tick(timedelta(hours=2))  # well past completion
    tracked = await sim.track(booking.booking_id)
    assert tracked.status == "requested"
