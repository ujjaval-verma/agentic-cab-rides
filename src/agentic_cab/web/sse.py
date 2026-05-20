"""SessionEventBus — one asyncio.Queue per session, used to stream action-log rows and
pending-confirmation prompts to the browser via SSE.

Design:
- Bounded queue (maxsize=1000): if a subscriber is too slow, oldest items are dropped
  on publish so the server never blocks waiting for a slow client.
- The bus is fire-and-forget: a late subscriber misses rows that were published before
  it subscribed. The JSONL action log (logs/<session_id>.jsonl) is the durable store;
  the bus is only for real-time delivery.
- Layer: web/* only. Nothing in tools/ or adapters/ may import from here.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any, Self

_QUEUE_MAX = 1000


class SessionEventBus:
    """Fan-out event bus: one queue per session_id."""

    def __init__(self: Self) -> None:
        self._queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}

    def _get_or_create(self: Self, session_id: str) -> asyncio.Queue[dict[str, Any]]:
        if session_id not in self._queues:
            self._queues[session_id] = asyncio.Queue(maxsize=_QUEUE_MAX)
        return self._queues[session_id]

    def publish(self: Self, session_id: str, row: dict[str, Any]) -> None:
        """Push *row* to the session queue.

        If the queue is full, the oldest item is dropped so this call never blocks.
        """
        q = self._get_or_create(session_id)
        if q.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                q.get_nowait()  # drop oldest
        with contextlib.suppress(asyncio.QueueFull):
            q.put_nowait(row)  # concurrent overflow race — silently skip

    async def subscribe(self: Self, session_id: str) -> AsyncIterator[dict[str, Any]]:
        """Yield rows from the session queue as they arrive.

        A subscriber that calls this after rows were published will miss those rows
        (fire-and-forget; the JSONL log is durable). The iterator runs until the
        caller breaks or the generator is garbage-collected.
        """
        q = self._get_or_create(session_id)
        while True:
            row = await q.get()
            yield row
