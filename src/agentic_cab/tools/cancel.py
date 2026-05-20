"""cancel_booking — gated, fee-quoted, idempotent. See spec §7 + CLAUDE.md inv. 2, 5, 6, 10, 11.

Algorithm:
  1. Load booking; reject if missing.
  2. Mint idempotency key; claim_or_recall BEFORE status guard — a replay on a booking
     that already transitioned to "cancelled" via the first call must short-circuit here,
     not fall through to the "already cancelled" rejection path (spec §10a).
  3. Reject if booking status is already terminal or not cancellable (after recall check).
  4. quote_cancel_fee → fee shown in gate summary.
  5. Gate via ConfirmationChannel; abort on decline (invariant 2).
  6. Emit verified(ok).
  7. Execute provider.cancel(); emit executed row.
  8. put_booking + finalize_idempotency; emit outcome(cancelled).

Four-row log pattern (invariant 6):
  requested → verified → executed → outcome
  verified.result == 'rejected' short-circuits: NO executed row follows.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from pydantic import BaseModel

from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.core.fingerprint import canonical_json
from agentic_cab.core.ids import new_action_id, new_idempotency_key
from agentic_cab.core.ports import (
    BookingProvider,
    ConfirmationChannel,
    ConfirmationRequest,
    StateStore,
)

# Statuses that are already terminal and need no gate (no-op cancel path).
_ALREADY_TERMINAL = {"cancelled", "failed"}
# Statuses where cancellation is not permitted.
_NOT_CANCELLABLE = {"in_progress", "completed"}


class CancelArgs(BaseModel):
    booking_id: str


def _cancel_fingerprint(booking_id: str, fee_cents: int, status_at_quote: str) -> str:
    # Cancel fingerprint covers intent-specific fields, NOT the original draft snapshot.
    # This prevents a mutated-booking replay from slipping past as if it were the original
    # confirmed-state booking — the fee and status are what the user is consenting to cancel.
    payload = {"booking_id": booking_id, "fee_cents": fee_cents, "status_at_quote": status_at_quote}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


async def cancel_booking(
    args: CancelArgs,
    *,
    provider: BookingProvider,
    store: StateStore,
    log: JSONLActionLog,
    channel: ConfirmationChannel,
    clock: FrozenClock,
    session_id: str,
    turn_id: str,
    key_factory: Callable[[], str] = new_idempotency_key,
) -> dict[str, Any]:
    action_id = new_action_id()
    log.emit(
        {
            "ts": clock.now().isoformat(),
            "session_id": session_id,
            "turn_id": turn_id,
            "action_id": action_id,
            "kind": "requested",
            "tool": "cancel_booking",
            "args": {"booking_id": args.booking_id},
            "actor": "agent",
        }
    )

    def _emit_verified(checks: dict[str, Any], result_str: str, reason: str | None = None) -> None:
        row: dict[str, Any] = {
            "ts": clock.now().isoformat(),
            "session_id": session_id,
            "action_id": action_id,
            "kind": "verified",
            "checks": checks,
            "result": result_str,
        }
        if reason is not None:
            row["reason"] = reason
        log.emit(row)

    def _emit_outcome(
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        log.emit(
            {
                "ts": clock.now().isoformat(),
                "session_id": session_id,
                "action_id": action_id,
                "kind": "outcome",
                "status": status,
                "result": result,
                "error": error,
            }
        )
        out: dict[str, Any] = {"status": status, "action_id": action_id}
        if result:
            out.update(result)
        return out

    # Step 1: Load booking; reject if missing.
    booking = store.get_booking(args.booking_id)
    if booking is None:
        _emit_verified({"booking_found": False}, "rejected", "unknown booking")
        return _emit_outcome("rejected", {"reason": "unknown booking"})

    # Step 2: Idempotency claim BEFORE the status guard (invariant 5 + spec §10a).
    # A second cancel call with the same key must short-circuit here even if the booking
    # is now "cancelled" (because the first call put it there). Original approval is canonical.
    key = key_factory()
    claim = store.claim_or_recall(key, scope="cancel")
    if claim.status == "recalled" and claim.booking is not None:
        _emit_verified(
            {
                "booking_found": True,
                "status": booking.status,
                "idempotency": "recalled",
            },
            "ok",
        )
        log.emit(
            {
                "ts": clock.now().isoformat(),
                "session_id": session_id,
                "action_id": action_id,
                "kind": "executed",
                "provider": provider.name,
                "method": "cancel",
                "idempotency_key": key,
                "request": {"booking_id": args.booking_id},
                "latency_ms": 0,
                "replayed": True,
            }
        )
        return _emit_outcome("cancelled", {"booking_id": claim.booking.booking_id})

    # Step 3: Reject terminal / non-cancellable statuses (AFTER idempotency recall check).
    if booking.status in _ALREADY_TERMINAL:
        _emit_verified(
            {"booking_found": True, "status": booking.status},
            "rejected",
            f"already {booking.status}",
        )
        return _emit_outcome("rejected", {"reason": f"already {booking.status}"})

    if booking.status in _NOT_CANCELLABLE:
        _emit_verified(
            {"booking_found": True, "status": booking.status},
            "rejected",
            f"cannot cancel; ride in status={booking.status!r}",
        )
        return _emit_outcome(
            "rejected", {"reason": f"cannot cancel; ride in status={booking.status!r}"}
        )

    # Step 4: Quote cancellation fee.
    fee = await provider.quote_cancel_fee(args.booking_id)
    status_at_quote = booking.status

    # Step 5: Gate (invariant 2) — ConfirmationChannel owns the y/n decision.
    fp = _cancel_fingerprint(args.booking_id, fee.amount_cents, status_at_quote)
    req = ConfirmationRequest(
        draft_id=args.booking_id,  # repurpose draft_id field for the booking_id
        summary=(
            f"Cancel {booking.summary.title}? Fee: ${fee.amount_cents / 100:.2f} {fee.currency}"
        ),
        fingerprint=fp,
        details={
            "booking_id": args.booking_id,
            "fee_cents": fee.amount_cents,
            "status_at_quote": status_at_quote,
        },
        expires_at=clock.now() + timedelta(seconds=60),
    )
    confirmation = await channel.request(req)
    base_checks: dict[str, Any] = {
        "booking_found": True,
        "status": status_at_quote,
        "fee_cents": fee.amount_cents,
        "confirmation": confirmation.model_dump(mode="json"),
    }
    if not confirmation.approved:
        _emit_verified(base_checks, "rejected", "user declined")
        return _emit_outcome("rejected", {"reason": "user declined"})

    # Step 6: Emit verified(ok) — after gate passes.
    _emit_verified(base_checks, "ok")

    # Steps 7 + 8: Execute + persist (try/except so the action_id always closes).
    try:
        updated_booking = await provider.cancel(args.booking_id, idempotency_key=key)
        log.emit(
            {
                "ts": clock.now().isoformat(),
                "session_id": session_id,
                "action_id": action_id,
                "kind": "executed",
                "provider": provider.name,
                "method": "cancel",
                "idempotency_key": key,
                "request": {"booking_id": args.booking_id},
                "latency_ms": 0,
                "replayed": False,
            }
        )
        store.put_booking(updated_booking)
        store.finalize_idempotency(key, booking=updated_booking)
    except Exception as e:
        log.emit(
            {
                "ts": clock.now().isoformat(),
                "session_id": session_id,
                "action_id": action_id,
                "kind": "outcome",
                "status": "error",
                "result": None,
                "error": str(e),
            }
        )
        raise

    return _emit_outcome("cancelled", {"booking_id": updated_booking.booking_id})


__all__ = ["CancelArgs", "cancel_booking"]
