import pytest
from pydantic import ValidationError

from agentic_cab.core.types import (
    AddressRef,
    Money,
    RideIntent,
    RideOptionDetails,
)


def test_money_is_integer_cents_only():
    m = Money(amount_cents=1234, currency="USD")
    assert m.amount_cents == 1234
    with pytest.raises(ValidationError):
        Money(amount_cents=12.34, currency="USD")  # type: ignore[arg-type]


def test_ride_intent_defaults():
    intent = RideIntent(
        pickup=AddressRef(raw="A", formatted="A", lat=0.0, lng=0.0, confidence=1.0),
        dropoff=AddressRef(raw="B", formatted="B", lat=0.0, lng=0.0, confidence=1.0),
        when="now",
    )
    assert intent.kind == "ride"
    assert intent.party_size == 1


def test_option_details_discriminated():
    details = RideOptionDetails(vehicle_class="uberx", capacity=4)
    assert details.kind == "ride"
