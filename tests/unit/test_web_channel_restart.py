"""Tests for WebConfirmationChannel durable-pending-row behaviour (T30 restart-survival)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.core.ports import ConfirmationRequest
from agentic_cab.web.channel import WebConfirmationChannel
from agentic_cab.web.sse import SessionEventBus

_SESSION = "sess-restart-test"


def _store(tmp_path: Path) -> SqliteStateStore:
    s = SqliteStateStore(db_path=tmp_path / "state.db")
    s.apply_schema()
    return s


def _req(subject_id: str = "draft-r1", seconds: float = 60.0) -> ConfirmationRequest:
    return ConfirmationRequest(
        subject_id=subject_id,
        subject_kind="draft",
        summary="Book UberX $15.00?",
        fingerprint="b" * 64,
        details={"price_cents": 1500},
        expires_at=datetime.now(UTC) + timedelta(seconds=seconds),
    )


@pytest.mark.asyncio
async def test_request_writes_pending_row(tmp_path: Path) -> None:
    """request() must persist the confirmation row BEFORE awaiting the future."""
    store = _store(tmp_path)
    clock = FrozenClock(start=datetime(2026, 5, 20, 12, 0, tzinfo=UTC))
    bus = SessionEventBus()
    channel = WebConfirmationChannel(bus=bus, session_id=_SESSION, store=store, clock=clock)

    req = _req(subject_id="draft-write-test")

    # Start request() as a background task — it will block awaiting the future.
    task = asyncio.ensure_future(channel.request(req))
    await asyncio.sleep(0)  # yield so request() runs up to the await

    # The durable row should be present while the future is pending.
    pending = store.list_pending_confirmations(now=datetime.now(UTC))
    ids = {r.request.subject_id for r in pending}
    assert "draft-write-test" in ids

    # Clean up: resolve and await.
    channel.resolve(req.subject_id, approved=False, approver=None)
    await asyncio.wait_for(task, timeout=2.0)


@pytest.mark.asyncio
async def test_resolve_clears_pending_row(tmp_path: Path) -> None:
    """After resolve(), the durable row must be gone."""
    store = _store(tmp_path)
    clock = FrozenClock(start=datetime(2026, 5, 20, 12, 0, tzinfo=UTC))
    bus = SessionEventBus()
    channel = WebConfirmationChannel(bus=bus, session_id=_SESSION, store=store, clock=clock)

    req = _req(subject_id="draft-resolve-test")

    task = asyncio.ensure_future(channel.request(req))
    await asyncio.sleep(0)

    channel.resolve(req.subject_id, approved=True, approver="tester")
    await asyncio.wait_for(task, timeout=2.0)

    pending = store.list_pending_confirmations(now=datetime.now(UTC))
    ids = {r.request.subject_id for r in pending}
    assert "draft-resolve-test" not in ids


@pytest.mark.asyncio
async def test_timeout_clears_pending_row(tmp_path: Path) -> None:
    """After a timeout, the durable row must be gone."""
    store = _store(tmp_path)
    clock = FrozenClock(start=datetime(2026, 5, 20, 12, 0, tzinfo=UTC))
    bus = SessionEventBus()
    channel = WebConfirmationChannel(bus=bus, session_id=_SESSION, store=store, clock=clock)

    req = _req(subject_id="draft-timeout-test", seconds=0.05)

    result = await asyncio.wait_for(channel.request(req), timeout=3.0)
    assert result.reason == "timeout"

    pending = store.list_pending_confirmations(now=datetime.now(UTC))
    ids = {r.request.subject_id for r in pending}
    assert "draft-timeout-test" not in ids
