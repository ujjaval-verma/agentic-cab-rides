"""ULID factories and deterministic idempotency-key derivation helpers.

ULID factories: one per ID kind so call sites read as intent, not 'new_ulid'.

Intent-bound idempotency keys: derived deterministically from intent state via sha256
so that an agent retry — including across process restarts — produces the same key
and collides on the existing claim_or_recall entry (no double-booking / double-cancel).

See `docs/ARCHITECTURE.md` section "Core flow: `confirm_booking`" (steps 4-5)
and `docs/WRITEUP.md` section "Cross-session idempotency".
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Annotated

from pydantic import StringConstraints, TypeAdapter
from ulid import ULID

from agentic_cab.core.fingerprint import canonical_json
from agentic_cab.core.types import Intent, RideIntent

_NOW_BUCKET_MINUTES = 10

# Canonical ULID: 26 Crockford base32 chars (no I L O U), uppercase only, first char 0-7
# so the 128-bit value cannot overflow.
SESSION_ID_PATTERN = r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$"
SessionId = Annotated[str, StringConstraints(pattern=SESSION_ID_PATTERN)]
_SESSION_ID_ADAPTER: TypeAdapter[str] = TypeAdapter(SessionId)


def parse_session_id(raw: str) -> str:
    """Validate `raw` as a canonical ULID session id; raise ValueError otherwise."""
    return _SESSION_ID_ADAPTER.validate_python(raw)


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


def _bucketed_now(now: datetime) -> str:
    bucket_minute = (now.minute // _NOW_BUCKET_MINUTES) * _NOW_BUCKET_MINUTES
    floored = now.replace(minute=bucket_minute, second=0, microsecond=0)
    return floored.isoformat()


def intent_hash(intent: Intent, vehicle_class: str, *, now: datetime) -> str:
    """Stable hash of the user's ride intent.

    Inputs: pickup + dropoff (formatted/lat/lng), `when` (bucketed when "now"),
    party_size, vehicle_class. Two requests for the same trip within a 10-minute
    window hash identically; a different vehicle class, party size, or location
    diverges the hash.

    Ride-only. AppointmentIntent raises NotImplementedError — appointment hashing
    is tracked in docs/FOLLOWUPS.md.
    """
    if not isinstance(intent, RideIntent):
        raise NotImplementedError(
            "intent_hash supports RideIntent only; appointment hashing is a follow-up"
        )

    when_repr: str = _bucketed_now(now) if intent.when == "now" else intent.when.isoformat()

    payload = {
        "pickup": {
            "formatted": intent.pickup.formatted,
            "lat": intent.pickup.lat,
            "lng": intent.pickup.lng,
        },
        "dropoff": {
            "formatted": intent.dropoff.formatted,
            "lat": intent.dropoff.lat,
            "lng": intent.dropoff.lng,
        },
        "when": when_repr,
        "party_size": intent.party_size,
        "vehicle_class": vehicle_class,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def confirm_idempotency_key(
    intent_hash_hex: str,
    generation: int,
    nonce: str | None,
) -> str:
    """Derive a deterministic idempotency key for a confirm_booking call.

    Bound to (intent_hash, generation, nonce, op="confirm"). The `generation`
    counter advances when a prior booking with this intent reached a terminal
    status, letting the user legitimately re-book the same trip later. The
    optional `nonce` is an agent-supplied disambiguator when the user has been
    shown an existing active booking and explicitly asked for a second one.

    Returns a 64-char sha256 hex digest.
    """
    payload = {
        "intent_hash": intent_hash_hex,
        "generation": generation,
        "nonce": nonce,
        "op": "confirm",
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def cancel_idempotency_key(booking_id: str, fee_cents: int, status_at_quote: str) -> str:
    """Derive a deterministic idempotency key for a cancel_booking call.

    Unchanged by the cross-session idempotency rework — booking_id is already
    stable across sessions for a given booking.
    """
    payload = {
        "booking_id": booking_id,
        "fee_cents": fee_cents,
        "status_at_quote": status_at_quote,
        "op": "cancel",
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
