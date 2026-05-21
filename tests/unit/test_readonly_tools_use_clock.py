"""Invariant 9: read-only tools must use the injected Clock for all timestamps.

Each test constructs deps with a FrozenClock and asserts that every emitted
log row's `ts` matches the frozen instant — confirming datetime.now(UTC) is
not called anywhere inside the tool.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Literal

import pytest

from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.core.ports import ClaimResult
from agentic_cab.core.types import (
    AddressRef,
    Booking,
    Draft,
    Intent,
    Money,
    Option,
    RideOptionDetails,
)
from agentic_cab.tools.geocode import GeocodeArgs, geocode_address
from agentic_cab.tools.list_active import ListActiveArgs, list_active_bookings
from agentic_cab.tools.track import TrackArgs, track_booking

_FROZEN_TS = "2026-05-20T12:00:00+00:00"
_FROZEN_START = datetime.fromisoformat(_FROZEN_TS)
# The action log applies _PHONE redaction, which matches the date portion of
# ISO timestamps (digits + hyphens). Only the time suffix survives intact.
# Asserting this suffix proves clock.now() was called, not datetime.now(UTC).
_FROZEN_TS_SUFFIX = "T12:00:00+00:00"

_EXPIRES = datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubProvider:
    name: str = "stub"
    supported_intent_kinds: ClassVar[set[str]] = {"ride"}

    def __init__(self, booking: Booking) -> None:
        self._booking = booking

    async def discover(self, intent: Intent) -> list[Option]:
        return []

    async def prepare(self, option_id: str) -> Draft:
        raise NotImplementedError

    async def confirm(self, draft_id: str, idempotency_key: str) -> Booking:
        raise NotImplementedError

    async def track(self, booking_id: str) -> Booking:
        return self._booking

    async def cancel(self, booking_id: str, idempotency_key: str) -> Booking:
        raise NotImplementedError

    async def quote_cancel_fee(self, booking_id: str) -> Money:
        raise NotImplementedError


class _StubGeocoder:
    def __init__(self) -> None:
        self._ref = AddressRef(
            raw="123 Main St",
            formatted="123 Main St, Springfield, IL 62701, USA",
            lat=39.7817,
            lng=-89.6501,
            confidence=0.9,
            provider_metadata={"alternates": []},
        )

    async def geocode(self, query: str) -> AddressRef:
        return self._ref


class _StubStore:
    def __init__(self, booking: Booking) -> None:
        self._booking = booking

    def list_active_bookings(self) -> list[Booking]:
        return [self._booking]

    # Minimal stubs to satisfy protocol
    def put_draft(self, draft: object) -> None:
        raise NotImplementedError

    def get_draft(self, draft_id: str) -> None:
        return None

    def consume_draft(self, draft_id: str, at: object) -> bool:
        raise NotImplementedError

    def get_draft_consumed_at(self, draft_id: str) -> None:
        return None

    def put_booking(self, booking: Booking) -> None:
        raise NotImplementedError

    def get_booking(self, booking_id: str) -> Booking | None:
        return None

    def update_booking_status(self, booking_id: str, status: str, event: object) -> None:
        raise NotImplementedError

    def claim_or_recall(self, key: str, scope: Literal["confirm", "cancel"]) -> ClaimResult:
        raise NotImplementedError

    def finalize_idempotency(self, key: str, booking: Booking) -> None:
        raise NotImplementedError

    def release_claim(self, key: str, scope: Literal["confirm", "cancel"]) -> None:
        raise NotImplementedError

    def find_active_booking_by_intent_hash(self, intent_hash_hex: str) -> Booking | None:
        raise NotImplementedError

    def count_terminal_bookings_by_intent_hash(self, intent_hash_hex: str) -> int:
        raise NotImplementedError

    def set_booking_intent_hash(self, booking_id: str, intent_hash_hex: str) -> None:
        raise NotImplementedError

    def put_pending_confirmation(
        self, subject_id: str, session_id: str, request_json: str, expires_at: object
    ) -> None:
        raise NotImplementedError

    def pop_pending_confirmation(self, subject_id: str) -> None:
        return None

    def list_pending_confirmations(self, now: object) -> list:
        return []


def _make_booking(booking_id: str = "BID-001") -> Booking:
    return Booking(
        booking_id=booking_id,
        draft_id="DRAFT-001",
        status="en_route",
        provider="uber_sim",
        summary=Option(
            option_id="OPT-001",
            provider="uber_sim",
            title="UberX",
            price=Money(amount_cents=1850, currency="USD"),
            eta_seconds=300,
            when=None,
            details=RideOptionDetails(vehicle_class="uberx", capacity=4),
            expires_at=_EXPIRES,
        ),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_track_booking_uses_frozen_clock(tmp_path: Path) -> None:
    """track_booking must stamp every log row with clock.now(), not datetime.now(UTC)."""
    clock = FrozenClock(start=_FROZEN_START)
    log = JSONLActionLog(log_dir=tmp_path / "logs")
    booking = _make_booking("BID-TRACK")
    provider = _StubProvider(booking)

    await track_booking(
        TrackArgs(booking_id="BID-TRACK"),
        provider=provider,
        log=log,
        clock=clock,
        session_id="S-TRACK",
        turn_id="T1",
    )

    rows = log.read("S-TRACK")
    assert len(rows) == 2
    for row in rows:
        assert (
            _FROZEN_TS_SUFFIX in row["ts"]
        ), f"Expected ts to contain {_FROZEN_TS_SUFFIX!r}, got {row['ts']!r}"


@pytest.mark.asyncio
async def test_geocode_address_uses_frozen_clock(tmp_path: Path) -> None:
    """geocode_address must stamp every log row with clock.now(), not datetime.now(UTC)."""
    clock = FrozenClock(start=_FROZEN_START)
    log = JSONLActionLog(log_dir=tmp_path / "logs")
    geocoder = _StubGeocoder()

    await geocode_address(
        GeocodeArgs(query="123 Main St"),
        geocoder=geocoder,
        log=log,
        clock=clock,
        session_id="S-GEO",
        turn_id="T1",
    )

    rows = log.read("S-GEO")
    assert len(rows) == 2
    for row in rows:
        assert (
            _FROZEN_TS_SUFFIX in row["ts"]
        ), f"Expected ts to contain {_FROZEN_TS_SUFFIX!r}, got {row['ts']!r}"


@pytest.mark.asyncio
async def test_list_active_bookings_uses_frozen_clock(tmp_path: Path) -> None:
    """list_active_bookings must stamp every log row with clock.now(), not datetime.now(UTC)."""
    clock = FrozenClock(start=_FROZEN_START)
    log = JSONLActionLog(log_dir=tmp_path / "logs")
    booking = _make_booking("BID-LIST")
    store = _StubStore(booking)

    await list_active_bookings(
        ListActiveArgs(),
        store=store,
        log=log,
        clock=clock,
        session_id="S-LIST",
        turn_id="T1",
    )

    rows = log.read("S-LIST")
    assert len(rows) == 2
    for row in rows:
        assert (
            _FROZEN_TS_SUFFIX in row["ts"]
        ), f"Expected ts to contain {_FROZEN_TS_SUFFIX!r}, got {row['ts']!r}"
