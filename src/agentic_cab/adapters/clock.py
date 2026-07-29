from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Self

# Fixed step (nanoseconds) FrozenClock.monotonic_ns() advances by per call, so
# latency measurements taken under FrozenClock stay deterministic (CLAUDE.md inv. 9)
# instead of reading a real wall clock.
_FROZEN_MONOTONIC_STEP_NS = 1_000_000  # 1ms


class SystemClock:
    def now(self: Self) -> datetime:
        return datetime.now(UTC)

    def monotonic_ns(self: Self) -> int:
        return time.monotonic_ns()


class FrozenClock:
    def __init__(self: Self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("FrozenClock requires tz-aware datetime")
        self._t = start
        self._monotonic_ns = 0

    def now(self: Self) -> datetime:
        return self._t

    def tick(self: Self, delta: timedelta) -> None:
        self._t = self._t + delta

    def monotonic_ns(self: Self) -> int:
        """Return a deterministic, monotonically increasing counter.

        Advances by a fixed step per call rather than reading the real clock, so
        latency_ms computed from two calls under FrozenClock is reproducible
        across re-runs (CLAUDE.md inv. 9).
        """
        self._monotonic_ns += _FROZEN_MONOTONIC_STEP_NS
        return self._monotonic_ns
