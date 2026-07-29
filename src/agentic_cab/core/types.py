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


# Why microcents and not cents: a single model call costs a fraction of a cent, so
# integer cents would round nearly every call to zero and the ledger would report 0.
# Microcents (1 cent = 1_000_000 microcents) keep the arithmetic exact and integer —
# this honours CLAUDE.md invariant 10 rather than carving a float exception into it.
# Conversion to Money happens only at a render boundary, after summing.
class LLMCall(BaseModel):
    """One model API call, priced. Cost is integer microcents — see CLAUDE.md invariant 10."""

    model_config = ConfigDict(frozen=True)
    provider: str
    model: str
    input_tokens: StrictInt
    output_tokens: StrictInt
    cache_read_input_tokens: StrictInt = 0
    cache_creation_input_tokens: StrictInt = 0
    cost_microcents: StrictInt
    latency_ms: StrictInt


class AddressRef(BaseModel):
    model_config = ConfigDict(frozen=True)
    raw: str
    formatted: str
    lat: float
    lng: float
    confidence: float  # [0.0, 1.0]; <0.5 is the bad-address signal (P2/P3)
    provider_metadata: dict[str, object] = Field(default_factory=dict)
    # Low-confidence structural signal (spec §10): confidence < 0.5 AND
    # provider_metadata["alternates"] is a non-empty list of candidate dicts.
    # High-confidence results have provider_metadata={"alternates": []} (present, empty).


class RideIntent(BaseModel):
    kind: Literal["ride"] = "ride"
    pickup: AddressRef
    dropoff: AddressRef
    when: Literal["now"] | datetime
    party_size: int = 1
    vehicle_class_preference: list[str] | None = None


class AppointmentIntent(BaseModel):
    kind: Literal["appointment"] = "appointment"
    subject: str  # e.g. "GP check-up"
    earliest_at: datetime
    latest_at: datetime
    location: AddressRef


Intent = Annotated[
    RideIntent | AppointmentIntent,
    Field(discriminator="kind"),
]


class RideOptionDetails(BaseModel):
    kind: Literal["ride"] = "ride"
    vehicle_class: str  # "uberx" | "comfort" | "uberxl" | "black" — P1 only ships uberx
    capacity: int


class AppointmentOptionDetails(BaseModel):
    kind: Literal["appointment"] = "appointment"
    clinic: str
    slot_at: datetime
    specialty: str


OptionDetails = Annotated[
    RideOptionDetails | AppointmentOptionDetails,
    Field(discriminator="kind"),
]


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


VehicleClass = Literal["uberx", "comfort", "uberxl", "black"]

BookingStatus = Literal[
    "requested",
    "matched",
    "en_route",
    "in_progress",
    "completed",
    "cancelled",
    "failed",
]


class Draft(BaseModel):
    model_config = ConfigDict(frozen=True)
    draft_id: str
    option_snapshot: Option
    fingerprint: str
    created_at: datetime
    expires_at: datetime
    intent_snapshot: Intent | None = None  # populated by provider.prepare(); used for re-quote


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
        # sim state-machine statuses (BookingStatus)
        "requested",
        "matched",
    ]
    provider: str
    summary: Option
    events: list[BookingEvent] = Field(default_factory=list[BookingEvent])
    cancellation: CancellationInfo | None = None
