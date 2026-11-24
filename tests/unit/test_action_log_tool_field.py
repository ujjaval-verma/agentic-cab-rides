"""Every action-log row (requested/verified/executed/outcome) must carry a non-empty
`tool` field — added 2026-05-20 so plain grep works without an action_id join.

Exercises one gated call (confirm_booking) and one read-only call (track_booking)
end-to-end through their tool functions, then walks the JSONL and asserts every row
has `tool` set to the expected name.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest

from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.confirmation.scripted import ScriptedConfirmationChannel
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.adapters.uber_sim.provider import UberSimAdapter
from agentic_cab.core.types import Booking, Draft, Intent, Money, Option, RideOptionDetails
from agentic_cab.tools.confirm import ConfirmArgs, confirm_booking
from agentic_cab.tools.discover import DiscoverArgs, discover_options
from agentic_cab.tools.prepare import PrepareArgs, prepare_booking
from agentic_cab.tools.track import TrackArgs, track_booking

# ---------------------------------------------------------------------------
# Stub provider for track_booking test
# ---------------------------------------------------------------------------


class _StubProvider:
    name: str = "stub"
    supported_intent_kinds: ClassVar[set[str]] = {"ride"}

    def __init__(self, result: Booking) -> None:
        self._result = result

    async def discover(self, intent: Intent) -> list[Option]:
        return []

    async def prepare(self, option_id: str) -> Draft:
        raise NotImplementedError

    async def confirm(self, draft_id: str, idempotency_key: str) -> Booking:
        raise NotImplementedError

    async def track(self, booking_id: str) -> Booking:
        return self._result

    async def cancel(self, booking_id: str, idempotency_key: str) -> Booking:
        raise NotImplementedError

    async def quote_cancel_fee(self, booking_id: str) -> Money:
        raise NotImplementedError


_EXPIRES = datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC)


def _make_booking(booking_id: str = "BID-001") -> Booking:
    return Booking(
        booking_id=booking_id,
        draft_id="DRAFT-001",
        status="en_route",
        provider="stub",
        summary=Option(
            option_id="OPT-001",
            provider="stub",
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
async def test_every_row_kind_carries_tool_field_confirm(tmp_path: Path, meter) -> None:
    """All four rows emitted by confirm_booking carry tool='confirm_booking'."""
    store = SqliteStateStore(db_path=tmp_path / "s.db")
    store.apply_schema()
    log = JSONLActionLog(log_dir=tmp_path / "logs")
    clock = FrozenClock(start=datetime(2026, 5, 19, 12, 0, tzinfo=UTC))
    sim = UberSimAdapter(store=store, clock=clock, rng_seed=1)

    # discover → prepare to get a valid draft_id
    d_result = await discover_options(
        DiscoverArgs(
            pickup_address="A",
            pickup_lat=37.7749,
            pickup_lng=-122.4194,
            dropoff_address="B",
            dropoff_lat=37.7849,
            dropoff_lng=-122.4094,
        ),
        provider=sim,
        log=log,
        clock=clock,
        session_id="S",
        turn_id="T",
    )
    p_result = await prepare_booking(
        PrepareArgs(option_id=d_result["options"][0]["option_id"]),
        provider=sim,
        log=log,
        clock=clock,
        session_id="S",
        turn_id="T",
    )
    draft_id = p_result["draft_id"]

    ch = ScriptedConfirmationChannel(script=[{"match": "UberX", "action": "approve"}], clock=clock)
    result = await confirm_booking(
        ConfirmArgs(draft_id=draft_id),
        provider=sim,
        store=store,
        log=log,
        channel=ch,
        clock=clock,
        meter=meter,
        session_id="S",
        turn_id="T",
    )
    assert result["status"] == "confirmed"  # noqa: S101 pytest assertion

    action_id = result["action_id"]
    rows = [r for r in log.read("S") if r["action_id"] == action_id]
    assert [r["kind"] for r in rows] == ["requested", "verified", "executed", "outcome"]  # noqa: S101 pytest assertion

    for row in rows:
        assert row.get("tool") == "confirm_booking"  # noqa: S101 pytest assertion


@pytest.mark.asyncio
async def test_every_row_kind_carries_tool_field_track(tmp_path: Path) -> None:
    """Both rows emitted by track_booking carry tool='track_booking'."""
    log = JSONLActionLog(log_dir=tmp_path / "logs")
    provider = _StubProvider(_make_booking("BID-TRACK"))

    result = await track_booking(
        TrackArgs(booking_id="BID-TRACK"),
        provider=provider,
        log=log,
        clock=FrozenClock(start=datetime(2026, 5, 19, 12, 0, tzinfo=UTC)),
        session_id="S",
        turn_id="T",
    )
    assert result["booking_id"] == "BID-TRACK"  # noqa: S101 pytest assertion

    rows = log.read("S")
    assert [r["kind"] for r in rows] == ["requested", "outcome"]  # noqa: S101 pytest assertion

    for row in rows:
        assert row.get("tool") == "track_booking"  # noqa: S101 pytest assertion
