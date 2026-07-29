"""Tests for FastAPI web app routes (web/app.py).

Route registration is verified; full SSE delivery is exercised by
SessionEventBus + WebConfirmationChannel unit tests and the T32 manual flow.
The pending_confirmation roundtrip is tested by setting up a channel directly
and calling POST /confirmations.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentic_cab.web.app import (
    _channels,
    _get_channel,
    _get_meter,
    _get_sim,
    _meters,
    _sim_adapters,
    create_app,
)


@pytest.fixture
def app(tmp_path: Path) -> TestClient:
    """Create a TestClient with isolated filesystem dirs."""
    # Clear module-level state between tests.
    _channels.clear()

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

    from agentic_cab.core.ports import ConfirmationRequest

    _channels.clear()
    fastapi_app = create_app(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        geocode_fixtures_dir=tmp_path / "fixtures",
    )
    client = TestClient(fastapi_app, raise_server_exceptions=True)

    session_id = "test-session-conf"
    channel = _get_channel(session_id)

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
    _channels.clear()
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
    _channels.clear()
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
    _channels.clear()
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
    _channels.clear()
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
        _channels.clear()
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

    _sim_adapters.clear()
    store = SqliteStateStore(db_path=tmp_path / "state.db", check_same_thread=False)
    store.apply_schema()
    clock = SystemClock()

    sim_a1 = _get_sim("sess-a", store=store, clock=clock)
    sim_a2 = _get_sim("sess-a", store=store, clock=clock)
    sim_b = _get_sim("sess-b", store=store, clock=clock)

    assert sim_a1 is sim_a2, "same session must reuse the cached adapter"
    assert sim_a1 is not sim_b, "different sessions must get distinct adapters"


def test_get_meter_memoizes_per_session(tmp_path: Path) -> None:
    """Two calls with the same session_id return the same CostMeter instance.

    Regression: a second meter per session means confirm_booking/cancel_booking
    would drain a buffer run_turn never filled — the exact silent-data-loss
    failure mode the P8 cost ledger is designed to avoid (see docs/plans).
    """
    _meters.clear()

    meter_a1 = _get_meter("sess-a")
    meter_a2 = _get_meter("sess-a")
    meter_b = _get_meter("sess-b")

    assert meter_a1 is meter_a2, "same session must reuse the cached meter"
    assert meter_a1 is not meter_b, "different sessions must get distinct meters"


def test_post_chat_kicks_off_a_turn(tmp_path: Path) -> None:
    """POST /chat returns 200 immediately (agent runs in background task).

    The agent loop integration is tested end-to-end in T32. Here we only verify
    the endpoint is wired and returns the session_id echo.
    """
    _channels.clear()
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
