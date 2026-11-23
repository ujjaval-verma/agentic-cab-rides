"""Unit tests for P8 cost-ledger wiring: draining the CostMeter onto gated outcome rows.

Covers `_emit_outcome` in tools/confirm.py and tools/cancel.py — see
docs/plans/2026-07-29-cost-ledger-wiring.md. CLAUDE.md invariants
6 (action log shape), 7 (persistence split), 9 (determinism — latency_ms must not
leak into the log), 13 (no deferred-work markers in code).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.confirmation.scripted import ScriptedConfirmationChannel
from agentic_cab.adapters.cost_meter import InMemoryCostMeter
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.adapters.uber_sim.provider import UberSimAdapter
from agentic_cab.core.types import LLMCall
from agentic_cab.tools.cancel import CancelArgs, cancel_booking
from agentic_cab.tools.confirm import ConfirmArgs, confirm_booking
from agentic_cab.tools.discover import DiscoverArgs, discover_options
from agentic_cab.tools.prepare import PrepareArgs, prepare_booking


def _call(cost_microcents: int, *, input_tokens: int = 10, output_tokens: int = 5) -> LLMCall:
    return LLMCall(
        provider="anthropic",
        model="claude-opus-4-7",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
        cost_microcents=cost_microcents,
        latency_ms=999,  # deliberately non-zero — must never reach the emitted row
    )


@pytest.fixture
def harness(tmp_path: Path):
    store = SqliteStateStore(db_path=tmp_path / "s.db")
    store.apply_schema()
    log = JSONLActionLog(log_dir=tmp_path / "logs")
    clock = FrozenClock(start=datetime(2026, 5, 19, 12, 0, tzinfo=UTC))
    sim = UberSimAdapter(store=store, clock=clock, rng_seed=1)
    return store, log, clock, sim


async def _prepare(sim, log, clock) -> str:
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
    return p_result["draft_id"]


async def _book(sim, store, log, clock, meter) -> str:
    draft_id = await _prepare(sim, log, clock)
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
    assert result["status"] == "confirmed"
    return result["booking_id"]


def _outcome_row(log: JSONLActionLog, session_id: str, action_id: str) -> dict:
    rows = [r for r in log.read(session_id) if r["action_id"] == action_id]
    return next(r for r in rows if r["kind"] == "outcome")


@pytest.mark.asyncio
async def test_confirm_happy_path_carries_cost_fields(harness) -> None:
    store, log, clock, sim = harness
    meter = InMemoryCostMeter()
    draft_id = await _prepare(sim, log, clock)
    meter.record(_call(1000))
    meter.record(_call(2500))
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

    outcome = _outcome_row(log, "S", result["action_id"])
    assert len(outcome["llm_calls_since_last_action"]) == 2
    assert outcome["cost_microcents_since_last_action"] == 3500
    assert all("latency_ms" not in c for c in outcome["llm_calls_since_last_action"])


@pytest.mark.asyncio
async def test_cancel_happy_path_carries_cost_fields(harness) -> None:
    store, log, clock, sim = harness
    meter = InMemoryCostMeter()
    booking_id = await _book(sim, store, log, clock, meter)
    meter.record(_call(777))
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

    outcome = _outcome_row(log, "S", result["action_id"])
    assert len(outcome["llm_calls_since_last_action"]) == 1
    assert outcome["cost_microcents_since_last_action"] == 777
    assert "latency_ms" not in outcome["llm_calls_since_last_action"][0]


@pytest.mark.asyncio
async def test_confirm_provider_raises_still_carries_cost_fields(
    harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The provider-raises path is the one that matters: 618b842 collapsed confirm.py
    to a single outcome emit point specifically so this path falls out for free."""
    store, log, clock, sim = harness
    meter = InMemoryCostMeter()
    draft_id = await _prepare(sim, log, clock)
    meter.record(_call(4200))
    ch = ScriptedConfirmationChannel(script=[{"match": "UberX", "action": "approve"}], clock=clock)

    async def boom(*args, **kwargs):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(sim, "confirm", boom)

    with pytest.raises(RuntimeError, match="provider exploded"):
        await confirm_booking(
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

    confirm_requested = [
        r for r in log.read("S") if r["kind"] == "requested" and r.get("tool") == "confirm_booking"
    ]
    action_id = confirm_requested[0]["action_id"]
    outcome = _outcome_row(log, "S", action_id)
    assert outcome["status"] == "error"
    assert outcome["llm_calls_since_last_action"] == [
        {
            "provider": "anthropic",
            "model": "claude-opus-4-7",
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cost_microcents": 4200,
        }
    ]
    assert outcome["cost_microcents_since_last_action"] == 4200
    assert "latency_ms" not in outcome["llm_calls_since_last_action"][0]


@pytest.mark.asyncio
async def test_cancel_provider_raises_still_carries_cost_fields(
    harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Symmetric to the confirm case: cancel.py's provider.cancel raising after gate
    approval must still close the action_id with an outcome row carrying cost fields."""
    store, log, clock, sim = harness
    meter = InMemoryCostMeter()
    booking_id = await _book(sim, store, log, clock, meter)
    meter.record(_call(999))
    ch = ScriptedConfirmationChannel(script=[{"match": "Cancel", "action": "approve"}], clock=clock)

    async def boom(*args, **kwargs):
        raise RuntimeError("cancel exploded")

    monkeypatch.setattr(sim, "cancel", boom)

    with pytest.raises(RuntimeError, match="cancel exploded"):
        await cancel_booking(
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

    cancel_requested = [
        r for r in log.read("S") if r["kind"] == "requested" and r.get("tool") == "cancel_booking"
    ]
    action_id = cancel_requested[0]["action_id"]
    outcome = _outcome_row(log, "S", action_id)
    assert outcome["status"] == "error"
    assert outcome["cost_microcents_since_last_action"] == 999
    assert "latency_ms" not in outcome["llm_calls_since_last_action"][0]


@pytest.mark.asyncio
async def test_empty_drain_emits_empty_list_and_zero_total(harness) -> None:
    """A drain returning no calls must still emit the fields — a reader needs to
    distinguish 'no calls attributed' from 'this build predates the ledger'."""
    store, log, clock, sim = harness
    meter = InMemoryCostMeter()
    draft_id = await _prepare(sim, log, clock)
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

    outcome = _outcome_row(log, "S", result["action_id"])
    assert outcome["llm_calls_since_last_action"] == []
    assert outcome["cost_microcents_since_last_action"] == 0


@pytest.mark.asyncio
async def test_read_only_tool_outcome_row_unchanged(harness) -> None:
    """discover_options is read-only and does not take a meter — its outcome row
    must gain no cost fields."""
    _store, log, clock, sim = harness
    d_args = DiscoverArgs(
        pickup_address="A",
        pickup_lat=37.7749,
        pickup_lng=-122.4194,
        dropoff_address="B",
        dropoff_lat=37.7849,
        dropoff_lng=-122.4094,
    )
    await discover_options(d_args, provider=sim, log=log, clock=clock, session_id="S", turn_id="T")

    outcome_rows = [
        r for r in log.read("S") if r["kind"] == "outcome" and r["tool"] == "discover_options"
    ]
    assert len(outcome_rows) == 1
    assert "llm_calls_since_last_action" not in outcome_rows[0]
    assert "cost_microcents_since_last_action" not in outcome_rows[0]


@pytest.mark.asyncio
async def test_second_gated_action_sees_only_calls_since_previous_drain(harness) -> None:
    """A shared meter across two gated actions in one session: the second action's
    outcome row must reflect only calls recorded after the first drain, not a
    re-summation of the first action's calls."""
    store, log, clock, sim = harness
    meter = InMemoryCostMeter()

    draft_id = await _prepare(sim, log, clock)
    meter.record(_call(1000))
    ch = ScriptedConfirmationChannel(script=[{"match": "UberX", "action": "approve"}], clock=clock)
    r1 = await confirm_booking(
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
    outcome1 = _outcome_row(log, "S", r1["action_id"])
    assert outcome1["cost_microcents_since_last_action"] == 1000

    # Second gated action (cancel) on the same session/meter — records a fresh call.
    meter.record(_call(50))
    ch2 = ScriptedConfirmationChannel(
        script=[{"match": "Cancel", "action": "approve"}], clock=clock
    )
    r2 = await cancel_booking(
        CancelArgs(booking_id=r1["booking_id"]),
        provider=sim,
        store=store,
        log=log,
        channel=ch2,
        clock=clock,
        meter=meter,
        session_id="S",
        turn_id="T2",
    )
    outcome2 = _outcome_row(log, "S", r2["action_id"])
    assert outcome2["cost_microcents_since_last_action"] == 50
    assert len(outcome2["llm_calls_since_last_action"]) == 1
    assert "latency_ms" not in outcome2["llm_calls_since_last_action"][0]
