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
    sim: UberSimAdapter,
    store: SqliteStateStore,
    log: JSONLActionLog,
    clock: FrozenClock,
    meter,
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
        meter=meter,
        session_id="S",
        turn_id="T",
    )
    assert c_result["status"] == "confirmed"  # noqa: S101 pytest assertion
    return c_result["booking_id"]


@pytest.mark.asyncio
async def test_cancel_happy_path_emits_four_rows(harness, meter) -> None:
    """book → cancel → assert exactly requested → verified(ok) → executed → outcome(cancelled)."""
    store, log, clock, sim = harness
    booking_id = await _book(sim, store, log, clock, meter)

    ch = ScriptedConfirmationChannel(script=[{"match": "Cancel", "action": "approve"}], clock=clock)
    result = await cancel_booking(
        CancelArgs(booking_id=booking_id),
        provider=sim,
        store=store,
        log=log,
        channel=ch,
        clock=clock,
        meter=meter,
        session_id="S",
        turn_id="T",
    )
    assert result["status"] == "cancelled"  # noqa: S101 pytest assertion
    action_id = result["action_id"]
    rows = [r for r in log.read("S") if r["action_id"] == action_id]
    assert [r["kind"] for r in rows] == ["requested", "verified", "executed", "outcome"]  # noqa: S101 pytest assertion


@pytest.mark.asyncio
async def test_cancel_verified_row_records_approver_id_and_channel(harness, meter) -> None:
    """The gate's approver identity and channel land on the cancel verified row."""
    store, log, clock, sim = harness
    booking_id = await _book(sim, store, log, clock, meter)

    ch = ScriptedConfirmationChannel(script=[{"match": "Cancel", "action": "approve"}], clock=clock)
    result = await cancel_booking(
        CancelArgs(booking_id=booking_id),
        provider=sim,
        store=store,
        log=log,
        channel=ch,
        clock=clock,
        meter=meter,
        session_id="S",
        turn_id="T",
    )
    rows = [r for r in log.read("S") if r["action_id"] == result["action_id"]]
    assert [r["kind"] for r in rows] == ["requested", "verified", "executed", "outcome"]  # noqa: S101 pytest assertion
    confirmation = next(r for r in rows if r["kind"] == "verified")["checks"]["confirmation"]
    assert confirmation["approver_id"] == "scripted"  # noqa: S101 pytest assertion
    assert confirmation["channel"] == "scripted"  # noqa: S101 pytest assertion


@pytest.mark.asyncio
async def test_cancel_declined_emits_three_rows_no_executed(harness, meter) -> None:
    """Scripted channel returns approved=False → no executed row, outcome:status='rejected'."""
    store, log, clock, sim = harness
    booking_id = await _book(sim, store, log, clock, meter)

    ch = ScriptedConfirmationChannel(script=[{"match": "Cancel", "action": "decline"}], clock=clock)
    result = await cancel_booking(
        CancelArgs(booking_id=booking_id),
        provider=sim,
        store=store,
        log=log,
        channel=ch,
        clock=clock,
        meter=meter,
        session_id="S",
        turn_id="T",
    )
    assert result["status"] == "rejected"  # noqa: S101 pytest assertion
    action_id = result["action_id"]
    rows = [r for r in log.read("S") if r["action_id"] == action_id]
    kinds = [r["kind"] for r in rows]
    assert "executed" not in kinds  # CRITICAL: invariant 6 — rejected → no executed  # noqa: S101 pytest assertion
    verified = next(r for r in rows if r["kind"] == "verified")
    assert verified["result"] == "rejected"  # noqa: S101 pytest assertion


@pytest.mark.asyncio
async def test_cancel_unknown_booking(harness, meter) -> None:
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
        meter=meter,
        session_id="S",
        turn_id="T",
    )
    assert result["status"] == "rejected"  # noqa: S101 pytest assertion
    assert "unknown" in result["reason"].lower()  # noqa: S101 pytest assertion
    assert call_count == 0, "gate must NOT be invoked for unknown booking"  # noqa: S101 pytest assertion


@pytest.mark.asyncio
async def test_cancel_already_cancelled_short_circuits(harness, meter) -> None:
    """A booking already in 'cancelled' state → outcome:status='rejected'. No gate."""
    store, log, clock, sim = harness
    booking_id = await _book(sim, store, log, clock, meter)

    # Cancel once (gate approves)
    ch = ScriptedConfirmationChannel(script=[{"match": "Cancel", "action": "approve"}], clock=clock)
    r1 = await cancel_booking(
        CancelArgs(booking_id=booking_id),
        provider=sim,
        store=store,
        log=log,
        channel=ch,
        clock=clock,
        meter=meter,
        session_id="S",
        turn_id="T",
    )
    assert r1["status"] == "cancelled"  # noqa: S101 pytest assertion

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
        meter=meter,
        session_id="S",
        turn_id="T2",
    )
    assert r2["status"] == "rejected"  # noqa: S101 pytest assertion
    assert "already cancelled" in r2["reason"].lower()  # noqa: S101 pytest assertion
    assert call_count == 0, "gate must NOT be invoked for already-cancelled booking"  # noqa: S101 pytest assertion


@pytest.mark.asyncio
async def test_cancel_idempotency_replay(harness, meter) -> None:
    """Two cancel calls with the same key_factory → second recalls, gate NOT re-invoked."""
    store, log, clock, sim = harness
    booking_id = await _book(sim, store, log, clock, meter)

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

    def fixed_key(booking_id: str, fee_cents: int, status_at_quote: str) -> str:
        return "K-CANCEL-FIXED"

    ch = OnceChannel()

    r1 = await cancel_booking(
        CancelArgs(booking_id=booking_id),
        provider=sim,
        store=store,
        log=log,
        channel=ch,
        clock=clock,
        meter=meter,
        session_id="S",
        turn_id="T",
        key_factory=fixed_key,
    )
    assert r1["status"] == "cancelled"  # noqa: S101 pytest assertion

    r2 = await cancel_booking(
        CancelArgs(booking_id=booking_id),
        provider=sim,
        store=store,
        log=log,
        channel=ch,
        clock=clock,
        meter=meter,
        session_id="S",
        turn_id="T2",
        key_factory=fixed_key,
    )
    assert r2["status"] == "cancelled"  # noqa: S101 pytest assertion
    # The second action's executed row must carry replayed=True
    rows = [
        r for r in log.read("S") if r["action_id"] == r2["action_id"] and r["kind"] == "executed"
    ]
    assert len(rows) == 1  # noqa: S101 pytest assertion
    assert rows[0].get("replayed") is True  # noqa: S101 pytest assertion
    assert invoke_count == 1, "gate must only be invoked once"  # noqa: S101 pytest assertion


@pytest.mark.asyncio
async def test_cancel_request_uses_subject_kind_booking(harness, meter) -> None:
    """cancel_booking must emit subject_kind='booking' and subject_id=booking_id on the gate."""
    store, log, clock, sim = harness
    booking_id = await _book(sim, store, log, clock, meter)

    captured: list[ConfirmationRequest] = []

    class CapturingChannel:
        async def request(self, req: ConfirmationRequest) -> ConfirmationResult:
            captured.append(req)
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
        meter=meter,
        session_id="S",
        turn_id="T",
    )
    assert result["status"] == "cancelled"  # noqa: S101 pytest assertion
    assert len(captured) == 1  # noqa: S101 pytest assertion
    req = captured[0]
    assert req.subject_kind == "booking"  # noqa: S101 pytest assertion
    assert req.subject_id == booking_id  # noqa: S101 pytest assertion


@pytest.mark.asyncio
async def test_cancel_idempotency_key_is_deterministic_in_tool_path(
    harness, monkeypatch: pytest.MonkeyPatch, meter
) -> None:
    """Key determinism in the cancel tool path: same inputs → same key, key logged, gate once.

    What this test proves:
    - The intent-bound key is deterministic: same (booking_id, fee_cents, status_at_quote)
      always produces the same sha256 hex digest.
    - The executed row carries the idempotency_key field (tool wires the key into the log).
    - The gate is invoked exactly once (OnceApproveChannel raises on a second call).

    What this test does NOT prove (impossible at T27 scope):
    - The full recall short-circuit path.  After a successful cancel the booking transitions
      to "cancelled", so a second cancel derives a DIFFERENT key (status_at_quote changed)
      and hits the status-guard rather than the recall branch.  True in-flight retry replay
      requires the "in_progress" claim status from T30 (restart-survival semantics).
      See docs/FOLLOWUPS.md — "Restart-survival semantics for claim_or_recall".
    """
    from agentic_cab.core.ids import cancel_idempotency_key

    store, log, clock, sim = harness
    booking_id = await _book(sim, store, log, clock, meter)

    booking = store.get_booking(booking_id)
    assert booking is not None  # noqa: S101 pytest assertion
    status_at_quote = booking.status

    # Quote the fee before the cancel so we can independently derive the expected key.
    fee_before_cancel = await sim.quote_cancel_fee(booking_id)
    expected_key = cancel_idempotency_key(
        booking_id, fee_before_cancel.amount_cents, status_at_quote
    )

    # First (and only) cancel: gate approves once, key captured via spy_factory.
    invoke_count = 0

    class OnceApproveChannel:
        async def request(self, req: ConfirmationRequest) -> ConfirmationResult:
            nonlocal invoke_count
            invoke_count += 1
            if invoke_count > 1:
                raise AssertionError("gate invoked more than once — idempotency replay broken")
            return ConfirmationResult(
                approved=True, reason="approved", approved_at=clock.now(), approver="scripted"
            )

    captured_keys: list[str] = []

    def spy_factory(bid: str, fee_cents: int, status: str) -> str:
        k = cancel_idempotency_key(bid, fee_cents, status)
        captured_keys.append(k)
        return k

    r1 = await cancel_booking(
        CancelArgs(booking_id=booking_id),
        provider=sim,
        store=store,
        log=log,
        channel=OnceApproveChannel(),
        clock=clock,
        meter=meter,
        session_id="S",
        turn_id="T",
        key_factory=spy_factory,
    )
    assert r1["status"] == "cancelled"  # noqa: S101 pytest assertion
    assert len(captured_keys) == 1  # noqa: S101 pytest assertion
    key_first_call = captured_keys[0]

    # The executed row must carry the idempotency_key field.
    exec_rows_1 = [
        r for r in log.read("S") if r["action_id"] == r1["action_id"] and r["kind"] == "executed"
    ]
    assert len(exec_rows_1) == 1  # noqa: S101 pytest assertion
    assert exec_rows_1[0].get("idempotency_key"), "executed row must carry idempotency_key"  # noqa: S101 pytest assertion

    # Key-determinism: same (booking_id, fee_cents, status_at_quote) → same key, always.
    key_a = cancel_idempotency_key(booking_id, fee_before_cancel.amount_cents, status_at_quote)
    key_b = cancel_idempotency_key(booking_id, fee_before_cancel.amount_cents, status_at_quote)
    assert key_a == key_b, "same inputs must produce same idempotency key"  # noqa: S101 pytest assertion
    assert key_a == key_first_call, "key_factory output and direct derivation must match"  # noqa: S101 pytest assertion
    assert key_a == expected_key, "pre-computed expected key must match actual key used"  # noqa: S101 pytest assertion

    # Different fee → different key (different intent → different key, no accidental collision).
    key_different_fee = cancel_idempotency_key(
        booking_id, fee_before_cancel.amount_cents + 1, status_at_quote
    )
    assert key_different_fee != key_a  # noqa: S101 pytest assertion

    # Different status → different key.
    key_different_status = cancel_idempotency_key(
        booking_id, fee_before_cancel.amount_cents, "pending"
    )
    assert key_different_status != key_a  # noqa: S101 pytest assertion

    assert invoke_count == 1, "gate must only be invoked once across the full cancel"  # noqa: S101 pytest assertion


@pytest.mark.asyncio
async def test_cancel_fee_in_gate_summary(harness, meter) -> None:
    """Spy channel captures req.summary; assert it contains the dollar amount from _compute_fee."""
    store, log, clock, sim = harness
    booking_id = await _book(sim, store, log, clock, meter)

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
        meter=meter,
        session_id="S",
        turn_id="T",
    )
    assert result["status"] == "cancelled"  # noqa: S101 pytest assertion
    assert len(captured_summary) == 1  # noqa: S101 pytest assertion

    # Booking status at quote time is "confirmed" → _compute_fee returns $0.00
    expected_fee: Money = UberSimAdapter._compute_fee("confirmed")
    fee_str = f"${expected_fee.amount_cents / 100:.2f}"
    summary = captured_summary[0]
    assert fee_str in summary, f"Expected fee {fee_str!r} in summary {summary!r}"  # noqa: S101 pytest assertion
