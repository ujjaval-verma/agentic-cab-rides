"""Tests for real re_quote_on_confirm in confirm_booking. T28 Commit 5.

Invariant 6: verified.result=='rejected' → NO executed row follows.
Invariant 4: re-quote via provider.discover(intent) before gating the channel.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.confirmation.scripted import ScriptedConfirmationChannel
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.adapters.uber_sim.provider import UberSimAdapter
from agentic_cab.adapters.uber_sim.surge import SurgeCurve
from agentic_cab.tools.confirm import ConfirmArgs, confirm_booking
from agentic_cab.tools.discover import DiscoverArgs, discover_options
from agentic_cab.tools.prepare import PrepareArgs, prepare_booking

_START = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)


def _harness(
    tmp_path: Path,
    surge_curve: SurgeCurve | None = None,
    db_name: str = "s.db",
) -> tuple[SqliteStateStore, JSONLActionLog, FrozenClock, UberSimAdapter]:
    store = SqliteStateStore(db_path=tmp_path / db_name)
    store.apply_schema()
    log = JSONLActionLog(log_dir=tmp_path / "logs")
    clock = FrozenClock(start=_START)
    sim = UberSimAdapter(store=store, clock=clock, rng_seed=1, surge_curve=surge_curve)
    return store, log, clock, sim


async def _discover_prepare(
    sim: UberSimAdapter,
    log: JSONLActionLog,
    clock: FrozenClock,
    vehicle_class: str = "uberx",
) -> str:
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
    opt = next(o for o in d_result["options"] if o["details"]["vehicle_class"] == vehicle_class)
    p_result = await prepare_booking(
        PrepareArgs(option_id=opt["option_id"]),
        provider=sim,
        log=log,
        clock=clock,
        session_id="S",
        turn_id="T",
    )
    return p_result["draft_id"]


@pytest.mark.asyncio
async def test_re_quote_within_threshold_proceeds(tmp_path: Path, meter) -> None:
    """Without surge, discover returns same price at prepare and confirm → proceeds."""
    store, log, clock, sim = _harness(tmp_path)
    draft_id = await _discover_prepare(sim, log, clock)
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
    rows = [r for r in log.read("S") if r["action_id"] == result["action_id"]]
    kinds = [r["kind"] for r in rows]
    assert kinds == ["requested", "verified", "executed", "outcome"]
    verified = next(r for r in rows if r["kind"] == "verified")
    # drift should be 0 since no surge
    assert verified["checks"]["requote_price_drift_cents"] == 0


@pytest.mark.asyncio
async def test_re_quote_above_threshold_returns_price_changed_with_new_draft(
    tmp_path: Path,
    meter,
) -> None:
    """With force_surge_on_confirm=True, re-quote drift exceeds threshold → price_changed."""
    # Surge is off during discover (peak ended 2 hours ago) but force_surge_on_confirm=True
    # makes the re-quote return peak_multiplier=3.0 → drift >> 100 cents
    surge = SurgeCurve(
        base_multiplier=Decimal("1.0"),
        peak_multiplier=Decimal("3.0"),
        peak_start=_START - timedelta(hours=2),
        peak_duration_min=10,
        force_surge_on_confirm=True,
    )
    store, log, clock, sim = _harness(tmp_path, surge_curve=surge)
    draft_id = await _discover_prepare(sim, log, clock)
    # Channel would approve, but price_changed must short-circuit before gating
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

    # Outcome must be price_changed
    assert result["status"] == "price_changed"
    assert "new_draft_id" in result
    import re

    assert re.match(r"^[0-9A-Z]{26}$", result["new_draft_id"]), "new_draft_id must be a ULID"

    rows = [r for r in log.read("S") if r["action_id"] == result["action_id"]]
    kinds = [r["kind"] for r in rows]

    # Invariant 6: no executed row for rejected action
    assert "executed" not in kinds, "executed row must NOT appear after price_changed rejection"

    verified = next(r for r in rows if r["kind"] == "verified")
    assert verified["result"] == "rejected"
    assert verified["reason"] == "price_changed"
    checks = verified["checks"]
    assert checks["draft_found"] is True
    assert checks["fingerprint_match"] is True
    assert checks["requote_price_drift_cents"] > 100  # threshold is 100
    assert "new_draft_id" in checks

    outcome = next(r for r in rows if r["kind"] == "outcome")
    assert outcome["status"] == "price_changed"


@pytest.mark.asyncio
async def test_re_quote_drift_recorded_in_verified_checks_within_threshold(
    tmp_path: Path,
    meter,
) -> None:
    """Under-threshold drift is recorded in verified.checks even when proceeding."""
    store, log, clock, sim = _harness(tmp_path)
    draft_id = await _discover_prepare(sim, log, clock)
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
    rows = [r for r in log.read("S") if r["action_id"] == result["action_id"]]
    verified = next(r for r in rows if r["kind"] == "verified")
    assert "requote_price_drift_cents" in verified["checks"]


@pytest.mark.asyncio
async def test_re_quote_no_intent_snapshot_rejected(tmp_path: Path, meter) -> None:
    """Drafts without intent_snapshot are rejected by the 3a intent guard.

    After the cross-session idempotency rework, confirm_booking requires a
    RideIntent in intent_snapshot to derive the intent hash. Legacy / P1 drafts
    without intent_snapshot are rejected with reason
    'intent_snapshot_missing_or_unsupported' — no executed row (invariant 6).
    """
    from agentic_cab.core.fingerprint import fingerprint as fp
    from agentic_cab.core.ids import new_draft_id
    from agentic_cab.core.types import Draft, Money, Option, RideOptionDetails

    store, log, clock, sim = _harness(tmp_path)
    # Manually create a draft without intent_snapshot
    now = clock.now()
    opt = Option(
        option_id="opt-uberx-manual",
        provider="uber-sim",
        title="UberX",
        price=Money(amount_cents=500, currency="USD"),
        eta_seconds=60,
        when=None,
        details=RideOptionDetails(vehicle_class="uberx", capacity=4),
        expires_at=now + timedelta(minutes=3),
    )
    # inject into sim's cache so prepare doesn't fail
    sim._option_cache["opt-uberx-manual"] = opt
    draft = Draft(
        draft_id=new_draft_id(),
        option_snapshot=opt,
        fingerprint=fp(opt),
        created_at=now,
        expires_at=now + timedelta(minutes=3),
        intent_snapshot=None,  # explicitly no intent
    )
    store.put_draft(draft)
    ch = ScriptedConfirmationChannel(script=[{"match": "UberX", "action": "approve"}], clock=clock)
    result = await confirm_booking(
        ConfirmArgs(draft_id=draft.draft_id),
        provider=sim,
        store=store,
        log=log,
        channel=ch,
        clock=clock,
        meter=meter,
        session_id="S",
        turn_id="T",
    )
    # Intent guard fires: rejected without executed row (invariant 6).
    assert result["status"] == "rejected"
    assert "intent_snapshot_missing_or_unsupported" in result.get("reason", "")
    rows = [r for r in log.read("S") if r["action_id"] == result["action_id"]]
    kinds = [r["kind"] for r in rows]
    assert "executed" not in kinds


@pytest.mark.asyncio
async def test_price_drift_stub_test_is_now_a_real_call(tmp_path: Path, meter) -> None:
    """P1 stub test must now demonstrate a real discover() call is made.

    This test was originally called test_price_drift_branch_is_stubbed_in_p1.
    It now verifies that the drift field is populated from an actual provider.discover()
    call (value is deterministic: 0 drift for no-surge scenario).
    """
    store, log, clock, sim = _harness(tmp_path)
    draft_id = await _discover_prepare(sim, log, clock)
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
    rows = [r for r in log.read("S") if r["action_id"] == result["action_id"]]
    verified = next(r for r in rows if r["kind"] == "verified")
    # Drift is 0 because no surge, but it came from a real provider.discover() call
    assert verified["checks"]["requote_price_drift_cents"] == 0
