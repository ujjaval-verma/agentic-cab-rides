"""track_booking tool — non-gated, read-only. Emits requested + outcome rows. Spec §10."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final

from pydantic import BaseModel

from agentic_cab.core.ids import new_action_id
from agentic_cab.core.ports import ActionLog, BookingProvider

DESCRIPTION: Final[str] = "Read-only: returns current status of an existing booking_id."


class TrackArgs(BaseModel):
    booking_id: str


async def track_booking(
    args: TrackArgs,
    *,
    provider: BookingProvider,
    log: ActionLog,
    session_id: str,
    turn_id: str,
) -> dict[str, Any]:
    action_id = new_action_id()

    log.emit(
        {
            "ts": datetime.now(UTC).isoformat(),
            "session_id": session_id,
            "turn_id": turn_id,
            "action_id": action_id,
            "kind": "requested",
            "tool": "track_booking",
            "args": args.model_dump(),
            "actor": "agent",
        }
    )

    try:
        booking = await provider.track(args.booking_id)
    except ValueError as exc:
        error_payload: dict[str, Any] = {
            "status": "error",
            "error": str(exc),
        }
        log.emit(
            {
                "ts": datetime.now(UTC).isoformat(),
                "session_id": session_id,
                "action_id": action_id,
                "kind": "outcome",
                "status": "error",
                "result": None,
                "error": str(exc),
            }
        )
        return error_payload

    result: dict[str, Any] = {
        "booking_id": booking.booking_id,
        "status": booking.status,
        "summary_title": booking.summary.title,
        "price_cents": booking.summary.price.amount_cents,
    }
    log.emit(
        {
            "ts": datetime.now(UTC).isoformat(),
            "session_id": session_id,
            "action_id": action_id,
            "kind": "outcome",
            "status": "ok",
            "result": result,
            "error": None,
        }
    )
    return result
