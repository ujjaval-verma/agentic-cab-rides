"""Interactive REPL for the agentic cab-booking agent.

Wire: SystemClock, SqliteStateStore, JSONLActionLog, NominatimGeocoder,
CLIConfirmationChannel, UberSimAdapter → seven tools → agent loop.

Invariants: 9 (interactive uses SystemClock + os.urandom), 11 (ULIDs),
14 (Rich console writes are fine here — this IS the CLI rendering layer).
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import structlog
from anthropic import Anthropic
from rich.console import Console
from rich.panel import Panel

from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.clock import SystemClock
from agentic_cab.adapters.confirmation.cli import CLIConfirmationChannel
from agentic_cab.adapters.cost_meter import InMemoryCostMeter
from agentic_cab.adapters.geocoding.cache import GeocodeCache
from agentic_cab.adapters.geocoding.nominatim import NominatimGeocoder
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.adapters.uber_sim.provider import UberSimAdapter
from agentic_cab.agent.loop import ToolHandler, run_turn
from agentic_cab.core.ids import new_session_id, new_turn_id
from agentic_cab.core.ports import (
    ActionLog,
    BookingProvider,
    Clock,
    ConfirmationChannel,
    CostMeter,
    GeocoderPort,
    StateStore,
)
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

_log = structlog.get_logger(__name__)

_TOOL_DEFS = [
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


def build_handlers(
    *,
    sim: BookingProvider,
    store: StateStore,
    log: ActionLog,
    clock: Clock,
    channel: ConfirmationChannel,
    geocoder: GeocoderPort,
    meter: CostMeter,
    session_id: str,
    turn_id_ref: list[str],
) -> dict[str, ToolHandler]:
    """Build the seven tool handlers.

    All parameters are typed as port protocols so any conforming adapter
    (including test fakes like FrozenClock or ScriptedConfirmationChannel)
    can be passed without a pyright strict error.

    turn_id_ref is a mutable one-element list so handlers always use the
    *current* turn_id without rebinding the closure on every turn.
    """

    def _turn_id() -> str:
        return turn_id_ref[0]

    async def _geocode(a: dict[str, Any]) -> dict[str, Any]:
        return await geocode_address(
            GeocodeArgs(**a),
            geocoder=geocoder,
            log=log,
            clock=clock,
            session_id=session_id,
            turn_id=_turn_id(),
        )

    async def _discover(a: dict[str, Any]) -> dict[str, Any]:
        return await discover_options(
            DiscoverArgs(**a),
            provider=sim,
            log=log,
            clock=clock,
            session_id=session_id,
            turn_id=_turn_id(),
        )

    async def _prepare(a: dict[str, Any]) -> dict[str, Any]:
        return await prepare_booking(
            PrepareArgs(**a),
            provider=sim,
            log=log,
            clock=clock,
            session_id=session_id,
            turn_id=_turn_id(),
        )

    async def _confirm(a: dict[str, Any]) -> dict[str, Any]:
        return await confirm_booking(
            ConfirmArgs(**a),
            store=store,
            channel=channel,
            provider=sim,
            log=log,
            clock=clock,
            meter=meter,
            session_id=session_id,
            turn_id=_turn_id(),
        )

    async def _cancel(a: dict[str, Any]) -> dict[str, Any]:
        return await cancel_booking(
            CancelArgs(**a),
            store=store,
            channel=channel,
            provider=sim,
            log=log,
            clock=clock,
            meter=meter,
            session_id=session_id,
            turn_id=_turn_id(),
        )

    async def _track(a: dict[str, Any]) -> dict[str, Any]:
        return await track_booking(
            TrackArgs(**a),
            provider=sim,
            log=log,
            clock=clock,
            session_id=session_id,
            turn_id=_turn_id(),
        )

    async def _list_active(a: dict[str, Any]) -> dict[str, Any]:
        return await list_active_bookings(
            ListActiveArgs(**a),
            store=store,
            log=log,
            clock=clock,
            session_id=session_id,
            turn_id=_turn_id(),
        )

    return {
        "geocode_address": _geocode,
        "discover_options": _discover,
        "prepare_booking": _prepare,
        "confirm_booking": _confirm,
        "cancel_booking": _cancel,
        "track_booking": _track,
        "list_active_bookings": _list_active,
    }


InputSource = Callable[[], str | None]


def _cli_input_source(console: Console) -> InputSource:
    """Return a callable that prompts the user for input, returning None on EOF."""

    def _read() -> str | None:
        try:
            return console.input("[bold green]>>> [/bold green]")
        except EOFError:
            return None

    return _read


async def _repl_loop(
    *,
    client: Anthropic,
    handlers: dict[str, ToolHandler],
    console: Console,
    input_source: InputSource,
    session_id: str,
    turn_id_ref: list[str],
    chat_log_path: Path,
    meter: CostMeter,
    clock: Clock,
) -> None:
    """Core REPL loop — shared between interactive and scripted modes."""
    history: list[dict[str, Any]] = []

    console.print(
        Panel(
            "Agentic Cab Rides — type your request or [bold]/exit[/bold] to leave.",
            title="[cyan]agent[/cyan]",
            border_style="cyan",
        )
    )

    while True:
        user_input = input_source()
        if user_input is None:
            # EOF / Ctrl-D
            console.print("\n[dim]Goodbye.[/dim]")
            break
        user_input = user_input.strip()
        if not user_input:
            continue
        if user_input in {"/exit", "/quit"}:
            console.print("[dim]Goodbye.[/dim]")
            break

        # New turn — refresh turn_id in the shared ref before handlers run.
        turn_id_ref[0] = new_turn_id()
        _log.info("repl.turn", session_id=session_id, turn_id=turn_id_ref[0])

        try:
            text, history = await run_turn(
                client,
                user_message=user_input,
                history=history,
                tools=_TOOL_DEFS,
                handlers=handlers,
                meter=meter,
                clock=clock,
            )
        except Exception as exc:
            _log.exception("repl.turn.error", exc=exc)
            console.print(f"[red]Error:[/red] {exc}")
            continue

        console.print(Panel(text, title="[cyan]agent[/cyan]", border_style="cyan"))

        # Persist chat history after each turn so a crash mid-session
        # preserves everything up to the last completed turn.
        chat_log_path.write_text("\n".join(json.dumps(m, default=str) for m in history) + "\n")


async def run_repl(
    *,
    data_dir: Path | None = None,
    log_dir: Path | None = None,
    geocode_fixtures_dir: Path | None = None,
    console: Console | None = None,
    input_source: InputSource | None = None,
    channel: ConfirmationChannel | None = None,
) -> None:
    """Entry point for the interactive REPL.

    All parameters are dependency-injectable for testing. When omitted, production
    defaults are used (SystemClock, /dev/tty, real filesystem paths).
    """
    _data_dir = data_dir or Path("data")
    _log_dir = log_dir or Path("logs")
    _geocode_fixtures_dir = geocode_fixtures_dir or Path("fixtures/geocode")

    _data_dir.mkdir(parents=True, exist_ok=True)
    _log_dir.mkdir(parents=True, exist_ok=True)

    session_id = new_session_id()
    clock = SystemClock()

    store = SqliteStateStore(db_path=_data_dir / "state.db")
    store.apply_schema()

    action_log = JSONLActionLog(log_dir=_log_dir)

    rng_seed = int.from_bytes(os.urandom(4), "big")
    sim = UberSimAdapter(store=store, clock=clock, rng_seed=rng_seed)

    _channel = channel or CLIConfirmationChannel(clock=clock)

    geocode_cache = GeocodeCache(fixtures_dir=_geocode_fixtures_dir)
    geocoder = NominatimGeocoder(
        cache=geocode_cache,
        http=httpx.AsyncClient(timeout=10),
    )

    _console = console or Console()
    _input_source = input_source or _cli_input_source(_console)

    turn_id_ref: list[str] = [new_turn_id()]

    # One CostMeter per session, shared between the agent loop (which fills it)
    # and the gated tools (which will drain it in a later slice — P8).
    meter = InMemoryCostMeter()

    handlers = build_handlers(
        sim=sim,
        store=store,
        log=action_log,
        clock=clock,
        channel=_channel,
        geocoder=geocoder,
        meter=meter,
        session_id=session_id,
        turn_id_ref=turn_id_ref,
    )

    client = Anthropic()
    chat_log_path = _log_dir / f"{session_id}.chat.jsonl"

    _log.info("repl.start", session_id=session_id)

    await _repl_loop(
        client=client,
        handlers=handlers,
        console=_console,
        input_source=_input_source,
        session_id=session_id,
        turn_id_ref=turn_id_ref,
        chat_log_path=chat_log_path,
        meter=meter,
        clock=clock,
    )

    _log.info("repl.end", session_id=session_id)


def main() -> None:
    """Synchronous entry point for `python -m agentic_cab repl`."""
    asyncio.run(run_repl())
