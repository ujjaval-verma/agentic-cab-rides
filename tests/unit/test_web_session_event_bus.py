"""Tests for SessionEventBus (web/sse.py): per-subscriber fan-out and lifecycle."""

from __future__ import annotations

import asyncio
from typing import Any

from agentic_cab.web.sse import (
    _MAX_SUBSCRIBERS_PER_SESSION,
    _QUEUE_MAX,
    SessionEventBus,
    Subscription,
)


async def _take(sub: Subscription, n: int) -> list[dict[str, Any]]:
    """Read exactly *n* rows from *sub*, failing fast instead of hanging."""
    return [await asyncio.wait_for(anext(sub), timeout=2.0) for _ in range(n)]


async def _drain_to_end(sub: Subscription) -> list[dict[str, Any]]:
    """Collect every row left on *sub* until its iteration ends (bounded)."""

    async def _collect() -> list[dict[str, Any]]:
        return [row async for row in sub]

    return await asyncio.wait_for(_collect(), timeout=2.0)


async def test_two_subscribers_on_one_session_both_receive_every_event() -> None:
    bus = SessionEventBus()
    rows = [{"seq": i} for i in range(3)]
    with bus.open("sess") as a, bus.open("sess") as b:
        for row in rows:
            bus.publish("sess", row)
        assert await _take(a, 3) == rows  # noqa: S101 pytest assertion
        assert await _take(b, 3) == rows  # noqa: S101 pytest assertion


async def test_publish_with_no_subscribers_creates_no_entry() -> None:
    bus = SessionEventBus()
    for i in range(1000):
        bus.publish(f"unknown-{i}", {"seq": i})
    assert bus.session_count() == 0  # noqa: S101 pytest assertion
    assert bus.subscriber_count("unknown-0") == 0  # noqa: S101 pytest assertion


async def test_closing_last_subscriber_removes_session_entry() -> None:
    bus = SessionEventBus()
    a = bus.open("sess")
    b = bus.open("sess")
    assert bus.session_count() == 1  # noqa: S101 pytest assertion
    assert bus.subscriber_count("sess") == 2  # noqa: S101 pytest assertion

    bus.close(a)
    assert bus.subscriber_count("sess") == 1  # noqa: S101 pytest assertion
    assert bus.session_count() == 1  # noqa: S101 pytest assertion

    with b:
        pass  # leaving the context closes the subscription
    assert bus.subscriber_count("sess") == 0  # noqa: S101 pytest assertion
    assert bus.session_count() == 0  # noqa: S101 pytest assertion


async def test_close_ends_a_waiting_iteration() -> None:
    bus = SessionEventBus()
    sub = bus.open("sess")
    waiter = asyncio.ensure_future(_drain_to_end(sub))
    await asyncio.sleep(0)
    bus.close(sub)
    assert await asyncio.wait_for(waiter, timeout=2.0) == []  # noqa: S101 pytest assertion


async def test_sessions_are_isolated() -> None:
    bus = SessionEventBus()
    with bus.open("sess-A") as a, bus.open("sess-B") as b:
        bus.publish("sess-A", {"kind": "a"})
        bus.publish("sess-B", {"kind": "b"})
        assert await _take(a, 1) == [{"kind": "a"}]  # noqa: S101 pytest assertion
        assert await _take(b, 1) == [{"kind": "b"}]  # noqa: S101 pytest assertion
        bus.close(a)
        bus.close(b)
        # Nothing else was queued on either side.
        assert await _drain_to_end(a) == []  # noqa: S101 pytest assertion
        assert await _drain_to_end(b) == []  # noqa: S101 pytest assertion


async def test_overflow_disconnects_only_the_slow_subscriber() -> None:
    bus = SessionEventBus()
    slow = bus.open("sess")
    fast = bus.open("sess")

    for i in range(_QUEUE_MAX):
        bus.publish("sess", {"seq": i})
    # The fast subscriber keeps up; the slow one never reads.
    assert len(await _take(fast, _QUEUE_MAX)) == _QUEUE_MAX  # noqa: S101 pytest assertion
    assert bus.subscriber_count("sess") == 2  # noqa: S101 pytest assertion

    bus.publish("sess", {"seq": _QUEUE_MAX})  # one past the slow queue's bound

    assert bus.subscriber_count("sess") == 1  # noqa: S101 pytest assertion
    assert await _drain_to_end(slow) == []  # noqa: S101 pytest assertion
    assert await _take(fast, 1) == [{"seq": _QUEUE_MAX}]  # noqa: S101 pytest assertion

    bus.publish("sess", {"seq": "after"})
    assert await _take(fast, 1) == [{"seq": "after"}]  # noqa: S101 pytest assertion
    bus.close(fast)
    assert bus.session_count() == 0  # noqa: S101 pytest assertion


async def test_subscriber_cap_supersedes_oldest() -> None:
    bus = SessionEventBus()
    subs = [bus.open("sess") for _ in range(_MAX_SUBSCRIBERS_PER_SESSION)]
    assert bus.subscriber_count("sess") == _MAX_SUBSCRIBERS_PER_SESSION  # noqa: S101 pytest assertion

    newest = bus.open("sess")
    assert bus.subscriber_count("sess") == _MAX_SUBSCRIBERS_PER_SESSION  # noqa: S101 pytest assertion
    assert await _drain_to_end(subs[0]) == []  # noqa: S101 pytest assertion

    bus.publish("sess", {"kind": "x"})
    assert await _take(newest, 1) == [{"kind": "x"}]  # noqa: S101 pytest assertion
    assert await _take(subs[1], 1) == [{"kind": "x"}]  # noqa: S101 pytest assertion

    bus.close_session("sess")
    assert bus.session_count() == 0  # noqa: S101 pytest assertion


async def test_close_is_idempotent() -> None:
    bus = SessionEventBus()
    a = bus.open("sess")
    b = bus.open("sess")
    bus.close(a)
    bus.close(a)
    assert bus.subscriber_count("sess") == 1  # noqa: S101 pytest assertion

    bus.close_session("sess")
    bus.close_session("sess")
    bus.close(b)
    with b:
        pass
    assert bus.session_count() == 0  # noqa: S101 pytest assertion
    assert await _drain_to_end(b) == []  # noqa: S101 pytest assertion


async def test_close_session_ends_every_subscription() -> None:
    bus = SessionEventBus()
    a = bus.open("sess")
    b = bus.open("sess")
    other = bus.open("other")
    bus.close_session("sess")
    assert await _drain_to_end(a) == []  # noqa: S101 pytest assertion
    assert await _drain_to_end(b) == []  # noqa: S101 pytest assertion
    assert bus.session_count() == 1  # noqa: S101 pytest assertion
    assert bus.subscriber_count("other") == 1  # noqa: S101 pytest assertion
    bus.close(other)


async def test_subscribe_wrapper_yields_rows_and_cleans_up() -> None:
    bus = SessionEventBus()
    gen = bus.subscribe("sess")
    first = asyncio.ensure_future(anext(gen))
    for _ in range(100):
        if bus.subscriber_count("sess") == 1:
            break
        await asyncio.sleep(0)
    bus.publish("sess", {"kind": "a"})
    assert await asyncio.wait_for(first, timeout=2.0) == {"kind": "a"}  # noqa: S101 pytest assertion
    await gen.aclose()
    assert bus.session_count() == 0  # noqa: S101 pytest assertion
