"""Unit tests for cancel_booking — spec §7 + CLAUDE.md inv. 2, 5, 6, 10, 11."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.confirmation.scripted import ScriptedConfirmationChannel
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.adapters.uber_sim.provider import UberSimAdapter
from agentic_cab.core.ports import ConfirmationRequest, ConfirmationResult
from agentic_cab.core.types import Money
from agentic_cab.tools.cancel import CancelArgs, cancel_booking
from agentic_cab.tools.confirm import ConfirmArgs, confirm_booking
from agentic_cab.tools.discover import DiscoverArgs, discover_options
from agentic_cab.tools.prepare import PrepareArgs, prepare_booking


@pytest.fixture
def harness(tmp_path: Path):
    store = SqliteStateStore(db_path=tmp_path / "s.db")
    store.apply_schema()
    log = JSONLActionLog(log_dir=tmp_path / "logs")
    clock = FrozenClock(start=datetime(2026, 5, 19, 12, 0, tzinfo=UTC))
    sim = UberSimAdapter(store=store, clock=clock, rng_seed=1)
    return store, log, clock, sim


async def _book(
    sim: UberSimAdapter, store: SqliteStateStore, log: JSONLActionLog, clock: FrozenClock
) -> str:
    """Helper: discover → prepare → confirm → return booking_id."""
    d_args = DiscoverArgs(
        pickup_address="A",
        pickup_lat=37.7749,
        pickup_lng=-122.4194,
        dropoff_address="B",
        dropoff_lat=37.7849,
        dropoff_lng=-122.4094,
    )
    d_result = await discover_options(
        d_args, provider=sim, log=log, clock=clock, session_id="S", turn_id="T"
    )
    opt_id = d_result["options"][0]["option_id"]
    p_result = await prepare_booking(
        PrepareArgs(option_id=opt_id),
        provider=sim,
        log=log,
        clock=clock,
        session_id="S",
        turn_id="T",
    )
    draft_id = p_result["draft_id"]
    ch = ScriptedConfirmationChannel(script=[{"match": "UberX", "action": "approve"}], clock=clock)
    c_result = await confirm_booking(
        ConfirmArgs(draft_id=draft_id),
        provider=sim,
        store=store,
        log=log,
        channel=ch,
        clock=clock,
        session_id="S",
        turn_id="T",
    )
    assert c_result["status"] == "confirmed"
    return c_result["booking_id"]


@pytest.mark.asyncio
async def test_cancel_happy_path_emits_four_rows(harness) -> None:
    """book → cancel → assert exactly requested → verified(ok) → executed → outcome(cancelled)."""
    store, log, clock, sim = harness
    booking_id = await _book(sim, store, log, clock)

    ch = ScriptedConfirmationChannel(script=[{"match": "Cancel", "action": "approve"}], clock=clock)
    result = await cancel_booking(
        CancelArgs(booking_id=booking_id),
        provider=sim,
        store=store,
        log=log,
        channel=ch,
        clock=clock,
        session_id="S",
        turn_id="T",
    )
    assert result["status"] == "cancelled"
    action_id = result["action_id"]
    rows = [r for r in log.read("S") if r["action_id"] == action_id]
    assert [r["kind"] for r in rows] == ["requested", "verified", "executed", "outcome"]


@pytest.mark.asyncio
async def test_cancel_declined_emits_three_rows_no_executed(harness) -> None:
    """Scripted channel returns approved=False → no executed row, outcome:status='rejected'."""
    store, log, clock, sim = harness
    booking_id = await _book(sim, store, log, clock)

    ch = ScriptedConfirmationChannel(script=[{"match": "Cancel", "action": "decline"}], clock=clock)
    result = await cancel_booking(
        CancelArgs(booking_id=booking_id),
        provider=sim,
        store=store,
        log=log,
        channel=ch,
        clock=clock,
        session_id="S",
        turn_id="T",
    )
    assert result["status"] == "rejected"
    action_id = result["action_id"]
    rows = [r for r in log.read("S") if r["action_id"] == action_id]
    kinds = [r["kind"] for r in rows]
    assert "executed" not in kinds  # CRITICAL: invariant 6 — rejected → no executed
    verified = next(r for r in rows if r["kind"] == "verified")
    assert verified["result"] == "rejected"


@pytest.mark.asyncio
async def test_cancel_unknown_booking(harness) -> None:
    """outcome:status='rejected', reason='unknown booking'. No gate invocation."""
    store, log, clock, sim = harness

    call_count = 0

    class SpyChannel:
        async def request(self, req: ConfirmationRequest) -> ConfirmationResult:
            nonlocal call_count
            call_count += 1
            return ConfirmationResult(
                approved=True, reason="approved", approved_at=clock.now(), approver="spy"
            )

    result = await cancel_booking(
        CancelArgs(booking_id="nonexistent-booking"),
        provider=sim,
        store=store,
        log=log,
        channel=SpyChannel(),
        clock=clock,
        session_id="S",
        turn_id="T",
    )
    assert result["status"] == "rejected"
    assert "unknown" in result["reason"].lower()
    assert call_count == 0, "gate must NOT be invoked for unknown booking"


@pytest.mark.asyncio
async def test_cancel_already_cancelled_short_circuits(harness) -> None:
    """A booking already in 'cancelled' state → outcome:status='rejected'. No gate."""
    store, log, clock, sim = harness
    booking_id = await _book(sim, store, log, clock)

    # Cancel once (gate approves)
    ch = ScriptedConfirmationChannel(script=[{"match": "Cancel", "action": "approve"}], clock=clock)
    r1 = await cancel_booking(
        CancelArgs(booking_id=booking_id),
        provider=sim,
        store=store,
        log=log,
        channel=ch,
        clock=clock,
        session_id="S",
        turn_id="T",
    )
    assert r1["status"] == "cancelled"

    call_count = 0

    class CountingChannel:
        async def request(self, req: ConfirmationRequest) -> ConfirmationResult:
            nonlocal call_count
            call_count += 1
            return ConfirmationResult(
                approved=True, reason="approved", approved_at=clock.now(), approver="spy"
            )

    # Cancel again — already cancelled, must short-circuit without gate
    r2 = await cancel_booking(
        CancelArgs(booking_id=booking_id),
        provider=sim,
        store=store,
        log=log,
        channel=CountingChannel(),
        clock=clock,
        session_id="S",
        turn_id="T2",
    )
    assert r2["status"] == "rejected"
    assert "already cancelled" in r2["reason"].lower()
    assert call_count == 0, "gate must NOT be invoked for already-cancelled booking"


@pytest.mark.asyncio
async def test_cancel_idempotency_replay(harness) -> None:
    """Two cancel calls with the same key_factory → second recalls, gate NOT re-invoked."""
    store, log, clock, sim = harness
    booking_id = await _book(sim, store, log, clock)

    invoke_count = 0

    class OnceChannel:
        async def request(self, req: ConfirmationRequest) -> ConfirmationResult:
            nonlocal invoke_count
            invoke_count += 1
            if invoke_count > 1:
                raise AssertionError("gate invoked more than once — idempotency replay broken")
            return ConfirmationResult(
                approved=True, reason="approved", approved_at=clock.now(), approver="scripted"
            )

    def fixed_key() -> str:
        return "K-CANCEL-FIXED"

    ch = OnceChannel()

    r1 = await cancel_booking(
        CancelArgs(booking_id=booking_id),
        provider=sim,
        store=store,
        log=log,
        channel=ch,
        clock=clock,
        session_id="S",
        turn_id="T",
        key_factory=fixed_key,
    )
    assert r1["status"] == "cancelled"

    r2 = await cancel_booking(
        CancelArgs(booking_id=booking_id),
        provider=sim,
        store=store,
        log=log,
        channel=ch,
        clock=clock,
        session_id="S",
        turn_id="T2",
        key_factory=fixed_key,
    )
    assert r2["status"] == "cancelled"
    # The second action's executed row must carry replayed=True
    rows = [
        r for r in log.read("S") if r["action_id"] == r2["action_id"] and r["kind"] == "executed"
    ]
    assert len(rows) == 1
    assert rows[0].get("replayed") is True
    assert invoke_count == 1, "gate must only be invoked once"


@pytest.mark.asyncio
async def test_cancel_fee_in_gate_summary(harness) -> None:
    """Spy channel captures req.summary; assert it contains the dollar amount from _compute_fee."""
    store, log, clock, sim = harness
    booking_id = await _book(sim, store, log, clock)

    captured_summary: list[str] = []

    class CapturingChannel:
        async def request(self, req: ConfirmationRequest) -> ConfirmationResult:
            captured_summary.append(req.summary)
            return ConfirmationResult(
                approved=True, reason="approved", approved_at=clock.now(), approver="spy"
            )

    result = await cancel_booking(
        CancelArgs(booking_id=booking_id),
        provider=sim,
        store=store,
        log=log,
        channel=CapturingChannel(),
        clock=clock,
        session_id="S",
        turn_id="T",
    )
    assert result["status"] == "cancelled"
    assert len(captured_summary) == 1

    # Booking status at quote time is "confirmed" → _compute_fee returns $0.00
    expected_fee: Money = UberSimAdapter._compute_fee("confirmed")
    fee_str = f"${expected_fee.amount_cents / 100:.2f}"
    assert (
        fee_str in captured_summary[0]
    ), f"Expected fee {fee_str!r} in summary {captured_summary[0]!r}"
