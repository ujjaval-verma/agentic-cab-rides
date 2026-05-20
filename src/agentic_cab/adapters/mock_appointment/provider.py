"""MockAppointmentProvider — in-memory appointment booking adapter.

Second adapter in the contract suite, proving the BookingProvider Protocol holds
for a non-ride-share intent. Supports AppointmentIntent; deterministic via
injected random.Random seed and Clock.

Design decisions:
- All state is in-memory; no SQLite (contract suite isolates per provider instance).
- Idempotency is maintained via internal dicts keyed on idempotency_key.
- quote_cancel_fee returns $0 for terminal bookings (as per T27 FOLLOWUP contract).
- discover() returns 1-3 options seeded from intent fields for determinism.
"""

from __future__ import annotations

import random
from datetime import timedelta
from typing import ClassVar, Self

from agentic_cab.core.fingerprint import fingerprint
from agentic_cab.core.ids import new_draft_id, new_idempotency_key
from agentic_cab.core.ports import Clock
from agentic_cab.core.types import (
    AppointmentIntent,
    AppointmentOptionDetails,
    Booking,
    CancellationInfo,
    Draft,
    Intent,
    Money,
    Option,
)

_CLINICS = [
    "SF General Hospital",
    "UCSF Medical Center",
    "Sutter Health Clinic",
]

_SPECIALTIES = ["general", "pediatrics", "cardiology"]

_TERMINAL_STATUSES = {"completed", "cancelled", "failed"}


class MockAppointmentProvider:
    """In-memory BookingProvider for AppointmentIntent — swap proof for the contract suite."""

    name = "mock_appointment"
    supported_intent_kinds: ClassVar[set[str]] = {"appointment"}

    def __init__(self: Self, clock: Clock, rng_seed: int = 0) -> None:
        self._clock = clock
        self._rng = random.Random(rng_seed)
        # option_id → Option
        self._option_cache: dict[str, Option] = {}
        # draft_id → Draft
        self._draft_cache: dict[str, Draft] = {}
        # booking_id → Booking
        self._bookings: dict[str, Booking] = {}
        # idempotency_key → Booking (for confirm and cancel dedup)
        self._idempotency: dict[str, Booking] = {}

    async def discover(self: Self, intent: Intent) -> list[Option]:
        if not isinstance(intent, AppointmentIntent):
            raise TypeError(
                f"MockAppointmentProvider only supports AppointmentIntent; "
                f"got {type(intent).__name__}"
            )
        # Seed from intent fields for determinism across repeated calls with same intent
        seed_str = (
            f"{intent.subject}|{intent.earliest_at.isoformat()}|{intent.latest_at.isoformat()}"
        )
        rng = random.Random(hash(seed_str) & 0xFFFFFFFF)

        now = self._clock.now()
        # Spread slots across the earliest_at..latest_at window
        window_seconds = max(1, int((intent.latest_at - intent.earliest_at).total_seconds()))
        n_options = rng.randint(1, min(3, len(_CLINICS)))

        options: list[Option] = []
        for i in range(n_options):
            clinic = _CLINICS[i % len(_CLINICS)]
            specialty = _SPECIALTIES[i % len(_SPECIALTIES)]
            slot_offset = rng.randint(0, window_seconds)
            slot_at = intent.earliest_at + timedelta(seconds=slot_offset)
            price_cents = rng.randint(1500, 25000)  # $15-$250 appointment fee

            details = AppointmentOptionDetails(
                clinic=clinic,
                slot_at=slot_at,
                specialty=specialty,
            )
            opt = Option(
                option_id=f"appt-{i}-{rng.randrange(10 ** 8)}",
                provider=self.name,
                title=f"{clinic} @ {slot_at.strftime('%H:%M')}",
                price=Money(amount_cents=price_cents, currency="USD"),
                eta_seconds=None,
                when=slot_at,
                details=details,
                provider_metadata={"specialty": specialty},
                expires_at=now + timedelta(minutes=10),
            )
            self._option_cache[opt.option_id] = opt
            options.append(opt)
        return options

    async def prepare(self: Self, option_id: str) -> Draft:
        opt = self._option_cache.get(option_id)
        if opt is None:
            raise ValueError(f"unknown option_id: {option_id!r}")
        now = self._clock.now()
        draft = Draft(
            draft_id=new_draft_id(),
            option_snapshot=opt,
            fingerprint=fingerprint(opt),
            created_at=now,
            expires_at=now + timedelta(minutes=5),
        )
        self._draft_cache[draft.draft_id] = draft
        return draft

    async def confirm(self: Self, draft_id: str, idempotency_key: str) -> Booking:
        # Idempotency replay: return the existing booking without side effects
        if idempotency_key in self._idempotency:
            return self._idempotency[idempotency_key]

        draft = self._draft_cache.get(draft_id)
        if draft is None:
            raise ValueError(f"unknown draft_id: {draft_id!r}")

        booking = Booking(
            booking_id=f"appt-bk-{new_idempotency_key()}",
            draft_id=draft_id,
            status="confirmed",
            provider=self.name,
            summary=draft.option_snapshot,
        )
        self._bookings[booking.booking_id] = booking
        self._idempotency[idempotency_key] = booking
        return booking

    async def track(self: Self, booking_id: str) -> Booking:
        b = self._bookings.get(booking_id)
        if b is None:
            raise ValueError(f"unknown booking_id: {booking_id!r}")
        return b

    async def quote_cancel_fee(self: Self, booking_id: str) -> Money:
        b = self._bookings.get(booking_id)
        if b is None:
            raise ValueError(f"unknown booking_id: {booking_id!r}")
        # Terminal bookings: no fee (contract requirement, T27 FOLLOWUP)
        if b.status in _TERMINAL_STATUSES:
            return Money(amount_cents=0, currency="USD")
        # Free cancel (appointment bookings have no fee in this sim)
        return Money(amount_cents=0, currency="USD")

    async def cancel(self: Self, booking_id: str, idempotency_key: str) -> Booking:
        # Idempotency replay
        if idempotency_key in self._idempotency:
            return self._idempotency[idempotency_key]

        b = self._bookings.get(booking_id)
        if b is None:
            raise ValueError(f"unknown booking_id: {booking_id!r}")

        cancelled = Booking(
            booking_id=b.booking_id,
            draft_id=b.draft_id,
            status="cancelled",
            provider=b.provider,
            summary=b.summary,
            events=b.events,
            cancellation=CancellationInfo(
                cancelled_at=self._clock.now(),
                fee=Money(amount_cents=0, currency="USD"),
                reason=None,
            ),
        )
        self._bookings[booking_id] = cancelled
        self._idempotency[idempotency_key] = cancelled
        return cancelled
