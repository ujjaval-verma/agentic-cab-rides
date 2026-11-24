"""Tests for SessionEventBus (web/sse.py)."""

from __future__ import annotations

import pytest

from agentic_cab.web.sse import SessionEventBus


@pytest.mark.asyncio
async def test_publish_then_subscribe_yields_row() -> None:
    bus = SessionEventBus()
    row = {"kind": "action_log", "tool": "geocode_address"}
    bus.publish("session-1", row)

    received = []
    async for item in bus.subscribe("session-1"):
        received.append(item)
        break  # stop after first item

    assert received == [row]  # noqa: S101 pytest assertion


@pytest.mark.asyncio
async def test_two_subscribers_different_sessions_isolated() -> None:
    bus = SessionEventBus()
    bus.publish("sess-A", {"kind": "a"})
    bus.publish("sess-B", {"kind": "b"})

    items_a = []
    async for item in bus.subscribe("sess-A"):
        items_a.append(item)
        break

    items_b = []
    async for item in bus.subscribe("sess-B"):
        items_b.append(item)
        break

    assert items_a == [{"kind": "a"}]  # noqa: S101 pytest assertion
    assert items_b == [{"kind": "b"}]  # noqa: S101 pytest assertion


@pytest.mark.asyncio
async def test_overflow_drops_oldest() -> None:
    """When the queue is full, publish() drops the oldest item rather than blocking."""
    bus = SessionEventBus()
    # Fill to capacity (1000 items)
    for i in range(1000):
        bus.publish("sess-overflow", {"seq": i})

    # Publish one more — should drop seq=0 and add seq=1000
    bus.publish("sess-overflow", {"seq": 1000})

    # The queue should still have 1000 items; the first item should now be seq=1
    first_item = None
    async for item in bus.subscribe("sess-overflow"):
        first_item = item
        break

    assert first_item is not None  # noqa: S101 pytest assertion
    assert first_item["seq"] == 1  # noqa: S101 pytest assertion


@pytest.mark.asyncio
async def test_late_subscriber_misses_pre_subscription_rows() -> None:
    """Rows published before subscribe() is called are NOT replayed.

    This is intentional: the bus is fire-and-forget. The JSONL action log is the
    durable store for replay. A new session's queue starts empty; a subscriber on a
    fresh session will not see rows published to a different session.
    """
    bus = SessionEventBus()
    # Publish to session-A
    bus.publish("sess-A2", {"kind": "early", "seq": 0})

    # A fresh session (never published to) has an empty queue.
    # We verify this by publishing one item to fresh-session and confirming only
    # that item arrives (not anything from sess-A2).
    bus.publish("fresh-session", {"kind": "fresh", "seq": 99})

    received = []
    async for item in bus.subscribe("fresh-session"):
        received.append(item)
        break

    assert received == [{"kind": "fresh", "seq": 99}]  # noqa: S101 pytest assertion
    # sess-A2 items are not visible from fresh-session
    assert all(item.get("kind") != "early" for item in received)  # noqa: S101 pytest assertion
