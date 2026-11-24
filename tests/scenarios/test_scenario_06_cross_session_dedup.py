"""End-to-end coverage for scenarios/06_cross_session_dedup.yaml.

Drives the scenario with a live LLM call. Asserts:
1. All YAML invariants pass via evaluate().
2. Hard sanity: exactly two non-replayed executed rows for confirm_booking.
3. Hard sanity: exactly one outcome row with status="duplicate_intent".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agentic_cab.scenarios.invariants import evaluate
from agentic_cab.scenarios.runner import run_scenario

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCENARIO_PATH = _REPO_ROOT / "scenarios" / "06_cross_session_dedup.yaml"


@pytest.mark.scenario
@pytest.mark.asyncio
async def test_06_cross_session_dedup(tmp_path: Path) -> None:
    session_id = await run_scenario(
        _SCENARIO_PATH,
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
    )

    log_path = tmp_path / "logs" / f"{session_id}.jsonl"
    rows = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]

    spec = yaml.safe_load(_SCENARIO_PATH.read_text())
    results = evaluate(rows, spec["invariants"])
    failures = [(kind, params) for kind, passed, params in results if not passed]
    assert not failures, f"invariant failures: {failures}"

    # Hard sanity: exactly two non-replayed executed rows for confirm_booking.
    confirm_executes = [
        r
        for r in rows
        if r.get("kind") == "executed"
        and r.get("tool") == "confirm_booking"
        and not r.get("replayed", False)
    ]
    assert len(confirm_executes) == 2, (
        f"expected 2 non-replayed confirm_booking executed rows, got {len(confirm_executes)}"
    )

    # Hard sanity: exactly one outcome row with status="duplicate_intent".
    dups = [
        r
        for r in rows
        if r.get("kind") == "outcome"
        and r.get("tool") == "confirm_booking"
        and r.get("status") == "duplicate_intent"
    ]
    assert len(dups) == 1, f"expected exactly 1 duplicate_intent outcome, got {len(dups)}"
