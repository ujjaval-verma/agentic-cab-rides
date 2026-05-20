"""Minimal UberSim — uberx only, no surge, deterministic. P3 thickens this."""

from __future__ import annotations

import math
import random
from datetime import timedelta
from typing import ClassVar, Self

from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.core.fingerprint import fingerprint
from agentic_cab.core.ids import new_draft_id
from agentic_cab.core.types import (
    Booking,
    Draft,
    Intent,
    Money,
    Option,
    RideOptionDetails,
)

_BASE_FARE_CENTS = 250
_PER_KM_CENTS = 150
_PER_MIN_CENTS = 30


def _km(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    earth_radius_km = 6371.0
    phi1 = math.radians(a_lat)
    phi2 = math.radians(b_lat)
    dphi = math.radians(b_lat - a_lat)
    dlambda = math.radians(b_lng - a_lng)
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * earth_radius_km * math.asin(math.sqrt(h))


class UberSimAdapter:
    name = "uber-sim"
    supported_intent_kinds: ClassVar[set[str]] = {"ride"}

    def __init__(
        self: Self, store: SqliteStateStore, clock: FrozenClock, rng_seed: int = 0
    ) -> None:
        self._store = store
        self._clock = clock
        self._rng = random.Random(rng_seed)
        self._option_cache: dict[str, Option] = {}

    async def discover(self: Self, intent: Intent) -> list[Option]:
        km = _km(intent.pickup.lat, intent.pickup.lng, intent.dropoff.lat, intent.dropoff.lng)
        est_min = max(1, int(km * 2))
        price_cents = _BASE_FARE_CENTS + int(_PER_KM_CENTS * km * 1.35) + _PER_MIN_CENTS * est_min
        now = self._clock.now()
        opt = Option(
            option_id="opt-uberx-" + str(self._rng.randrange(10**12)),
            provider=self.name,
            title=f"UberX — ~{est_min} min",
            price=Money(amount_cents=price_cents, currency="USD"),
            eta_seconds=180,
            when=None,
            details=RideOptionDetails(vehicle_class="uberx", capacity=4),
            provider_metadata={"distance_km": round(km, 3)},
            expires_at=now + timedelta(minutes=5),
        )
        self._option_cache[opt.option_id] = opt
        return [opt]

    async def prepare(self: Self, option_id: str) -> Draft:
        opt = self._option_cache.get(option_id)
        if opt is None:
            raise ValueError(f"unknown option_id: {option_id}")
        now = self._clock.now()
        draft = Draft(
            draft_id=new_draft_id(),
            option_snapshot=opt,
            fingerprint=fingerprint(opt),
            created_at=now,
            expires_at=now + timedelta(minutes=3),
        )
        self._store.put_draft(draft)
        return draft

    async def confirm(self: Self, draft_id: str, idempotency_key: str) -> Booking:
        draft = self._store.get_draft(draft_id)
        if draft is None:
            raise ValueError(f"unknown draft: {draft_id}")
        booking = Booking(
            booking_id="bk-" + idempotency_key[:8],
            draft_id=draft_id,
            status="confirmed",
            provider=self.name,
            summary=draft.option_snapshot,
        )
        self._store.put_booking(booking)
        return booking

    async def track(self: Self, booking_id: str) -> Booking:
        b = self._store.get_booking(booking_id)
        if b is None:
            raise ValueError(f"unknown booking: {booking_id}")
        return b

    async def cancel(self: Self, booking_id: str, idempotency_key: str) -> Booking:
        # P2 wires the gate; P1 keeps this minimal but contract-shaped.
        b = self._store.get_booking(booking_id)
        if b is None:
            raise ValueError(f"unknown booking: {booking_id}")
        return b
