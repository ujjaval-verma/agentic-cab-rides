"""Pydantic v2 contracts shared across all layers. See spec §4."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt


class Money(BaseModel):
    """Integer cents only. No floats for money — spec §4 + CLAUDE.md invariant 10."""

    model_config = ConfigDict(frozen=True)
    amount_cents: StrictInt
    currency: str


class AddressRef(BaseModel):
    model_config = ConfigDict(frozen=True)
    raw: str
    formatted: str
    lat: float
    lng: float
    confidence: float  # [0.0, 1.0]; <0.5 is the bad-address signal (P2/P3)


class RideIntent(BaseModel):
    kind: Literal["ride"] = "ride"
    pickup: AddressRef
    dropoff: AddressRef
    when: Literal["now"] | datetime
    party_size: int = 1
    vehicle_class_preference: list[str] | None = None


Intent = RideIntent  # union widens in P3+ to | AppointmentIntent


class RideOptionDetails(BaseModel):
    kind: Literal["ride"] = "ride"
    vehicle_class: str  # "uberx" | "comfort" | "uberxl" | "black" — P1 only ships uberx
    capacity: int


OptionDetails = Annotated[RideOptionDetails, Field(discriminator="kind")]


class Option(BaseModel):
    model_config = ConfigDict(frozen=True)
    option_id: str
    provider: str
    title: str
    price: Money
    eta_seconds: int | None
    when: datetime | None
    details: OptionDetails
    provider_metadata: dict[str, object] = Field(default_factory=dict)
    expires_at: datetime


class Draft(BaseModel):
    model_config = ConfigDict(frozen=True)
    draft_id: str
    option_snapshot: Option
    fingerprint: str
    created_at: datetime
    expires_at: datetime


class BookingEvent(BaseModel):
    ts: datetime
    kind: str
    payload: dict[str, object]


class CancellationInfo(BaseModel):
    cancelled_at: datetime
    fee: Money | None
    reason: str | None


class Booking(BaseModel):
    booking_id: str
    draft_id: str
    status: Literal[
        "confirmed",
        "driver_assigned",
        "en_route",
        "arrived",
        "in_progress",
        "completed",
        "cancelled",
        "failed",
    ]
    provider: str
    summary: Option
    events: list[BookingEvent] = Field(default_factory=list[BookingEvent])
    cancellation: CancellationInfo | None = None
