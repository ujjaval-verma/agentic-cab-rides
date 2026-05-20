"""Unit tests for UberSim state machine — T28 Commit 3."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.uber_sim.state_machine import DriverAvailability, advance
from agentic_cab.core.types import Booking, Money, Option, RideOptionDetails


def _now() -> datetime:
    return datetime(2026, 5, 19, 12, 0, tzinfo=UTC)


_OPT = Option(
    option_id="opt-x",
    provider="uber-sim",
    title="UberX",
    price=Money(amount_cents=500, currency="USD"),
    eta_seconds=60,
    when=None,
    details=RideOptionDetails(vehicle_class="uberx", capacity=4),
    expires_at=_now(),
)


def _booking(status: str = "requested", created_offset_seconds: int = 0) -> Booking:
    return Booking(
        booking_id="bk-test",
        draft_id="dr-test",
        status=status,  # type: ignore[arg-type]
        provider="uber-sim",
        summary=_OPT,
        events=[],
    )


def test_advance_through_full_lifecycle_under_frozen_clock() -> None:
    """Booking progresses through all states as the clock advances."""
    start = _now()
    clock = FrozenClock(start=start)
    avail = DriverAvailability(available=True)
    booking = _booking("requested")

    # At t=0 → still requested
    b = advance(booking, clock, avail, start)
    assert b.status == "requested"

    # At t=31s → matched
    clock.tick(timedelta(seconds=31))
    b = advance(booking, clock, avail, start)
    assert b.status == "matched"

    # At t=61s → en_route
    clock.tick(timedelta(seconds=30))
    b = advance(booking, clock, avail, start)
    assert b.status == "en_route"

    # At t=181s → in_progress
    clock.tick(timedelta(seconds=120))
    b = advance(booking, clock, avail, start)
    assert b.status == "in_progress"

    # At t=481s → completed
    clock.tick(timedelta(seconds=300))
    b = advance(booking, clock, avail, start)
    assert b.status == "completed"


def test_force_no_availability_keeps_booking_in_requested() -> None:
    """When force_no_availability=True, booking stays at requested regardless of time."""
    start = _now()
    clock = FrozenClock(start=start)
    clock.tick(timedelta(seconds=600))  # well past completion time
    avail = DriverAvailability(available=True, force_no_availability=True)
    booking = _booking("requested")
    b = advance(booking, clock, avail, start)
    assert b.status == "requested"


def test_available_false_keeps_booking_in_requested() -> None:
    """When available=False, booking stays at requested."""
    start = _now()
    clock = FrozenClock(start=start)
    clock.tick(timedelta(seconds=600))
    avail = DriverAvailability(available=False)
    booking = _booking("requested")
    b = advance(booking, clock, avail, start)
    assert b.status == "requested"


def test_terminal_status_does_not_advance() -> None:
    """completed/cancelled/failed bookings must not advance further."""
    start = _now()
    for terminal in ("completed", "cancelled", "failed"):
        clock = FrozenClock(start=start)
        clock.tick(timedelta(hours=2))
        avail = DriverAvailability(available=True)
        booking = _booking(terminal)
        b = advance(booking, clock, avail, start)
        assert b.status == terminal, f"Expected {terminal!r} to remain terminal"


def test_advance_is_deterministic() -> None:
    """Same inputs always produce same output (no hidden state)."""
    start = _now()
    clock = FrozenClock(start=start)
    clock.tick(timedelta(seconds=45))
    avail = DriverAvailability(available=True)
    booking = _booking("requested")
    b1 = advance(booking, clock, avail, start)
    b2 = advance(booking, clock, avail, start)
    assert b1.status == b2.status
