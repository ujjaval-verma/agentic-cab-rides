"""Contract test fixtures for the BookingProvider parametrized suite.

Each params entry is a provider name. The fixture returns a (provider, intent_factory)
tuple:
  - provider: a BookingProvider instance backed by an isolated in-memory or SQLite store
  - intent_factory: callable() -> Intent that returns a valid intent for that provider

UberSimAdapter uses a SQLite-backed SqliteStateStore (tmp_path per test).
MockAppointmentProvider is entirely in-memory.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.mock_appointment.provider import MockAppointmentProvider
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.adapters.uber_sim.provider import UberSimAdapter
from agentic_cab.core.ports import BookingProvider
from agentic_cab.core.types import AddressRef, AppointmentIntent, Intent, RideIntent

_START = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _ride_intent_factory() -> Intent:
    return RideIntent(
        pickup=AddressRef(raw="A", formatted="A", lat=37.7749, lng=-122.4194, confidence=1.0),
        dropoff=AddressRef(raw="B", formatted="B", lat=37.7849, lng=-122.4094, confidence=1.0),
        when="now",
    )


def _appointment_intent_factory() -> Intent:
    return AppointmentIntent(
        subject="GP check-up",
        earliest_at=datetime(2026, 6, 2, 9, 0, tzinfo=UTC),
        latest_at=datetime(2026, 6, 2, 17, 0, tzinfo=UTC),
        location=AddressRef(
            raw="SF General",
            formatted="SF General Hospital",
            lat=37.7554,
            lng=-122.4057,
            confidence=1.0,
        ),
    )


def _make_uber_sim(tmp_path: Path) -> BookingProvider:
    store = SqliteStateStore(db_path=tmp_path / "uber.db")
    store.apply_schema()
    clock = FrozenClock(start=_START)
    return UberSimAdapter(store=store, clock=clock, rng_seed=42)


def _make_mock_appointment() -> BookingProvider:
    clock = FrozenClock(start=_START)
    return MockAppointmentProvider(clock=clock, rng_seed=42)


@pytest.fixture(
    params=["uber_sim", "mock_appointment"],
)
def provider_fixture(request: Any, tmp_path: Path) -> tuple[BookingProvider, Callable[[], Intent]]:
    """Return (provider, intent_factory) for each registered BookingProvider."""
    name: str = request.param
    if name == "uber_sim":
        return _make_uber_sim(tmp_path), _ride_intent_factory
    if name == "mock_appointment":
        return _make_mock_appointment(), _appointment_intent_factory
    raise ValueError(f"unknown provider param: {name!r}")
