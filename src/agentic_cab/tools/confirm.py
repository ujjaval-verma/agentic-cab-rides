"""confirm_booking — the gated tool. See spec §7 + CLAUDE.md inv. 2, 3, 4, 5, 6.

Algorithm:
  1. Load draft; reject if missing / expired / already consumed.
  2. Fingerprint check — abort on mismatch (invariant 3).
  3. Re-quote stub (P1: drift = 0; P3 will call provider.discover() again).
  4. Mint idempotency key via injected factory; claim_or_recall for short-circuit.
  5. Gate via ConfirmationChannel; abort on decline (invariant 2).
  6. Emit verified row (ok).
  7. Execute provider.confirm(); emit executed row.
  8. consume_draft + finalize_idempotency; emit outcome row.

Four-row log pattern (invariant 6):
  requested → verified → executed → outcome
  verified.result == 'rejected' short-circuits: NO executed row follows.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.core.fingerprint import fingerprint
from agentic_cab.core.ids import new_action_id, new_idempotency_key
from agentic_cab.core.ports import (
    BookingProvider,
    ConfirmationChannel,
    ConfirmationRequest,
    StateStore,
)
from agentic_cab.core.types import Draft


class ConfirmArgs(BaseModel):
    draft_id: str


def _summary(draft: Draft) -> str:
    snap = draft.option_snapshot
    return f"Confirm {snap.title} for ${snap.price.amount_cents / 100:.2f} {snap.price.currency}?"


def _validate_draft(
    draft_id: str,
    store: StateStore,
    now: datetime,
) -> tuple[Draft | None, dict[str, Any], str | None]:
    """Return (draft, checks, rejection_reason) — reason is None when valid."""
    draft = store.get_draft(draft_id)
    if draft is None:
        return None, {"draft_found": False}, "unknown draft"

    if draft.expires_at < now:
        return None, {"draft_found": True, "draft_expired": True}, "draft expired"

    if store.get_draft_consumed_at(draft_id) is not None:
        return (
            None,
            {"draft_found": True, "draft_expired": False, "already_consumed": True},
            "draft already consumed",
        )

    fp_now = fingerprint(draft.option_snapshot)
    if fp_now != draft.fingerprint:
        return (
            None,
            {"draft_found": True, "draft_expired": False, "fingerprint_match": False},
            "fingerprint mismatch",
        )

    return (
        draft,
        {
            "draft_found": True,
            "draft_expired": False,
            "already_consumed": False,
            "fingerprint_match": True,
        },
        None,
    )


async def confirm_booking(
    args: ConfirmArgs,
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
            "tool": "confirm_booking",
            "args": {"draft_id": args.draft_id},
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

    # Steps 1 + 2: validate draft + fingerprint in a single helper
    draft_or_none, checks, rejection = _validate_draft(args.draft_id, store, clock.now())
    if rejection is not None:
        _emit_verified(checks, "rejected", rejection)
        return _emit_outcome("rejected", {"reason": rejection})

    # Narrowing: _validate_draft guarantees draft is not None when rejection is None
    draft: Draft = draft_or_none  # type: ignore[assignment]

    # 3. Re-quote (P1 stub — drift always 0; P3 retrofits real provider.discover() call
    # alongside intent-bound idempotency keys; tracked in FOLLOWUPS.md, noted in WRITEUP.md).
    requote_drift_cents = 0

    # 4. Idempotency claim (invariant 5)
    key = key_factory()
    claim = store.claim_or_recall(key, scope="confirm")
    if claim.status == "recalled" and claim.booking is not None:
        _emit_verified(
            {
                "draft_found": True,
                "fingerprint_match": True,
                "requote_price_drift_cents": 0,
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
                "method": "confirm",
                "idempotency_key": key,
                "request": {"draft_id": args.draft_id},
                "latency_ms": 0,
                "replayed": True,
            }
        )
        return _emit_outcome("confirmed", {"booking_id": claim.booking.booking_id})

    # 5. Gate (invariant 2) — ConfirmationChannel owns the y/n decision
    req = ConfirmationRequest(
        draft_id=draft.draft_id,
        summary=_summary(draft),
        fingerprint=draft.fingerprint,
        details={"price_cents": draft.option_snapshot.price.amount_cents},
        expires_at=draft.expires_at,
    )
    confirmation = await channel.request(req)
    base_checks: dict[str, Any] = {
        "draft_found": True,
        "fingerprint_match": True,
        "requote_price_drift_cents": requote_drift_cents,
        "confirmation": confirmation.model_dump(mode="json"),
    }
    if not confirmation.approved:
        _emit_verified(base_checks, "rejected", "user declined")
        return _emit_outcome("rejected", {"reason": "user declined"})

    # 6. Emit verified (ok) — after gate passes
    _emit_verified(base_checks, "ok")

    # 7 + 8. Execute + persist (try/except so the action_id always closes)
    try:
        booking = await provider.confirm(draft.draft_id, idempotency_key=key)
        log.emit(
            {
                "ts": clock.now().isoformat(),
                "session_id": session_id,
                "action_id": action_id,
                "kind": "executed",
                "provider": provider.name,
                "method": "confirm",
                "idempotency_key": key,
                "request": {"draft_id": args.draft_id},
                "latency_ms": 0,
                "replayed": False,
            }
        )
        store.consume_draft(args.draft_id, at=clock.now())
        store.finalize_idempotency(key, booking=booking)
    except Exception as e:  # re-raise after closing the action_id in the log
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

    return _emit_outcome("confirmed", {"booking_id": booking.booking_id})


__all__ = ["ConfirmArgs", "confirm_booking"]
