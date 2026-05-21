"""FastAPI application factory for the agentic-cab-rides web surface.

Routes:
  GET  /                       → index.html (chat + log pane)
  GET  /static/{path}         → static files
  POST /chat                  → drive one agent turn; results stream via SSE
  GET  /events?session_id=... → SSE stream of action-log + confirmation events
  POST /confirmations/{id}    → resolve a pending WebConfirmationChannel future
  GET  /sessions/{id}/log     → raw JSONL action-log for the session

Layer: web/* may import from agent/, tools/, adapters/. Nothing in tools/ or
adapters/ may import from here (invariant 1).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import httpx
import structlog
from anthropic import Anthropic
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.clock import SystemClock
from agentic_cab.adapters.geocoding.cache import GeocodeCache
from agentic_cab.adapters.geocoding.nominatim import NominatimGeocoder
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.adapters.uber_sim.provider import UberSimAdapter
from agentic_cab.agent.chat import _TOOL_DEFS as _AGENT_TOOL_DEFS
from agentic_cab.agent.chat import build_handlers
from agentic_cab.agent.loop import run_turn
from agentic_cab.core.ids import new_turn_id
from agentic_cab.web.channel import WebConfirmationChannel
from agentic_cab.web.sse import SessionEventBus

_log = structlog.get_logger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"

# ---------- Request / Response models ----------


class ChatRequest(BaseModel):
    session_id: str
    message: str


class ConfirmationRequest(BaseModel):
    approved: bool
    approver: str = "web-user"


# ---------- Shared bus (module-level singleton per process) ----------

_bus = SessionEventBus()

# Per-session state: channels and turn-id refs.
_channels: dict[str, WebConfirmationChannel] = {}
_turn_id_refs: dict[str, list[str]] = {}
_chat_histories: dict[str, list[dict[str, Any]]] = {}


def _get_channel(
    session_id: str,
    store: SqliteStateStore | None = None,
    clock: SystemClock | None = None,
) -> WebConfirmationChannel:
    if session_id not in _channels:
        _channels[session_id] = WebConfirmationChannel(
            bus=_bus, session_id=session_id, store=store, clock=clock
        )
    return _channels[session_id]


# ---------- App factory ----------


def create_app(
    *,
    data_dir: Path | None = None,
    log_dir: Path | None = None,
    geocode_fixtures_dir: Path | None = None,
) -> FastAPI:
    """Create and return the FastAPI application.

    All paths are injectable so tests can isolate filesystem state.
    """
    _data_dir = data_dir or Path("data")
    _log_dir = log_dir or Path("logs")
    _geocode_fixtures_dir = geocode_fixtures_dir or Path("fixtures/geocode")

    _data_dir.mkdir(parents=True, exist_ok=True)
    _log_dir.mkdir(parents=True, exist_ok=True)

    @contextlib.asynccontextmanager
    async def _lifespan(a: FastAPI) -> AsyncGenerator[None, None]:
        """Re-publish pending confirmations that survived a prior process crash."""
        store = SqliteStateStore(db_path=_data_dir / "state.db", check_same_thread=False)
        store.apply_schema()
        clock = SystemClock()
        pending = store.list_pending_confirmations(now=clock.now())
        for record in pending:
            req = record.request
            _log.info(
                "web.startup.flush_pending_confirmation",
                session_id=record.session_id,
                subject_id=req.subject_id,
            )
            # Publish into the session's SSE channel so a reloaded browser tab
            # can render the confirmation modal.
            _bus.publish(
                record.session_id,  # use the original session_id, not subject_id
                {
                    "kind": "pending_confirmation",
                    "subject_id": req.subject_id,
                    "subject_kind": req.subject_kind,
                    "summary": req.summary,
                    "fingerprint": req.fingerprint,
                    "expires_at": req.expires_at.isoformat(),
                },
            )
        yield  # app runs here

    app = FastAPI(title="Agentic Cab Rides", lifespan=_lifespan)

    # Mount static files (CSS, JS) — served under /static/
    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # ---------- Routes ----------

    @app.get("/")
    async def index() -> FileResponse:
        index_path = _STATIC_DIR / "index.html"
        if not index_path.exists():
            raise HTTPException(status_code=404, detail="index.html not found")
        return FileResponse(str(index_path))

    @app.get("/events")
    async def events(session_id: str) -> EventSourceResponse:
        """SSE stream of action-log rows and pending_confirmation events."""
        _log.info("web.events.subscribe", session_id=session_id)

        async def _generator() -> AsyncGenerator[dict[str, str], None]:
            async for row in _bus.subscribe(session_id):
                yield {"data": json.dumps(row, default=str)}

        # ping=15 sends a `: ping` comment every 15s so browsers / proxies / OS
        # idle timers don't kill the stream when the agent is quiet. ping=0
        # disabled this and surfaced as spurious "connection lost" banners.
        return EventSourceResponse(_generator(), ping=15)

    @app.post("/chat")
    async def chat(body: ChatRequest) -> JSONResponse:
        """Drive one agent turn and stream tool outcomes via SSE.

        Returns 200 immediately; actual results arrive on the SSE channel.
        The turn runs in a background task so the response returns promptly.
        """
        session_id = body.session_id
        _log.info("web.chat", session_id=session_id)

        # Build per-session infrastructure (idempotent on second call for same session).
        store = SqliteStateStore(db_path=_data_dir / "state.db", check_same_thread=False)
        store.apply_schema()

        action_log = JSONLActionLog(log_dir=_log_dir)
        clock = SystemClock()
        rng_seed = int.from_bytes(os.urandom(4), "big")
        sim = UberSimAdapter(store=store, clock=clock, rng_seed=rng_seed)
        channel = _get_channel(session_id, store=store, clock=clock)

        geocode_cache = GeocodeCache(fixtures_dir=_geocode_fixtures_dir)
        geocoder = NominatimGeocoder(
            cache=geocode_cache,
            http=httpx.AsyncClient(timeout=10),
        )

        if session_id not in _turn_id_refs:
            _turn_id_refs[session_id] = [new_turn_id()]
        turn_id_ref = _turn_id_refs[session_id]
        turn_id_ref[0] = new_turn_id()

        if session_id not in _chat_histories:
            _chat_histories[session_id] = []
        history = _chat_histories[session_id]

        # Wrap action_log to also publish each row to the SSE bus.
        _sid_cap = session_id  # capture for the closure

        class _BusActionLog:
            def emit(self: _BusActionLog, row: dict[str, Any]) -> None:
                action_log.emit(row)
                _bus.publish(_sid_cap, row)

            def read(self: _BusActionLog, sid: str) -> list[dict[str, Any]]:
                return action_log.read(sid)

        bus_log = _BusActionLog()

        handlers = build_handlers(
            sim=sim,
            store=store,
            log=bus_log,  # type: ignore[arg-type]
            clock=clock,
            channel=channel,
            geocoder=geocoder,
            session_id=session_id,
            turn_id_ref=turn_id_ref,
        )

        client = Anthropic()

        async def _run_turn() -> None:
            try:
                text, new_history = await run_turn(
                    client,
                    user_message=body.message,
                    history=history,
                    tools=_get_tool_defs(),
                    handlers=handlers,
                )
                _chat_histories[session_id] = new_history
                # Publish the final agent reply as an SSE event.
                _bus.publish(session_id, {"kind": "agent_reply", "text": text})
            except Exception as exc:
                _log.exception("web.chat.error", session_id=session_id, exc=str(exc))
                _bus.publish(session_id, {"kind": "error", "message": str(exc)})

        # Keep a reference so the task isn't garbage-collected before it completes.
        _task = asyncio.create_task(_run_turn())
        _task.add_done_callback(lambda t: _log.debug("web.chat.task.done", exc=t.exception()))
        return JSONResponse({"status": "ok", "session_id": session_id})

    @app.post("/confirmations/{subject_id}")
    async def resolve_confirmation(subject_id: str, body: ConfirmationRequest) -> JSONResponse:
        """Resolve a pending WebConfirmationChannel future from the browser."""
        # Find the channel for the session that owns this subject_id.
        for _sid, ch in _channels.items():
            resolved = ch.resolve(subject_id, approved=body.approved, approver=body.approver)
            if resolved:
                _log.info(
                    "web.confirmation.resolved",
                    subject_id=subject_id,
                    approved=body.approved,
                )
                return JSONResponse({"status": "resolved"})

        raise HTTPException(status_code=404, detail=f"No pending confirmation for {subject_id!r}")

    @app.get("/sessions/{session_id}/log")
    async def session_log(session_id: str) -> JSONResponse:
        """Return the raw action-log rows for this session."""
        action_log = JSONLActionLog(log_dir=_log_dir)
        rows = action_log.read(session_id)
        return JSONResponse({"session_id": session_id, "rows": rows})

    return app


# ---------- Tool definitions (reuse from chat.py) ----------


def _get_tool_defs() -> list[dict[str, Any]]:
    """Return the seven tool definitions (same as the CLI agent)."""
    return _AGENT_TOOL_DEFS  # type: ignore[return-value]
