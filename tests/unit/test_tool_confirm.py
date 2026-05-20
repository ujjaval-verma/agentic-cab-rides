"""Unit tests for confirm_booking — spec §7 + CLAUDE.md inv. 2, 3, 4, 5, 6."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.confirmation.scripted import ScriptedConfirmationChannel
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.adapters.uber_sim.provider import UberSimAdapter
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
        d_args,
        provider=sim,
        log=log,
        clock=clock,
        session_id="S",
        turn_id="T",
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


@pytest.mark.asyncio
async def test_happy_path_emits_4_rows_in_order(harness) -> None:
    store, log, clock, sim = harness
    draft_id = await _prepare(sim, log, clock)
    ch = ScriptedConfirmationChannel(script=[{"match": "UberX", "action": "approve"}], clock=clock)
    result = await confirm_booking(
        ConfirmArgs(draft_id=draft_id),
        provider=sim,
        store=store,
        log=log,
        channel=ch,
        clock=clock,
        session_id="S",
        turn_id="T",
    )
    assert result["status"] == "confirmed"
    action_id = result["action_id"]
    rows = [r for r in log.read("S") if r["action_id"] == action_id]
    assert [r["kind"] for r in rows] == ["requested", "verified", "executed", "outcome"]


@pytest.mark.asyncio
async def test_fingerprint_mismatch_short_circuits(harness) -> None:
    store, log, clock, sim = harness
    draft_id = await _prepare(sim, log, clock)
    # Mutate the stored draft's option_snapshot to break the fingerprint
    conn = sqlite3.connect(str(store._db_path))
    conn.execute(
        "UPDATE drafts SET option_snapshot = json_set(option_snapshot, '$.title', 'TAMPERED') "
        "WHERE draft_id = ?",
        (draft_id,),
    )
    conn.commit()
    conn.close()
    ch = ScriptedConfirmationChannel(script=[{"match": "UberX", "action": "approve"}], clock=clock)
    result = await confirm_booking(
        ConfirmArgs(draft_id=draft_id),
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
async def test_consumed_draft_rejected(harness) -> None:
    store, log, clock, sim = harness
    draft_id = await _prepare(sim, log, clock)
    store.consume_draft(draft_id, at=clock.now())
    ch = ScriptedConfirmationChannel(script=[{"match": "UberX", "action": "approve"}], clock=clock)
    result = await confirm_booking(
        ConfirmArgs(draft_id=draft_id),
        provider=sim,
        store=store,
        log=log,
        channel=ch,
        clock=clock,
        session_id="S",
        turn_id="T",
    )
    assert result["status"] == "rejected"
    assert "consumed" in result["reason"].lower()


@pytest.mark.asyncio
async def test_idempotency_replay_short_circuits_second_provider_call(harness) -> None:
    """Two confirms with the same idempotency key → second recalls, no double-book."""
    store, log, clock, sim = harness

    def fixed_key() -> str:
        return "K-FIXED"

    # First confirm
    draft_id_1 = await _prepare(sim, log, clock)
    ch = ScriptedConfirmationChannel(script=[{"match": "UberX", "action": "approve"}], clock=clock)
    r1 = await confirm_booking(
        ConfirmArgs(draft_id=draft_id_1),
        provider=sim,
        store=store,
        log=log,
        channel=ch,
        clock=clock,
        session_id="S",
        turn_id="T",
        key_factory=fixed_key,
    )
    assert r1["status"] == "confirmed"
    # Second confirm with same key on a fresh draft
    draft_id_2 = await _prepare(sim, log, clock)
    r2 = await confirm_booking(
        ConfirmArgs(draft_id=draft_id_2),
        provider=sim,
        store=store,
        log=log,
        channel=ch,
        clock=clock,
        session_id="S",
        turn_id="T",
        key_factory=fixed_key,
    )
    assert r2["status"] == "confirmed"
    assert r2["booking_id"] == r1["booking_id"]  # same booking returned
    # Second action's executed row carries replayed=true
    rows = [
        r for r in log.read("S") if r["action_id"] == r2["action_id"] and r["kind"] == "executed"
    ]
    assert len(rows) == 1
    assert rows[0].get("replayed") is True
    assert rows[0]["latency_ms"] == 0


@pytest.mark.asyncio
async def test_provider_exception_closes_log_with_outcome(harness, monkeypatch) -> None:
    """If provider.confirm raises, action_id must still close with an outcome row.
    Invariant 6 protects against orphaned verified=ok rows with no executed/outcome."""
    store, log, clock, sim = harness
    draft_id = await _prepare(sim, log, clock)
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
            session_id="S",
            turn_id="T",
        )

    # Find the action_id for this confirm by looking at the last requested row
    confirm_requested = [
        r for r in log.read("S") if r["kind"] == "requested" and r.get("tool") == "confirm_booking"
    ]
    assert len(confirm_requested) == 1
    action_id = confirm_requested[0]["action_id"]
    rows = [r for r in log.read("S") if r["action_id"] == action_id]
    # verified=ok happened, but then provider raised. We must have an outcome.
    kinds = [r["kind"] for r in rows]
    assert "verified" in kinds
    assert kinds[-1] == "outcome"
    outcome = rows[-1]
    assert outcome["status"] == "error"
    assert "provider exploded" in (outcome.get("error") or "")


@pytest.mark.asyncio
async def test_confirm_request_uses_subject_kind_draft(harness) -> None:
    """confirm_booking must emit subject_kind='draft' and subject_id=draft_id on the gate."""
    from agentic_cab.core.ports import ConfirmationRequest, ConfirmationResult

    store, log, clock, sim = harness
    draft_id = await _prepare(sim, log, clock)

    captured: list[ConfirmationRequest] = []

    class CapturingChannel:
        async def request(self, req: ConfirmationRequest) -> ConfirmationResult:
            captured.append(req)
            return ConfirmationResult(
                approved=True, reason="approved", approved_at=clock.now(), approver="spy"
            )

    result = await confirm_booking(
        ConfirmArgs(draft_id=draft_id),
        provider=sim,
        store=store,
        log=log,
        channel=CapturingChannel(),
        clock=clock,
        session_id="S",
        turn_id="T",
    )
    assert result["status"] == "confirmed"
    assert len(captured) == 1
    req = captured[0]
    assert req.subject_kind == "draft"
    assert req.subject_id == draft_id


@pytest.mark.asyncio
async def test_price_drift_branch_is_stubbed_in_p1(harness) -> None:
    """P1 sets requote_price_drift_cents=0 without re-discovering. Documents the stub;
    P3 will land real re-quote alongside intent-bound idempotency keys (FOLLOWUPS)."""
    store, log, clock, sim = harness
    draft_id = await _prepare(sim, log, clock)
    ch = ScriptedConfirmationChannel(script=[{"match": "UberX", "action": "approve"}], clock=clock)
    result = await confirm_booking(
        ConfirmArgs(draft_id=draft_id),
        provider=sim,
        store=store,
        log=log,
        channel=ch,
        clock=clock,
        session_id="S",
        turn_id="T",
    )
    rows = [r for r in log.read("S") if r["action_id"] == result["action_id"]]
    verified = next(r for r in rows if r["kind"] == "verified")
    assert verified["checks"]["requote_price_drift_cents"] == 0
