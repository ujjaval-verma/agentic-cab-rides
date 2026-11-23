"""FastAPI application factory for the agentic-cab-rides web surface.

Routes:
  GET  /                       → index.html (chat + log pane)
  GET  /static/{path}         → static files
  POST /sessions              → mint a server-side session ULID
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
import hmac
import json
import os
import secrets
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import structlog
from anthropic import Anthropic
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.clock import SystemClock
from agentic_cab.adapters.cost_meter import InMemoryCostMeter
from agentic_cab.adapters.geocoding.cache import GeocodeCache
from agentic_cab.adapters.geocoding.nominatim import NominatimGeocoder
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.adapters.uber_sim.provider import UberSimAdapter
from agentic_cab.agent.chat import _TOOL_DEFS as _AGENT_TOOL_DEFS
from agentic_cab.agent.chat import build_handlers
from agentic_cab.agent.loop import run_turn
from agentic_cab.core.ids import SessionId, new_principal_id, new_session_id, new_turn_id
from agentic_cab.core.ports import Clock
from agentic_cab.web.channel import WebConfirmationChannel
from agentic_cab.web.sse import SessionEventBus

_log = structlog.get_logger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"

# ---------- Request / Response models ----------


class ChatRequest(BaseModel):
    session_id: SessionId
    message: str


class ConfirmationRequest(BaseModel):
    """Decision only. Extra fields (e.g. a client-sent `approver`) are ignored."""

    approved: bool


# ---------- Shared bus (module-level singleton per process) ----------

_bus = SessionEventBus()

# Bounded per-session state.
#
# One dict, one lifetime, one eviction point. Five parallel dicts keyed
# identically by session_id would make partial eviction a live hazard —
# evicting four of five could leave a session holding a stale meter or a
# stale option cache, a subtler bug than the leak itself.
_SESSION_IDLE_TTL = timedelta(hours=1)
_MAX_SESSIONS = 64


@dataclass
class _SessionState:
    """Everything one browser session needs, created together and evicted together.

    The sim adapter carries in-memory state (option cache, intent cache, the
    surge re-quote flag) that must survive across web turns within a process —
    otherwise a fresh adapter on every POST /chat sees an empty option cache
    and `prepare_booking(option_id=…)` raises `unknown option_id`. The meter
    must be the same instance `run_turn` fills and the gated tools drain (P8).
    """

    channel: WebConfirmationChannel
    turn_id_ref: list[str]
    history: list[dict[str, Any]]
    sim: UberSimAdapter
    meter: InMemoryCostMeter
    last_used_at: datetime
    # Opaque server-issued approver identity (a ULID). Safe to log: an identifier,
    # not a credential. The channel records it as `approver_id` on approval.
    principal_id: str
    # Session bearer secret, delivered only via an HttpOnly cookie. Never logged,
    # never returned in a body, never in repr (invariant 15).
    token: str = field(repr=False)


_sessions: dict[str, _SessionState] = {}

_TOKEN_BYTES = 32  # 256 bits → 43 urlsafe-base64 chars


def _session_cookie_name(session_id: str) -> str:
    """One cookie per session so two open tabs never overwrite each other's token."""
    return f"cab_session_{session_id}"


def _create_session(
    session_id: str,
    *,
    store: SqliteStateStore,
    clock: Clock,
) -> _SessionState:
    """Create and register the full per-session state record.

    One constructor owns all members so a session's state is never partially
    initialized or partially evicted. Mints the session's bearer token and its
    approver principal; both are server-issued, never client-supplied.
    """
    principal_id = new_principal_id()
    rng_seed = int.from_bytes(os.urandom(4), "big")
    state = _SessionState(
        channel=WebConfirmationChannel(
            bus=_bus,
            session_id=session_id,
            principal_id=principal_id,
            store=store,
            clock=clock,
        ),
        turn_id_ref=[new_turn_id()],
        history=[],
        sim=UberSimAdapter(store=store, clock=clock, rng_seed=rng_seed),
        meter=InMemoryCostMeter(),
        last_used_at=clock.now(),
        principal_id=principal_id,
        token=secrets.token_urlsafe(_TOKEN_BYTES),
    )
    _sessions[session_id] = state
    return state


_FORBIDDEN = "forbidden"


def _authorize(request: Request, session_id: str, *, clock: Clock) -> _SessionState:
    """Look up the caller's session (never creating one) and touch it, or 403.

    Unknown/evicted session, missing cookie, and wrong cookie all produce the
    same 403 so the response never reveals which session ids exist. Evicted
    sessions are never revived — the client must POST /sessions again.
    """
    presented = request.cookies.get(_session_cookie_name(session_id))
    state = _sessions.get(session_id)
    if (
        state is None
        or presented is None
        or not hmac.compare_digest(presented.encode(), state.token.encode())
    ):
        raise HTTPException(status_code=403, detail=_FORBIDDEN)
    state.last_used_at = clock.now()
    return state


def _evict_stale_sessions(clock: Clock) -> None:
    """Bound `_sessions` memory: idle TTL, then a hard LRU cap.

    A session whose `WebConfirmationChannel` still holds an unresolved pending
    confirmation is pinned — never evicted by either rule — so a human waiting
    at the gate is never stranded. Pending confirmations carry their own
    `expires_at`, so a pinned session cannot pin memory indefinitely.
    """
    now = clock.now()

    idle_ids = [
        sid
        for sid, state in _sessions.items()
        if now - state.last_used_at > _SESSION_IDLE_TTL and not state.channel.has_pending()
    ]
    for sid in idle_ids:
        del _sessions[sid]
        _log.info("web.session.evicted", session_id=sid, reason="idle")

    overflow = len(_sessions) - _MAX_SESSIONS
    if overflow > 0:
        evictable = sorted(
            ((sid, state) for sid, state in _sessions.items() if not state.channel.has_pending()),
            key=lambda item: item[1].last_used_at,
        )
        for sid, _state in evictable[:overflow]:
            del _sessions[sid]
            _log.info("web.session.evicted", session_id=sid, reason="lru")

        remaining_overflow = len(_sessions) - _MAX_SESSIONS
        if remaining_overflow > 0:
            _log.warning(
                "web.session.cap_exceeded_pinned",
                overflow=remaining_overflow,
            )


# ---------- App factory ----------


def create_app(
    *,
    data_dir: Path | None = None,
    log_dir: Path | None = None,
    geocode_fixtures_dir: Path | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    """Create and return the FastAPI application.

    All paths are injectable so tests can isolate filesystem state. `clock` is
    injectable too — production wiring defaults to `SystemClock`, and tests
    inject a `FrozenClock` to drive session-eviction timing without sleeping
    (CLAUDE.md invariant 9).
    """
    _data_dir = data_dir or Path("data")
    _log_dir = log_dir or Path("logs")
    _geocode_fixtures_dir = geocode_fixtures_dir or Path("fixtures/geocode")
    _clock: Clock = clock or SystemClock()

    _data_dir.mkdir(parents=True, exist_ok=True)
    _log_dir.mkdir(parents=True, exist_ok=True)

    @contextlib.asynccontextmanager
    async def _lifespan(a: FastAPI) -> AsyncGenerator[None, None]:
        """Re-publish pending confirmations that survived a prior process crash."""
        store = SqliteStateStore(db_path=_data_dir / "state.db", check_same_thread=False)
        store.apply_schema()
        pending = store.list_pending_confirmations(now=_clock.now())
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
    async def events(session_id: SessionId, request: Request) -> EventSourceResponse:
        """SSE stream of action-log rows and pending_confirmation events.

        Authorized before any stream opens; EventSource sends the same-origin
        session cookie automatically, so the token never rides in the URL.
        """
        _authorize(request, session_id, clock=_clock)
        _log.info("web.events.subscribe", session_id=session_id)

        async def _generator() -> AsyncGenerator[dict[str, str], None]:
            async for row in _bus.subscribe(session_id):
                yield {"data": json.dumps(row, default=str)}

        # ping=15 sends a `: ping` comment every 15s so browsers / proxies / OS
        # idle timers don't kill the stream when the agent is quiet. ping=0
        # disabled this and surfaced as spurious "connection lost" banners.
        return EventSourceResponse(_generator(), ping=15)

    @app.post("/sessions")
    async def create_session() -> JSONResponse:
        """Mint a server-side session ULID, its state, and its bearer-token cookie.

        The token travels only in an HttpOnly, SameSite=Strict cookie scoped to
        this session id; the body carries `session_id` alone.
        """
        _evict_stale_sessions(_clock)
        store = SqliteStateStore(db_path=_data_dir / "state.db", check_same_thread=False)
        store.apply_schema()
        session_id = new_session_id()
        state = _create_session(session_id, store=store, clock=_clock)
        _log.info("web.session.created", session_id=session_id)
        response = JSONResponse({"session_id": session_id})
        response.set_cookie(
            key=_session_cookie_name(session_id),
            value=state.token,
            path="/",
            httponly=True,
            samesite="strict",
        )
        return response

    @app.post("/chat")
    async def chat(body: ChatRequest, request: Request) -> JSONResponse:
        """Drive one agent turn and stream tool outcomes via SSE.

        Returns 200 immediately; actual results arrive on the SSE channel.
        The turn runs in a background task so the response returns promptly.
        Sessions are never created here — only POST /sessions mints them.
        """
        session_id = body.session_id
        clock = _clock

        # Bound session memory before this session's own state is touched; an
        # evicted session is then rejected by _authorize rather than revived.
        _evict_stale_sessions(clock)
        state = _authorize(request, session_id, clock=clock)
        _log.info("web.chat", session_id=session_id)

        store = SqliteStateStore(db_path=_data_dir / "state.db", check_same_thread=False)
        store.apply_schema()

        action_log = JSONLActionLog(log_dir=_log_dir)
        sim = state.sim
        channel = state.channel
        meter = state.meter

        geocode_cache = GeocodeCache(fixtures_dir=_geocode_fixtures_dir)
        geocoder = NominatimGeocoder(
            cache=geocode_cache,
            http=httpx.AsyncClient(timeout=10),
        )

        turn_id_ref = state.turn_id_ref
        turn_id_ref[0] = new_turn_id()

        history = state.history

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
            meter=meter,
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
                    meter=meter,
                    clock=clock,
                )
                state.history = new_history
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
        for _sid, state in _sessions.items():
            resolved = state.channel.resolve(subject_id, approved=body.approved)
            if resolved:
                _log.info(
                    "web.confirmation.resolved",
                    subject_id=subject_id,
                    approved=body.approved,
                )
                return JSONResponse({"status": "resolved"})

        raise HTTPException(status_code=404, detail=f"No pending confirmation for {subject_id!r}")

    @app.get("/sessions/{session_id}/log")
    async def session_log(session_id: SessionId, request: Request) -> JSONResponse:
        """Return the raw action-log rows for this session."""
        _authorize(request, session_id, clock=_clock)
        action_log = JSONLActionLog(log_dir=_log_dir)
        rows = action_log.read(session_id)
        return JSONResponse({"session_id": session_id, "rows": rows})

    return app


# ---------- Tool definitions (reuse from chat.py) ----------


def _get_tool_defs() -> list[dict[str, Any]]:
    """Return the seven tool definitions (same as the CLI agent)."""
    return _AGENT_TOOL_DEFS  # type: ignore[return-value]
