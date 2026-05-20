"""ULID factories and deterministic idempotency-key derivation helpers.

ULID factories: one per ID kind so call sites read as intent, not 'new_ulid'.

Intent-bound idempotency keys: derived deterministically from intent state via sha256
so that an agent retry after a network timeout produces the same key and collides on
the existing claim_or_recall entry (no double-booking / double-cancel).
"""

from __future__ import annotations

import hashlib

from ulid import ULID

from agentic_cab.core.fingerprint import canonical_json


def _new() -> str:
    return str(ULID())


def new_session_id() -> str:
    return _new()


def new_turn_id() -> str:
    return _new()


def new_action_id() -> str:
    return _new()


def new_draft_id() -> str:
    return _new()


def new_idempotency_key() -> str:
    return _new()


def confirm_idempotency_key(draft_id: str, fingerprint: str) -> str:
    """Derive a deterministic idempotency key for a confirm_booking call.

    Key is bound to (draft_id, fingerprint) so retries after a network timeout produce
    the same key and hit the claim_or_recall short-circuit instead of re-booking.
    Returns a 64-char sha256 hex digest.
    """
    payload = {"draft_id": draft_id, "fingerprint": fingerprint, "op": "confirm"}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def cancel_idempotency_key(booking_id: str, fee_cents: int, status_at_quote: str) -> str:
    """Derive a deterministic idempotency key for a cancel_booking call.

    Key is bound to (booking_id, fee_cents, status_at_quote) so retries produce the same
    key. Two cancels quoted at different fee amounts are *different intents* (user consented
    to a specific fee), so fee and status-at-quote are part of the key.
    Returns a 64-char sha256 hex digest.
    """
    payload = {
        "booking_id": booking_id,
        "fee_cents": fee_cents,
        "status_at_quote": status_at_quote,
        "op": "cancel",
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
