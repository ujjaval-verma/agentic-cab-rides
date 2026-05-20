"""CLI entry point. Subcommands: init-db, render-transcripts, repl (more coming in P2+)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Annotated

import typer
import uvicorn
import yaml

from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.agent.chat import run_repl
from agentic_cab.render.transcript import render
from agentic_cab.scenarios.invariants import evaluate
from agentic_cab.scenarios.runner import run_scenario
from agentic_cab.web.app import create_app

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
def render_transcripts(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Validate YAML schema only; skip LLM calls (used by CI)."),
    ] = False,
    assert_invariants: Annotated[
        bool,
        typer.Option(
            "--assert-invariants",
            help="After each run, assert all invariants pass (exit 1 if any fail).",
        ),
    ] = False,
) -> None:
    """Render all scenarios under scenarios/ into transcripts/ (P2 wires the actual MD render)."""
    scenarios_dir = Path("scenarios")
    data_dir = Path("data")
    log_dir = Path("logs")
    data_dir.mkdir(exist_ok=True)
    log_dir.mkdir(exist_ok=True)

    scenario_paths = sorted(scenarios_dir.glob("*.yaml"))
    if not scenario_paths:
        print("No scenarios found under scenarios/")  # noqa: T201
        return

    if dry_run:
        # Validate YAML schema only — no LLM, no provider calls.
        for scn_path in scenario_paths:
            scn = yaml.safe_load(scn_path.read_text())
            for required in ("name", "clock", "sim", "confirmation", "user_turns", "invariants"):
                if required not in scn:
                    print(f"FAIL {scn_path.name}: missing required field '{required}'")  # noqa: T201
                    sys.exit(1)
            print(f"ok   {scn_path.name}")  # noqa: T201
        return

    all_passed = True
    transcripts_dir = Path("transcripts")
    for scn_path in scenario_paths:
        session_id = asyncio.run(run_scenario(scn_path, data_dir=data_dir, log_dir=log_dir))

        # Render Markdown transcript for this scenario run.
        scn = yaml.safe_load(scn_path.read_text())
        action_log_adapter = JSONLActionLog(log_dir=log_dir)
        rows = action_log_adapter.read(session_id)
        chat_path = log_dir / f"{session_id}.chat.jsonl"
        chat = (
            [json.loads(line) for line in chat_path.read_text().splitlines() if line.strip()]
            if chat_path.exists()
            else []
        )
        results = evaluate(rows, scn.get("invariants", []))
        md = render(
            scenario_name=scn.get("name", scn_path.stem),
            seed=scn.get("seed", 0),
            chat=chat,
            action_log=rows,
            invariants=results,
        )
        transcripts_dir.mkdir(exist_ok=True)
        (transcripts_dir / f"{scn_path.stem}.md").write_text(md)

        # print() is allowed here — __main__.py is the CLI surface (CLAUDE.md inv 14).
        print(f"{scn_path.name} → session {session_id}")  # noqa: T201

        if assert_invariants:
            for kind, passed, params in results:
                mark = "PASS" if passed else "FAIL"
                print(f"  {mark} {kind} {params}")  # noqa: T201
                if not passed:
                    all_passed = False

    if assert_invariants and not all_passed:
        sys.exit(1)


@app.command("repl")
def repl() -> None:
    """Interactive CLI agent (CLIConfirmationChannel via /dev/tty)."""
    asyncio.run(run_repl())


@app.command("web")
def web(
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port")] = 8000,
) -> None:
    """FastAPI + browser UI (WebConfirmationChannel). Prefer `make web`."""
    uvicorn.run(create_app(), host=host, port=port, reload=False)


if __name__ == "__main__":
    app()
