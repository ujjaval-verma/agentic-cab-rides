"""All port Protocols. Adapters depend on this; the agent never imports an adapter directly.

See spec §3 (layers), §5 (BookingProvider), §6 (ConfirmationChannel), §10a (StateStore).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar, Literal, Protocol, Self, runtime_checkable

from pydantic import BaseModel

from agentic_cab.core.types import Booking, BookingEvent, Draft, Intent, Option

# ---------- BookingProvider ----------


@runtime_checkable
class BookingProvider(Protocol):
    name: str
    supported_intent_kinds: ClassVar[set[str]]

    async def discover(self: Self, intent: Intent) -> list[Option]: ...

    async def prepare(self: Self, option_id: str) -> Draft: ...

    async def confirm(self: Self, draft_id: str, idempotency_key: str) -> Booking: ...

    async def track(self: Self, booking_id: str) -> Booking: ...

    async def cancel(self: Self, booking_id: str, idempotency_key: str) -> Booking: ...


# ---------- ConfirmationChannel ----------


class ConfirmationRequest(BaseModel):
    draft_id: str
    summary: str
    fingerprint: str
    details: dict[str, Any]
    expires_at: datetime


class ConfirmationResult(BaseModel):
    approved: bool
    reason: Literal["approved", "declined", "expired", "timeout"]
    approved_at: datetime | None
    approver: str | None


@runtime_checkable
class ConfirmationChannel(Protocol):
    async def request(self: Self, req: ConfirmationRequest) -> ConfirmationResult: ...


# ---------- Clock ----------


@runtime_checkable
class Clock(Protocol):
    def now(self: Self) -> datetime: ...


# ---------- ActionLog ----------


@runtime_checkable
class ActionLog(Protocol):
    def emit(self: Self, row: dict[str, Any]) -> None: ...

    def read(self: Self, session_id: str) -> list[dict[str, Any]]: ...


# ---------- StateStore ----------


class ClaimResult(BaseModel):
    status: Literal["new", "recalled"]
    booking: Booking | None = None  # populated only when status == "recalled"


@runtime_checkable
class StateStore(Protocol):
    # Drafts
    def put_draft(self: Self, draft: Draft) -> None: ...

    def get_draft(self: Self, draft_id: str) -> Draft | None: ...

    def consume_draft(self: Self, draft_id: str, at: datetime) -> bool: ...

    # Bookings
    def put_booking(self: Self, booking: Booking) -> None: ...

    def get_booking(self: Self, booking_id: str) -> Booking | None: ...

    def update_booking_status(
        self: Self, booking_id: str, status: str, event: BookingEvent
    ) -> None: ...

    def list_active_bookings(self: Self) -> list[Booking]: ...

    # Idempotency
    def claim_or_recall(
        self: Self, key: str, scope: Literal["confirm", "cancel"]
    ) -> ClaimResult: ...

    def finalize_idempotency(self: Self, key: str, booking: Booking) -> None: ...
