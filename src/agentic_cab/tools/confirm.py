"""confirm_booking — the gated tool. See spec §7 + CLAUDE.md inv. 2, 3, 4, 5, 6.

Algorithm:
  1. Load draft; reject if missing / expired / already consumed.
  2. Fingerprint check — abort on mismatch (invariant 3).
  3. Re-quote stub (P1: drift = 0; P3 will call provider.discover() again).
  3a. Intent guard — must be a RideIntent with RideOptionDetails.
  3b. Compute intent_hash from stable user inputs + chosen vehicle class.
  3c. Look up active prior booking; if found and no nonce → duplicate_intent (invariant 2).
  3d. Compute generation = count of terminal prior bookings for this intent.
  4. Derive intent-bound idempotency key (intent_hash, generation, nonce); claim_or_recall.
  5. Gate via ConfirmationChannel; abort on decline (invariant 2).
  6. Emit verified row (ok).
  7. Execute provider.confirm(); emit executed row.
  8. consume_draft + finalize_idempotency + set_booking_intent_hash; emit outcome row.

Four-row log pattern (invariant 6):
  requested → verified → executed → outcome
  verified.result == 'rejected' short-circuits: NO executed row follows.
  duplicate_intent short-circuit fires BEFORE the gate (invariant 2).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from agentic_cab.core.constants import RE_QUOTE_DRIFT_THRESHOLD_CENTS
from agentic_cab.core.fingerprint import fingerprint
from agentic_cab.core.ids import confirm_idempotency_key, intent_hash, new_action_id
from agentic_cab.core.ports import (
    ActionLog,
    BookingProvider,
    Clock,
    ConfirmationChannel,
    ConfirmationRequest,
    StateStore,
)
from agentic_cab.core.types import Draft, RideIntent, RideOptionDetails


class ConfirmArgs(BaseModel):
    draft_id: str
    intent_override_nonce: str | None = None


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


def _compute_intent_context(
    draft: Draft,
    store: StateStore,
    nonce: str | None,
    clock: Clock,
    requote_drift_cents: int,
    emit_verified: Callable[..., None],
    base_checks: dict[str, Any],
) -> tuple[str, int, tuple[str, dict[str, Any] | None] | None]:
    """Steps 3a-3d: guard intent type, compute intent_hash, check dedup, derive generation.

    Returns (intent_hash_hex, generation, rejection_info | None).
    rejection_info is a (status, result_dict) tuple for _emit_outcome when not None.
    emit_verified is called with the rejection before returning a non-None rejection.
    """
    # 3a. Intent guard — ride-only for now (appointment hashing tracked in FOLLOWUPS).
    if not isinstance(draft.intent_snapshot, RideIntent):
        emit_verified(
            {**base_checks, "intent_snapshot_missing": True},
            "rejected",
            "intent_snapshot_missing_or_unsupported",
        )
        return "", 0, ("rejected", {"reason": "intent_snapshot_missing_or_unsupported"})

    # 3b. Compute the intent hash from stable user inputs + chosen vehicle class.
    if not isinstance(draft.option_snapshot.details, RideOptionDetails):
        # Shouldn't happen for ride intents, but guard for type narrowing.
        emit_verified(
            {**base_checks, "intent_snapshot_missing": True},
            "rejected",
            "option_details_unsupported",
        )
        return "", 0, ("rejected", {"reason": "option_details_unsupported"})

    ih = intent_hash(
        draft.intent_snapshot,
        draft.option_snapshot.details.vehicle_class,
        now=clock.now(),
    )

    # 3c. Look up an active prior booking with this intent. If found and the agent
    #     did not supply a nonce, short-circuit with duplicate_intent.
    existing = store.find_active_booking_by_intent_hash(ih)
    if existing is not None and nonce is None:
        emit_verified(
            {
                "draft_found": True,
                "fingerprint_match": True,
                "requote_price_drift_cents": requote_drift_cents,
                "intent_hash": ih,
                "existing_booking_id": existing.booking_id,
            },
            "rejected",
            "duplicate_intent",
        )
        return (
            ih,
            0,
            (
                "duplicate_intent",
                {"existing_booking_id": existing.booking_id, "existing_status": existing.status},
            ),
        )

    # 3d. Compute generation (count of terminal prior bookings with this intent).
    generation = store.count_terminal_bookings_by_intent_hash(ih)
    return ih, generation, None


def _handle_claim_shortcircuit(
    claim: Any,
    store: StateStore,
    log: ActionLog,
    clock: Clock,
    provider: BookingProvider,
    key: str,
    draft_id: str,
    session_id: str,
    action_id: str,
    requote_drift_cents: int,
    emit_verified: Callable[..., None],
    intent_hash_hex: str,
) -> tuple[str, dict[str, Any] | None] | None:
    """Handle in_progress and recalled short-circuits after claim_or_recall.

    Returns (status, result_dict) tuple for _emit_outcome when a short-circuit fires,
    or None when the caller should proceed to the gate and provider call.
    """
    if claim.status == "in_progress":
        emit_verified(
            {
                "draft_found": True,
                "fingerprint_match": True,
                "requote_price_drift_cents": requote_drift_cents,
                "idempotency": "in_progress",
            },
            "rejected",
            "concurrent claim in progress",
        )
        return "in_progress", {"reason": "concurrent claim"}

    if claim.status == "recalled" and claim.booking_id is not None:
        # Re-fetch from the store so events and status are current (closes Booking.events
        # round-trip inconsistency: finalize_idempotency snapshot may have stale events).
        _bk = store.get_booking(claim.booking_id)
        # Defensive heal: if the prior confirm crashed between finalize_idempotency and
        # set_booking_intent_hash, intent_hash is NULL on the booking row. Writing it here
        # is idempotent (overwrite is a no-op when already set) and ensures future dedup
        # lookups via find_active_booking_by_intent_hash can find the booking.
        store.set_booking_intent_hash(claim.booking_id, intent_hash_hex)
        emit_verified(
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
                "request": {"draft_id": draft_id},
                "latency_ms": 0,
                "replayed": True,
            }
        )
        return "confirmed", {"booking_id": _bk.booking_id if _bk else claim.booking_id}

    return None


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

    # 3a-3d: compute intent hash, check for duplicate, derive generation.
    ih, generation, _dedup_rejection = _compute_intent_context(
        draft,
        store,
        args.intent_override_nonce,
        clock,
        requote_drift_cents,
        _emit_verified,
        checks,
    )
    if _dedup_rejection is not None:
        return _emit_outcome(*_dedup_rejection)

    # 4. Idempotency claim (invariant 5). Key derived from (intent_hash, generation, nonce)
    # so an agent retry — even across process restarts — produces the same key and hits
    # claim_or_recall instead of re-booking.
    key = confirm_idempotency_key(ih, generation, args.intent_override_nonce)
    claim = store.claim_or_recall(key, scope="confirm")

    # Concurrent or restarted caller: short-circuit without calling the gate or provider.
    _claim_result = _handle_claim_shortcircuit(
        claim,
        store,
        log,
        clock,
        provider,
        key,
        args.draft_id,
        session_id,
        action_id,
        requote_drift_cents,
        _emit_verified,
        intent_hash_hex=ih,
    )
    if _claim_result is not None:
        return _emit_outcome(*_claim_result)

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
        store.set_booking_intent_hash(booking.booking_id, ih)
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
