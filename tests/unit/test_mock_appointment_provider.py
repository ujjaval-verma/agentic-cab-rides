"""Unit tests for MockAppointmentProvider — adapter-specific tests.

The contract suite (tests/contract/test_booking_provider_contract.py) covers
Protocol conformance. These tests are adapter-specific behaviour.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.mock_appointment.provider import MockAppointmentProvider
from agentic_cab.core.ports import BookingProvider
from agentic_cab.core.types import AddressRef, AppointmentIntent

_START = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
_LOCATION = AddressRef(
    raw="SF General Hospital",
    formatted="SF General Hospital, San Francisco, CA",
    lat=37.7554,
    lng=-122.4057,
    confidence=1.0,
)
_INTENT = AppointmentIntent(
    subject="GP check-up",
    earliest_at=datetime(2026, 6, 2, 9, 0, tzinfo=UTC),
    latest_at=datetime(2026, 6, 2, 17, 0, tzinfo=UTC),
    location=_LOCATION,
)


@pytest.fixture
def provider() -> MockAppointmentProvider:
    clock = FrozenClock(start=_START)
    return MockAppointmentProvider(clock=clock, rng_seed=42)


def test_isinstance_booking_provider(provider: MockAppointmentProvider) -> None:
    """runtime_checkable Protocol check — invariant."""
    assert isinstance(provider, BookingProvider)  # noqa: S101 pytest assertion


@pytest.mark.asyncio
async def test_discover_returns_at_least_one_option_for_valid_intent(
    provider: MockAppointmentProvider,
) -> None:
    options = await provider.discover(_INTENT)
    assert len(options) >= 1  # noqa: S101 pytest assertion
    assert len(options) <= 3  # noqa: S101 pytest assertion
    # All options belong to this provider
    assert all(o.provider == "mock_appointment" for o in options)  # noqa: S101 pytest assertion
    # All prices are positive and in USD
    assert all(o.price.amount_cents > 0 for o in options)  # noqa: S101 pytest assertion
    assert all(o.price.currency == "USD" for o in options)  # noqa: S101 pytest assertion


@pytest.mark.asyncio
async def test_discover_is_deterministic_for_same_intent(
    provider: MockAppointmentProvider,
) -> None:
    opts1 = await provider.discover(_INTENT)
    opts2 = await provider.discover(_INTENT)
    # Same number of options (seeded from intent)
    assert len(opts1) == len(opts2)  # noqa: S101 pytest assertion
    # Same titles (same clinics/slots in same order)
    assert [o.title for o in opts1] == [o.title for o in opts2]  # noqa: S101 pytest assertion


@pytest.mark.asyncio
async def test_prepare_returns_draft_for_known_option(
    provider: MockAppointmentProvider,
) -> None:
    options = await provider.discover(_INTENT)
    opt = options[0]
    draft = await provider.prepare(opt.option_id)
    assert draft.option_snapshot == opt  # noqa: S101 pytest assertion
    assert draft.fingerprint != ""  # noqa: S101 pytest assertion


@pytest.mark.asyncio
async def test_prepare_raises_for_unknown_option(
    provider: MockAppointmentProvider,
) -> None:
    with pytest.raises(ValueError, match="unknown option_id"):
        await provider.prepare("no-such-opt")


@pytest.mark.asyncio
async def test_confirm_idempotency_replays_same_booking(
    provider: MockAppointmentProvider,
) -> None:
    opts = await provider.discover(_INTENT)
    draft = await provider.prepare(opts[0].option_id)
    key = "idem-key-abc"
    b1 = await provider.confirm(draft.draft_id, idempotency_key=key)
    b2 = await provider.confirm(draft.draft_id, idempotency_key=key)
    assert b1.booking_id == b2.booking_id  # noqa: S101 pytest assertion


@pytest.mark.asyncio
async def test_cancel_fee_is_zero_for_terminal_bookings(
    provider: MockAppointmentProvider,
) -> None:
    opts = await provider.discover(_INTENT)
    draft = await provider.prepare(opts[0].option_id)
    booking = await provider.confirm(draft.draft_id, idempotency_key="K-cancel")
    # Cancel it first
    await provider.cancel(booking.booking_id, idempotency_key="K-cancel2")
    # Now quote fee — terminal bookings return $0
    fee = await provider.quote_cancel_fee(booking.booking_id)
    assert fee.amount_cents == 0  # noqa: S101 pytest assertion


@pytest.mark.asyncio
async def test_cancel_idempotency_no_double_cancel(
    provider: MockAppointmentProvider,
) -> None:
    opts = await provider.discover(_INTENT)
    draft = await provider.prepare(opts[0].option_id)
    booking = await provider.confirm(draft.draft_id, idempotency_key="K-conf")
    b1 = await provider.cancel(booking.booking_id, idempotency_key="K-cancel")
    b2 = await provider.cancel(booking.booking_id, idempotency_key="K-cancel")
    assert b1.booking_id == b2.booking_id  # noqa: S101 pytest assertion
    assert b2.status == "cancelled"  # noqa: S101 pytest assertion
