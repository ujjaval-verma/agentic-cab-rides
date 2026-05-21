"""discover_options args must not leak raw pickup_address/dropoff_address (invariant 8)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic_cab.adapters.action_log import JSONLActionLog, redact
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.adapters.uber_sim.provider import UberSimAdapter
from agentic_cab.tools.discover import DiscoverArgs, discover_options

SECRET_PICKUP = "742 Evergreen Terrace, Springfield"
SECRET_DROPOFF = "31 Spooner Street, Quahog"


def test_redact_strips_pickup_address_and_dropoff_address_strings() -> None:
    raw = {
        "args": {
            "pickup_address": SECRET_PICKUP,
            "dropoff_address": SECRET_DROPOFF,
            "pickup_lat": 1.0,
            "pickup_lng": 2.0,
            "dropoff_lat": 3.0,
            "dropoff_lng": 4.0,
            "party_size": 1,
        }
    }
    out = redact(raw)
    serialized = json.dumps(out)
    assert SECRET_PICKUP not in serialized, "pickup_address leaked"
    assert SECRET_DROPOFF not in serialized, "dropoff_address leaked"
    # lat/lng are not PII — they remain.
    assert out["args"]["pickup_lat"] == 1.0
    assert out["args"]["dropoff_lat"] == 3.0


@pytest.mark.asyncio
async def test_discover_options_log_does_not_contain_raw_addresses(
    tmp_path: Path,
) -> None:
    store = SqliteStateStore(db_path=tmp_path / "s.db")
    store.apply_schema()
    log = JSONLActionLog(log_dir=tmp_path / "logs")
    clock = FrozenClock(start=datetime(2026, 5, 19, 12, 0, tzinfo=UTC))
    sim = UberSimAdapter(store=store, clock=clock, rng_seed=1)
    args = DiscoverArgs(
        pickup_address=SECRET_PICKUP,
        pickup_lat=37.7749,
        pickup_lng=-122.4194,
        dropoff_address=SECRET_DROPOFF,
        dropoff_lat=37.7849,
        dropoff_lng=-122.4094,
    )
    await discover_options(
        args,
        provider=sim,
        log=log,
        clock=clock,
        session_id="S",
        turn_id="T",
    )
    log_file = tmp_path / "logs" / "S.jsonl"
    contents = log_file.read_text()
    assert SECRET_PICKUP not in contents, "pickup_address leaked into JSONL log"
    assert SECRET_DROPOFF not in contents, "dropoff_address leaked into JSONL log"
