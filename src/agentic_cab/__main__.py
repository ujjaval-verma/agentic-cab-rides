"""CLI entry point. Subcommands: init-db (more coming in P2+)."""

from __future__ import annotations

from pathlib import Path

import typer

from agentic_cab.adapters.state_store import SqliteStateStore

app = typer.Typer(no_args_is_help=True)


@app.callback()
def _main() -> None:
    """Agentic Cab Rides — AI-driven cab booking agent."""


@app.command("init-db")
def init_db() -> None:
    """Apply schema.sql to data/state.db (used by `make reset`)."""
    s = SqliteStateStore(db_path=Path("data/state.db"))
    s.apply_schema()


if __name__ == "__main__":
    app()
