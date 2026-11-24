"""Unit test: cancel_booking releases in_progress claim on user-decline.

Invariant 5 — a declined cancel must not permanently block future retries
with the same (booking_id, fee_cents, status_at_quote) key.
Symmetric to confirm_booking's release on decline (see tools/confirm.py).

cancel_booking, like confirm_booking, guards concurrent in-flight claims with
an "in_progress" short-circuit. Without release_claim on decline, the declined
call leaves an unfinalized row, and the retry hits that guard forever.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.confirmation.scripted import ScriptedConfirmationChannel
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.adapters.uber_sim.provider import UberSimAdapter
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
async def test_declined_cancel_does_not_block_retry(harness, meter) -> None:
    """Decline → retry with approve → retry must succeed (claim was released on decline).

    Without release_claim on the decline path, the declined call leaves an unfinalized
    row in idempotency_keys. The retry hits claim_or_recall, gets "in_progress", and is
    blocked permanently — the declined cancel becomes a poison pill.
    """
    store, log, clock, sim = harness
    booking_id = await _book(sim, store, log, clock, meter)

    # Use a fixed key_factory so both calls derive the same idempotency key,
    # replicating the real scenario where key = f(booking_id, fee_cents, status_at_quote)
    # and nothing has changed between decline and retry.
    def fixed_key(bid: str, fee_cents: int, status_at_quote: str) -> str:
        return "K-CANCEL-DECLINE-RETRY"

    # First call: user declines.
    decline_ch = ScriptedConfirmationChannel(
        script=[{"match": "Cancel", "action": "decline"}], clock=clock
    )
    r1 = await cancel_booking(
        CancelArgs(booking_id=booking_id),
        provider=sim,
        store=store,
        log=log,
        channel=decline_ch,
        clock=clock,
        meter=meter,
        session_id="S",
        turn_id="T1",
        key_factory=fixed_key,
    )
    assert r1["status"] == "rejected", f"Expected rejected, got: {r1}"  # noqa: S101 pytest assertion

    # Second call: user approves. Must succeed — the declined claim must have been released.
    approve_ch = ScriptedConfirmationChannel(
        script=[{"match": "Cancel", "action": "approve"}], clock=clock
    )
    r2 = await cancel_booking(
        CancelArgs(booking_id=booking_id),
        provider=sim,
        store=store,
        log=log,
        channel=approve_ch,
        clock=clock,
        meter=meter,
        session_id="S",
        turn_id="T2",
        key_factory=fixed_key,
    )
    assert r2["status"] == "cancelled", (  # noqa: S101 pytest assertion
        f"Retry after declined cancel must succeed, got: {r2}. "
        "The in_progress claim was not released on decline."
    )


@pytest.mark.asyncio
async def test_declined_cancel_leaves_no_unfinalized_claim(harness, meter) -> None:
    """After a user decline, claim_or_recall must return 'new' (not 'in_progress').

    This directly asserts that release_claim cleaned up the unfinalized row.
    Without the fix, claim_or_recall would return 'in_progress' after a decline.
    """
    store, log, clock, sim = harness
    booking_id = await _book(sim, store, log, clock, meter)

    def fixed_key(bid: str, fee_cents: int, status_at_quote: str) -> str:
        return "K-CANCEL-CLAIM-CHECK"

    # Decline the cancel.
    decline_ch = ScriptedConfirmationChannel(
        script=[{"match": "Cancel", "action": "decline"}], clock=clock
    )
    r1 = await cancel_booking(
        CancelArgs(booking_id=booking_id),
        provider=sim,
        store=store,
        log=log,
        channel=decline_ch,
        clock=clock,
        meter=meter,
        session_id="S",
        turn_id="T1",
        key_factory=fixed_key,
    )
    assert r1["status"] == "rejected"  # noqa: S101 pytest assertion

    # After decline, the claim must be gone — a fresh claim_or_recall returns "new".
    claim = store.claim_or_recall("K-CANCEL-CLAIM-CHECK", scope="cancel")
    assert claim.status == "new", (  # noqa: S101 pytest assertion
        f"Expected 'new' after decline (release_claim should have cleaned up), "
        f"got: {claim.status!r}"
    )
    # Clean up the new claim we just inserted (test bookkeeping).
    store.release_claim("K-CANCEL-CLAIM-CHECK", scope="cancel")
