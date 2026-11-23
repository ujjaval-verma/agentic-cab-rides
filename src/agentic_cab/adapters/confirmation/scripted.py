"""ScriptedConfirmationChannel — drives scenarios deterministically. See spec §6."""

from __future__ import annotations

from typing import Any, Self

from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.core.ports import ConfirmationRequest, ConfirmationResult

# Constant principal so scenario logs stay deterministic (invariant 9).
_SCRIPTED_PRINCIPAL_ID = "scripted"


class ScriptedConfirmationChannel:
    def __init__(self: Self, script: list[dict[str, Any]], clock: FrozenClock) -> None:
        self._script = script
        self._clock = clock

    async def request(self: Self, req: ConfirmationRequest) -> ConfirmationResult:
        for rule in self._script:
            if rule["match"] in req.summary:
                approved = rule["action"] == "approve"
                return ConfirmationResult(
                    approved=approved,
                    reason="approved" if approved else "declined",
                    approved_at=self._clock.now() if approved else None,
                    approver="scripted",
                    approver_id=_SCRIPTED_PRINCIPAL_ID if approved else None,
                    channel="scripted",
                )
        msg = (
            f"ScriptedConfirmationChannel: no rule matched summary={req.summary!r}; "
            "scenarios must script every confirmation explicitly"
        )
        raise AssertionError(msg)
