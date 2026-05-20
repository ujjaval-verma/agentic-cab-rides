"""prepare_booking tool — non-gated, emits requested + outcome. Spec §7."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.core.ids import new_action_id
from agentic_cab.core.ports import BookingProvider, Clock


class PrepareArgs(BaseModel):
    option_id: str


async def prepare_booking(
    args: PrepareArgs,
    *,
    provider: BookingProvider,
    log: JSONLActionLog,
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
            "tool": "prepare_booking",
            "args": args.model_dump(),
            "actor": "agent",
        }
    )
    draft = await provider.prepare(args.option_id)
    result: dict[str, Any] = {
        "draft_id": draft.draft_id,
        "fingerprint": draft.fingerprint,
        "summary": draft.option_snapshot.title,
        "price_cents": draft.option_snapshot.price.amount_cents,
        "expires_at": draft.expires_at.isoformat(),
    }
    log.emit(
        {
            "ts": clock.now().isoformat(),
            "session_id": session_id,
            "action_id": action_id,
            "kind": "outcome",
            "status": "ok",
            "result": result,
            "error": None,
        }
    )
    return result
