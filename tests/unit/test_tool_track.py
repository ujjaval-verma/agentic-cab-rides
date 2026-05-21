"""Unit tests for tools/track.py — read-only tool, emits requested + outcome only."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest

from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.core.types import Booking, Draft, Intent, Money, Option, RideOptionDetails
from agentic_cab.tools.track import TrackArgs, track_booking

# ---------------------------------------------------------------------------
# Stub BookingProvider — DI only, no real adapter in unit tests
# Implements the full protocol so pyright is satisfied.
# ---------------------------------------------------------------------------


class _StubProvider:
    name: str = "stub"
    supported_intent_kinds: ClassVar[set[str]] = {"ride"}

    def __init__(self, result: Booking | Exception) -> None:
        self._result = result

    async def discover(self, intent: Intent) -> list[Option]:
        return []

    async def prepare(self, option_id: str) -> Draft:
        raise NotImplementedError

    async def confirm(self, draft_id: str, idempotency_key: str) -> Booking:
        raise NotImplementedError

    async def track(self, booking_id: str) -> Booking:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result

    async def cancel(self, booking_id: str, idempotency_key: str) -> Booking:
        raise NotImplementedError

    async def quote_cancel_fee(self, booking_id: str) -> Money:
        raise NotImplementedError


_EXPIRES = datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC)
_FROZEN_CLOCK = FrozenClock(start=datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC))


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
async def test_happy_path_returns_booking_dict(tmp_path: Path) -> None:
    log = JSONLActionLog(log_dir=tmp_path / "logs")
    booking = _make_booking("BID-001")
    provider = _StubProvider(booking)
    args = TrackArgs(booking_id="BID-001")

    result = await track_booking(
        args,
        provider=provider,
        log=log,
        clock=_FROZEN_CLOCK,
        session_id="S1",
        turn_id="T1",
    )

    expected = {
        "booking_id": "BID-001",
        "status": "en_route",
        "summary_title": "UberX",
        "price_cents": 1850,
    }
    assert result == expected

    rows = log.read("S1")
    outcome = rows[1]
    assert outcome["kind"] == "outcome"
    assert outcome["status"] == "ok"
    assert outcome["result"] == expected


@pytest.mark.asyncio
async def test_emits_requested_then_outcome_only(tmp_path: Path) -> None:
    log = JSONLActionLog(log_dir=tmp_path / "logs")
    provider = _StubProvider(_make_booking())
    args = TrackArgs(booking_id="BID-002")

    await track_booking(
        args,
        provider=provider,
        log=log,
        clock=_FROZEN_CLOCK,
        session_id="S2",
        turn_id="T2",
    )

    rows = log.read("S2")
    assert len(rows) == 2, f"Expected exactly 2 rows, got {len(rows)}: {rows}"

    requested, outcome = rows
    assert requested["kind"] == "requested"
    assert outcome["kind"] == "outcome"
    assert requested["action_id"] == outcome["action_id"]

    kinds = {r["kind"] for r in rows}
    assert "verified" not in kinds
    assert "executed" not in kinds


@pytest.mark.asyncio
async def test_runner_kwargs_with_clock_no_type_error(tmp_path: Path) -> None:
    """Regression: runner._track passes (provider, log, clock, session_id, turn_id).

    Clock is now a required param (invariant 9). Verify the full call-site signature
    compiles and executes without TypeError.
    """
    log = JSONLActionLog(log_dir=tmp_path / "logs")
    booking = _make_booking("BID-REG")
    provider = _StubProvider(booking)

    # Replicate the exact kwargs the runner threads (with clock)
    result = await track_booking(
        TrackArgs(booking_id="BID-REG"),
        provider=provider,
        log=log,
        clock=_FROZEN_CLOCK,
        session_id="S-REG",
        turn_id="T-REG",
    )

    assert result["booking_id"] == "BID-REG"
    assert result["status"] == "en_route"


@pytest.mark.asyncio
async def test_unknown_booking_returns_error_dict_no_raise(tmp_path: Path) -> None:
    log = JSONLActionLog(log_dir=tmp_path / "logs")
    provider = _StubProvider(ValueError("unknown booking"))
    args = TrackArgs(booking_id="BID-MISSING")

    result = await track_booking(
        args,
        provider=provider,
        log=log,
        clock=_FROZEN_CLOCK,
        session_id="S3",
        turn_id="T3",
    )

    assert result == {"status": "error", "error": "unknown booking"}

    rows = log.read("S3")
    assert len(rows) == 2
    requested, outcome = rows
    assert requested["kind"] == "requested"
    assert outcome["kind"] == "outcome"
    assert outcome["status"] == "error"
    assert outcome["error"] == "unknown booking"
    assert outcome["result"] is None
