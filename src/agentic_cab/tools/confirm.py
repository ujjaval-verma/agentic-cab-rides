"""confirm_booking — the gated tool. See spec §7 + CLAUDE.md inv. 2, 3, 4, 5, 6.

Algorithm:
  1. Load draft; reject if missing / expired / already consumed.
  2. Fingerprint check — abort on mismatch (invariant 3).
  3. Re-quote stub (P1: drift = 0; P3 will call provider.discover() again).
  4. Derive intent-bound idempotency key (draft_id + fingerprint); claim_or_recall.
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

from agentic_cab.core.constants import RE_QUOTE_DRIFT_THRESHOLD_CENTS
from agentic_cab.core.fingerprint import fingerprint
from agentic_cab.core.ids import confirm_idempotency_key, new_action_id
from agentic_cab.core.ports import (
    ActionLog,
    BookingProvider,
    Clock,
    ConfirmationChannel,
    ConfirmationRequest,
    StateStore,
)
from agentic_cab.core.types import Draft, RideOptionDetails


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
    log: ActionLog,
    channel: ConfirmationChannel,
    clock: Clock,
    session_id: str,
    turn_id: str,
    key_factory: Callable[[str, str], str] = confirm_idempotency_key,
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
            "tool": "confirm_booking",
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
                "tool": "confirm_booking",
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

    # 3. Re-quote — real provider.discover() call (spec §7 + T28).
    # If draft has no intent_snapshot (legacy P1 drafts), drift = 0 (no re-quote possible).
    requote_drift_cents = 0
    _new_draft_id_for_requote: str | None = None
    if draft.intent_snapshot is not None and isinstance(
        draft.option_snapshot.details, RideOptionDetails
    ):
        fresh_options = await provider.discover(draft.intent_snapshot)
        # Match by vehicle_class (option_id changes each discover call; ride-only path)
        _draft_vc = draft.option_snapshot.details.vehicle_class
        _matched = next(
            (
                o
                for o in fresh_options
                if isinstance(o.details, RideOptionDetails) and o.details.vehicle_class == _draft_vc
            ),
            None,
        )
        if _matched is not None:
            requote_drift_cents = (
                _matched.price.amount_cents - draft.option_snapshot.price.amount_cents
            )
            if abs(requote_drift_cents) > RE_QUOTE_DRIFT_THRESHOLD_CENTS:
                # Mint a new draft for the agent to work with at the new price
                new_draft = await provider.prepare(_matched.option_id)
                _new_draft_id_for_requote = new_draft.draft_id
                price_changed_checks: dict[str, Any] = {
                    "draft_found": True,
                    "fingerprint_match": True,
                    "requote_price_drift_cents": requote_drift_cents,
                    "new_draft_id": _new_draft_id_for_requote,
                }
                _emit_verified(price_changed_checks, "rejected", "price_changed")
                return _emit_outcome(
                    "price_changed",
                    {
                        "new_draft_id": _new_draft_id_for_requote,
                        "new_price_cents": _matched.price.amount_cents,
                    },
                )

    # 4. Idempotency claim (invariant 5). Key derived from (draft_id, fingerprint) so an
    # agent retry after a network timeout produces the same key and hits claim_or_recall
    # instead of re-booking. draft.fingerprint is available after _validate_draft.
    key = key_factory(draft.draft_id, draft.fingerprint)
    claim = store.claim_or_recall(key, scope="confirm")

    # Concurrent or restarted caller: a prior claim exists but was never finalised.
    # Do NOT call the provider again — emit in_progress and let the agent re-prompt.
    if claim.status == "in_progress":
        _emit_verified(
            {
                "draft_found": True,
                "fingerprint_match": True,
                "requote_price_drift_cents": requote_drift_cents,
                "idempotency": "in_progress",
            },
            "rejected",
            "concurrent claim in progress",
        )
        return _emit_outcome("in_progress", {"reason": "concurrent claim"})

    if claim.status == "recalled" and claim.booking_id is not None:
        # Re-fetch from the store so events and status are current (closes Booking.events
        # round-trip inconsistency: finalize_idempotency snapshot may have stale events).
        _bk = store.get_booking(claim.booking_id)
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
                "tool": "confirm_booking",
                "provider": provider.name,
                "method": "confirm",
                "idempotency_key": key,
                "request": {"draft_id": args.draft_id},
                "latency_ms": 0,
                "replayed": True,
            }
        )
        return _emit_outcome(
            "confirmed", {"booking_id": _bk.booking_id if _bk else claim.booking_id}
        )

    # 5. Gate (invariant 2) — ConfirmationChannel owns the y/n decision
    req = ConfirmationRequest(
        subject_id=draft.draft_id,
        subject_kind="draft",
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
                "tool": "confirm_booking",
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
                "tool": "confirm_booking",
                "status": "error",
                "result": None,
                "error": str(e),
            }
        )
        raise

    return _emit_outcome("confirmed", {"booking_id": booking.booking_id})


__all__ = ["ConfirmArgs", "confirm_booking"]
