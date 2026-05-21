"""Tests for confirm_booking's duplicate-intent short-circuit. See spec §4.4 + §6.2."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.confirmation.scripted import ScriptedConfirmationChannel
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.adapters.uber_sim.provider import UberSimAdapter
from agentic_cab.core.ids import intent_hash, new_session_id, new_turn_id
from agentic_cab.core.types import AddressRef, RideIntent, RideOptionDetails
from agentic_cab.tools.confirm import ConfirmArgs, confirm_booking


def _addr(name: str, lat: float, lng: float) -> AddressRef:
    return AddressRef(
        raw=name,
        formatted=name,
        lat=lat,
        lng=lng,
        confidence=0.99,
        provider_metadata={"alternates": []},
    )


T0 = datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def harness(tmp_path: Path):
    store = SqliteStateStore(tmp_path / "state.db")
    store.apply_schema()
    log = JSONLActionLog(log_dir=tmp_path / "logs")
    clock = FrozenClock(T0)
    sim = UberSimAdapter(store=store, clock=clock, rng_seed=42)
    return store, log, clock, sim


async def _prepare_draft(
    sim: UberSimAdapter, store: SqliteStateStore, clock: FrozenClock, intent: RideIntent
):
    options = await sim.discover(intent)
    uberx = next(
        o
        for o in options
        if isinstance(o.details, RideOptionDetails) and o.details.vehicle_class == "uberx"
    )
    return await sim.prepare(uberx.option_id)


@pytest.mark.asyncio
async def test_active_prior_booking_without_nonce_returns_duplicate_intent(harness) -> None:
    store, log, clock, sim = harness
    intent = RideIntent(
        pickup=_addr("A", 37.0, -122.0),
        dropoff=_addr("B", 37.1, -122.1),
        when="now",
    )
    channel = ScriptedConfirmationChannel(
        script=[{"match": "UberX", "action": "approve"}, {"match": "UberX", "action": "approve"}],
        clock=clock,
    )
    session_id = new_session_id()

    # First confirm — succeeds.
    d1 = await _prepare_draft(sim, store, clock, intent)
    r1 = await confirm_booking(
        ConfirmArgs(draft_id=d1.draft_id),
        provider=sim,
        store=store,
        log=log,
        channel=channel,
        clock=clock,
        session_id=session_id,
        turn_id=new_turn_id(),
    )
    assert r1["status"] == "confirmed"

    # Second confirm — fresh draft, same intent, no nonce.
    d2 = await _prepare_draft(sim, store, clock, intent)
    assert d2.draft_id != d1.draft_id
    r2 = await confirm_booking(
        ConfirmArgs(draft_id=d2.draft_id),
        provider=sim,
        store=store,
        log=log,
        channel=channel,
        clock=clock,
        session_id=session_id,
        turn_id=new_turn_id(),
    )
    assert r2["status"] == "duplicate_intent"
    assert r2["existing_booking_id"] == r1["booking_id"]
    assert r2["existing_status"] not in {"completed", "cancelled", "failed"}


@pytest.mark.asyncio
async def test_active_prior_booking_with_nonce_proceeds(harness) -> None:
    store, log, clock, sim = harness
    intent = RideIntent(
        pickup=_addr("A", 37.0, -122.0),
        dropoff=_addr("B", 37.1, -122.1),
        when="now",
    )
    channel = ScriptedConfirmationChannel(
        script=[{"match": "UberX", "action": "approve"}, {"match": "UberX", "action": "approve"}],
        clock=clock,
    )
    session_id = new_session_id()

    d1 = await _prepare_draft(sim, store, clock, intent)
    r1 = await confirm_booking(
        ConfirmArgs(draft_id=d1.draft_id),
        provider=sim,
        store=store,
        log=log,
        channel=channel,
        clock=clock,
        session_id=session_id,
        turn_id=new_turn_id(),
    )
    assert r1["status"] == "confirmed"

    d2 = await _prepare_draft(sim, store, clock, intent)
    r2 = await confirm_booking(
        ConfirmArgs(draft_id=d2.draft_id, intent_override_nonce="user-said-yes"),
        provider=sim,
        store=store,
        log=log,
        channel=channel,
        clock=clock,
        session_id=session_id,
        turn_id=new_turn_id(),
    )
    assert r2["status"] == "confirmed"
    assert r2["booking_id"] != r1["booking_id"]


@pytest.mark.asyncio
async def test_terminal_prior_booking_lets_new_through(harness) -> None:
    store, log, clock, sim = harness
    intent = RideIntent(
        pickup=_addr("A", 37.0, -122.0),
        dropoff=_addr("B", 37.1, -122.1),
        when="now",
    )
    channel = ScriptedConfirmationChannel(
        script=[{"match": "UberX", "action": "approve"}, {"match": "UberX", "action": "approve"}],
        clock=clock,
    )
    session_id = new_session_id()

    d1 = await _prepare_draft(sim, store, clock, intent)
    r1 = await confirm_booking(
        ConfirmArgs(draft_id=d1.draft_id),
        provider=sim,
        store=store,
        log=log,
        channel=channel,
        clock=clock,
        session_id=session_id,
        turn_id=new_turn_id(),
    )
    assert r1["status"] == "confirmed"

    # Force the first booking to terminal via the StateStore (no public API needed).
    from agentic_cab.core.types import BookingEvent

    store.update_booking_status(
        r1["booking_id"],
        "completed",
        BookingEvent(ts=T0 + timedelta(minutes=20), kind="completed", payload={}),
    )

    # Advance clock past the 10-minute bucket so when=now hashes differently?
    # NO — generation must advance independently of bucket. Keep clock fixed.
    d2 = await _prepare_draft(sim, store, clock, intent)
    r2 = await confirm_booking(
        ConfirmArgs(draft_id=d2.draft_id),
        provider=sim,
        store=store,
        log=log,
        channel=channel,
        clock=clock,
        session_id=session_id,
        turn_id=new_turn_id(),
    )
    assert r2["status"] == "confirmed"
    assert r2["booking_id"] != r1["booking_id"]


@pytest.mark.asyncio
async def test_duplicate_intent_log_shape_no_executed_row(harness) -> None:
    """Invariant 6: duplicate_intent must produce requested→verified(rejected)→outcome only."""
    store, log, clock, sim = harness
    intent = RideIntent(
        pickup=_addr("A", 37.0, -122.0),
        dropoff=_addr("B", 37.1, -122.1),
        when="now",
    )
    channel = ScriptedConfirmationChannel(
        script=[{"match": "UberX", "action": "approve"}],
        clock=clock,
    )
    session_id = new_session_id()

    d1 = await _prepare_draft(sim, store, clock, intent)
    r1 = await confirm_booking(
        ConfirmArgs(draft_id=d1.draft_id),
        provider=sim,
        store=store,
        log=log,
        channel=channel,
        clock=clock,
        session_id=session_id,
        turn_id=new_turn_id(),
    )
    assert r1["status"] == "confirmed"

    d2 = await _prepare_draft(sim, store, clock, intent)
    r2 = await confirm_booking(
        ConfirmArgs(draft_id=d2.draft_id),
        provider=sim,
        store=store,
        log=log,
        channel=channel,
        clock=clock,
        session_id=session_id,
        turn_id=new_turn_id(),
    )
    assert r2["status"] == "duplicate_intent"

    # Check log shape for the second action
    action_id_2 = r2["action_id"]
    rows = [r for r in log.read(session_id) if r["action_id"] == action_id_2]
    kinds = [r["kind"] for r in rows]
    assert "executed" not in kinds, "duplicate_intent must NOT emit an executed row (invariant 6)"
    assert kinds == ["requested", "verified", "outcome"]
    verified = next(r for r in rows if r["kind"] == "verified")
    assert verified["result"] == "rejected"
    assert verified.get("reason") == "duplicate_intent"
    assert isinstance(
        verified.get("checks"), dict
    ), "verified row must carry a 'checks' dict (spec §5)"
    assert (
        "intent_hash" in verified["checks"]
    ), "verified.checks must include 'intent_hash' (spec §5)"
    assert (
        "existing_booking_id" in verified["checks"]
    ), "verified.checks must include 'existing_booking_id' (spec §5)"
    outcome = next(r for r in rows if r["kind"] == "outcome")
    assert outcome["status"] == "duplicate_intent"
    assert outcome["result"]["existing_booking_id"] == r1["booking_id"]


@pytest.mark.asyncio
async def test_recall_path_heals_null_intent_hash(harness) -> None:
    """Crash-recovery: if a prior confirm crashed between finalize_idempotency and
    set_booking_intent_hash, the booking row has intent_hash IS NULL.  The recall
    path must write the intent_hash so future dedup lookups can find the booking.

    Simulates the crash by inserting an idempotency key pointing to a booking that
    has no intent_hash set (i.e., set_booking_intent_hash was never called).
    """
    store, log, clock, sim = harness
    intent = RideIntent(
        pickup=_addr("A", 37.0, -122.0),
        dropoff=_addr("B", 37.1, -122.1),
        when="now",
    )
    # Compute the intent hash exactly as confirm_booking will.
    ih = intent_hash(intent, "uberx", now=T0)

    # Prepare a draft that will produce the right intent_hash when confirmed.
    draft = await _prepare_draft(sim, store, clock, intent)

    # First confirm — succeeds normally (this sets intent_hash correctly).
    channel_first = ScriptedConfirmationChannel(
        script=[{"match": "UberX", "action": "approve"}],
        clock=clock,
    )
    session_id = new_session_id()
    r1 = await confirm_booking(
        ConfirmArgs(draft_id=draft.draft_id),
        provider=sim,
        store=store,
        log=log,
        channel=channel_first,
        clock=clock,
        session_id=session_id,
        turn_id=new_turn_id(),
    )
    assert r1["status"] == "confirmed"
    booking_id = r1["booking_id"]

    # Simulate the crash: NULL out the intent_hash on the booking row so it looks
    # like set_booking_intent_hash was never called after finalize_idempotency.
    import sqlite3

    with sqlite3.connect(str(store._db_path)) as conn:
        conn.execute("UPDATE bookings SET intent_hash = NULL WHERE booking_id = ?", (booking_id,))

    # Verify the setup: intent_hash IS NULL now, so find_active_booking_by_intent_hash
    # returns None (the booking is invisible to the dedup lookup).
    assert (
        store.find_active_booking_by_intent_hash(ih) is None
    ), "Test precondition: booking must NOT be found before the heal"

    # Now call confirm_booking again with the same intent (new draft, same key derivation).
    # generation=0 still (no terminal bookings with this intent_hash visible).
    # claim_or_recall returns "recalled" because the idempotency key is already finalized.
    draft2 = await _prepare_draft(sim, store, clock, intent)
    channel_second = ScriptedConfirmationChannel(script=[], clock=clock)  # gate must not fire
    r2 = await confirm_booking(
        ConfirmArgs(draft_id=draft2.draft_id),
        provider=sim,
        store=store,
        log=log,
        channel=channel_second,
        clock=clock,
        session_id=session_id,
        turn_id=new_turn_id(),
    )
    assert r2["status"] == "confirmed"
    assert r2["booking_id"] == booking_id

    # THE KEY ASSERTION: after the recall, intent_hash must be healed on the booking row.
    healed = store.find_active_booking_by_intent_hash(ih)
    assert healed is not None, "intent_hash must be healed on the recall path"
    assert healed.booking_id == booking_id
