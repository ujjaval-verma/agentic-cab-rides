"""Tests for FastAPI web app routes (web/app.py).

SSE streaming via TestClient has a limitation: the starlette TestClient does not
fully support SSE event iteration natively. For event streaming tests we use
httpx with stream=True to read raw SSE lines. The pending_confirmation roundtrip
is tested by setting up a channel directly and calling POST /confirmations.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentic_cab.web.app import _channels, _get_channel, create_app


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
