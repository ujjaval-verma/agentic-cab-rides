"""CLI entry point. Subcommands: init-db, render-transcripts (more coming in P2+)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.scenarios.runner import run_scenario

app = typer.Typer(no_args_is_help=True)


@app.callback()
def _main() -> None:
    """Agentic Cab Rides — AI-driven cab booking agent."""


@app.command("init-db")
def init_db() -> None:
    """Apply schema.sql to data/state.db (used by `make reset`)."""
    s = SqliteStateStore(db_path=Path("data/state.db"))
    s.apply_schema()


@app.command("render-transcripts")
def render_transcripts() -> None:
    """Render all scenarios under scenarios/ into transcripts/ (P2 wires the actual MD render)."""
    scenarios_dir = Path("scenarios")
    data_dir = Path("data")
    log_dir = Path("logs")
    data_dir.mkdir(exist_ok=True)
    log_dir.mkdir(exist_ok=True)
    for scn_path in sorted(scenarios_dir.glob("*.yaml")):
        session_id = asyncio.run(run_scenario(scn_path, data_dir=data_dir, log_dir=log_dir))
        # T16 will plug in the markdown renderer here; for T15 we just produce logs.
        # print() is allowed here — __main__.py is the CLI surface (CLAUDE.md inv 14).
        print(f"{scn_path.name} → session {session_id}")  # noqa: T201


if __name__ == "__main__":
    app()
