"""End-to-end join of the P8 cost ledger: what `run_turn` records is what a gated
`outcome` row reports.

Everything else in the P8 suite tests one half. `test_agent_loop_cost.py` asserts on a
fake meter's internal list (producer side); `test_cost_ledger.py` hand-records calls
straight onto an `InMemoryCostMeter` (consumer side). Neither notices a wiring site that
hands the gated tools a *second* meter — the ledger then looks populated and reports
`[]` / `0` forever, silently.

This test runs the whole path with a stubbed Anthropic client of known token usage:
run_turn → meter.record → gated tool → meter.drain → emitted outcome row, and asserts a
hardcoded microcent total (not a re-derivation from the row under test).

No real API call is ever made: `Anthropic` is patched with a scripted stub that reads
the loop's own tool_result history to supply the ids it could not know in advance.

CLAUDE.md invariants 6 (action-log shape), 9 (FrozenClock + seeded RNG), 10 (integer
money).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.scenarios.runner import run_scenario

_SCENARIO: dict[str, Any] = {
    "name": "unit_cost_ledger_e2e",
    "clock": {"start": "2026-05-19T12:00:00+00:00", "mode": "frozen"},
    "sim": {"rng_seed": 1},
    "confirmation": {
        "channel": "scripted",
        "script": [
            {"match": "Confirm", "action": "approve"},
            {"match": "Cancel", "action": "approve"},
        ],
    },
    "user_turns": ["Book me a ride, then cancel it."],
}

# Token counts per model call, in the order the scripted agent makes them. Each step
# has a distinct cost so a drain of the wrong calls cannot coincidentally match.
#
#   cost_microcents = input * 500 + output * 2_500   (claude-opus-4-7, core/constants.py)
#
#   step  tool                input  output   cost
#   0     discover_options      100      10     75_000
#   1     prepare_booking       200      20    150_000
#   2     confirm_booking       300      30    225_000
#   3     cancel_booking        400      40    300_000
#   4     (final text)          500      50    375_000
_USAGE_BY_STEP: list[tuple[int, int]] = [(100, 10), (200, 20), (300, 30), (400, 40), (500, 50)]

# confirm_booking drains steps 0-2 (the calls that preceded its handler).
_EXPECTED_CONFIRM_MICROCENTS = 450_000
# cancel_booking drains step 3 only — the first drain emptied the buffer.
_EXPECTED_CANCEL_MICROCENTS = 300_000


def _usage(step: int) -> MagicMock:
    """A usage stub with real ints — run_turn builds a StrictInt LLMCall from these."""
    input_tokens, output_tokens = _USAGE_BY_STEP[step]
    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens
    usage.cache_read_input_tokens = 0
    usage.cache_creation_input_tokens = 0
    return usage


def _tool_use_response(step: int, tool_name: str, tool_input: dict[str, Any]) -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.id = f"tu_{step:03d}"
    block.input = tool_input

    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [block]
    response.usage = _usage(step)
    return response


def _end_turn_response(step: int) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = "Booked and cancelled."

    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [block]
    response.usage = _usage(step)
    return response


def _last_tool_result(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the most recent tool_result payload the agent loop appended to history.

    The scripted agent cannot hardcode `option_id` / `draft_id` / `booking_id` — they are
    minted at runtime — so it reads them back out of the conversation, exactly as a real
    model would.
    """
    for message in reversed(messages):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                return json.loads(block["content"])  # type: ignore[no-any-return]
    msg = "scripted agent expected a tool_result in history and found none"
    raise AssertionError(msg)


def _scripted_client() -> MagicMock:
    """A stub Anthropic client that books then cancels a ride, in five model calls."""
    step = 0

    def _create(**kwargs: Any) -> MagicMock:
        nonlocal step
        current = step
        step += 1
        messages: list[dict[str, Any]] = kwargs["messages"]

        if current == 0:
            return _tool_use_response(
                current,
                "discover_options",
                {
                    "pickup_address": "A",
                    "pickup_lat": 37.7749,
                    "pickup_lng": -122.4194,
                    "dropoff_address": "B",
                    "dropoff_lat": 37.7849,
                    "dropoff_lng": -122.4094,
                },
            )
        if current == 1:
            options = _last_tool_result(messages)["options"]
            return _tool_use_response(
                current, "prepare_booking", {"option_id": options[0]["option_id"]}
            )
        if current == 2:
            draft_id = _last_tool_result(messages)["draft_id"]
            return _tool_use_response(current, "confirm_booking", {"draft_id": draft_id})
        if current == 3:
            booking_id = _last_tool_result(messages)["booking_id"]
            return _tool_use_response(current, "cancel_booking", {"booking_id": booking_id})
        return _end_turn_response(current)

    client = MagicMock()
    client.messages.create.side_effect = _create
    return client


def _outcome_row(rows: list[dict[str, Any]], tool: str) -> dict[str, Any]:
    matches = [r for r in rows if r["kind"] == "outcome" and r.get("tool") == tool]
    assert len(matches) == 1, f"expected exactly one {tool} outcome row, got {len(matches)}"  # noqa: S101 pytest assertion
    return matches[0]


@pytest.mark.asyncio
async def test_cost_recorded_by_run_turn_lands_on_the_gated_outcome_rows(tmp_path: Path) -> None:
    """A cost the agent loop records must reach the gated tool's emitted outcome row.

    This is the producer→consumer join. If any wiring site hands the gated tools a
    second CostMeter, both totals below collapse to 0 while every other test in the
    suite stays green.
    """
    scenario_path = tmp_path / "unit_cost_ledger_e2e.yaml"
    scenario_path.write_text(yaml.safe_dump(_SCENARIO))

    with patch("agentic_cab.scenarios.runner.Anthropic", return_value=_scripted_client()):
        session_id = await run_scenario(
            scenario_path,
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )

    rows = JSONLActionLog(log_dir=tmp_path / "logs").read(session_id)

    confirm_outcome = _outcome_row(rows, "confirm_booking")
    assert confirm_outcome["status"] == "confirmed"  # noqa: S101 pytest assertion
    assert confirm_outcome["cost_microcents_since_last_action"] == _EXPECTED_CONFIRM_MICROCENTS  # noqa: S101 pytest assertion
    confirm_calls = confirm_outcome["llm_calls_since_last_action"]
    assert len(confirm_calls) == 3  # noqa: S101 pytest assertion
    assert [c["input_tokens"] for c in confirm_calls] == [100, 200, 300]  # noqa: S101 pytest assertion
    assert [c["output_tokens"] for c in confirm_calls] == [10, 20, 30]  # noqa: S101 pytest assertion
    assert all("latency_ms" not in c for c in confirm_calls)  # noqa: S101 pytest assertion

    cancel_outcome = _outcome_row(rows, "cancel_booking")
    assert cancel_outcome["status"] == "cancelled"  # noqa: S101 pytest assertion
    assert cancel_outcome["cost_microcents_since_last_action"] == _EXPECTED_CANCEL_MICROCENTS  # noqa: S101 pytest assertion
    cancel_calls = cancel_outcome["llm_calls_since_last_action"]
    assert len(cancel_calls) == 1  # noqa: S101 pytest assertion
    assert cancel_calls[0]["input_tokens"] == 400  # noqa: S101 pytest assertion
    assert cancel_calls[0]["output_tokens"] == 40  # noqa: S101 pytest assertion
    assert "latency_ms" not in cancel_calls[0]  # noqa: S101 pytest assertion
