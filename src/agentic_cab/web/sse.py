"""SessionEventBus — per-subscriber fan-out of action-log rows and confirmation
prompts to the browser via SSE.

Design:
- Each subscriber (one `/events` stream) gets its own bounded queue; `publish`
  fans a row out to every subscriber of that session, so two tabs on one
  session both receive every event.
- A session entry exists only while it has at least one subscriber. `publish`
  never creates one (no subscribers → no-op), the last `close` deletes it, and
  `close_session` drops it on session eviction. Bus keys are therefore a subset
  of the live sessions, and unknown session ids never grow memory.
- Bounds: at most `_QUEUE_MAX` rows per subscriber and
  `_MAX_SUBSCRIBERS_PER_SESSION` subscribers per session. A subscriber that
  overflows is disconnected (its queue drained, iteration ended) rather than
  silently losing rows; opening one past the cap supersedes the oldest. The
  browser's EventSource reconnects on its own.
- Concurrency: everything runs on the single event loop. `open`, `publish`,
  `close` and `close_session` are synchronous and never await, so check-then-act
  cannot interleave. `publish` iterates a snapshot because an overflow can close
  a subscriber mid-loop; `close` is idempotent.
- The bus is a real-time projection only: rows published while nobody listens
  are not kept. The JSONL action log (logs/<session_id>.jsonl) is the durable
  store; pending confirmation prompts are replayed by the channel.
- Layer: web/* only. Nothing in tools/ or adapters/ may import from here.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from types import TracebackType
from typing import Any, Self

import structlog

_log = structlog.get_logger(__name__)

_QUEUE_MAX = 256
_MAX_SUBSCRIBERS_PER_SESSION = 8

# Private end-of-stream marker; never a row a publisher could send.
_CLOSED: Any = object()


class Subscription:
    """One subscriber's queue. Context manager (closes on exit) and async iterator.

    Iteration ends once the bus closes the subscription (explicit close,
    session eviction, overflow or supersession).
    """

    def __init__(self: Self, bus: SessionEventBus, session_id: str) -> None:
        self._bus = bus
        self.session_id = session_id
        # Unbounded at the asyncio level so the end marker always fits; the
        # `_QUEUE_MAX` row bound is enforced by `_offer`.
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._closed = False
        self._ended = False

    @property
    def closed(self: Self) -> bool:
        return self._closed

    def _offer(self: Self, row: dict[str, Any]) -> bool:
        """Enqueue *row*; False if this subscriber's queue is already full."""
        if self._queue.qsize() >= _QUEUE_MAX:
            return False
        self._queue.put_nowait(row)
        return True

    def _terminate(self: Self) -> None:
        """Discard buffered rows and enqueue the end marker (bus-internal)."""
        self._closed = True
        while not self._queue.empty():
            self._queue.get_nowait()
        self._queue.put_nowait(_CLOSED)

    def __enter__(self: Self) -> Self:
        return self

    def __exit__(
        self: Self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._bus.close(self)

    def __aiter__(self: Self) -> Self:
        return self

    async def __anext__(self: Self) -> dict[str, Any]:
        if self._ended:
            raise StopAsyncIteration
        item = await self._queue.get()
        if item is _CLOSED:
            self._ended = True
            raise StopAsyncIteration
        return item


class SessionEventBus:
    """Fan-out event bus: one bounded queue per subscriber, grouped by session_id."""

    def __init__(self: Self) -> None:
        self._subs: dict[str, list[Subscription]] = {}

    def open(self: Self, session_id: str) -> Subscription:
        """Register and return a new subscription, effective immediately.

        Synchronous so a caller can take a snapshot right after it with no gap.
        Opening past the per-session cap supersedes the oldest subscriber.
        """
        existing = self._subs.get(session_id, [])
        while len(existing) >= _MAX_SUBSCRIBERS_PER_SESSION:
            self._drop(existing[0], reason="superseded")
        sub = Subscription(self, session_id)
        self._subs.setdefault(session_id, []).append(sub)
        return sub

    def close(self: Self, sub: Subscription) -> None:
        """Close *sub* and forget the session once its last subscriber is gone.

        Idempotent: closing an already-closed subscription is a no-op.
        """
        if sub.closed:
            return
        sub._terminate()  # the bus owns the subscription lifecycle
        subs = self._subs.get(sub.session_id)
        if subs is None:
            return
        if sub in subs:
            subs.remove(sub)
        if not subs:
            del self._subs[sub.session_id]

    def close_session(self: Self, session_id: str) -> None:
        """Close every subscription on *session_id* and delete its entry."""
        for sub in tuple(self._subs.get(session_id, ())):
            self.close(sub)
        self._subs.pop(session_id, None)

    def publish(self: Self, session_id: str, row: dict[str, Any]) -> None:
        """Fan *row* out to every subscriber of the session; never blocks.

        With no subscribers this is a no-op and creates no entry. A subscriber
        whose queue is full is disconnected instead of silently losing rows.
        """
        subs = self._subs.get(session_id)
        if not subs:
            return
        for sub in tuple(subs):
            if not sub._offer(row):
                self._drop(sub, reason="overflow")

    def session_count(self: Self) -> int:
        """Number of sessions with at least one live subscriber."""
        return len(self._subs)

    def subscriber_count(self: Self, session_id: str) -> int:
        """Number of live subscribers on *session_id*."""
        return len(self._subs.get(session_id, ()))

    def _drop(self: Self, sub: Subscription, *, reason: str) -> None:
        self.close(sub)
        _log.info("web.sse.subscriber_dropped", session_id=sub.session_id, reason=reason)

    async def subscribe(self: Self, session_id: str) -> AsyncGenerator[dict[str, Any], None]:
        """Yield rows for *session_id* until the subscription is closed.

        Thin wrapper over `open`: the subscription registers when the generator
        first runs and is closed when it finishes or is closed.
        """
        with self.open(session_id) as sub:
            async for row in sub:
                yield row
