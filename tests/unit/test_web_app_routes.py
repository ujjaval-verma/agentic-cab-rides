"""Tests for FastAPI web app routes (web/app.py).

Route registration is verified; full SSE delivery is exercised by
SessionEventBus + WebConfirmationChannel unit tests and the T32 manual flow.
The pending_confirmation roundtrip is tested by minting a session, starting a
request on its channel, and calling POST /sessions/{sid}/confirmations/{subject_id}.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.core.ports import ConfirmationRequest
from agentic_cab.web.app import (
    _MAX_SESSIONS,
    _SESSION_COOKIE_MAX_AGE,
    _SESSION_IDLE_TTL,
    _create_session,
    _evict_stale_sessions,
    _sessions,
    create_app,
)
from agentic_cab.web.channel import WebConfirmationChannel


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


# Loopback base URL for every web test client. Starlette's default Host
# (`testserver`) is not on the production allowlist and must stay off it.
_LOOPBACK_BASE_URL = "http://127.0.0.1:8000"


def _client(fastapi_app: FastAPI, **kw: object) -> TestClient:
    return TestClient(fastapi_app, base_url=_LOOPBACK_BASE_URL, **kw)  # type: ignore[arg-type]


def _async_client(fastapi_app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fastapi_app),
        base_url=_LOOPBACK_BASE_URL,
    )


_ULID = "01KS5HMK9QJZ8PCJ92B9VM7BRA"


def _ulid(n: int) -> str:
    """Deterministic valid session ULID, distinct per `n`."""
    return f"01KS5HMK9QJZ8PCJ92B9{n:06d}"


_BAD_SESSION_IDS = [
    "../../etc/x",
    "/abs",
    _ULID.lower(),
    "123e4567-e89b-42d3-a456-426614174000",
]


@pytest.mark.parametrize("bad", _BAD_SESSION_IDS)
def test_chat_rejects_invalid_session_id(app: TestClient, tmp_path: Path, bad: str) -> None:
    response = app.post("/chat", json={"session_id": bad, "message": "hi"})
    assert response.status_code == 422  # noqa: S101 pytest assertion
    assert not (tmp_path / "logs").exists() or list((tmp_path / "logs").iterdir()) == []  # noqa: S101 pytest assertion
    assert not _sessions  # noqa: S101 pytest assertion


@pytest.mark.parametrize("bad", _BAD_SESSION_IDS)
def test_events_rejects_invalid_session_id(app: TestClient, bad: str) -> None:
    assert app.get("/events", params={"session_id": bad}).status_code == 422  # noqa: S101 pytest assertion


@pytest.mark.parametrize("bad", [_ULID.lower(), "not-a-ulid", "%2E%2E"])
def test_session_log_rejects_invalid_session_id(app: TestClient, bad: str) -> None:
    assert app.get(f"/sessions/{bad}/log").status_code == 422  # noqa: S101 pytest assertion


@pytest.mark.parametrize("bad", ["..%2F..%2Fetc%2Fx", "%2Fabs"])
def test_session_log_encoded_slashes_never_reach_the_handler(app: TestClient, bad: str) -> None:
    # Decoded slashes change the route shape, so the router refuses before validation.
    assert app.get(f"/sessions/{bad}/log").status_code in (404, 422)  # noqa: S101 pytest assertion


def test_chat_accepts_minted_session(app: TestClient) -> None:
    sid = _mint(app)
    response = app.post("/chat", json={"session_id": sid, "message": "hi"})
    assert response.status_code == 200  # noqa: S101 pytest assertion
    assert response.json()["session_id"] == sid  # noqa: S101 pytest assertion


def test_session_log_accepts_minted_session(app: TestClient) -> None:
    sid = _mint(app)
    response = app.get(f"/sessions/{sid}/log")
    assert response.status_code == 200  # noqa: S101 pytest assertion
    assert response.json() == {"session_id": sid, "rows": []}  # noqa: S101 pytest assertion


def test_post_sessions_mints_valid_ulid(app: TestClient) -> None:
    from agentic_cab.core.ids import parse_session_id

    response = app.post("/sessions")
    assert response.status_code == 200  # noqa: S101 pytest assertion
    assert parse_session_id(response.json()["session_id"])  # noqa: S101 pytest assertion


def _session_cookie_header(response: object, sid: str) -> str:
    headers = response.headers.get_list("set-cookie")  # type: ignore[attr-defined]
    matching = [h for h in headers if h.startswith(f"cab_session_8000_{sid}=")]
    assert len(matching) == 1, headers  # noqa: S101 pytest assertion
    return matching[0]


def test_post_sessions_sets_httponly_samesite_strict_session_cookie(app: TestClient) -> None:
    response = app.post("/sessions")
    sid = response.json()["session_id"]
    header = _session_cookie_header(response, sid)
    attrs = {part.strip().lower() for part in header.split(";")[1:]}
    assert "httponly" in attrs  # noqa: S101 pytest assertion
    assert "samesite=strict" in attrs  # noqa: S101 pytest assertion
    assert "path=/" in attrs  # noqa: S101 pytest assertion
    # Bounded lifetime so cookies from reloaded tabs cannot pile up for the whole
    # browser session and overflow the Cookie header.
    assert f"max-age={int(_SESSION_COOKIE_MAX_AGE.total_seconds())}" in attrs  # noqa: S101 pytest assertion


def _make_clocked_client(tmp_path: Path) -> tuple[TestClient, FrozenClock]:
    clock = FrozenClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    client = _client(
        create_app(
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
            geocode_fixtures_dir=tmp_path / "fixtures",
            clock=clock,
        )
    )
    return client, clock


def test_session_cookie_name_carries_the_served_port(tmp_path: Path) -> None:
    from agentic_cab.core.settings import RuntimeSettings

    settings = RuntimeSettings.from_env({"AGENTIC_CAB_WEB_PORT": "8101"})
    client = _client(
        create_app(
            settings=settings,
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
            geocode_fixtures_dir=tmp_path / "fixtures",
        )
    )
    # Another instance's cookie on a port that is a prefix of ours: never swept.
    client.cookies.set(f"cab_session_810_{_ulid(1)}", "x")
    response = client.post("/sessions")
    sid = response.json()["session_id"]
    headers = response.headers.get_list("set-cookie")
    assert [h.split("=", 1)[0] for h in headers] == [f"cab_session_8101_{sid}"]  # noqa: S101 pytest assertion
    assert client.get(f"/sessions/{sid}/log").status_code == 200  # noqa: S101 pytest assertion


def test_post_sessions_expires_cookies_of_sessions_no_longer_live(tmp_path: Path) -> None:
    client, clock = _make_clocked_client(tmp_path)
    stale_sid = _mint(client)
    clock.tick(_SESSION_IDLE_TTL + timedelta(seconds=1))

    response = client.post("/sessions")  # evicts stale_sid, then sweeps its cookie
    assert stale_sid not in _sessions  # noqa: S101 pytest assertion
    header = _session_cookie_header(response, stale_sid)
    attrs = {part.strip().lower() for part in header.split(";")[1:]}
    assert "max-age=0" in attrs  # noqa: S101 pytest assertion
    assert "path=/" in attrs  # noqa: S101 pytest assertion
    assert client.cookies.get(f"cab_session_8000_{stale_sid}") is None  # noqa: S101 pytest assertion
    assert client.cookies.get(f"cab_session_8000_{response.json()['session_id']}")  # noqa: S101 pytest assertion


def test_post_sessions_keeps_cookies_of_live_sessions(tmp_path: Path) -> None:
    client, _clock = _make_clocked_client(tmp_path)
    live_sid = _mint(client)  # another open tab
    response = client.post("/sessions")
    headers = response.headers.get_list("set-cookie")
    assert not any(h.startswith(f"cab_session_8000_{live_sid}=") for h in headers), headers  # noqa: S101 pytest assertion
    assert client.cookies.get(f"cab_session_8000_{live_sid}")  # noqa: S101 pytest assertion


def test_post_sessions_body_carries_only_session_id(app: TestClient) -> None:
    response = app.post("/sessions")
    sid = response.json()["session_id"]
    assert list(response.json()) == ["session_id"]  # noqa: S101 pytest assertion
    token = app.cookies.get(f"cab_session_8000_{sid}")
    assert token  # noqa: S101 pytest assertion
    assert token not in response.text  # noqa: S101 pytest assertion


def test_post_sessions_mints_distinct_unguessable_tokens(app: TestClient) -> None:
    sid_a = app.post("/sessions").json()["session_id"]
    sid_b = app.post("/sessions").json()["session_id"]
    token_a = app.cookies.get(f"cab_session_8000_{sid_a}")
    token_b = app.cookies.get(f"cab_session_8000_{sid_b}")
    assert token_a  # noqa: S101 pytest assertion
    assert token_b  # noqa: S101 pytest assertion
    assert token_a != token_b  # noqa: S101 pytest assertion
    assert len(token_a) >= 43  # noqa: S101 pytest assertion
    assert len(token_b) >= 43  # noqa: S101 pytest assertion


def test_post_sessions_registers_state_with_token_and_principal(app: TestClient) -> None:
    from agentic_cab.core.ids import parse_session_id

    sid = app.post("/sessions").json()["session_id"]
    state = _sessions[sid]
    assert state.token == app.cookies.get(f"cab_session_8000_{sid}")  # noqa: S101 pytest assertion
    assert parse_session_id(state.principal_id)  # noqa: S101 pytest assertion
    assert state.principal_id != sid  # noqa: S101 pytest assertion
    assert state.token not in repr(state), "token must never appear in repr/logs"  # noqa: S101 pytest assertion


def _mint(client: TestClient) -> str:
    """Mint a session through the real route; the client's jar now holds its token."""
    response = client.post("/sessions")
    assert response.status_code == 200  # noqa: S101 pytest assertion
    return response.json()["session_id"]


def _call(client: TestClient, route: str, sid: str) -> httpx.Response:
    if route == "chat":
        return client.post("/chat", json={"session_id": sid, "message": "hi"})
    if route == "events":
        # Only ever called on paths that must be refused before a stream opens.
        return client.get("/events", params={"session_id": sid})
    return client.get(f"/sessions/{sid}/log")


_AUTH_ROUTES = ["chat", "events", "log"]


@pytest.mark.parametrize("route", _AUTH_ROUTES)
def test_session_route_without_cookie_is_forbidden(app: TestClient, route: str) -> None:
    sid = _mint(app)
    app.cookies.clear()
    assert _call(app, route, sid).status_code == 403  # noqa: S101 pytest assertion


@pytest.mark.parametrize("route", _AUTH_ROUTES)
def test_session_route_with_wrong_cookie_is_forbidden(app: TestClient, route: str) -> None:
    import secrets

    sid = _mint(app)
    app.cookies.clear()
    app.cookies.set(f"cab_session_8000_{sid}", secrets.token_urlsafe(32))
    assert _call(app, route, sid).status_code == 403  # noqa: S101 pytest assertion


@pytest.mark.parametrize("route", _AUTH_ROUTES)
def test_session_route_for_never_minted_session_is_forbidden(app: TestClient, route: str) -> None:
    assert _call(app, route, _ulid(42)).status_code == 403  # noqa: S101 pytest assertion
    assert _ulid(42) not in _sessions, "an unknown session id must never be created on demand"  # noqa: S101 pytest assertion


@pytest.mark.parametrize("route", _AUTH_ROUTES)
def test_session_route_for_evicted_session_is_forbidden(tmp_path: Path, route: str) -> None:
    from datetime import UTC, datetime

    from agentic_cab.adapters.clock import FrozenClock

    clock = FrozenClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    client = _client(
        create_app(
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
            geocode_fixtures_dir=tmp_path / "fixtures",
            clock=clock,
        )
    )
    sid = _mint(client)
    stale_token = client.cookies.get(f"cab_session_8000_{sid}")
    assert stale_token  # noqa: S101 pytest assertion
    clock.tick(_SESSION_IDLE_TTL + timedelta(seconds=1))
    _mint(client)  # the mint runs the eviction sweep (and expires the stale cookie)
    assert sid not in _sessions  # noqa: S101 pytest assertion
    # Present the once-valid token anyway, as a client that ignored the expiry would.
    client.cookies.set(f"cab_session_8000_{sid}", stale_token)
    assert _call(client, route, sid).status_code == 403  # noqa: S101 pytest assertion
    assert sid not in _sessions, "an evicted session id is never revived"  # noqa: S101 pytest assertion


@pytest.mark.parametrize("route", _AUTH_ROUTES)
def test_session_a_cookie_cannot_reach_session_b(tmp_path: Path, route: str) -> None:
    fastapi_app = create_app(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        geocode_fixtures_dir=tmp_path / "fixtures",
    )
    client_a = _client(fastapi_app)
    client_b = _client(fastapi_app)
    sid_a = _mint(client_a)
    sid_b = _mint(client_b)

    # A's jar only carries cab_session_<A>; addressing B's id finds no B cookie.
    assert _call(client_a, route, sid_b).status_code == 403  # noqa: S101 pytest assertion

    # A's token replayed under B's cookie name is still a mismatch.
    token_a = client_a.cookies.get(f"cab_session_8000_{sid_a}")
    assert token_a  # noqa: S101 pytest assertion
    client_a.cookies.set(f"cab_session_8000_{sid_b}", token_a)
    assert _call(client_a, route, sid_b).status_code == 403  # noqa: S101 pytest assertion


def test_app_js_uses_server_minted_session_id() -> None:
    js = (Path(__file__).resolve().parents[2] / "src/agentic_cab/web/static/app.js").read_text(
        encoding="utf-8"
    )
    assert "randomUUID" not in js  # noqa: S101 pytest assertion
    assert '"/sessions"' in js  # noqa: S101 pytest assertion


def test_app_js_posts_confirmations_to_session_route_without_approver() -> None:
    import re

    js = (Path(__file__).resolve().parents[2] / "src/agentic_cab/web/static/app.js").read_text(
        encoding="utf-8"
    )
    assert "approver" not in js, "the browser must never send an approver"  # noqa: S101 pytest assertion
    assert '"/confirmations/"' in js  # noqa: S101 pytest assertion
    # Every "/confirmations/" path is built under the "/sessions/" prefix.
    for match in re.finditer(r'"/confirmations/"', js):
        preceding = js[max(0, match.start() - 120) : match.start()]
        assert '"/sessions/"' in preceding, preceding  # noqa: S101 pytest assertion
    assert 'credentials: "same-origin"' in js  # noqa: S101 pytest assertion


@pytest.fixture
def app(tmp_path: Path) -> TestClient:
    """Create a TestClient with isolated filesystem dirs."""
    fastapi_app = create_app(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        geocode_fixtures_dir=tmp_path / "fixtures",
    )
    return _client(fastapi_app, raise_server_exceptions=True)


def test_test_client_targets_loopback_host(tmp_path: Path) -> None:
    """Every web test client talks to a loopback host, the production default allowlist."""
    assert _client(_new_app(tmp_path)).base_url.host == "127.0.0.1"  # noqa: S101 pytest assertion
    assert _async_client(_new_app(tmp_path)).base_url.host == "127.0.0.1"  # noqa: S101 pytest assertion


# ---------- Host allowlist (DNS-rebinding defence) ----------

_HOST_CHECKED_REQUESTS: list[tuple[str, str, dict[str, object] | None]] = [
    ("GET", "/", None),
    ("POST", "/sessions", None),
    ("POST", "/chat", {"session_id": _ULID, "message": "hi"}),
    ("POST", f"/sessions/{_ULID}/confirmations/draft-x", {"approved": True}),
]


@pytest.mark.parametrize("host", ["evil.example", "testserver", "evil.example:8000"])
@pytest.mark.parametrize(("method", "path", "body"), _HOST_CHECKED_REQUESTS)
def test_foreign_host_is_rejected_with_400(
    app: TestClient, host: str, method: str, path: str, body: dict[str, object] | None
) -> None:
    response = app.request(method, path, json=body, headers={"host": host})
    assert response.status_code == 400  # noqa: S101 pytest assertion
    assert not _sessions, "a foreign-Host request must never reach a route"  # noqa: S101 pytest assertion


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "localhost:8000", "127.0.0.1:8000"])
def test_loopback_hosts_are_accepted(app: TestClient, host: str) -> None:
    assert app.get("/", headers={"host": host}).status_code == 200  # noqa: S101 pytest assertion
    assert app.post("/sessions", headers={"host": host}).status_code == 200  # noqa: S101 pytest assertion


def _app_with(tmp_path: Path, **kw: object) -> FastAPI:
    return create_app(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        geocode_fixtures_dir=tmp_path / "fixtures",
        **kw,  # type: ignore[arg-type]
    )


def _host_status(fastapi_app: FastAPI, host: str) -> int:
    return _client(fastapi_app).get("/", headers={"host": host}).status_code


def test_bind_host_argument_is_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTIC_CAB_WEB_HOST", raising=False)
    fastapi_app = _app_with(tmp_path, bind_host="192.168.1.5")
    assert _host_status(fastapi_app, "192.168.1.5") == 200  # noqa: S101 pytest assertion
    assert _host_status(fastapi_app, "192.168.1.5:8000") == 200  # noqa: S101 pytest assertion
    assert _host_status(fastapi_app, "127.0.0.1") == 200  # noqa: S101 pytest assertion
    assert _host_status(fastapi_app, "evil.example") == 400  # noqa: S101 pytest assertion


def test_bind_host_env_var_is_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTIC_CAB_WEB_HOST", "192.168.1.5")
    fastapi_app = _app_with(tmp_path)
    assert _host_status(fastapi_app, "192.168.1.5") == 200  # noqa: S101 pytest assertion
    assert _host_status(fastapi_app, "evil.example") == 400  # noqa: S101 pytest assertion


def test_bind_host_argument_wins_over_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTIC_CAB_WEB_HOST", "10.0.0.9")
    fastapi_app = _app_with(tmp_path, bind_host="192.168.1.5")
    assert _host_status(fastapi_app, "192.168.1.5") == 200  # noqa: S101 pytest assertion
    assert _host_status(fastapi_app, "10.0.0.9") == 400  # noqa: S101 pytest assertion


def _app_bound_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: str, source: str
) -> FastAPI:
    """Build the app with `host` configured via the `bind_host` argument or the env var."""
    if source == "env":
        monkeypatch.setenv("AGENTIC_CAB_WEB_HOST", host)
        return _app_with(tmp_path)
    monkeypatch.delenv("AGENTIC_CAB_WEB_HOST", raising=False)
    return _app_with(tmp_path, bind_host=host)


@pytest.mark.parametrize("wildcard", ["0.0.0.0", "::", ""])  # noqa: S104 wildcard is the input under test
@pytest.mark.parametrize("source", ["arg", "env"])
def test_wildcard_bind_host_adds_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wildcard: str, source: str
) -> None:
    fastapi_app = _app_bound_to(tmp_path, monkeypatch, wildcard, source)
    assert _host_status(fastapi_app, "evil.example") == 400  # noqa: S101 pytest assertion
    assert _host_status(fastapi_app, "127.0.0.1") == 200  # noqa: S101 pytest assertion


@pytest.mark.parametrize("star", ["*", "*.example"])
@pytest.mark.parametrize("source", ["arg", "env"])
def test_star_bind_host_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, star: str, source: str
) -> None:
    with pytest.raises(ValueError, match="bind host"):
        _app_bound_to(tmp_path, monkeypatch, star, source)


def test_architecture_doc_names_host_and_origin_checks() -> None:
    doc = (Path(__file__).resolve().parents[2] / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "TrustedHostMiddleware" in doc  # noqa: S101 pytest assertion
    assert "AGENTIC_CAB_WEB_HOST" in doc  # noqa: S101 pytest assertion
    assert "SameOriginMiddleware" in doc  # noqa: S101 pytest assertion
    assert "Origin" in doc  # noqa: S101 pytest assertion


def test_get_index_serves_html(app: TestClient) -> None:
    """GET / returns 200 with HTML content (or 404 if static not built yet)."""
    response = app.get("/")
    # In the test environment the static dir exists with index.html.
    # If not present, a 404 is acceptable at this stage.
    assert response.status_code in (200, 404)  # noqa: S101 pytest assertion
    if response.status_code == 200:
        assert "text/html" in response.headers["content-type"]  # noqa: S101 pytest assertion


# ---------- Session-scoped confirmations ----------
#
# These drive the app through httpx.AsyncClient + ASGITransport so the route and
# the awaiting WebConfirmationChannel future share one event loop (no thread hop).


async def _amint(client: httpx.AsyncClient) -> str:
    response = await client.post("/sessions")
    assert response.status_code == 200  # noqa: S101 pytest assertion
    return response.json()["session_id"]


def _confirmation_req(subject_id: str) -> ConfirmationRequest:
    from datetime import UTC, datetime

    return ConfirmationRequest(
        subject_id=subject_id,
        subject_kind="draft",
        summary="Book UberX from A to B",
        fingerprint="a" * 64,
        details={},
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
    )


async def _await_pending(channel: WebConfirmationChannel) -> None:
    for _ in range(200):
        if channel.has_pending():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("confirmation never became pending")


def _new_app(tmp_path: Path) -> FastAPI:
    return create_app(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        geocode_fixtures_dir=tmp_path / "fixtures",
    )


async def test_session_scoped_confirmation_resolves_pending_future(tmp_path: Path) -> None:
    """POST /sessions/{sid}/confirmations/{subject_id} resolves that session's future."""
    async with _async_client(_new_app(tmp_path)) as client:
        sid = await _amint(client)
        channel = _sessions[sid].channel
        task = asyncio.ensure_future(channel.request(_confirmation_req("draft-xyz")))
        await _await_pending(channel)

        response = await client.post(
            f"/sessions/{sid}/confirmations/draft-xyz", json={"approved": True}
        )
        assert response.status_code == 200  # noqa: S101 pytest assertion
        assert response.json()["status"] == "resolved"  # noqa: S101 pytest assertion

        result = await asyncio.wait_for(task, timeout=3.0)
        assert result.approved is True  # noqa: S101 pytest assertion
        assert result.reason == "approved"  # noqa: S101 pytest assertion


async def test_session_scoped_confirmation_returns_404_when_no_pending(tmp_path: Path) -> None:
    async with _async_client(_new_app(tmp_path)) as client:
        sid = await _amint(client)
        response = await client.post(
            f"/sessions/{sid}/confirmations/nonexistent-id", json={"approved": True}
        )
        assert response.status_code == 404  # noqa: S101 pytest assertion


async def test_session_scoped_confirmation_requires_session_token(tmp_path: Path) -> None:
    async with _async_client(_new_app(tmp_path)) as client:
        sid = await _amint(client)
        channel = _sessions[sid].channel
        task = asyncio.ensure_future(channel.request(_confirmation_req("draft-tok")))
        await _await_pending(channel)

        client.cookies.clear()
        response = await client.post(
            f"/sessions/{sid}/confirmations/draft-tok", json={"approved": True}
        )
        assert response.status_code == 403  # noqa: S101 pytest assertion
        assert channel.has_pending(), "an unauthenticated POST must not touch the gate"  # noqa: S101 pytest assertion

        channel.resolve("draft-tok", approved=False)
        await asyncio.wait_for(task, timeout=3.0)


async def test_cross_session_approval_is_rejected(tmp_path: Path) -> None:
    """Session A can neither address B's route nor reach B's pending request via its own."""
    fastapi_app = _new_app(tmp_path)
    async with _async_client(fastapi_app) as client_a, _async_client(fastapi_app) as client_b:
        sid_a = await _amint(client_a)
        sid_b = await _amint(client_b)
        channel_b = _sessions[sid_b].channel
        task = asyncio.ensure_future(channel_b.request(_confirmation_req("draft-of-b")))
        await _await_pending(channel_b)

        # A addresses B's session: no B token in A's jar → 403.
        response = await client_a.post(
            f"/sessions/{sid_b}/confirmations/draft-of-b", json={"approved": True}
        )
        assert response.status_code == 403  # noqa: S101 pytest assertion

        # A addresses its own session with B's subject id → not pending there → 404.
        response = await client_a.post(
            f"/sessions/{sid_a}/confirmations/draft-of-b", json={"approved": True}
        )
        assert response.status_code == 404  # noqa: S101 pytest assertion

        assert channel_b.has_pending(), "B's pending confirmation must be untouched"  # noqa: S101 pytest assertion
        assert not task.done()  # noqa: S101 pytest assertion

        # B resolves its own request normally.
        response = await client_b.post(
            f"/sessions/{sid_b}/confirmations/draft-of-b", json={"approved": True}
        )
        assert response.status_code == 200  # noqa: S101 pytest assertion
        result = await asyncio.wait_for(task, timeout=3.0)
        assert result.approved is True  # noqa: S101 pytest assertion
        assert result.approver_id == _sessions[sid_b].principal_id  # noqa: S101 pytest assertion


async def test_body_supplied_approver_is_ignored(tmp_path: Path) -> None:
    async with _async_client(_new_app(tmp_path)) as client:
        sid = await _amint(client)
        channel = _sessions[sid].channel
        task = asyncio.ensure_future(channel.request(_confirmation_req("draft-mallory")))
        await _await_pending(channel)

        response = await client.post(
            f"/sessions/{sid}/confirmations/draft-mallory",
            json={"approved": True, "approver": "mallory", "approver_id": "mallory"},
        )
        assert response.status_code == 200  # noqa: S101 pytest assertion

        result = await asyncio.wait_for(task, timeout=3.0)
        assert result.approver == "web-user"  # noqa: S101 pytest assertion
        assert result.approver_id == _sessions[sid].principal_id  # noqa: S101 pytest assertion
        assert result.channel == "web"  # noqa: S101 pytest assertion
        assert "mallory" not in result.model_dump_json()  # noqa: S101 pytest assertion


def test_old_unscoped_confirmation_route_is_gone(app: TestClient) -> None:
    route_paths = [getattr(r, "path", None) for r in app.app.routes]  # type: ignore[attr-defined]
    assert "/confirmations/{subject_id}" not in route_paths  # noqa: S101 pytest assertion
    assert "/sessions/{session_id}/confirmations/{subject_id}" in route_paths  # noqa: S101 pytest assertion
    response = app.post("/confirmations/anything", json={"approved": True})
    assert response.status_code in (404, 405)  # noqa: S101 pytest assertion


async def test_full_confirm_flow_logs_principal_and_never_the_token(
    tmp_path: Path, meter: object
) -> None:
    """End to end: confirm_booking awaits the session's web gate, the browser approves
    over the session route with a forged approver, and the action log records the
    server-side principal — and nowhere contains the session token (invariant 15)."""
    from agentic_cab.adapters.action_log import JSONLActionLog
    from agentic_cab.adapters.clock import SystemClock
    from agentic_cab.adapters.state_store import SqliteStateStore
    from agentic_cab.tools.confirm import ConfirmArgs, confirm_booking
    from agentic_cab.tools.discover import DiscoverArgs, discover_options
    from agentic_cab.tools.prepare import PrepareArgs, prepare_booking

    async with _async_client(_new_app(tmp_path)) as client:
        sid = await _amint(client)
        token = client.cookies.get(f"cab_session_8000_{sid}")
        assert token  # noqa: S101 pytest assertion
        state = _sessions[sid]
        store = SqliteStateStore(db_path=tmp_path / "data" / "state.db", check_same_thread=False)
        log = JSONLActionLog(log_dir=tmp_path / "logs")
        clock = SystemClock()

        discovered = await discover_options(
            DiscoverArgs(
                pickup_address="A",
                pickup_lat=37.7749,
                pickup_lng=-122.4194,
                dropoff_address="B",
                dropoff_lat=37.7849,
                dropoff_lng=-122.4094,
            ),
            provider=state.sim,
            log=log,
            clock=clock,
            session_id=sid,
            turn_id="T",
        )
        prepared = await prepare_booking(
            PrepareArgs(option_id=discovered["options"][0]["option_id"]),
            provider=state.sim,
            log=log,
            clock=clock,
            session_id=sid,
            turn_id="T",
        )
        draft_id = prepared["draft_id"]

        task = asyncio.ensure_future(
            confirm_booking(
                ConfirmArgs(draft_id=draft_id),
                provider=state.sim,
                store=store,
                log=log,
                channel=state.channel,
                clock=clock,
                meter=meter,  # type: ignore[arg-type]
                session_id=sid,
                turn_id="T",
            )
        )
        await _await_pending(state.channel)
        response = await client.post(
            f"/sessions/{sid}/confirmations/{draft_id}",
            json={"approved": True, "approver": "mallory"},
        )
        assert response.status_code == 200  # noqa: S101 pytest assertion
        result = await asyncio.wait_for(task, timeout=5.0)
        assert result["status"] == "confirmed"  # noqa: S101 pytest assertion

        rows = [r for r in log.read(sid) if r["action_id"] == result["action_id"]]
        assert [r["kind"] for r in rows] == ["requested", "verified", "executed", "outcome"]  # noqa: S101 pytest assertion
        confirmation = next(r for r in rows if r["kind"] == "verified")["checks"]["confirmation"]
        assert confirmation["approver"] == "web-user"  # noqa: S101 pytest assertion
        assert confirmation["approver_id"] == state.principal_id  # noqa: S101 pytest assertion
        assert confirmation["channel"] == "web"  # noqa: S101 pytest assertion

        log_files = list((tmp_path / "logs").rglob("*"))
        assert log_files  # noqa: S101 pytest assertion
        for path in log_files:
            if path.is_file():
                assert token not in path.read_text(encoding="utf-8"), path  # noqa: S101 pytest assertion


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
    assert "/events" in route_paths, f"Expected /events route; found: {route_paths}"  # noqa: S101 pytest assertion


def test_get_session_log_returns_empty_for_fresh_session(tmp_path: Path) -> None:
    """GET /sessions/{id}/log returns an empty rows list for a freshly minted session."""
    fastapi_app = create_app(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        geocode_fixtures_dir=tmp_path / "fixtures",
    )
    client = _client(fastapi_app, raise_server_exceptions=True)

    sid = _mint(client)
    response = client.get(f"/sessions/{sid}/log")
    assert response.status_code == 200  # noqa: S101 pytest assertion
    data = response.json()
    assert data["session_id"] == sid  # noqa: S101 pytest assertion
    assert data["rows"] == []  # noqa: S101 pytest assertion


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
    # Entering the TestClient context runs lifespan startup.
    with _client(fastapi_app, raise_server_exceptions=True):
        pass

    # The pending row is still in the DB (startup hook publishes but does NOT pop).
    pending = store.list_pending_confirmations(now=datetime.now(UTC))
    ids = {r.request.subject_id for r in pending}
    assert "draft-startup-test" in ids  # noqa: S101 pytest assertion


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
        with _client(fastapi_app, raise_server_exceptions=True):
            # Lifespan startup runs on __enter__.
            pass
    finally:
        _app_module._bus = original_bus

    assert publish_calls, "Expected at least one publish call from the startup hook"  # noqa: S101 pytest assertion
    keys_used = [call[0] for call in publish_calls]
    # session_id must be used as the bus key, not subject_id
    assert session_id in keys_used  # noqa: S101 pytest assertion
    assert subject_id not in keys_used  # noqa: S101 pytest assertion


def test_get_sim_memoizes_per_session(app: TestClient) -> None:
    """Two turns on the same session use the same UberSimAdapter.

    Regression: a fresh adapter per turn loses `_option_cache`, so the second
    turn's `prepare_booking(option_id=…)` raises `unknown option_id` for an
    option discovered in the first turn.
    """
    sid_a = _mint(app)
    sid_b = _mint(app)

    sim_a1 = _sessions[sid_a].sim
    assert app.post("/chat", json={"session_id": sid_a, "message": "hi"}).status_code == 200  # noqa: S101 pytest assertion
    sim_a2 = _sessions[sid_a].sim
    sim_b = _sessions[sid_b].sim

    assert sim_a1 is sim_a2, "same session must reuse the cached adapter"  # noqa: S101 pytest assertion
    assert sim_a1 is not sim_b, "different sessions must get distinct adapters"  # noqa: S101 pytest assertion


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
        _client(fastapi_app, raise_server_exceptions=True) as client,
    ):
        sid = _mint(client)
        response = client.post(
            "/chat",
            json={"session_id": sid, "message": "confirm then cancel"},
        )
        assert response.status_code == 200  # noqa: S101 pytest assertion

        # The turn runs in a background task on the TestClient's portal loop; poll
        # (bounded) until both gated tools have been reached.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and "cancel_booking_meter" not in captured:
            time.sleep(0.01)

    assert "run_turn_meter" in captured, "run_turn was not called"  # noqa: S101 pytest assertion
    assert "confirm_booking_meter" in captured, "the confirm_booking handler was not reached"  # noqa: S101 pytest assertion
    assert "cancel_booking_meter" in captured, "the cancel_booking handler was not reached"  # noqa: S101 pytest assertion

    session_meter = _sessions[sid].meter
    assert captured["run_turn_meter"] is session_meter  # noqa: S101 pytest assertion
    assert captured["confirm_booking_meter"] is session_meter, (  # noqa: S101 pytest assertion
        "confirm_booking must receive the session's own CostMeter"
    )
    assert captured["cancel_booking_meter"] is session_meter, (  # noqa: S101 pytest assertion
        "cancel_booking must receive the session's own CostMeter"
    )


def test_get_meter_is_stable_per_session_and_distinct_across_sessions(app: TestClient) -> None:
    """Two turns on one session see the same CostMeter; two sessions never share one.

    The route-level test above pins the wiring; this pins the registry itself — a
    shared meter across sessions would cross-attribute one user's spend to another.
    """
    sid_a = _mint(app)
    sid_b = _mint(app)

    meter_a1 = _sessions[sid_a].meter
    assert app.post("/chat", json={"session_id": sid_a, "message": "hi"}).status_code == 200  # noqa: S101 pytest assertion
    meter_a2 = _sessions[sid_a].meter
    meter_b = _sessions[sid_b].meter

    assert meter_a1 is meter_a2, "same session must reuse the cached meter"  # noqa: S101 pytest assertion
    assert meter_a1 is not meter_b, "different sessions must get distinct meters"  # noqa: S101 pytest assertion


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
    client = _client(fastapi_app, raise_server_exceptions=False)

    sid = _mint(client)
    response = client.post(
        "/chat",
        json={"session_id": sid, "message": "hello"},
    )
    # 200 means the background task was kicked off; the agent error (no API key)
    # will be emitted as an SSE error event, not as an HTTP error.
    assert response.status_code == 200  # noqa: S101 pytest assertion
    data = response.json()
    assert data["status"] == "ok"  # noqa: S101 pytest assertion
    assert data["session_id"] == sid  # noqa: S101 pytest assertion


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
    client = _client(fastapi_app, raise_server_exceptions=False)

    idle_sid = _mint(client)
    client.post("/chat", json={"session_id": idle_sid, "message": "hi"})
    assert idle_sid in _sessions  # noqa: S101 pytest assertion

    clock.tick(_SESSION_IDLE_TTL + timedelta(seconds=1))
    # Any subsequent mint or /chat call runs the eviction sweep first.
    _mint(client)

    assert idle_sid not in _sessions, "the whole session record must be gone"  # noqa: S101 pytest assertion


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
    client = _client(fastapi_app, raise_server_exceptions=False)

    kept_sid = _mint(client)
    client.post("/chat", json={"session_id": kept_sid, "message": "hi"})
    clock.tick(timedelta(minutes=30))
    other_sid = _mint(client)
    client.post("/chat", json={"session_id": other_sid, "message": "hi"})

    assert kept_sid in _sessions  # noqa: S101 pytest assertion


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
    client = _client(fastapi_app, raise_server_exceptions=False)

    minted = []
    for _ in range(_MAX_SESSIONS):
        minted.append(_mint(client))
        clock.tick(timedelta(seconds=1))
    assert len(_sessions) == _MAX_SESSIONS  # noqa: S101 pytest assertion

    # A brand-new session pushes the count to _MAX_SESSIONS + 1; the sweep at
    # its entry still saw exactly the cap, so no eviction happens yet.
    newest = _mint(client)
    clock.tick(timedelta(seconds=1))
    assert len(_sessions) == _MAX_SESSIONS + 1  # noqa: S101 pytest assertion

    # Touching an existing session runs the sweep again, which now sees
    # _MAX_SESSIONS + 1 and evicts the LRU session (the first minted, oldest
    # last_used_at) before touching its own state — /chat never grows the dict.
    response = client.post("/chat", json={"session_id": newest, "message": "hi again"})
    assert response.status_code == 200  # noqa: S101 pytest assertion

    assert minted[0] not in _sessions, "least-recently-used session should be evicted first"  # noqa: S101 pytest assertion
    assert newest in _sessions  # noqa: S101 pytest assertion
    assert len(_sessions) == _MAX_SESSIONS  # noqa: S101 pytest assertion


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

    state = _create_session("sess-pinned", store=store, clock=clock)
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
    assert "sess-pinned" in _sessions  # noqa: S101 pytest assertion

    # It must also survive an LRU sweep even once the cap is exceeded — the
    # pinned session has the oldest last_used_at of the lot, so it would be
    # the first LRU candidate if it weren't filtered out.
    for i in range(_MAX_SESSIONS + 5):
        _create_session(f"sess-filler-{i}", store=store, clock=clock)
    _evict_stale_sessions(clock)
    assert "sess-pinned" in _sessions  # noqa: S101 pytest assertion
    assert len(_sessions) <= _MAX_SESSIONS + 1  # +1 for the pinned session itself  # noqa: S101 pytest assertion

    # Clean up: resolve so the awaited task doesn't dangle past the test.
    state.channel.resolve("draft-pinned", approved=False)
    await asyncio.wait_for(task, timeout=2.0)


def test_evicted_session_id_is_rejected_with_403(tmp_path: Path) -> None:
    """Re-using a session id after eviction is refused; the client must mint again."""
    from datetime import UTC, datetime

    from agentic_cab.adapters.clock import FrozenClock

    clock = FrozenClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    fastapi_app = create_app(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        geocode_fixtures_dir=tmp_path / "fixtures",
        clock=clock,
    )
    client = _client(fastapi_app, raise_server_exceptions=False)

    sid = _mint(client)
    assert client.post("/chat", json={"session_id": sid, "message": "hi"}).status_code == 200  # noqa: S101 pytest assertion
    stale_token = client.cookies.get(f"cab_session_8000_{sid}")
    assert stale_token  # noqa: S101 pytest assertion

    clock.tick(_SESSION_IDLE_TTL + timedelta(seconds=1))
    _mint(client)
    assert sid not in _sessions  # noqa: S101 pytest assertion

    # Even presenting the old, once-valid token, the session is gone for good.
    client.cookies.set(f"cab_session_8000_{sid}", stale_token)
    response = client.post("/chat", json={"session_id": sid, "message": "hi again"})
    assert response.status_code == 403  # noqa: S101 pytest assertion
    assert sid not in _sessions  # noqa: S101 pytest assertion
