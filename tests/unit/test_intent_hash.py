"""Unit tests for intent_hash + new confirm_idempotency_key. See spec §4.1."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

import pytest

from agentic_cab.core.ids import confirm_idempotency_key, intent_hash
from agentic_cab.core.types import AddressRef, AppointmentIntent, RideIntent


def _addr(name: str, lat: float, lng: float) -> AddressRef:
    return AddressRef(
        raw=name,
        formatted=name,
        lat=lat,
        lng=lng,
        confidence=0.99,
        provider_metadata={"alternates": []},
    )


def _ride(
    pickup: AddressRef | None = None,
    dropoff: AddressRef | None = None,
    when: Literal["now"] | datetime = "now",
    party_size: int = 1,
) -> RideIntent:
    return RideIntent(
        pickup=pickup or _addr("A", 37.0, -122.0),
        dropoff=dropoff or _addr("B", 37.1, -122.1),
        when=when,
        party_size=party_size,
    )


T0 = datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC)


def test_same_intent_same_hash() -> None:
    a = intent_hash(_ride(), "uberx", now=T0)
    b = intent_hash(_ride(), "uberx", now=T0)
    assert a == b  # noqa: S101 pytest assertion
    assert len(a) == 64  # sha256 hex  # noqa: S101 pytest assertion


def test_now_collapses_within_ten_minute_bucket() -> None:
    a = intent_hash(_ride(when="now"), "uberx", now=T0)
    b = intent_hash(_ride(when="now"), "uberx", now=T0.replace(minute=9, second=59))
    assert a == b  # noqa: S101 pytest assertion


def test_now_differs_across_ten_minute_bucket() -> None:
    a = intent_hash(_ride(when="now"), "uberx", now=T0)
    b = intent_hash(_ride(when="now"), "uberx", now=T0.replace(minute=10))
    assert a != b  # noqa: S101 pytest assertion


def test_vehicle_class_changes_hash() -> None:
    a = intent_hash(_ride(), "uberx", now=T0)
    b = intent_hash(_ride(), "comfort", now=T0)
    assert a != b  # noqa: S101 pytest assertion


def test_party_size_changes_hash() -> None:
    a = intent_hash(_ride(party_size=1), "uberx", now=T0)
    b = intent_hash(_ride(party_size=2), "uberx", now=T0)
    assert a != b  # noqa: S101 pytest assertion


def test_pickup_changes_hash() -> None:
    a = intent_hash(_ride(), "uberx", now=T0)
    b = intent_hash(_ride(pickup=_addr("A2", 37.5, -122.0)), "uberx", now=T0)
    assert a != b  # noqa: S101 pytest assertion


def test_dropoff_changes_hash() -> None:
    a = intent_hash(_ride(), "uberx", now=T0)
    b = intent_hash(_ride(dropoff=_addr("B2", 37.5, -122.5)), "uberx", now=T0)
    assert a != b  # noqa: S101 pytest assertion


def test_concrete_when_hashes_to_its_iso_string() -> None:
    fixed = datetime(2026, 6, 1, 15, 30, tzinfo=UTC)
    a = intent_hash(_ride(when=fixed), "uberx", now=T0)
    b = intent_hash(_ride(when=fixed), "uberx", now=T0.replace(hour=23))
    # Concrete `when` ignores `now`; hash stays stable.
    assert a == b  # noqa: S101 pytest assertion


def test_appointment_intent_raises_not_implemented() -> None:
    appt = AppointmentIntent(
        subject="GP",
        earliest_at=T0,
        latest_at=T0.replace(hour=14),
        location=_addr("Clinic", 37.0, -122.0),
    )
    with pytest.raises(NotImplementedError):
        intent_hash(appt, "uberx", now=T0)


# --- new confirm_idempotency_key ---


def test_confirm_idempotency_key_is_deterministic() -> None:
    k1 = confirm_idempotency_key("ih-abc", 0, None)
    k2 = confirm_idempotency_key("ih-abc", 0, None)
    assert k1 == k2  # noqa: S101 pytest assertion
    assert len(k1) == 64  # noqa: S101 pytest assertion


def test_confirm_idempotency_key_changes_with_generation() -> None:
    k1 = confirm_idempotency_key("ih-abc", 0, None)
    k2 = confirm_idempotency_key("ih-abc", 1, None)
    assert k1 != k2  # noqa: S101 pytest assertion


def test_confirm_idempotency_key_changes_with_nonce() -> None:
    k1 = confirm_idempotency_key("ih-abc", 0, None)
    k2 = confirm_idempotency_key("ih-abc", 0, "nonce-1")
    assert k1 != k2  # noqa: S101 pytest assertion


def test_confirm_idempotency_key_changes_with_intent_hash() -> None:
    k1 = confirm_idempotency_key("ih-abc", 0, None)
    k2 = confirm_idempotency_key("ih-xyz", 0, None)
    assert k1 != k2  # noqa: S101 pytest assertion
