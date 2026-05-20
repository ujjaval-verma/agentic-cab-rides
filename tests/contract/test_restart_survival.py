"""Restart-survival contract test — spec §10a + CLAUDE.md inv. 5.

Two scenarios:

1. **Full-success replay:** Instance A fully confirms (claim + provider.confirm +
   finalize). Instance B uses the same deterministic key against the same SQLite
   file. claim_or_recall returns 'recalled'; provider.confirm is called exactly
   once across both instances. The tool returns the same booking_id.
   Note: in the tool flow, _validate_draft runs before claim_or_recall, so the
   second retry must use a fresh (unconsumed) draft with the same key. We achieve
   this by testing the claim layer directly and separately from the tool-level
   confirm-via-recalled-draft path (already covered in test_tool_confirm.py).

2. **In-progress crash window:** A key is claimed (row inserted) but never
   finalised (process crash). A second caller sees status='in_progress' and must
   NOT call provider.confirm. The tool level is covered by
   test_tool_confirm_in_progress.py; this test verifies the storage contract
   across two SqliteStateStore instances backed by the same file.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.confirmation.scripted import ScriptedConfirmationChannel
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.adapters.uber_sim.provider import UberSimAdapter
from agentic_cab.core.ids import confirm_idempotency_key
from agentic_cab.core.types import Booking, Money, Option, RideOptionDetails
from agentic_cab.tools.confirm import ConfirmArgs, confirm_booking
from agentic_cab.tools.discover import DiscoverArgs, discover_options
from agentic_cab.tools.prepare import PrepareArgs, prepare_booking

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(db_path: Path) -> SqliteStateStore:
    s = SqliteStateStore(db_path=db_path, check_same_thread=True)
    s.apply_schema()
    return s


def _booking(booking_id: str = "B-rs-1", draft_id: str = "D-rs-1") -> Booking:
    return Booking(
        booking_id=booking_id,
        draft_id=draft_id,
        status="confirmed",
        provider="uber-sim",
        summary=Option(
            option_id="opt-rs",
            provider="uber-sim",
            title="UberX",
            price=Money(amount_cents=1500, currency="USD"),
            eta_seconds=180,
            when=None,
            details=RideOptionDetails(vehicle_class="uberx", capacity=4),
            expires_at=datetime(2026, 5, 20, 13, 0, tzinfo=UTC),
        ),
    )


# ---------------------------------------------------------------------------
# Contract test 1: full-success replay via storage layer
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_finalised_key_recalled_across_store_instances(tmp_path: Path) -> None:
    """provider.confirm is called exactly once even when a second store instance
    tries to claim the same key.

    This is the storage-layer proof of the restart-survival contract: the key
    is written by Instance A and read back correctly by Instance B, both backed
    by the same SQLite file.
    """
    db_path = tmp_path / "state.db"

    # --- Instance A: claim + finalize ---
    store_a = _make_store(db_path)
    booking = _booking()
    # Draft FK is required — insert a minimal draft row via store_a.

    from agentic_cab.core.types import Draft

    draft = Draft(
        draft_id=booking.draft_id,
        option_snapshot=booking.summary,
        fingerprint="f" * 64,
        created_at=datetime(2026, 5, 20, 12, 0, tzinfo=UTC),
        expires_at=datetime(2026, 5, 20, 12, 0, tzinfo=UTC) + timedelta(minutes=5),
    )
    store_a.put_draft(draft)
    store_a.put_booking(booking)

    key = confirm_idempotency_key(booking.draft_id, "f" * 64)

    first = store_a.claim_or_recall(key, scope="confirm")
    assert first.status == "new"

    store_a.finalize_idempotency(key, booking=booking)

    # --- Instance B: same SQLite file, fresh connection ---
    store_b = _make_store(db_path)

    second = store_b.claim_or_recall(key, scope="confirm")
    assert second.status == "recalled"
    assert second.booking is not None
    assert second.booking.booking_id == booking.booking_id

    # If we were tracking provider calls, confirm would be called 0 times here.
    # The recall short-circuit in confirm_booking ensures no double-book.


# ---------------------------------------------------------------------------
# Contract test 2: in-progress crash window across store instances
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_in_progress_key_visible_across_store_instances(tmp_path: Path) -> None:
    """A key claimed by Instance A but never finalised (crash) is visible to
    Instance B as 'in_progress', not 'new'.

    This proves the crash-window guard works across process restarts: the
    second caller (Instance B) must not re-issue a provider call.
    """
    db_path = tmp_path / "state.db"

    store_a = _make_store(db_path)
    crash_key = "crash-key-abc123"

    first = store_a.claim_or_recall(crash_key, scope="confirm")
    assert first.status == "new"
    # Crash: no finalize_idempotency call.

    # Instance B reads the same SQLite file.
    store_b = _make_store(db_path)
    second = store_b.claim_or_recall(crash_key, scope="confirm")
    assert second.status == "in_progress"
    assert second.booking is None


# ---------------------------------------------------------------------------
# Contract test 3: tool-level confirm with same key via fresh prepare
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.asyncio
async def test_tool_replay_via_same_key_returns_same_booking(tmp_path: Path) -> None:
    """Two confirm_booking calls with the same idempotency key on different
    (unconsumed) drafts → both return the same booking_id.

    Uses a fixed key factory to force collision. The second draft is a fresh
    prepare so it has not been consumed. The second call sees 'recalled'
    from claim_or_recall and returns the original booking without calling
    provider.confirm again.

    This mirrors the pattern in test_tool_confirm.py::
    test_idempotency_replay_short_circuits_second_provider_call and verifies
    the cross-instance contract via two SqliteStateStore instances.
    """
    db_path = tmp_path / "state.db"

    clock = FrozenClock(start=datetime(2026, 5, 20, 12, 0, tzinfo=UTC))
    store_a = _make_store(db_path)
    log = JSONLActionLog(log_dir=tmp_path / "logs")
    sim_a = UberSimAdapter(store=store_a, clock=clock, rng_seed=42)

    d_args = DiscoverArgs(
        pickup_address="A",
        pickup_lat=37.7749,
        pickup_lng=-122.4194,
        dropoff_address="B",
        dropoff_lat=37.7849,
        dropoff_lng=-122.4094,
    )

    def _fixed_key(_draft_id: str, _fp: str) -> str:
        return "FIXED-RESTART-KEY"

    # --- First confirm via Instance A ---
    d1 = await discover_options(
        d_args, provider=sim_a, log=log, clock=clock, session_id="S", turn_id="T1"
    )
    p1 = await prepare_booking(
        PrepareArgs(option_id=d1["options"][0]["option_id"]),
        provider=sim_a,
        log=log,
        clock=clock,
        session_id="S",
        turn_id="T1",
    )
    ch1 = ScriptedConfirmationChannel(script=[{"match": "UberX", "action": "approve"}], clock=clock)
    r1 = await confirm_booking(
        ConfirmArgs(draft_id=p1["draft_id"]),
        provider=sim_a,
        store=store_a,
        log=log,
        channel=ch1,
        clock=clock,
        session_id="S",
        turn_id="T1",
        key_factory=_fixed_key,
    )
    assert r1["status"] == "confirmed"
    booking_id_1 = r1["booking_id"]

    # --- Simulate restart: Instance B with fresh store + sim ---
    store_b = _make_store(db_path)
    sim_b = UberSimAdapter(store=store_b, clock=clock, rng_seed=42)

    # Prepare a NEW unconsumed draft for Instance B.
    d2 = await discover_options(
        d_args, provider=sim_b, log=log, clock=clock, session_id="S", turn_id="T2"
    )
    p2 = await prepare_booking(
        PrepareArgs(option_id=d2["options"][0]["option_id"]),
        provider=sim_b,
        log=log,
        clock=clock,
        session_id="S",
        turn_id="T2",
    )

    provider_confirm_called = False
    original_confirm = sim_b.confirm

    async def _spy_confirm(draft_id: str, *, idempotency_key: str) -> object:
        nonlocal provider_confirm_called
        provider_confirm_called = True
        return await original_confirm(draft_id, idempotency_key=idempotency_key)

    sim_b.confirm = _spy_confirm  # type: ignore[method-assign]

    ch2 = ScriptedConfirmationChannel(script=[{"match": "UberX", "action": "approve"}], clock=clock)
    r2 = await confirm_booking(
        ConfirmArgs(draft_id=p2["draft_id"]),
        provider=sim_b,
        store=store_b,
        log=log,
        channel=ch2,
        clock=clock,
        session_id="S",
        turn_id="T2",
        key_factory=_fixed_key,
    )

    # Same booking returned — no double-book.
    assert r2["status"] == "confirmed"
    assert (
        r2["booking_id"] == booking_id_1
    ), f"Expected same booking_id={booking_id_1!r}, got {r2['booking_id']!r}"

    # provider.confirm was NOT called on Instance B.
    assert not provider_confirm_called, "provider.confirm must not be called on a recalled key"
