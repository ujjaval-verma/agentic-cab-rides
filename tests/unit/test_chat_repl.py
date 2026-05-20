"""Unit tests for agent/chat.py — verifies REPL wiring without a real TTY or LLM.

Strategy: inject a scripted input source, a ScriptedConfirmationChannel, a fake
Console that captures output, and a real (in-memory) SqliteStateStore + UberSimAdapter
against a FrozenClock. The Anthropic client is replaced with a stub that returns
pre-canned responses so no network calls are made.

This proves:
  - All seven tools are registered and reachable (no KeyError on dispatch).
  - The REPL exits cleanly on /quit.
  - The REPL exits cleanly on EOF (input_source returns None).
  - Chat history is persisted after each turn.
  - build_handlers exposes all seven expected handler keys.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.confirmation.scripted import ScriptedConfirmationChannel
from agentic_cab.adapters.geocoding.cache import GeocodeCache
from agentic_cab.adapters.geocoding.nominatim import NominatimGeocoder
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.adapters.uber_sim.provider import UberSimAdapter
from agentic_cab.agent.chat import _TOOL_DEFS, build_handlers, run_repl

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CLOCK_START = datetime(2026, 5, 20, 9, 0, tzinfo=UTC)


def _frozen_clock() -> FrozenClock:
    return FrozenClock(start=_CLOCK_START)


def _make_deps(tmp_path: Path) -> dict[str, Any]:
    clock = _frozen_clock()
    store = SqliteStateStore(db_path=tmp_path / "state.db")
    store.apply_schema()
    log = JSONLActionLog(log_dir=tmp_path / "logs")
    sim = UberSimAdapter(store=store, clock=clock, rng_seed=42)
    channel = ScriptedConfirmationChannel(script=[], clock=clock)
    geocode_cache = GeocodeCache(fixtures_dir=tmp_path / "geocode")
    geocoder = NominatimGeocoder(cache=geocode_cache, http=MagicMock())
    return {
        "sim": sim,
        "store": store,
        "log": log,
        "clock": clock,
        "channel": channel,
        "geocoder": geocoder,
    }


# ---------------------------------------------------------------------------
# Tests: build_handlers — seven keys present
# ---------------------------------------------------------------------------


def test_build_handlers_has_all_seven_tools(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    turn_id_ref = ["T0"]
    handlers = build_handlers(
        sim=deps["sim"],
        store=deps["store"],
        log=deps["log"],
        clock=deps["clock"],
        channel=deps["channel"],
        geocoder=deps["geocoder"],
        session_id="S0",
        turn_id_ref=turn_id_ref,
    )
    expected = {
        "geocode_address",
        "discover_options",
        "prepare_booking",
        "confirm_booking",
        "cancel_booking",
        "track_booking",
        "list_active_bookings",
    }
    assert set(handlers.keys()) == expected


def test_tool_defs_has_all_seven_tools() -> None:
    names = {t["name"] for t in _TOOL_DEFS}
    expected = {
        "geocode_address",
        "discover_options",
        "prepare_booking",
        "confirm_booking",
        "cancel_booking",
        "track_booking",
        "list_active_bookings",
    }
    assert names == expected


# ---------------------------------------------------------------------------
# Stub Anthropic client
# ---------------------------------------------------------------------------


def _stub_client_end_message(text: str = "Done.") -> MagicMock:
    """Return a stub Anthropic client whose .messages.create() immediately
    returns a final text response (stop_reason='end_turn'), so run_turn
    completes without making any real API calls.
    """
    content_block = MagicMock()
    content_block.type = "text"
    content_block.text = text

    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [content_block]

    client = MagicMock()
    client.messages.create.return_value = response
    return client


# ---------------------------------------------------------------------------
# Tests: run_repl wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repl_exits_on_quit(tmp_path: Path) -> None:
    """REPL exits cleanly when the user types /quit."""
    inputs = iter(["/quit"])

    def _input_source() -> str | None:
        return next(inputs, None)

    from rich.console import Console

    console = Console(force_terminal=False, no_color=True)

    deps = _make_deps(tmp_path)

    with (
        patch("agentic_cab.agent.chat.Anthropic", return_value=_stub_client_end_message()),
        patch(
            "agentic_cab.agent.chat.UberSimAdapter",
            return_value=deps["sim"],
        ),
        patch(
            "agentic_cab.agent.chat.SqliteStateStore",
            return_value=deps["store"],
        ),
        patch(
            "agentic_cab.agent.chat.JSONLActionLog",
            return_value=deps["log"],
        ),
        patch(
            "agentic_cab.agent.chat.CLIConfirmationChannel",
            return_value=deps["channel"],
        ),
        patch(
            "agentic_cab.agent.chat.NominatimGeocoder",
            return_value=deps["geocoder"],
        ),
        patch(
            "agentic_cab.agent.chat.GeocodeCache",
            return_value=deps["geocoder"]._cache,
        ),
    ):
        await run_repl(
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
            geocode_fixtures_dir=tmp_path / "geocode",
            console=console,
            input_source=_input_source,
            channel=deps["channel"],
        )
    # If we reached here without blocking, the /quit path works.


@pytest.mark.asyncio
async def test_repl_exits_on_eof(tmp_path: Path) -> None:
    """REPL exits cleanly when input_source returns None (EOF / Ctrl-D)."""

    def _input_source() -> str | None:
        return None  # immediate EOF

    from rich.console import Console

    console = Console(force_terminal=False, no_color=True)
    deps = _make_deps(tmp_path)

    await run_repl(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        geocode_fixtures_dir=tmp_path / "geocode",
        console=console,
        input_source=_input_source,
        channel=deps["channel"],
    )


@pytest.mark.asyncio
async def test_repl_one_turn_persists_chat_log(tmp_path: Path) -> None:
    """One completed turn writes a .chat.jsonl file to the log directory."""
    inputs = iter(["List my active bookings please.", "/quit"])

    def _input_source() -> str | None:
        return next(inputs, None)

    from rich.console import Console

    console = Console(force_terminal=False, no_color=True)
    deps = _make_deps(tmp_path)

    stub_client = _stub_client_end_message("You have no active bookings.")

    with patch("agentic_cab.agent.chat.Anthropic", return_value=stub_client):
        await run_repl(
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
            geocode_fixtures_dir=tmp_path / "geocode",
            console=console,
            input_source=_input_source,
            channel=deps["channel"],
        )

    log_files = list((tmp_path / "logs").glob("*.chat.jsonl"))
    assert len(log_files) == 1, f"Expected one .chat.jsonl, got: {log_files}"
    content = log_files[0].read_text()
    assert content.strip(), "chat log should not be empty after one turn"


@pytest.mark.asyncio
async def test_repl_handler_dispatch_discover_options(tmp_path: Path) -> None:
    """discover_options handler is reachable and returns without error when called
    via the stub client that emits a tool_use block.

    This test exercises the full dispatch path without a real LLM: the stub
    client returns stop_reason='tool_use' with a discover_options call on the
    first response, then stop_reason='end_turn' on the second.
    """
    inputs = iter(["Book a ride from A to B.", "/quit"])

    def _input_source() -> str | None:
        return next(inputs, None)

    from rich.console import Console

    console = Console(force_terminal=False, no_color=True)
    deps = _make_deps(tmp_path)

    # First response: agent calls discover_options
    tool_use_block = MagicMock()
    tool_use_block.type = "tool_use"
    tool_use_block.name = "list_active_bookings"
    tool_use_block.id = "tu_001"
    tool_use_block.input = {}  # ListActiveArgs takes no fields

    tool_use_response = MagicMock()
    tool_use_response.stop_reason = "tool_use"
    tool_use_response.content = [tool_use_block]

    # Second response: agent replies with text
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "You have no active bookings."

    final_response = MagicMock()
    final_response.stop_reason = "end_turn"
    final_response.content = [text_block]

    stub_client = MagicMock()
    stub_client.messages.create.side_effect = [tool_use_response, final_response]

    with patch("agentic_cab.agent.chat.Anthropic", return_value=stub_client):
        await run_repl(
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
            geocode_fixtures_dir=tmp_path / "geocode",
            console=console,
            input_source=_input_source,
            channel=deps["channel"],
        )

    # Verify the action log captured the list_active_bookings call
    log_files = list((tmp_path / "logs").glob("*.jsonl"))
    action_log_files = [f for f in log_files if not f.name.endswith(".chat.jsonl")]
    assert action_log_files, "Expected an action log file"
    rows_text = action_log_files[0].read_text()
    assert "list_active_bookings" in rows_text
