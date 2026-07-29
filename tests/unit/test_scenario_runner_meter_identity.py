"""Pin the one-meter-per-session invariant at the scenario-runner wiring site.

`scenarios/runner.py::run_scenario` is the third of "the three wiring sites" named
in the P8 cost-ledger brief (agent/chat.py:run_repl and web/app.py's POST /chat are
the other two, already covered by test_chat_repl.py and test_web_app_routes.py).
Production wiring constructs a single `meter` local and passes the SAME instance to
`run_turn` and to the `confirm_booking`/`cancel_booking` closures — correct by
inspection, but nothing previously caught a future edit that constructs a second
meter, which would silently mean the gated tools drain a buffer `run_turn` never
filled.

No real LLM call: the Anthropic client is stubbed to call `confirm_booking` and then
`cancel_booking` via scripted tool_use responses, then return a final text response.
The stubbed ids do not exist in the (empty) store, so both tools legitimately reject
before the gate — that's fine, this test only cares about which `meter` instance each
spied call received, not the booking outcome.

Both gated tools are spied: the runner has two wiring sites, and a second meter at
either one is the same silent data loss.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

from agentic_cab.scenarios.runner import run_scenario

_SCENARIO: dict[str, Any] = {
    "name": "unit_meter_identity",
    "clock": {"start": "2026-05-19T12:00:00+00:00", "mode": "frozen"},
    "sim": {"rng_seed": 42},
    "confirmation": {"channel": "scripted", "script": []},
    "user_turns": ["Confirm draft d1 please."],
}


def _stub_usage() -> MagicMock:
    usage = MagicMock()
    usage.input_tokens = 100
    usage.output_tokens = 20
    usage.cache_read_input_tokens = 0
    usage.cache_creation_input_tokens = 0
    return usage


def _tool_use_response(tool_name: str, tool_input: dict[str, Any]) -> MagicMock:
    tool_use_block = MagicMock()
    tool_use_block.type = "tool_use"
    tool_use_block.name = tool_name
    tool_use_block.id = f"tu_{tool_name}"
    tool_use_block.input = tool_input

    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [tool_use_block]
    response.usage = _stub_usage()
    return response


def _end_turn_response(text: str = "Done.") -> MagicMock:
    content_block = MagicMock()
    content_block.type = "text"
    content_block.text = text

    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [content_block]
    response.usage = _stub_usage()
    return response


@pytest.mark.asyncio
async def test_run_scenario_uses_one_meter_instance_for_run_turn_and_both_gated_tools(
    tmp_path: Path,
) -> None:
    """run_scenario must construct exactly one CostMeter and pass the SAME instance
    to run_turn, to confirm_booking, and — symmetrically — to cancel_booking.

    Regression: a second meter at either gated site means that tool would drain a
    buffer run_turn never filled into — the exact silent cost-ledger data loss the
    brief names as the failure mode for "two meters in one session".
    """
    scenario_path = tmp_path / "unit_meter_identity.yaml"
    scenario_path.write_text(yaml.safe_dump(_SCENARIO))

    stub_client = MagicMock()
    stub_client.messages.create.side_effect = [
        _tool_use_response("confirm_booking", {"draft_id": "d1"}),
        _tool_use_response("cancel_booking", {"booking_id": "b1"}),
        _end_turn_response(),
    ]

    import agentic_cab.scenarios.runner as runner_mod

    captured: dict[str, Any] = {}
    real_run_turn = runner_mod.run_turn
    real_confirm_booking = runner_mod.confirm_booking
    real_cancel_booking = runner_mod.cancel_booking

    async def _spy_run_turn(client: Any, **kwargs: Any) -> Any:
        captured["run_turn_meter"] = kwargs["meter"]
        return await real_run_turn(client, **kwargs)

    async def _spy_confirm_booking(*args: Any, **kwargs: Any) -> Any:
        captured["confirm_booking_meter"] = kwargs["meter"]
        return await real_confirm_booking(*args, **kwargs)

    async def _spy_cancel_booking(*args: Any, **kwargs: Any) -> Any:
        captured["cancel_booking_meter"] = kwargs["meter"]
        return await real_cancel_booking(*args, **kwargs)

    with (
        patch("agentic_cab.scenarios.runner.Anthropic", return_value=stub_client),
        patch("agentic_cab.scenarios.runner.run_turn", side_effect=_spy_run_turn),
        patch("agentic_cab.scenarios.runner.confirm_booking", side_effect=_spy_confirm_booking),
        patch("agentic_cab.scenarios.runner.cancel_booking", side_effect=_spy_cancel_booking),
    ):
        await run_scenario(
            scenario_path,
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )

    assert "run_turn_meter" in captured, "run_turn was not called"
    assert "confirm_booking_meter" in captured, "confirm_booking handler was not reached"
    assert "cancel_booking_meter" in captured, "cancel_booking handler was not reached"
    assert (
        captured["run_turn_meter"] is captured["confirm_booking_meter"]
    ), "run_turn and the confirm_booking handler must share the SAME CostMeter instance"
    assert (
        captured["run_turn_meter"] is captured["cancel_booking_meter"]
    ), "run_turn and the cancel_booking handler must share the SAME CostMeter instance"
