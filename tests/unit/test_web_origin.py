"""Same-origin check on state-changing web requests (web/origin.py).

A page on another origin (another port on localhost, or a DNS-rebinding page)
must not be able to POST to the local server: not to mint a session, not to
drive `/chat` (quota), and above all not to approve a pending confirmation.
`SameSite=Strict` does not cover the other-port case, because
`localhost:3000` and `localhost:8000` are the same site.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from agentic_cab.core.ports import ConfirmationRequest
from agentic_cab.web.app import _sessions, create_app
from agentic_cab.web.channel import WebConfirmationChannel

_BASE_URL = "http://127.0.0.1:8000"
_OWN_ORIGIN = _BASE_URL
_FOREIGN_ORIGINS = [
    "http://evil.example",
    "null",
    "http://127.0.0.1:3000",
    "https://127.0.0.1:8000",
]
_ROUTES = ["sessions", "chat", "confirmation"]


@pytest.fixture(autouse=True)
def _isolated_sessions() -> Iterator[None]:
    """`web.app._sessions` is process-global; clear it on both sides of every test."""
    _sessions.clear()
    yield
    _sessions.clear()


@pytest.fixture(autouse=True)
def _offline_turns() -> Iterator[None]:
    """POST /chat must never reach the network from this file."""

    async def _stub_run_turn(*_args: object, **_kwargs: object) -> tuple[str, list[Any]]:
        return "ok", []

    with (
        patch("agentic_cab.web.app.Anthropic", return_value=MagicMock()),
        patch("agentic_cab.web.app.run_turn", side_effect=_stub_run_turn),
    ):
        yield


def _new_app(tmp_path: Path) -> FastAPI:
    return create_app(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        geocode_fixtures_dir=tmp_path / "fixtures",
        bind_host=None,
    )


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("AGENTIC_CAB_WEB_HOST", raising=False)
    return TestClient(_new_app(tmp_path), base_url=_BASE_URL)


def _mint(client: TestClient, headers: dict[str, str] | None = None) -> str:
    response = client.post("/sessions", headers=headers)
    assert response.status_code == 200  # noqa: S101 pytest assertion
    return response.json()["session_id"]


def _post(client: TestClient, route: str, headers: dict[str, str]) -> httpx.Response:
    """POST to one of the three state-changing routes, with a valid session where needed."""
    if route == "sessions":
        return client.post("/sessions", headers=headers)
    sid = _mint(client)
    if route == "chat":
        return client.post("/chat", json={"session_id": sid, "message": "hi"}, headers=headers)
    return client.post(
        f"/sessions/{sid}/confirmations/draft-none", json={"approved": True}, headers=headers
    )


# Normal (not origin-related) status of each route for the requests `_post` builds.
_NORMAL_STATUS = {"sessions": 200, "chat": 200, "confirmation": 404}


@pytest.mark.parametrize("origin", _FOREIGN_ORIGINS)
@pytest.mark.parametrize("route", _ROUTES)
def test_foreign_origin_on_post_is_rejected_with_403(
    client: TestClient, route: str, origin: str
) -> None:
    response = _post(client, route, {"origin": origin})
    assert response.status_code == 403  # noqa: S101 pytest assertion
    assert response.json() == {"detail": "forbidden"}  # noqa: S101 pytest assertion


def test_foreign_origin_post_sessions_mints_nothing(client: TestClient) -> None:
    response = client.post("/sessions", headers={"origin": "http://evil.example"})
    assert response.status_code == 403  # noqa: S101 pytest assertion
    assert not _sessions  # noqa: S101 pytest assertion
    assert "set-cookie" not in response.headers  # noqa: S101 pytest assertion


@pytest.mark.parametrize("route", _ROUTES)
def test_same_origin_post_passes(client: TestClient, route: str) -> None:
    response = _post(client, route, {"origin": _OWN_ORIGIN})
    assert response.status_code == _NORMAL_STATUS[route]  # noqa: S101 pytest assertion


@pytest.mark.parametrize("route", _ROUTES)
def test_same_origin_comparison_ignores_case(client: TestClient, route: str) -> None:
    response = _post(client, route, {"origin": _OWN_ORIGIN.upper()})
    assert response.status_code == _NORMAL_STATUS[route]  # noqa: S101 pytest assertion


@pytest.mark.parametrize("route", _ROUTES)
def test_absent_origin_post_passes(client: TestClient, route: str) -> None:
    response = _post(client, route, {})
    assert response.status_code == _NORMAL_STATUS[route]  # noqa: S101 pytest assertion


def test_localhost_host_with_its_own_origin_passes(client: TestClient) -> None:
    headers = {"host": "localhost:8000", "origin": "http://localhost:8000"}
    assert client.post("/sessions", headers=headers).status_code == 200  # noqa: S101 pytest assertion


def test_origin_of_other_allowed_host_is_rejected(client: TestClient) -> None:
    """Own origin is the Host actually used, not any host on the allowlist."""
    headers = {"host": "localhost:8000", "origin": "http://127.0.0.1:8000"}
    assert client.post("/sessions", headers=headers).status_code == 403  # noqa: S101 pytest assertion


def test_get_is_not_origin_checked(client: TestClient) -> None:
    assert client.get("/", headers={"origin": "http://evil.example"}).status_code == 200  # noqa: S101 pytest assertion


def test_foreign_host_checked_before_origin(client: TestClient) -> None:
    headers = {"host": "evil.example", "origin": "http://evil.example"}
    assert client.post("/sessions", headers=headers).status_code == 400  # noqa: S101 pytest assertion


def test_rejection_is_logged_without_origin_or_cookie(client: TestClient) -> None:
    sid = _mint(client)
    token = client.cookies.get(f"cab_session_8000_{sid}")
    assert token  # noqa: S101 pytest assertion
    with capture_logs() as logs:
        response = client.post(
            "/chat",
            json={"session_id": sid, "message": "hi"},
            headers={"origin": "http://evil.example"},
        )
    assert response.status_code == 403  # noqa: S101 pytest assertion
    rejected = [e for e in logs if e["event"] == "web.origin.rejected"]
    assert rejected == [  # noqa: S101 pytest assertion
        {"event": "web.origin.rejected", "log_level": "warning", "method": "POST", "path": "/chat"}
    ]
    assert "evil.example" not in repr(logs)  # noqa: S101 pytest assertion
    assert token not in repr(logs)  # noqa: S101 pytest assertion


# ---------- Gate: a foreign-Origin approval never reaches the channel ----------


async def _await_pending(channel: WebConfirmationChannel) -> None:
    for _ in range(200):
        if channel.has_pending():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("confirmation never became pending")


async def test_foreign_origin_confirmation_leaves_gate_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENTIC_CAB_WEB_HOST", raising=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_new_app(tmp_path)), base_url=_BASE_URL
    ) as client:
        response = await client.post("/sessions")
        assert response.status_code == 200  # noqa: S101 pytest assertion
        sid = response.json()["session_id"]
        channel = _sessions[sid].channel
        task = asyncio.ensure_future(
            channel.request(
                ConfirmationRequest(
                    subject_id="draft-xorigin",
                    subject_kind="draft",
                    summary="Book UberX from A to B",
                    fingerprint="a" * 64,
                    details={},
                    expires_at=datetime.now(UTC) + timedelta(seconds=30),
                )
            )
        )
        await _await_pending(channel)

        response = await client.post(
            f"/sessions/{sid}/confirmations/draft-xorigin",
            json={"approved": True},
            headers={"origin": "http://127.0.0.1:3000"},
        )
        assert response.status_code == 403  # noqa: S101 pytest assertion
        assert channel.has_pending(), "a cross-origin POST must not touch the gate"  # noqa: S101 pytest assertion
        assert not task.done()  # noqa: S101 pytest assertion

        channel.resolve("draft-xorigin", approved=False)
        await asyncio.wait_for(task, timeout=3.0)
