"""Deterministic state machine for UberSim ride progression. T28 Commit 3.

advance() is a pure function: given a booking, a clock, a driver-availability
table, and the booking's creation timestamp, it returns a new Booking whose
status reflects how much time has elapsed. No side effects, no module-level
state.

State progression timings (seconds elapsed from booking_created_at):
    requested  →  0s
    matched    → 30s
    en_route   → 60s
    in_progress→ 180s
    completed  → 480s
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from agentic_cab.core.ports import Clock
from agentic_cab.core.types import Booking, BookingStatus

# (threshold_seconds, target_status) — ordered ascending
_TRANSITIONS: list[tuple[int, BookingStatus]] = [
    (480, "completed"),
    (180, "in_progress"),
    (60, "en_route"),
    (30, "matched"),
    (0, "requested"),
]

_TERMINAL: frozenset[str] = frozenset({"completed", "cancelled", "failed"})


class DriverAvailability(BaseModel):
    """Scenario-level driver availability table.

    When `force_no_availability` is True the sim treats the driver pool
    as empty regardless of the `available` flag, keeping the booking in
    the `requested` state indefinitely (for scenario testing).
    """

    available: bool = True
    force_no_availability: bool = False


def advance(
    booking: Booking,
    clock: Clock,
    table: DriverAvailability,
    booking_created_at: datetime,
) -> Booking:
    """Return a new Booking with status updated to reflect elapsed time.

    Pure function — does not mutate the input booking or any external state.
    """
    # Terminal statuses do not advance
    if booking.status in _TERMINAL:
        return booking

    # No availability → stay at requested
    if not table.available or table.force_no_availability:
        return booking.model_copy(update={"status": "requested"})

    elapsed = (clock.now() - booking_created_at).total_seconds()
    new_status: BookingStatus = "requested"
    for threshold, status in _TRANSITIONS:
        if elapsed >= threshold:
            new_status = status
            break

    return booking.model_copy(update={"status": new_status})


__all__ = ["DriverAvailability", "advance"]
