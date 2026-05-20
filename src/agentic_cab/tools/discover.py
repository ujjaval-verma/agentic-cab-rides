"""discover_options tool — non-gated, emits requested + outcome rows. Spec §7."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.core.ids import new_action_id
from agentic_cab.core.ports import BookingProvider
from agentic_cab.core.types import AddressRef, RideIntent


class DiscoverArgs(BaseModel):
    pickup_address: str
    pickup_lat: float
    pickup_lng: float
    dropoff_address: str
    dropoff_lat: float
    dropoff_lng: float
    party_size: int = 1


async def discover_options(
    args: DiscoverArgs,
    *,
    provider: BookingProvider,
    log: JSONLActionLog,
    clock: FrozenClock,
    session_id: str,
    turn_id: str,
) -> dict[str, Any]:
    action_id = new_action_id()
    intent = RideIntent(
        pickup=AddressRef(
            raw=args.pickup_address,
            formatted=args.pickup_address,
            lat=args.pickup_lat,
            lng=args.pickup_lng,
            confidence=1.0,
            provider_metadata={"alternates": []},
        ),
        dropoff=AddressRef(
            raw=args.dropoff_address,
            formatted=args.dropoff_address,
            lat=args.dropoff_lat,
            lng=args.dropoff_lng,
            confidence=1.0,
            provider_metadata={"alternates": []},
        ),
        when="now",
        party_size=args.party_size,
    )
    log.emit(
        {
            "ts": clock.now().isoformat(),
            "session_id": session_id,
            "turn_id": turn_id,
            "action_id": action_id,
            "kind": "requested",
            "tool": "discover_options",
            "args": args.model_dump(),
            "actor": "agent",
        }
    )
    options = await provider.discover(intent)
    payload: dict[str, Any] = {"options": [o.model_dump(mode="json") for o in options]}
    log.emit(
        {
            "ts": clock.now().isoformat(),
            "session_id": session_id,
            "action_id": action_id,
            "kind": "outcome",
            "status": "ok",
            "result": payload,
            "error": None,
        }
    )
    return payload
