"""geocode_address tool — non-gated, read-only. Emits requested + outcome rows. Spec §10."""

from __future__ import annotations

from typing import Any, Final

from pydantic import BaseModel

from agentic_cab.core.ids import new_action_id
from agentic_cab.core.ports import ActionLog, Clock, GeocoderPort

DESCRIPTION: Final[str] = (
    "Resolve an address query to {formatted, lat, lng, confidence}. "
    "confidence<0.5 + non-zero alternates_count means the address is ambiguous "
    "— ask the user to clarify."
)


class GeocodeArgs(BaseModel):
    query: str


async def geocode_address(
    args: GeocodeArgs,
    *,
    geocoder: GeocoderPort,
    log: ActionLog,
    clock: Clock,
    session_id: str,
    turn_id: str,
) -> dict[str, Any]:
    action_id = new_action_id()

    log.emit(
        {
            "ts": clock.now().isoformat(),
            "session_id": session_id,
            "turn_id": turn_id,
            "action_id": action_id,
            "kind": "requested",
            "tool": "geocode_address",
            "args": args.model_dump(),
            "actor": "agent",
        }
    )

    try:
        ref = await geocoder.geocode(args.query)
    except Exception as exc:
        error_payload: dict[str, Any] = {
            "status": "error",
            "result": None,
            "error": str(exc),
        }
        log.emit(
            {
                "ts": clock.now().isoformat(),
                "session_id": session_id,
                "action_id": action_id,
                "kind": "outcome",
                "tool": "geocode_address",
                "status": "error",
                "result": None,
                "error": str(exc),
            }
        )
        return error_payload

    result: dict[str, Any] = {
        "formatted": ref.formatted,
        "lat": ref.lat,
        "lng": ref.lng,
        "confidence": ref.confidence,
        "alternates_count": len(ref.provider_metadata.get("alternates", [])),  # type: ignore[arg-type]
    }
    log.emit(
        {
            "ts": clock.now().isoformat(),
            "session_id": session_id,
            "action_id": action_id,
            "kind": "outcome",
            "tool": "geocode_address",
            "status": "ok",
            "result": result,
            "error": None,
        }
    )
    return result
