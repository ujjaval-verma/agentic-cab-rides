from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from agentic_cab.core.types import (
    AddressRef,
    AppointmentIntent,
    AppointmentOptionDetails,
    Intent,
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


def _location() -> AddressRef:
    return AddressRef(raw="Clinic A", formatted="Clinic A", lat=37.77, lng=-122.41, confidence=1.0)


def test_appointment_intent_validates_required_fields():
    intent = AppointmentIntent(
        subject="GP check-up",
        earliest_at=datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
        latest_at=datetime(2026, 6, 1, 17, 0, tzinfo=UTC),
        location=_location(),
    )
    assert intent.kind == "appointment"
    assert intent.subject == "GP check-up"


def test_appointment_intent_missing_subject_raises():
    with pytest.raises(ValidationError):
        AppointmentIntent(  # type: ignore[call-arg]
            earliest_at=datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
            latest_at=datetime(2026, 6, 1, 17, 0, tzinfo=UTC),
            location=_location(),
        )


_intent_adapter: TypeAdapter[Intent] = TypeAdapter(Intent)


def test_intent_union_discriminator_routes_ride():
    raw = {
        "kind": "ride",
        "pickup": {"raw": "A", "formatted": "A", "lat": 0.0, "lng": 0.0, "confidence": 1.0},
        "dropoff": {"raw": "B", "formatted": "B", "lat": 0.0, "lng": 0.0, "confidence": 1.0},
        "when": "now",
    }
    intent = _intent_adapter.validate_python(raw)
    assert isinstance(intent, RideIntent)
    assert intent.kind == "ride"


def test_intent_union_discriminator_routes_appointment():
    raw = {
        "kind": "appointment",
        "subject": "GP check-up",
        "earliest_at": "2026-06-01T09:00:00+00:00",
        "latest_at": "2026-06-01T17:00:00+00:00",
        "location": {"raw": "C", "formatted": "C", "lat": 37.77, "lng": -122.41, "confidence": 1.0},
    }
    intent = _intent_adapter.validate_python(raw)
    assert isinstance(intent, AppointmentIntent)
    assert intent.kind == "appointment"


def test_appointment_option_details_discriminated():
    details = AppointmentOptionDetails(
        clinic="SF General",
        slot_at=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
        specialty="general",
    )
    assert details.kind == "appointment"
