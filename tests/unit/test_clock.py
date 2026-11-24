from datetime import UTC, datetime, timedelta

from agentic_cab.adapters.clock import FrozenClock, SystemClock


def test_frozen_clock_does_not_advance():
    c = FrozenClock(start=datetime(2026, 5, 19, 12, 0, tzinfo=UTC))
    assert c.now() == datetime(2026, 5, 19, 12, 0, tzinfo=UTC)  # noqa: S101 pytest assertion
    assert c.now() == datetime(2026, 5, 19, 12, 0, tzinfo=UTC)  # noqa: S101 pytest assertion


def test_frozen_clock_tick():
    c = FrozenClock(start=datetime(2026, 5, 19, 12, 0, tzinfo=UTC))
    c.tick(timedelta(minutes=5))
    assert c.now() == datetime(2026, 5, 19, 12, 5, tzinfo=UTC)  # noqa: S101 pytest assertion


def test_system_clock_is_utc_aware():
    c = SystemClock()
    assert c.now().tzinfo is not None  # noqa: S101 pytest assertion
