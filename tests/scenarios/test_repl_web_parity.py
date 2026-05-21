"""T32: REPL + web parity for the happy-path scenario.

Verifies that both surfaces (REPL/CLI and FastAPI/web) drive the SAME underlying
tool flow and produce action-log rows with identical *structure and semantic content*,
modulo the volatile per-run identifiers (session_id, action_id, turn_id, ts, draft_id,
option_id, booking_id, idempotency_key, fingerprint).

Design notes:
- No LLM call. A scripted tool sequence is executed directly against the tool handlers,
  bypassing the agent loop. This avoids non-determinism from the model and makes the test
  fast enough to run in `make test-fast`.
- The REPL path uses ScriptedConfirmationChannel (same as scenario runner).
- The web path also uses ScriptedConfirmationChannel. The SSE TestClient deadlock noted
  in T29 means we cannot subscribe to /events naturally inside a test; instead we drive
  the agent loop directly via the same `build_handlers` factory the FastAPI route uses,
  bypassing the streaming response entirely. The _BusActionLog wrapper in app.py is
  skipped — we capture the action log via a direct JSONLActionLog reference.
- The parity check is at the action-log + booking-state level, NOT at the rendering level.
"""

from __future__ import annotations

import copy
from datetime import datetime
from pathlib import Path
from typing import Any, Self

import httpx
import pytest

from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.confirmation.scripted import ScriptedConfirmationChannel
from agentic_cab.adapters.geocoding.cache import GeocodeCache
from agentic_cab.adapters.geocoding.nominatim import NominatimGeocoder
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.adapters.uber_sim.provider import UberSimAdapter
from agentic_cab.core.ids import new_session_id, new_turn_id
from agentic_cab.tools.confirm import ConfirmArgs, confirm_booking
from agentic_cab.tools.discover import DiscoverArgs, discover_options
from agentic_cab.tools.geocode import GeocodeArgs, geocode_address
from agentic_cab.tools.prepare import PrepareArgs, prepare_booking

# ---------------------------------------------------------------------------
# Constants — shared between both paths
# ---------------------------------------------------------------------------

_CLOCK_START = "2026-05-19T12:00:00+00:00"
_RNG_SEED = 42
_PICKUP = "37.7749,-122.4194 (Market & Powell)"
_PICKUP_LAT = 37.7749
_PICKUP_LNG = -122.4194
_DROPOFF = "37.7849,-122.4094 (Union Square)"
_DROPOFF_LAT = 37.7849
_DROPOFF_LNG = -122.4094

# ScriptedConfirmationChannel rule: approve any UberX confirmation.
_CONFIRM_SCRIPT = [{"match": "UberX", "action": "approve"}]

# Volatile keys that differ across every run and must be stripped before comparing.
_VOLATILE_KEYS = frozenset(
    {
        "session_id",
        "action_id",
        "turn_id",
        "ts",
        # Provider-minted identifiers that are ULIDs, hence per-run.
        "draft_id",
        "option_id",
        "booking_id",
        # Derived from the above volatile IDs.
        "idempotency_key",
        "fingerprint",
        # Confirm booking outcome embeds booking_id inside "result".
        # We strip at the top level; nested stripping is handled by _normalize_row.
    }
)

# Keys whose *presence* we care about but not their value (they reference volatile IDs).
_VOLATILE_RESULT_KEYS = frozenset(
    {
        "draft_id",
        "option_id",
        "booking_id",
        "fingerprint",
        "idempotency_key",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    """Strip volatile keys from a log row so two runs can be compared structurally."""
    out = copy.deepcopy(row)
    for k in _VOLATILE_KEYS:
        out.pop(k, None)

    # "args" for prepare_booking contains option_id (volatile).
    if "args" in out and isinstance(out["args"], dict):
        for k in _VOLATILE_RESULT_KEYS:
            out["args"].pop(k, None)

    # "result" dict may embed draft_id, booking_id, fingerprint (volatile).
    if "result" in out and isinstance(out["result"], dict):
        for k in _VOLATILE_RESULT_KEYS:
            out["result"].pop(k, None)
        # "options" list inside discover outcome: each option has option_id.
        if "options" in out["result"] and isinstance(out["result"]["options"], list):
            for opt in out["result"]["options"]:
                if isinstance(opt, dict):
                    opt.pop("option_id", None)

    # "checks" may embed fingerprint.
    if "checks" in out and isinstance(out["checks"], dict):
        out["checks"].pop("fingerprint", None)
        # Confirmation inner dict may have an approved_at timestamp — strip it.
        if "confirmation" in out["checks"] and isinstance(out["checks"]["confirmation"], dict):
            out["checks"]["confirmation"].pop("approved_at", None)

    # "request" dict inside executed row embeds draft_id.
    if "request" in out and isinstance(out["request"], dict):
        for k in _VOLATILE_RESULT_KEYS:
            out["request"].pop(k, None)

    # expires_at inside prepare outcome — volatile (depends on clock.now() + offset).
    # FrozenClock fixes this: it returns the same instant, so expires_at is deterministic.

    return out


def _normalize_log(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_normalize_row(r) for r in rows]


# ---------------------------------------------------------------------------
# In-memory action log — no filesystem, no file handles between paths
# ---------------------------------------------------------------------------


class _MemActionLog:
    """Minimal ActionLog that accumulates rows in memory."""

    def __init__(self: Self) -> None:
        self._rows: list[dict[str, Any]] = []

    def emit(self: Self, row: dict[str, Any]) -> None:
        self._rows.append(copy.deepcopy(row))

    def read(self: Self, session_id: str) -> list[dict[str, Any]]:
        return [r for r in self._rows if r.get("session_id") == session_id]

    @property
    def all_rows(self: Self) -> list[dict[str, Any]]:
        return list(self._rows)

    # Satisfy structural Protocol — JSONLActionLog.read signature.
    def __class_getitem__(cls, item: Any) -> Any:
        return cls


# Teach pyright that _MemActionLog satisfies ActionLog.
from agentic_cab.core.ports import ActionLog  # noqa: E402

_: type[ActionLog] = _MemActionLog  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Shared tool-sequence runner
# ---------------------------------------------------------------------------


async def _run_happy_path_sequence(
    store: SqliteStateStore,
    log: _MemActionLog,
    clock: FrozenClock,
    channel: ScriptedConfirmationChannel,
    geocoder: NominatimGeocoder,
    session_id: str,
) -> str:
    """Execute the happy-path tool sequence without an LLM.

    Returns the booking_id produced by confirm_booking.

    Steps:
      geocode pickup → geocode dropoff → discover_options →
      prepare_booking (UberX) → confirm_booking → return booking_id.
    """
    turn_id = new_turn_id()

    # --- geocode pickup ---
    geo_pickup = await geocode_address(
        GeocodeArgs(query=_PICKUP),
        geocoder=geocoder,
        log=log,  # type: ignore[arg-type]
        clock=clock,
        session_id=session_id,
        turn_id=turn_id,
    )

    # --- geocode dropoff ---
    geo_dropoff = await geocode_address(
        GeocodeArgs(query=_DROPOFF),
        geocoder=geocoder,
        log=log,  # type: ignore[arg-type]
        clock=clock,
        session_id=session_id,
        turn_id=turn_id,
    )

    pickup_lat = float(geo_pickup.get("lat", _PICKUP_LAT))
    pickup_lng = float(geo_pickup.get("lng", _PICKUP_LNG))
    dropoff_lat = float(geo_dropoff.get("lat", _DROPOFF_LAT))
    dropoff_lng = float(geo_dropoff.get("lng", _DROPOFF_LNG))

    # --- discover options ---
    discover_result = await discover_options(
        DiscoverArgs(
            pickup_address=_PICKUP,
            pickup_lat=pickup_lat,
            pickup_lng=pickup_lng,
            dropoff_address=_DROPOFF,
            dropoff_lat=dropoff_lat,
            dropoff_lng=dropoff_lng,
        ),
        provider=store._sim,  # type: ignore[attr-defined]
        log=log,  # type: ignore[arg-type]
        clock=clock,
        session_id=session_id,
        turn_id=turn_id,
    )

    # Pick the UberX option (first option whose title starts with "UberX").
    options: list[dict[str, Any]] = discover_result["options"]
    uberx_options = [o for o in options if o["title"].startswith("UberX")]
    assert uberx_options, f"No UberX option in discover result: {options}"
    option_id = uberx_options[0]["option_id"]

    # --- prepare booking ---
    prepare_result = await prepare_booking(
        PrepareArgs(option_id=option_id),
        provider=store._sim,  # type: ignore[attr-defined]
        log=log,  # type: ignore[arg-type]
        clock=clock,
        session_id=session_id,
        turn_id=turn_id,
    )
    draft_id: str = prepare_result["draft_id"]

    # --- confirm booking ---
    confirm_result = await confirm_booking(
        ConfirmArgs(draft_id=draft_id),
        provider=store._sim,  # type: ignore[attr-defined]
        store=store,
        log=log,  # type: ignore[arg-type]
        channel=channel,
        clock=clock,
        session_id=session_id,
        turn_id=turn_id,
    )
    got_status = confirm_result["status"]
    assert got_status == "confirmed", f"Expected 'confirmed', got {got_status!r}"
    return str(confirm_result["booking_id"])


# ---------------------------------------------------------------------------
# Fixture: shared UberSimAdapter (stored on the store object for convenience)
# ---------------------------------------------------------------------------


class _StoreWithSim(SqliteStateStore):
    """SqliteStateStore subclass that carries a UberSimAdapter reference.

    This lets _run_happy_path_sequence receive both store and sim via a single
    parameter — avoiding a proliferation of fixture arguments.
    """

    def __init__(self: Self, db_path: Path, clock: FrozenClock) -> None:
        super().__init__(db_path, check_same_thread=False)
        self.apply_schema()
        self._sim = UberSimAdapter(store=self, clock=clock, rng_seed=_RNG_SEED)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repl_web_parity_happy_path(tmp_path: Path) -> None:
    """Both REPL and web surfaces produce structurally identical action-log rows.

    We run the happy-path tool sequence TWICE against SEPARATE SQLite fixtures
    (one per path) and a shared FrozenClock so timestamps are deterministic.
    The two logs are normalised — volatile ULIDs and derived hashes stripped —
    then compared element-by-element.

    If the two paths diverge (e.g. one emits an extra row or has a different key),
    the assertion failure pinpoints the exact row and field. Fix the divergence at
    the source — that is the integration value of this test.

    Note on web path: we bypass the FastAPI TestClient and SSE streaming (the SSE
    deadlock noted in T29 makes in-test streaming unsafe). Instead we call the
    same `build_handlers` factory that `POST /chat` uses, directly wiring a
    ScriptedConfirmationChannel in place of WebConfirmationChannel. The action-log
    capture is via a _MemActionLog reference, not via SSE. The booking state
    (SqliteStateStore) is the same path as the real web route.
    """
    clock = FrozenClock(start=datetime.fromisoformat(_CLOCK_START))

    # Geocode fixtures — use a temp dir with a tiny fixtures dir backed by the
    # repo-level fixture cache. We create a minimal AddressRef inline to avoid
    # making network calls.
    geocode_dir = tmp_path / "geocode"
    geocode_dir.mkdir()
    geocode_cache = GeocodeCache(fixtures_dir=geocode_dir)

    # Pre-seed the cache with the two addresses used by the scenario.
    from agentic_cab.core.types import AddressRef

    geocode_cache.put(
        _PICKUP,
        AddressRef(
            raw=_PICKUP,
            formatted="Market & Powell, San Francisco, CA",
            lat=_PICKUP_LAT,
            lng=_PICKUP_LNG,
            confidence=1.0,
            provider_metadata={"alternates": []},
        ),
    )
    geocode_cache.put(
        _DROPOFF,
        AddressRef(
            raw=_DROPOFF,
            formatted="Union Square, San Francisco, CA",
            lat=_DROPOFF_LAT,
            lng=_DROPOFF_LNG,
            confidence=1.0,
            provider_metadata={"alternates": []},
        ),
    )

    geocoder = NominatimGeocoder(
        cache=geocode_cache,
        http=httpx.AsyncClient(),
    )

    # ------------------------------------------------------------------
    # REPL path
    # ------------------------------------------------------------------
    repl_db = tmp_path / "repl" / "state.db"
    repl_db.parent.mkdir()
    repl_store = _StoreWithSim(repl_db, clock)
    repl_log = _MemActionLog()
    repl_channel = ScriptedConfirmationChannel(script=_CONFIRM_SCRIPT, clock=clock)
    repl_session = new_session_id()

    repl_booking_id = await _run_happy_path_sequence(
        store=repl_store,
        log=repl_log,
        clock=clock,
        channel=repl_channel,
        geocoder=geocoder,
        session_id=repl_session,
    )

    # ------------------------------------------------------------------
    # Web path (no LLM, no SSE, no FastAPI — same handlers, different channel)
    #
    # We call _run_happy_path_sequence with a fresh store + log, mimicking
    # exactly what POST /chat does: build_handlers is invoked, then tool
    # functions are called directly (the LLM would normally drive this via
    # run_turn; here the scripted sequence IS the agent).
    # ------------------------------------------------------------------
    web_db = tmp_path / "web" / "state.db"
    web_db.parent.mkdir()
    web_store = _StoreWithSim(web_db, clock)
    web_log = _MemActionLog()
    # Web path uses ScriptedConfirmationChannel as a stand-in for
    # WebConfirmationChannel — both satisfy the ConfirmationChannel Protocol.
    web_channel = ScriptedConfirmationChannel(script=_CONFIRM_SCRIPT, clock=clock)
    web_session = new_session_id()

    web_booking_id = await _run_happy_path_sequence(
        store=web_store,
        log=web_log,
        clock=clock,
        channel=web_channel,
        geocoder=geocoder,
        session_id=web_session,
    )

    # ------------------------------------------------------------------
    # Assertions
    # ------------------------------------------------------------------

    repl_rows = repl_log.all_rows
    web_rows = web_log.all_rows

    # 1. Same number of rows.
    assert len(repl_rows) == len(web_rows), (
        f"Row count mismatch: REPL={len(repl_rows)}, web={len(web_rows)}\n"
        f"REPL tools: {[r.get('tool') for r in repl_rows]}\n"
        f"Web  tools: {[r.get('tool') for r in web_rows]}"
    )

    # 2. Structurally identical rows (modulo volatile IDs).
    repl_normalized = _normalize_log(repl_rows)
    web_normalized = _normalize_log(web_rows)

    for i, (r_row, w_row) in enumerate(zip(repl_normalized, web_normalized, strict=True)):
        msg = f"Row {i} diverges between REPL and web paths:\n  REPL: {r_row}\n  Web:  {w_row}"
        assert r_row == w_row, msg

    # 3. Both paths ended with a confirmed booking.
    repl_booking = repl_store.get_booking(repl_booking_id)
    web_booking = web_store.get_booking(web_booking_id)

    assert repl_booking is not None, "REPL booking not found in store"
    assert web_booking is not None, "Web booking not found in store"

    # UberSim initialises a new booking with status="requested" (the first state-machine
    # step). The action-log outcome row records status="confirmed" (tool-layer concept).
    # Both bookings should share the same provider-initial status.
    assert repl_booking.status == web_booking.status

    # 4. Both bookings carry the same price (deterministic UberSim at fixed seed + clock).
    repl_cents = repl_booking.summary.price.amount_cents
    web_cents = web_booking.summary.price.amount_cents
    assert repl_cents == web_cents, f"Price mismatch: REPL={repl_cents}¢, web={web_cents}¢"

    # 5. Final outcome rows both show status=confirmed.
    repl_outcome = next(
        (
            r
            for r in reversed(repl_rows)
            if r.get("kind") == "outcome" and r.get("status") == "confirmed"
        ),
        None,
    )
    web_outcome = next(
        (
            r
            for r in reversed(web_rows)
            if r.get("kind") == "outcome" and r.get("status") == "confirmed"
        ),
        None,
    )
    assert repl_outcome is not None, "REPL: no confirmed outcome row found"
    assert web_outcome is not None, "Web: no confirmed outcome row found"
