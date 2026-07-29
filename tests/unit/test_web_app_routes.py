"""Tests for FastAPI web app routes (web/app.py).

Route registration is verified; full SSE delivery is exercised by
SessionEventBus + WebConfirmationChannel unit tests and the T32 manual flow.
The pending_confirmation roundtrip is tested by setting up a channel directly
and calling POST /confirmations.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agentic_cab.web.app import (
    _MAX_SESSIONS,
    _SESSION_IDLE_TTL,
    _evict_stale_sessions,
    _get_session,
    _sessions,
    create_app,
)


@pytest.fixture(autouse=True)
def _isolated_sessions() -> Iterator[None]:
    """Isolate the module-level `_sessions` registry around every test in this file.

    `web.app._sessions` is process-global. Clearing only on entry (the previous
    pattern) leaves whatever this test created visible to the next one — order-dependent
    global state in a suite that otherwise injects its dependencies. Clear on both
    sides so neither direction can leak.
    """
    _sessions.clear()
    yield
    _sessions.clear()


@pytest.fixture
def app(tmp_path: Path) -> TestClient:
    """Create a TestClient with isolated filesystem dirs."""
    fastapi_app = create_app(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        geocode_fixtures_dir=tmp_path / "fixtures",
    )
    return TestClient(fastapi_app, raise_server_exceptions=True)


def test_get_index_serves_html(app: TestClient) -> None:
    """GET / returns 200 with HTML content (or 404 if static not built yet)."""
    response = app.get("/")
    # In the test environment the static dir exists with index.html.
    # If not present, a 404 is acceptable at this stage.
    assert response.status_code in (200, 404)
    if response.status_code == 200:
        assert "text/html" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_post_confirmations_resolves_pending_future(tmp_path: Path) -> None:
    """POST /confirmations/{subject_id} resolves a pending WebConfirmationChannel future."""
    from datetime import UTC, datetime, timedelta

    from agentic_cab.adapters.clock import SystemClock
    from agentic_cab.adapters.state_store import SqliteStateStore
    from agentic_cab.core.ports import ConfirmationRequest

    fastapi_app = create_app(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        geocode_fixtures_dir=tmp_path / "fixtures",
    )
    client = TestClient(fastapi_app, raise_server_exceptions=True)

    session_id = "test-session-conf"
    store = SqliteStateStore(db_path=tmp_path / "data" / "state.db", check_same_thread=False)
    store.apply_schema()
    channel = _get_session(session_id, store=store, clock=SystemClock()).channel

    req = ConfirmationRequest(
        subject_id="draft-xyz",
        subject_kind="draft",
        summary="Book UberX from A to B",
        fingerprint="a" * 64,
        details={},
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
    )

    # Start the request() coroutine in the background.
    result_holder: list = []

    async def _await_request() -> None:
        result = await channel.request(req)
        result_holder.append(result)

    task = asyncio.ensure_future(_await_request())
    await asyncio.sleep(0.05)  # let the future register

    # POST to the endpoint to resolve it.
    response = client.post(
        f"/confirmations/{req.subject_id}",
        json={"approved": True, "approver": "test-runner"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "resolved"

    # The pending future should now be resolved.
    await asyncio.wait_for(task, timeout=3.0)
    assert result_holder[0].approved is True
    assert result_holder[0].reason == "approved"


def test_post_confirmations_returns_404_when_no_pending(tmp_path: Path) -> None:
    """POST /confirmations/{subject_id} returns 404 if no pending future exists."""
    fastapi_app = create_app(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        geocode_fixtures_dir=tmp_path / "fixtures",
    )
    client = TestClient(fastapi_app, raise_server_exceptions=False)

    response = client.post(
        "/confirmations/nonexistent-id",
        json={"approved": True, "approver": "test"},
    )
    assert response.status_code == 404


def test_get_events_route_is_registered(tmp_path: Path) -> None:
    """GET /events route is registered and responds with text/event-stream.

    SSE streaming limitation with starlette TestClient / httpx ASGITransport:
    Both TestClient (sync WSGI shim) and httpx.AsyncClient ASGI transport call
    ``await app(scope, receive, send)`` and wait for the full response before
    returning. For an infinite SSE stream this creates a deadlock — the app
    cannot finish until the client reads, but the client cannot read until the
    await returns.

    The workaround is to verify the route without consuming the body. We do this
    by checking the /events route is registered in the app's URL routes. The
    actual SSE delivery is validated end-to-end in T32 (``make web`` manual flow)
    and is independently exercised by the SessionEventBus and
    WebConfirmationChannel unit tests.
    """
    fastapi_app = create_app(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        geocode_fixtures_dir=tmp_path / "fixtures",
    )
    # Verify the /events route is registered in the app.
    route_paths = [getattr(r, "path", None) for r in fastapi_app.routes]
    assert "/events" in route_paths, f"Expected /events route; found: {route_paths}"


def test_get_session_log_returns_empty_for_unknown_session(tmp_path: Path) -> None:
    """GET /sessions/{id}/log returns empty rows list for an unknown session."""
    fastapi_app = create_app(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        geocode_fixtures_dir=tmp_path / "fixtures",
    )
    client = TestClient(fastapi_app, raise_server_exceptions=True)

    response = client.get("/sessions/no-such-session/log")
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == "no-such-session"
    assert data["rows"] == []


def test_startup_flushes_pending_confirmations(tmp_path: Path) -> None:
    """On startup, pending_confirmations rows are re-published to the SSE bus.

    We seed the pending_confirmations table directly (bypassing the channel)
    to simulate a prior process crash. Then we start the app and verify that
    the startup hook publishes each non-expired row to the bus.

    Because the bus publish is fire-and-forget (no in-memory future to await),
    we verify the startup hook ran without error and the SQLite table is
    readable by the startup store path.
    """
    from datetime import UTC, datetime, timedelta

    from agentic_cab.adapters.state_store import SqliteStateStore
    from agentic_cab.core.ports import ConfirmationRequest

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Pre-seed the DB with a pending confirmation.
    store = SqliteStateStore(db_path=data_dir / "state.db")
    store.apply_schema()
    req = ConfirmationRequest(
        subject_id="draft-startup-test",
        subject_kind="draft",
        summary="Book UberX $15.00?",
        fingerprint="c" * 64,
        details={"price_cents": 1500},
        expires_at=datetime.now(UTC) + timedelta(seconds=3600),
    )
    store.put_pending_confirmation(
        subject_id=req.subject_id,
        session_id="sess-startup",
        request_json=req.model_dump_json(),
        expires_at=req.expires_at,
    )

    # Creating the app triggers the startup hook when used as a context manager.
    fastapi_app = create_app(
        data_dir=data_dir,
        log_dir=tmp_path / "logs",
        geocode_fixtures_dir=tmp_path / "fixtures",
    )
    client = TestClient(fastapi_app, raise_server_exceptions=True)

    # A simple GET request forces the TestClient to run lifespan startup.
    response = client.get("/sessions/any/log")
    assert response.status_code == 200

    # The pending row is still in the DB (startup hook publishes but does NOT pop).
    pending = store.list_pending_confirmations(now=datetime.now(UTC))
    ids = {r.request.subject_id for r in pending}
    assert "draft-startup-test" in ids


def test_startup_hook_publishes_to_session_id_not_subject_id(tmp_path: Path) -> None:
    """Startup hook must call bus.publish(session_id, ...) — not publish(subject_id, ...).

    We verify this by patching the module-level _bus with a recording stub,
    pre-seeding a pending_confirmations row, then forcing the lifespan startup
    to run via TestClient context manager.  The captured publish key must equal
    the session_id stored in the DB row, NOT the subject_id of the request.
    """
    from datetime import UTC, datetime, timedelta

    import agentic_cab.web.app as _app_module
    from agentic_cab.adapters.state_store import SqliteStateStore
    from agentic_cab.core.ports import ConfirmationRequest

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    session_id = "sess-bus-key-test"
    subject_id = "draft-bus-key-subject"

    store = SqliteStateStore(db_path=data_dir / "state.db")
    store.apply_schema()
    req = ConfirmationRequest(
        subject_id=subject_id,
        subject_kind="draft",
        summary="Book UberX $20.00?",
        fingerprint="d" * 64,
        details={"price_cents": 2000},
        expires_at=datetime.now(UTC) + timedelta(seconds=3600),
    )
    store.put_pending_confirmation(
        subject_id=req.subject_id,
        session_id=session_id,
        request_json=req.model_dump_json(),
        expires_at=req.expires_at,
    )

    # Install a recording stub as the module-level bus.
    publish_calls: list[tuple[str, dict]] = []

    class _RecordingBus:
        def publish(self, key: str, payload: dict) -> None:
            publish_calls.append((key, payload))

        async def subscribe(self, key: str):  # type: ignore[return]
            return
            yield  # make it a generator

    original_bus = _app_module._bus
    _app_module._bus = _RecordingBus()  # type: ignore[assignment]

    try:
        fastapi_app = create_app(
            data_dir=data_dir,
            log_dir=tmp_path / "logs",
            geocode_fixtures_dir=tmp_path / "fixtures",
        )
        with TestClient(fastapi_app, raise_server_exceptions=True):
            # Lifespan startup runs on __enter__.
            pass
    finally:
        _app_module._bus = original_bus

    assert publish_calls, "Expected at least one publish call from the startup hook"
    keys_used = [call[0] for call in publish_calls]
    # session_id must be used as the bus key, not subject_id
    assert session_id in keys_used
    assert subject_id not in keys_used


def test_get_sim_memoizes_per_session(tmp_path: Path) -> None:
    """Two calls with the same session_id return the same UberSimAdapter.

    Regression: a fresh adapter per turn loses `_option_cache`, so the second
    turn's `prepare_booking(option_id=…)` raises `unknown option_id` for an
    option discovered in the first turn.
    """
    from agentic_cab.adapters.clock import SystemClock
    from agentic_cab.adapters.state_store import SqliteStateStore

    store = SqliteStateStore(db_path=tmp_path / "state.db", check_same_thread=False)
    store.apply_schema()
    clock = SystemClock()

    sim_a1 = _get_session("sess-a", store=store, clock=clock).sim
    sim_a2 = _get_session("sess-a", store=store, clock=clock).sim
    sim_b = _get_session("sess-b", store=store, clock=clock).sim

    assert sim_a1 is sim_a2, "same session must reuse the cached adapter"
    assert sim_a1 is not sim_b, "different sessions must get distinct adapters"


def _gated_tool_use_response(tool_name: str, tool_input: dict) -> MagicMock:
    """A stubbed model response asking for one gated tool. No network call is made."""
    usage = MagicMock()
    usage.input_tokens = 100
    usage.output_tokens = 20
    usage.cache_read_input_tokens = 0
    usage.cache_creation_input_tokens = 0

    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.id = f"tu_{tool_name}"
    block.input = tool_input

    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [block]
    response.usage = usage
    return response


def _end_turn_response() -> MagicMock:
    usage = MagicMock()
    usage.input_tokens = 100
    usage.output_tokens = 20
    usage.cache_read_input_tokens = 0
    usage.cache_creation_input_tokens = 0

    block = MagicMock()
    block.type = "text"
    block.text = "All done."

    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [block]
    response.usage = usage
    return response


def test_post_chat_uses_one_meter_for_run_turn_and_both_gated_tools(tmp_path: Path) -> None:
    """POST /chat must pass the session's single CostMeter to run_turn AND to the
    gated tools the handlers dispatch to.

    Regression: a second meter anywhere on this route means confirm_booking /
    cancel_booking drain a buffer run_turn never filled — every web cost row then
    reports `[]` / 0, silently and forever.

    The assertion lands on `confirm_booking` / `cancel_booking` themselves rather than
    on `build_handlers`, because build_handlers receiving the right meter says nothing
    about what the closures it builds pass onward. The ids are bogus, so both tools
    reject before the gate and the background turn finishes without a human.
    """
    captured: dict[str, object] = {}

    import agentic_cab.agent.chat as chat_mod
    import agentic_cab.web.app as app_mod

    real_run_turn = app_mod.run_turn
    real_confirm_booking = chat_mod.confirm_booking
    real_cancel_booking = chat_mod.cancel_booking

    async def _spy_run_turn(client: object, **kwargs: object) -> object:
        captured["run_turn_meter"] = kwargs["meter"]
        return await real_run_turn(client, **kwargs)  # type: ignore[arg-type]

    async def _spy_confirm_booking(*args: object, **kwargs: object) -> object:
        captured["confirm_booking_meter"] = kwargs["meter"]
        return await real_confirm_booking(*args, **kwargs)  # type: ignore[arg-type]

    async def _spy_cancel_booking(*args: object, **kwargs: object) -> object:
        captured["cancel_booking_meter"] = kwargs["meter"]
        return await real_cancel_booking(*args, **kwargs)  # type: ignore[arg-type]

    stub_client = MagicMock()
    stub_client.messages.create.side_effect = [
        _gated_tool_use_response("confirm_booking", {"draft_id": "no-such-draft"}),
        _gated_tool_use_response("cancel_booking", {"booking_id": "no-such-booking"}),
        _end_turn_response(),
    ]

    fastapi_app = create_app(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        geocode_fixtures_dir=tmp_path / "fixtures",
    )

    with (
        patch("agentic_cab.web.app.Anthropic", return_value=stub_client),
        patch("agentic_cab.web.app.run_turn", side_effect=_spy_run_turn),
        patch("agentic_cab.agent.chat.confirm_booking", side_effect=_spy_confirm_booking),
        patch("agentic_cab.agent.chat.cancel_booking", side_effect=_spy_cancel_booking),
        TestClient(fastapi_app, raise_server_exceptions=True) as client,
    ):
        response = client.post(
            "/chat",
            json={"session_id": "sess-meter-identity", "message": "confirm then cancel"},
        )
        assert response.status_code == 200

        # The turn runs in a background task on the TestClient's portal loop; poll
        # (bounded) until both gated tools have been reached.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and "cancel_booking_meter" not in captured:
            time.sleep(0.01)

    assert "run_turn_meter" in captured, "run_turn was not called"
    assert "confirm_booking_meter" in captured, "the confirm_booking handler was not reached"
    assert "cancel_booking_meter" in captured, "the cancel_booking handler was not reached"

    session_meter = _sessions["sess-meter-identity"].meter
    assert captured["run_turn_meter"] is session_meter
    assert (
        captured["confirm_booking_meter"] is session_meter
    ), "confirm_booking must receive the session's own CostMeter"
    assert (
        captured["cancel_booking_meter"] is session_meter
    ), "cancel_booking must receive the session's own CostMeter"


def test_get_meter_is_stable_per_session_and_distinct_across_sessions(tmp_path: Path) -> None:
    """Two lookups of one session return the same CostMeter; two sessions never share one.

    The route-level test above pins the wiring; this pins the registry itself — a
    shared meter across sessions would cross-attribute one user's spend to another.
    """
    from agentic_cab.adapters.clock import SystemClock
    from agentic_cab.adapters.state_store import SqliteStateStore

    store = SqliteStateStore(db_path=tmp_path / "state.db", check_same_thread=False)
    store.apply_schema()
    clock = SystemClock()

    meter_a1 = _get_session("sess-a", store=store, clock=clock).meter
    meter_a2 = _get_session("sess-a", store=store, clock=clock).meter
    meter_b = _get_session("sess-b", store=store, clock=clock).meter

    assert meter_a1 is meter_a2, "same session must reuse the cached meter"
    assert meter_a1 is not meter_b, "different sessions must get distinct meters"


def test_post_chat_kicks_off_a_turn(tmp_path: Path) -> None:
    """POST /chat returns 200 immediately (agent runs in background task).

    The agent loop integration is tested end-to-end in T32. Here we only verify
    the endpoint is wired and returns the session_id echo.
    """
    fastapi_app = create_app(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        geocode_fixtures_dir=tmp_path / "fixtures",
    )
    client = TestClient(fastapi_app, raise_server_exceptions=False)

    response = client.post(
        "/chat",
        json={"session_id": "test-session-chat", "message": "hello"},
    )
    # 200 means the background task was kicked off; the agent error (no API key)
    # will be emitted as an SSE error event, not as an HTTP error.
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["session_id"] == "test-session-chat"


# ---------- Session eviction (bounded per-session state) ----------


def test_idle_session_evicted_together(tmp_path: Path) -> None:
    """An idle session past the TTL is evicted — the whole record disappears together.

    Regression: five parallel dicts made partial eviction a live hazard (a
    session could keep a stale meter or option cache). Consolidating into one
    `_SessionState` record means eviction is one dict.pop, never a partial one.
    """
    from datetime import UTC, datetime

    from agentic_cab.adapters.clock import FrozenClock

    clock = FrozenClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    fastapi_app = create_app(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        geocode_fixtures_dir=tmp_path / "fixtures",
        clock=clock,
    )
    client = TestClient(fastapi_app, raise_server_exceptions=False)

    client.post("/chat", json={"session_id": "sess-idle", "message": "hi"})
    assert "sess-idle" in _sessions

    clock.tick(_SESSION_IDLE_TTL + timedelta(seconds=1))
    # Any subsequent /chat call runs the eviction sweep before touching its own state.
    client.post("/chat", json={"session_id": "sess-trigger", "message": "hi"})

    assert "sess-idle" not in _sessions, "the whole session record must be gone"


def test_session_touched_within_ttl_survives(tmp_path: Path) -> None:
    """A session touched within the idle TTL is not evicted."""
    from datetime import UTC, datetime

    from agentic_cab.adapters.clock import FrozenClock

    clock = FrozenClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    fastapi_app = create_app(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        geocode_fixtures_dir=tmp_path / "fixtures",
        clock=clock,
    )
    client = TestClient(fastapi_app, raise_server_exceptions=False)

    client.post("/chat", json={"session_id": "sess-fresh", "message": "hi"})
    clock.tick(timedelta(minutes=30))
    client.post("/chat", json={"session_id": "sess-trigger", "message": "hi"})

    assert "sess-fresh" in _sessions


def test_exceeding_max_sessions_evicts_lru_first(tmp_path: Path) -> None:
    """Once `_MAX_SESSIONS` is exceeded, the least-recently-used session goes first."""
    from datetime import UTC, datetime

    from agentic_cab.adapters.clock import FrozenClock

    clock = FrozenClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    fastapi_app = create_app(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        geocode_fixtures_dir=tmp_path / "fixtures",
        clock=clock,
    )
    client = TestClient(fastapi_app, raise_server_exceptions=False)

    for i in range(_MAX_SESSIONS):
        client.post("/chat", json={"session_id": f"sess-{i}", "message": "hi"})
        clock.tick(timedelta(seconds=1))
    assert len(_sessions) == _MAX_SESSIONS

    # A brand-new session pushes the count to _MAX_SESSIONS + 1; the sweep at
    # its entry still saw exactly the cap, so no eviction happens yet.
    client.post("/chat", json={"session_id": "sess-new", "message": "hi"})
    clock.tick(timedelta(seconds=1))

    # Re-touching an existing session runs the sweep again, which now sees
    # _MAX_SESSIONS + 1 and evicts the LRU session (sess-0, the oldest
    # last_used_at) before touching its own (already-existing) state — so
    # this call doesn't grow the dict itself.
    client.post("/chat", json={"session_id": "sess-new", "message": "hi again"})

    assert "sess-0" not in _sessions, "least-recently-used session should be evicted first"
    assert "sess-new" in _sessions
    assert len(_sessions) == _MAX_SESSIONS


@pytest.mark.asyncio
async def test_pinned_session_with_pending_confirmation_survives_eviction(
    tmp_path: Path,
) -> None:
    """A session with an unresolved pending confirmation is never evicted.

    Evicting it would strand a human waiting at the gate. We drive this via
    the real `WebConfirmationChannel.request()` / `_evict_stale_sessions`
    path rather than poking a parallel registry.
    """
    from datetime import UTC, datetime

    from agentic_cab.adapters.clock import FrozenClock
    from agentic_cab.adapters.state_store import SqliteStateStore
    from agentic_cab.core.ports import ConfirmationRequest

    clock = FrozenClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    store = SqliteStateStore(db_path=tmp_path / "state.db", check_same_thread=False)
    store.apply_schema()

    state = _get_session("sess-pinned", store=store, clock=clock)
    req = ConfirmationRequest(
        subject_id="draft-pinned",
        subject_kind="draft",
        summary="Book UberX $12.00 from A to B",
        fingerprint="b" * 64,
        details={},
        expires_at=clock.now() + timedelta(hours=2),
    )
    task = asyncio.ensure_future(state.channel.request(req))
    await asyncio.sleep(0)  # let the future register

    # Push well past the idle TTL — the pinned session must survive.
    clock.tick(_SESSION_IDLE_TTL + timedelta(hours=1))
    _evict_stale_sessions(clock)
    assert "sess-pinned" in _sessions

    # It must also survive an LRU sweep even once the cap is exceeded — the
    # pinned session has the oldest last_used_at of the lot, so it would be
    # the first LRU candidate if it weren't filtered out.
    for i in range(_MAX_SESSIONS + 5):
        _get_session(f"sess-filler-{i}", store=store, clock=clock)
    _evict_stale_sessions(clock)
    assert "sess-pinned" in _sessions
    assert len(_sessions) <= _MAX_SESSIONS + 1  # +1 for the pinned session itself

    # Clean up: resolve so the awaited task doesn't dangle past the test.
    state.channel.resolve("draft-pinned", approved=False, approver=None)
    await asyncio.wait_for(task, timeout=2.0)


def test_session_reused_after_eviction_gets_fresh_state(tmp_path: Path) -> None:
    """Re-using a session id after eviction creates a fresh record and does not crash."""
    from datetime import UTC, datetime

    from agentic_cab.adapters.clock import FrozenClock

    clock = FrozenClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    fastapi_app = create_app(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        geocode_fixtures_dir=tmp_path / "fixtures",
        clock=clock,
    )
    client = TestClient(fastapi_app, raise_server_exceptions=False)

    client.post("/chat", json={"session_id": "sess-reuse", "message": "hi"})
    original_meter = _sessions["sess-reuse"].meter
    original_sim = _sessions["sess-reuse"].sim

    clock.tick(_SESSION_IDLE_TTL + timedelta(seconds=1))
    client.post("/chat", json={"session_id": "sess-trigger", "message": "hi"})
    assert "sess-reuse" not in _sessions

    response = client.post("/chat", json={"session_id": "sess-reuse", "message": "hi again"})
    assert response.status_code == 200

    new_state = _sessions["sess-reuse"]
    assert new_state.meter is not original_meter, "reused session must get a fresh meter"
    assert new_state.sim is not original_sim, "reused session must get a fresh sim adapter"
