"""Unit tests for prepare_booking tool. Spec §7."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.adapters.uber_sim.provider import UberSimAdapter
from agentic_cab.tools.discover import DiscoverArgs, discover_options
from agentic_cab.tools.prepare import PrepareArgs, prepare_booking


@pytest.mark.asyncio
async def test_prepare_persists_draft_and_emits_2_rows(tmp_path: Path) -> None:
    store = SqliteStateStore(db_path=tmp_path / "s.db")
    store.apply_schema()
    log = JSONLActionLog(log_dir=tmp_path / "logs")
    clock = FrozenClock(start=datetime(2026, 5, 19, 12, 0, tzinfo=UTC))
    sim = UberSimAdapter(store=store, clock=clock, rng_seed=1)
    # Run discover to populate option cache
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
    option_id = d_result["options"][0]["option_id"]
    # Now prepare
    result = await prepare_booking(
        PrepareArgs(option_id=option_id),
        provider=sim,
        log=log,
        clock=clock,
        session_id="S",
        turn_id="T",
    )
    assert "draft_id" in result
    assert "fingerprint" in result
    assert store.get_draft(result["draft_id"]) is not None
    rows = log.read("S")
    # Last 2 rows are this tool's requested+outcome
    assert [r["kind"] for r in rows[-2:]] == ["requested", "outcome"]
    assert rows[-2]["tool"] == "prepare_booking"
