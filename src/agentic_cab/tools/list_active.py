"""list_active_bookings tool — non-gated, read-only. Emits requested + outcome rows. Spec §7."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final

from pydantic import BaseModel

from agentic_cab.core.ids import new_action_id
from agentic_cab.core.ports import ActionLog, StateStore

DESCRIPTION: Final[str] = (
    "Read-only: list bookings not yet completed/cancelled/failed."
    ' Use to recover booking_ids the user references obliquely ("my ride").'
)


class ListActiveArgs(BaseModel):
    pass


async def list_active_bookings(
    args: ListActiveArgs,
    *,
    store: StateStore,
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
            "tool": "list_active_bookings",
            "args": args.model_dump(),
            "actor": "agent",
        }
    )

    bookings = store.list_active_bookings()
    result: dict[str, Any] = {
        "bookings": [
            {
                "booking_id": b.booking_id,
                "status": b.status,
                "summary_title": b.summary.title,
            }
            for b in bookings
        ]
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
