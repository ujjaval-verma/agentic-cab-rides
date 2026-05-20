from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.adapters.uber_sim.provider import UberSimAdapter
from agentic_cab.tools.discover import DiscoverArgs, discover_options


@pytest.mark.asyncio
async def test_discover_emits_requested_and_outcome(tmp_path: Path) -> None:
    store = SqliteStateStore(db_path=tmp_path / "s.db")
    store.apply_schema()
    log = JSONLActionLog(log_dir=tmp_path / "logs")
    clock = FrozenClock(start=datetime(2026, 5, 19, 12, 0, tzinfo=UTC))
    sim = UberSimAdapter(store=store, clock=clock, rng_seed=1)
    args = DiscoverArgs(
        pickup_address="A",
        pickup_lat=37.7749,
        pickup_lng=-122.4194,
        dropoff_address="B",
        dropoff_lat=37.7849,
        dropoff_lng=-122.4094,
    )
    result = await discover_options(
        args,
        provider=sim,
        log=log,
        clock=clock,
        session_id="S",
        turn_id="T",
    )
    assert "options" in result
    assert len(result["options"]) == 4  # uberx, comfort, uberxl, black (P3 multi-class)
    rows = log.read("S")
    kinds = [r["kind"] for r in rows]
    assert kinds == ["requested", "outcome"]
