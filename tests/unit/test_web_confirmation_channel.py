"""Tests for WebConfirmationChannel (web/channel.py)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from agentic_cab.core.ids import new_principal_id
from agentic_cab.core.ports import ConfirmationChannel, ConfirmationRequest
from agentic_cab.web.channel import WebConfirmationChannel
from agentic_cab.web.sse import SessionEventBus

_SESSION = "sess-web-confirm"


def _make_req(
    *, subject_id: str = "draft-001", seconds_until_expiry: float = 60.0
) -> ConfirmationRequest:
    return ConfirmationRequest(
        subject_id=subject_id,
        subject_kind="draft",
        summary="Book UberX $15.00 from A to B",
        fingerprint="a" * 64,
        details={},
        expires_at=datetime.now(UTC) + timedelta(seconds=seconds_until_expiry),
    )


def _channel(bus: SessionEventBus, principal_id: str | None = None) -> WebConfirmationChannel:
    return WebConfirmationChannel(
        bus=bus, session_id=_SESSION, principal_id=principal_id or new_principal_id()
    )


@pytest.mark.asyncio
async def test_request_blocks_until_resolve() -> None:
    bus = SessionEventBus()
    principal_id = new_principal_id()
    channel = _channel(bus, principal_id)
    req = _make_req()

    # Fire request in background; it should block waiting for resolve().
    task = asyncio.ensure_future(channel.request(req))
    await asyncio.sleep(0)  # yield so the coroutine starts
    assert not task.done()  # noqa: S101 pytest assertion

    # Now resolve it.
    resolved = channel.resolve(req.subject_id, approved=True)
    assert resolved is True  # noqa: S101 pytest assertion

    result = await asyncio.wait_for(task, timeout=2.0)
    assert result.approved is True  # noqa: S101 pytest assertion
    assert result.reason == "approved"  # noqa: S101 pytest assertion
    assert result.approver == "web-user"  # noqa: S101 pytest assertion
    assert result.approver_id == principal_id  # noqa: S101 pytest assertion
    assert result.channel == "web"  # noqa: S101 pytest assertion


def test_resolve_rejects_client_supplied_approver() -> None:
    """Invariant 2: no approver value can be passed in from the request surface."""
    channel = _channel(SessionEventBus())
    with pytest.raises(TypeError):
        channel.resolve("draft-x", approved=True, approver="mallory")  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_request_publishes_pending_confirmation_event() -> None:
    bus = SessionEventBus()
    channel = _channel(bus)
    req = _make_req(subject_id="draft-pub-test")

    # Subscribe BEFORE calling request so we don't miss the event.
    received: list[dict] = []

    async def _collect() -> None:
        async for row in bus.subscribe(_SESSION):
            received.append(row)
            break

    collector = asyncio.ensure_future(_collect())
    await asyncio.sleep(0)

    # Kick off request (it will publish and then block).
    request_task = asyncio.ensure_future(channel.request(req))
    await asyncio.sleep(0)

    # The collector should now have received the pending_confirmation event.
    await asyncio.wait_for(collector, timeout=2.0)

    assert len(received) == 1  # noqa: S101 pytest assertion
    ev = received[0]
    assert ev["kind"] == "pending_confirmation"  # noqa: S101 pytest assertion
    assert ev["subject_id"] == "draft-pub-test"  # noqa: S101 pytest assertion
    assert ev["subject_kind"] == "draft"  # noqa: S101 pytest assertion
    assert "summary" in ev  # noqa: S101 pytest assertion
    assert "fingerprint" in ev  # noqa: S101 pytest assertion
    assert "expires_at" in ev  # noqa: S101 pytest assertion

    # Clean up
    channel.resolve(req.subject_id, approved=False)
    await asyncio.wait_for(request_task, timeout=2.0)


@pytest.mark.asyncio
async def test_request_returns_declined_on_resolve_false() -> None:
    bus = SessionEventBus()
    channel = _channel(bus)
    req = _make_req(subject_id="draft-decline")

    task = asyncio.ensure_future(channel.request(req))
    await asyncio.sleep(0)

    channel.resolve(req.subject_id, approved=False)
    result = await asyncio.wait_for(task, timeout=2.0)

    assert result.approved is False  # noqa: S101 pytest assertion
    assert result.reason == "declined"  # noqa: S101 pytest assertion
    assert result.approved_at is None  # noqa: S101 pytest assertion
    assert result.approver_id is None  # noqa: S101 pytest assertion
    assert result.channel == "web"  # noqa: S101 pytest assertion


@pytest.mark.asyncio
async def test_request_returns_timeout_on_expiry() -> None:
    bus = SessionEventBus()
    channel = _channel(bus)
    # Set expires_at very close in the future so the wait_for fires immediately.
    req = _make_req(subject_id="draft-timeout", seconds_until_expiry=0.05)

    result = await asyncio.wait_for(channel.request(req), timeout=3.0)

    assert result.approved is False  # noqa: S101 pytest assertion
    assert result.reason == "timeout"  # noqa: S101 pytest assertion
    assert result.approved_at is None  # noqa: S101 pytest assertion
    assert result.approver_id is None  # noqa: S101 pytest assertion
    assert result.channel == "web"  # noqa: S101 pytest assertion


@pytest.mark.asyncio
async def test_runtime_checkable_protocol_match() -> None:
    bus = SessionEventBus()
    channel = _channel(bus)
    assert isinstance(channel, ConfirmationChannel)  # noqa: S101 pytest assertion
