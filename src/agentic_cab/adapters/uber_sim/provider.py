"""UberSim provider — multi-class pricing, surge curve, state machine. T28 Commit 4.

This was "Minimal UberSim — uberx only, no surge, deterministic." in P1.
P3 wires in:
  - pricing.base_price per vehicle class
  - surge.evaluate (on_confirm=False for discover; on_confirm=True for confirm re-price)
  - state_machine.advance for track()
  - Injected SurgeCurve + DriverAvailability (scenario-driven, not hardcoded)
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta
from decimal import Decimal
from typing import ClassVar, Self

from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.adapters.uber_sim.pricing import base_price
from agentic_cab.adapters.uber_sim.state_machine import DriverAvailability, advance
from agentic_cab.adapters.uber_sim.surge import SurgeCurve
from agentic_cab.adapters.uber_sim.surge import evaluate as surge_evaluate
from agentic_cab.core.fingerprint import fingerprint
from agentic_cab.core.ids import new_draft_id
from agentic_cab.core.ports import Clock
from agentic_cab.core.types import (
    Booking,
    CancellationInfo,
    Draft,
    Intent,
    Money,
    Option,
    RideIntent,
    RideOptionDetails,
    VehicleClass,
)

_VEHICLE_CLASSES: list[VehicleClass] = ["uberx", "comfort", "uberxl", "black"]
_CAPACITY: dict[VehicleClass, int] = {
    "uberx": 4,
    "comfort": 4,
    "uberxl": 6,
    "black": 4,
}
_TITLE_TEMPLATE: dict[VehicleClass, str] = {
    "uberx": "UberX",
    "comfort": "UberComfort",
    "uberxl": "UberXL",
    "black": "Uber Black",
}


def _km(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    earth_radius_km = 6371.0
    phi1 = math.radians(a_lat)
    phi2 = math.radians(b_lat)
    dphi = math.radians(b_lat - a_lat)
    dlambda = math.radians(b_lng - a_lng)
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * earth_radius_km * math.asin(math.sqrt(h))


def _apply_surge(price: Money, multiplier: Decimal) -> Money:
    """Apply surge multiplier to a Money value, staying in integer cents."""
    new_cents = int(price.amount_cents * multiplier)
    return Money(amount_cents=new_cents, currency=price.currency)


class UberSimAdapter:
    name = "uber-sim"
    supported_intent_kinds: ClassVar[set[str]] = {"ride"}

    def __init__(
        self: Self,
        store: SqliteStateStore,
        clock: Clock,
        rng_seed: int = 0,
        surge_curve: SurgeCurve | None = None,
        availability: DriverAvailability | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._rng = random.Random(rng_seed)
        self._surge_curve = surge_curve
        self._availability = availability or DriverAvailability()
        self._option_cache: dict[str, Option] = {}
        self._intent_cache: dict[str, Intent] = {}  # option_id → intent that produced it
        # booking_id → created_at for state machine (we record when confirm happened)
        self._booking_created_at: dict[str, datetime] = {}
        # Exposed for tests: price cents used in last confirm re-quote
        self.last_confirm_price_cents: int = 0
        # Track whether any draft has been prepared (re-quote context signal)
        self._any_draft_prepared: bool = False

    async def discover(self: Self, intent: Intent) -> list[Option]:
        if not isinstance(intent, RideIntent):
            raise TypeError(f"UberSimAdapter only supports RideIntent; got {type(intent).__name__}")
        km = _km(intent.pickup.lat, intent.pickup.lng, intent.dropoff.lat, intent.dropoff.lng)
        est_min = max(1, int(km * 2))
        now = self._clock.now()

        # on_confirm=True when force_surge_on_confirm is set and a draft has been prepared
        # (re-quote context: discover is being called from within confirm_booking).
        _on_confirm_requote = (
            self._any_draft_prepared
            and self._surge_curve is not None
            and self._surge_curve.force_surge_on_confirm
        )
        surge_mult = (
            surge_evaluate(self._surge_curve, now, on_confirm=_on_confirm_requote)
            if self._surge_curve is not None
            else Decimal("1")
        )

        options: list[Option] = []
        for vc in _VEHICLE_CLASSES:
            base = base_price(vc, km, float(est_min))
            surged = _apply_surge(base, surge_mult)
            opt = Option(
                option_id=f"opt-{vc}-{self._rng.randrange(10**12)}",
                provider=self.name,
                title=f"{_TITLE_TEMPLATE[vc]} — ~{est_min} min",
                price=surged,
                eta_seconds=180,
                when=None,
                details=RideOptionDetails(vehicle_class=vc, capacity=_CAPACITY[vc]),
                provider_metadata={"distance_km": round(km, 3)},
                expires_at=now + timedelta(minutes=5),
            )
            self._option_cache[opt.option_id] = opt
            self._intent_cache[opt.option_id] = intent
            options.append(opt)
        return options

    async def prepare(self: Self, option_id: str) -> Draft:
        opt = self._option_cache.get(option_id)
        if opt is None:
            raise ValueError(f"unknown option_id: {option_id}")
        now = self._clock.now()
        intent_snap = self._intent_cache.get(option_id)
        draft = Draft(
            draft_id=new_draft_id(),
            option_snapshot=opt,
            fingerprint=fingerprint(opt),
            created_at=now,
            expires_at=now + timedelta(minutes=3),
            intent_snapshot=intent_snap,
        )
        self._store.put_draft(draft)
        self._any_draft_prepared = True  # signal re-quote context for subsequent discover calls
        return draft

    async def confirm(self: Self, draft_id: str, idempotency_key: str) -> Booking:
        draft = self._store.get_draft(draft_id)
        if draft is None:
            raise ValueError(f"unknown draft: {draft_id}")

        # Re-price with on_confirm=True to surface any force_surge_on_confirm spike.
        # The caller (tools/confirm.py) handles drift detection; we just record it.
        now = self._clock.now()
        if self._surge_curve is not None:
            confirm_mult = surge_evaluate(self._surge_curve, now, on_confirm=True)
            confirm_price = _apply_surge(draft.option_snapshot.price, confirm_mult)
        else:
            confirm_price = draft.option_snapshot.price
        self.last_confirm_price_cents = confirm_price.amount_cents

        booking = Booking(
            booking_id="bk-" + idempotency_key[:8],
            draft_id=draft_id,
            status="requested",
            provider=self.name,
            summary=draft.option_snapshot,
        )
        self._store.put_booking(booking)
        self._booking_created_at[booking.booking_id] = now
        return booking

    async def track(self: Self, booking_id: str) -> Booking:
        b = self._store.get_booking(booking_id)
        if b is None:
            raise ValueError(f"unknown booking: {booking_id}")
        created_at = self._booking_created_at.get(booking_id, self._clock.now())
        return advance(b, self._clock, self._availability, created_at)

    @staticmethod
    def _compute_fee(status: str) -> Money:
        """Single source of truth for cancel-fee policy.

        confirmed | requested | matched → $0.00 (free cancel before driver en route)
        en_route | driver_assigned |
          arrived                  → $5.00 flat
        in_progress | completed    → raises ValueError (cannot cancel)
        cancelled | failed         → $0.00 (no-op cancel)
        """
        if status in {"confirmed", "requested", "matched"}:
            return Money(amount_cents=0, currency="USD")
        if status in {"driver_assigned", "en_route", "arrived"}:
            return Money(amount_cents=500, currency="USD")
        if status in {"in_progress", "completed"}:
            raise ValueError(f"cannot cancel; ride already in progress (status={status!r})")
        # cancelled | failed
        return Money(amount_cents=0, currency="USD")

    async def quote_cancel_fee(self: Self, booking_id: str) -> Money:
        b = self._store.get_booking(booking_id)
        if b is None:
            raise ValueError(f"unknown booking: {booking_id}")
        return self._compute_fee(b.status)

    async def cancel(self: Self, booking_id: str, idempotency_key: str) -> Booking:
        b = self._store.get_booking(booking_id)
        if b is None:
            raise ValueError(f"unknown booking: {booking_id}")
        status_at_cancel = b.status
        fee = self._compute_fee(status_at_cancel)
        cancelled = Booking(
            booking_id=b.booking_id,
            draft_id=b.draft_id,
            status="cancelled",
            provider=b.provider,
            summary=b.summary,
            events=b.events,
            cancellation=CancellationInfo(
                cancelled_at=self._clock.now(),
                fee=fee,
                reason=None,
            ),
        )
        self._store.put_booking(cancelled)
        return cancelled
