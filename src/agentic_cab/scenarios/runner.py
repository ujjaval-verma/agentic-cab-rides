"""Loads a scenario YAML, wires deterministic adapters, runs the agent, writes transcript."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from anthropic import Anthropic

from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.confirmation.scripted import ScriptedConfirmationChannel
from agentic_cab.adapters.geocoding.cache import GeocodeCache
from agentic_cab.adapters.geocoding.nominatim import NominatimGeocoder
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.adapters.uber_sim.provider import UberSimAdapter
from agentic_cab.adapters.uber_sim.state_machine import DriverAvailability
from agentic_cab.adapters.uber_sim.surge import SurgeCurve
from agentic_cab.agent.loop import ToolHandler, run_turn
from agentic_cab.core.ids import new_session_id, new_turn_id
from agentic_cab.tools.cancel import CancelArgs, cancel_booking
from agentic_cab.tools.confirm import ConfirmArgs, confirm_booking
from agentic_cab.tools.discover import DiscoverArgs, discover_options
from agentic_cab.tools.geocode import DESCRIPTION as GEOCODE_DESCRIPTION
from agentic_cab.tools.geocode import GeocodeArgs, geocode_address
from agentic_cab.tools.list_active import DESCRIPTION as LIST_ACTIVE_DESCRIPTION
from agentic_cab.tools.list_active import ListActiveArgs, list_active_bookings
from agentic_cab.tools.prepare import PrepareArgs, prepare_booking
from agentic_cab.tools.registry import pydantic_to_anthropic_tool
from agentic_cab.tools.track import DESCRIPTION as TRACK_DESCRIPTION
from agentic_cab.tools.track import TrackArgs, track_booking


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text())  # type: ignore[no-any-return]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_GEOCODE_FIXTURES = _REPO_ROOT / "fixtures" / "geocode"


async def run_scenario(
    scenario_path: Path,
    data_dir: Path,
    log_dir: Path,
    geocode_fixtures_dir: Path | None = None,
) -> str:
    """Run a scenario end-to-end. Returns session_id."""
    scn = _load(scenario_path)
    session_id = new_session_id()
    clock = FrozenClock(start=datetime.fromisoformat(scn["clock"]["start"]))
    db_path = data_dir / f"{scn['name']}.db"
    # Each render must be fully reproducible from a clean slate. Delete any
    # leftover DB from a previous run so state does not bleed across renders.
    if db_path.exists():
        db_path.unlink()
    store = SqliteStateStore(db_path=db_path)
    store.apply_schema()
    log = JSONLActionLog(log_dir=log_dir)

    # Optional sim config: surge_curve and availability (T28)
    sim_cfg = scn.get("sim", {})
    surge_curve: SurgeCurve | None = None
    if "surge_curve" in sim_cfg:
        surge_curve = SurgeCurve.model_validate(sim_cfg["surge_curve"])
    availability: DriverAvailability | None = None
    if "availability" in sim_cfg:
        availability = DriverAvailability.model_validate(sim_cfg["availability"])

    sim = UberSimAdapter(
        store=store,
        clock=clock,
        rng_seed=sim_cfg.get("rng_seed", 0),
        surge_curve=surge_curve,
        availability=availability,
    )
    channel = ScriptedConfirmationChannel(script=scn["confirmation"]["script"], clock=clock)
    _geocode_fixtures_dir = (
        geocode_fixtures_dir if geocode_fixtures_dir is not None else _DEFAULT_GEOCODE_FIXTURES
    )
    geocode_cache = GeocodeCache(fixtures_dir=_geocode_fixtures_dir)
    http_client = httpx.AsyncClient()
    geocoder = NominatimGeocoder(cache=geocode_cache, http=http_client)

    tools = [
        pydantic_to_anthropic_tool(
            name="geocode_address",
            description=GEOCODE_DESCRIPTION,
            args_model=GeocodeArgs,
        ),
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
        pydantic_to_anthropic_tool(
            name="cancel_booking",
            description=(
                "Cancel an existing booking_id. Gated — user will be asked to approve"
                " the cancellation fee. The fee depends on current ride status."
            ),
            args_model=CancelArgs,
        ),
        pydantic_to_anthropic_tool(
            name="track_booking",
            description=TRACK_DESCRIPTION,
            args_model=TrackArgs,
        ),
        pydantic_to_anthropic_tool(
            name="list_active_bookings",
            description=LIST_ACTIVE_DESCRIPTION,
            args_model=ListActiveArgs,
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

    async def _geocode(a: dict[str, Any]) -> dict[str, Any]:
        return await geocode_address(
            GeocodeArgs(**a),
            geocoder=geocoder,
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

    async def _cancel(a: dict[str, Any]) -> dict[str, Any]:
        return await cancel_booking(
            CancelArgs(**a),
            store=store,
            channel=channel,
            **common,
        )

    async def _track(a: dict[str, Any]) -> dict[str, Any]:
        return await track_booking(
            TrackArgs(**a),
            provider=sim,
            log=log,
            clock=clock,
            session_id=session_id,
            turn_id=turn_id,
        )

    async def _list_active(a: dict[str, Any]) -> dict[str, Any]:
        return await list_active_bookings(
            ListActiveArgs(**a),
            store=store,
            log=log,
            clock=clock,
            session_id=session_id,
            turn_id=turn_id,
        )

    handlers: dict[str, ToolHandler] = {
        "geocode_address": _geocode,
        "discover_options": _discover,
        "prepare_booking": _prepare,
        "confirm_booking": _confirm,
        "cancel_booking": _cancel,
        "track_booking": _track,
        "list_active_bookings": _list_active,
    }
    client = Anthropic()
    history: list[dict[str, Any]] = []
    try:
        for user_msg in scn["user_turns"]:
            _text, history = await run_turn(
                client,
                user_message=user_msg,
                history=history,
                tools=tools,
                handlers=handlers,
            )
    finally:
        await http_client.aclose()

    # Persist chat for transcript renderer (one JSON object per line)
    chat_path = log_dir / f"{session_id}.chat.jsonl"
    chat_path.write_text("\n".join(json.dumps(m, default=str) for m in history) + "\n")
    return session_id
