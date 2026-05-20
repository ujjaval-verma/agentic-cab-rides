"""Loads a scenario YAML, wires deterministic adapters, runs the agent, writes transcript."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from anthropic import Anthropic

from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.confirmation.scripted import ScriptedConfirmationChannel
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.adapters.uber_sim.provider import UberSimAdapter
from agentic_cab.agent.loop import ToolHandler, run_turn
from agentic_cab.core.ids import new_session_id, new_turn_id
from agentic_cab.tools.confirm import ConfirmArgs, confirm_booking
from agentic_cab.tools.discover import DiscoverArgs, discover_options
from agentic_cab.tools.prepare import PrepareArgs, prepare_booking
from agentic_cab.tools.registry import pydantic_to_anthropic_tool


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text())  # type: ignore[no-any-return]


async def run_scenario(
    scenario_path: Path,
    data_dir: Path,
    log_dir: Path,
) -> str:
    """Run a scenario end-to-end. Returns session_id."""
    scn = _load(scenario_path)
    session_id = new_session_id()
    clock = FrozenClock(start=datetime.fromisoformat(scn["clock"]["start"]))
    store = SqliteStateStore(db_path=data_dir / f"{scn['name']}.db")
    store.apply_schema()
    log = JSONLActionLog(log_dir=log_dir)
    sim = UberSimAdapter(store=store, clock=clock, rng_seed=scn["sim"]["rng_seed"])
    channel = ScriptedConfirmationChannel(script=scn["confirmation"]["script"], clock=clock)

    tools = [
        pydantic_to_anthropic_tool(
            name="discover_options",
            description="Find available ride options for a pickup→dropoff intent.",
            args_model=DiscoverArgs,
        ),
        pydantic_to_anthropic_tool(
            name="prepare_booking",
            description="Create a booking draft from an option_id.",
            args_model=PrepareArgs,
        ),
        pydantic_to_anthropic_tool(
            name="confirm_booking",
            description="Confirm a draft (gated — user will be asked to approve).",
            args_model=ConfirmArgs,
        ),
    ]
    turn_id = new_turn_id()
    common: dict[str, Any] = dict(
        provider=sim,
        log=log,
        clock=clock,
        session_id=session_id,
        turn_id=turn_id,
    )

    async def _discover(a: dict[str, Any]) -> dict[str, Any]:
        return await discover_options(DiscoverArgs(**a), **common)

    async def _prepare(a: dict[str, Any]) -> dict[str, Any]:
        return await prepare_booking(PrepareArgs(**a), **common)

    async def _confirm(a: dict[str, Any]) -> dict[str, Any]:
        return await confirm_booking(
            ConfirmArgs(**a),
            store=store,
            channel=channel,
            **common,
        )

    handlers: dict[str, ToolHandler] = {
        "discover_options": _discover,
        "prepare_booking": _prepare,
        "confirm_booking": _confirm,
    }
    client = Anthropic()
    history: list[dict[str, Any]] = []
    for user_msg in scn["user_turns"]:
        _text, history = await run_turn(
            client,
            user_message=user_msg,
            history=history,
            tools=tools,
            handlers=handlers,
        )

    # Persist chat for transcript renderer (one JSON object per line)
    chat_path = log_dir / f"{session_id}.chat.jsonl"
    chat_path.write_text("\n".join(json.dumps(m, default=str) for m in history) + "\n")
    return session_id
