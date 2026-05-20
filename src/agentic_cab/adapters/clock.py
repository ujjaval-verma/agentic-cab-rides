from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Self


class SystemClock:
    def now(self: Self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    def __init__(self: Self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("FrozenClock requires tz-aware datetime")
        self._t = start

    def now(self: Self) -> datetime:
        return self._t

    def tick(self: Self, delta: timedelta) -> None:
        self._t = self._t + delta
