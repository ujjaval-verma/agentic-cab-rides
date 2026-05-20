"""Tests for WebConfirmationChannel (adapters/confirmation/web.py)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from agentic_cab.adapters.confirmation.web import WebConfirmationChannel
from agentic_cab.core.ports import ConfirmationChannel, ConfirmationRequest
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


@pytest.mark.asyncio
async def test_request_blocks_until_resolve() -> None:
    bus = SessionEventBus()
    channel = WebConfirmationChannel(bus=bus, session_id=_SESSION)
    req = _make_req()

    # Fire request in background; it should block waiting for resolve().
    task = asyncio.ensure_future(channel.request(req))
    await asyncio.sleep(0)  # yield so the coroutine starts
    assert not task.done()

    # Now resolve it.
    resolved = channel.resolve(req.subject_id, approved=True, approver="test-user")
    assert resolved is True

    result = await asyncio.wait_for(task, timeout=2.0)
    assert result.approved is True
    assert result.reason == "approved"
    assert result.approver == "test-user"


@pytest.mark.asyncio
async def test_request_publishes_pending_confirmation_event() -> None:
    bus = SessionEventBus()
    channel = WebConfirmationChannel(bus=bus, session_id=_SESSION)
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

    assert len(received) == 1
    ev = received[0]
    assert ev["kind"] == "pending_confirmation"
    assert ev["subject_id"] == "draft-pub-test"
    assert ev["subject_kind"] == "draft"
    assert "summary" in ev
    assert "fingerprint" in ev
    assert "expires_at" in ev

    # Clean up
    channel.resolve(req.subject_id, approved=False, approver=None)
    await asyncio.wait_for(request_task, timeout=2.0)


@pytest.mark.asyncio
async def test_request_returns_declined_on_resolve_false() -> None:
    bus = SessionEventBus()
    channel = WebConfirmationChannel(bus=bus, session_id=_SESSION)
    req = _make_req(subject_id="draft-decline")

    task = asyncio.ensure_future(channel.request(req))
    await asyncio.sleep(0)

    channel.resolve(req.subject_id, approved=False, approver=None)
    result = await asyncio.wait_for(task, timeout=2.0)

    assert result.approved is False
    assert result.reason == "declined"
    assert result.approved_at is None


@pytest.mark.asyncio
async def test_request_returns_timeout_on_expiry() -> None:
    bus = SessionEventBus()
    channel = WebConfirmationChannel(bus=bus, session_id=_SESSION)
    # Set expires_at very close in the future so the wait_for fires immediately.
    req = _make_req(subject_id="draft-timeout", seconds_until_expiry=0.05)

    result = await asyncio.wait_for(channel.request(req), timeout=3.0)

    assert result.approved is False
    assert result.reason == "timeout"
    assert result.approved_at is None


@pytest.mark.asyncio
async def test_runtime_checkable_protocol_match() -> None:
    bus = SessionEventBus()
    channel = WebConfirmationChannel(bus=bus, session_id=_SESSION)
    assert isinstance(channel, ConfirmationChannel)
