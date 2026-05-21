# Cross-Session Intent-Bound Idempotency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebind the `confirm_booking` idempotency key from `(draft_id, fingerprint)` to a hash of the user's stable intent so that a fresh process re-issuing the same ride request short-circuits at `claim_or_recall` instead of double-booking.

**Architecture:** Add an `intent_hash(intent, vehicle_class, *, now)` pure function in `core/ids.py`. The new `confirm_idempotency_key` mixes `(intent_hash, generation, nonce)`. The tool layer (`tools/confirm.py`) computes the hash from the draft's already-populated `intent_snapshot`, looks up any active booking with that hash, and either short-circuits with `duplicate_intent` or proceeds with the new key. Bookings table grows an `intent_hash` column. The provider port and contract suite are untouched.

**Tech Stack:** Python 3.12, Pydantic v2, sqlite3 (stdlib), pytest, ulid-py, ruff, pyright.

**Spec:** `docs/superpowers/specs/2026-05-20-cross-session-idempotency-design.md`. Read first; it owns the contract. This plan is the execution surface.

---

## File Structure

**Files created:**
- `tests/unit/test_intent_hash.py` — unit tests for `intent_hash` and the new `confirm_idempotency_key` signature.
- `tests/unit/test_confirm_dedup.py` — unit tests for `confirm_booking`'s duplicate-intent path.
- `tests/unit/test_state_store_intent_hash.py` — unit tests for the three new `SqliteStateStore` methods.
- `scenarios/06_cross_session_dedup.yaml` — the new scenario fixture.
- `tests/scenarios/test_scenario_06_cross_session_dedup.py` — bespoke pytest covering cross-draft dedup invariants.

**Files modified:**
- `src/agentic_cab/core/ids.py` — replace `confirm_idempotency_key`; add `intent_hash`.
- `src/agentic_cab/core/ports.py` — add three StateStore methods.
- `src/agentic_cab/adapters/schema.sql` — add `bookings.intent_hash` column + index.
- `src/agentic_cab/adapters/state_store.py` — implement the three methods.
- `src/agentic_cab/tools/confirm.py` — `ConfirmArgs.intent_override_nonce`; duplicate-intent algorithm; new key derivation.
- `src/agentic_cab/scenarios/invariants.py` — two new invariant checkers.
- `src/agentic_cab/agent/system_prompt.py` — one new bullet for `duplicate_intent` handling.
- `tests/unit/test_ids.py` — drop/rewrite tests against the old `confirm_idempotency_key` signature.
- `tests/unit/test_tool_confirm.py` — update tests that constructed keys via the old helper.
- `docs/superpowers/specs/2026-05-19-agentic-cab-rides-design.md` — §10a paragraph + `confirm_booking` returns table.
- `CLAUDE.md` — invariant 5 rewrite.
- `docs/WRITEUP.md` — §2 + §3 reflowing.
- `docs/FOLLOWUPS.md` — add appointment-intent hash item + UX polish item.
- `transcripts/01_happy_path.md`, `02_price_comparison.md`, `03_bad_address.md`, `05_cancel_with_fee.md` — regenerated; expect digest-only diffs on `executed.idempotency_key`.
- `transcripts/04_surge_on_confirm.md` — regenerated; must be byte-identical.

---

## Task 1: `intent_hash` and new `confirm_idempotency_key` in `core/ids.py`

**Files:**
- Modify: `src/agentic_cab/core/ids.py`
- Create: `tests/unit/test_intent_hash.py`
- Modify: `tests/unit/test_ids.py`

- [ ] **Step 1: Write failing tests for `intent_hash`**

Create `tests/unit/test_intent_hash.py`:

```python
"""Unit tests for intent_hash + new confirm_idempotency_key. See spec §4.1."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentic_cab.core.ids import confirm_idempotency_key, intent_hash
from agentic_cab.core.types import AddressRef, AppointmentIntent, RideIntent


def _addr(name: str, lat: float, lng: float) -> AddressRef:
    return AddressRef(
        raw=name,
        formatted=name,
        lat=lat,
        lng=lng,
        confidence=0.99,
        provider_metadata={"alternates": []},
    )


def _ride(
    pickup: AddressRef | None = None,
    dropoff: AddressRef | None = None,
    when: str | datetime = "now",
    party_size: int = 1,
) -> RideIntent:
    return RideIntent(
        pickup=pickup or _addr("A", 37.0, -122.0),
        dropoff=dropoff or _addr("B", 37.1, -122.1),
        when=when,
        party_size=party_size,
    )


T0 = datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC)


def test_same_intent_same_hash() -> None:
    a = intent_hash(_ride(), "uberx", now=T0)
    b = intent_hash(_ride(), "uberx", now=T0)
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_now_collapses_within_ten_minute_bucket() -> None:
    a = intent_hash(_ride(when="now"), "uberx", now=T0)
    b = intent_hash(
        _ride(when="now"), "uberx", now=T0.replace(minute=9, second=59)
    )
    assert a == b


def test_now_differs_across_ten_minute_bucket() -> None:
    a = intent_hash(_ride(when="now"), "uberx", now=T0)
    b = intent_hash(_ride(when="now"), "uberx", now=T0.replace(minute=10))
    assert a != b


def test_vehicle_class_changes_hash() -> None:
    a = intent_hash(_ride(), "uberx", now=T0)
    b = intent_hash(_ride(), "comfort", now=T0)
    assert a != b


def test_party_size_changes_hash() -> None:
    a = intent_hash(_ride(party_size=1), "uberx", now=T0)
    b = intent_hash(_ride(party_size=2), "uberx", now=T0)
    assert a != b


def test_pickup_changes_hash() -> None:
    a = intent_hash(_ride(), "uberx", now=T0)
    b = intent_hash(
        _ride(pickup=_addr("A2", 37.5, -122.0)), "uberx", now=T0
    )
    assert a != b


def test_dropoff_changes_hash() -> None:
    a = intent_hash(_ride(), "uberx", now=T0)
    b = intent_hash(
        _ride(dropoff=_addr("B2", 37.5, -122.5)), "uberx", now=T0
    )
    assert a != b


def test_concrete_when_hashes_to_its_iso_string() -> None:
    fixed = datetime(2026, 6, 1, 15, 30, tzinfo=UTC)
    a = intent_hash(_ride(when=fixed), "uberx", now=T0)
    b = intent_hash(_ride(when=fixed), "uberx", now=T0.replace(hour=23))
    # Concrete `when` ignores `now`; hash stays stable.
    assert a == b


def test_appointment_intent_raises_not_implemented() -> None:
    appt = AppointmentIntent(
        subject="GP",
        earliest_at=T0,
        latest_at=T0.replace(hour=14),
        location=_addr("Clinic", 37.0, -122.0),
    )
    with pytest.raises(NotImplementedError):
        intent_hash(appt, "uberx", now=T0)  # type: ignore[arg-type]


# --- new confirm_idempotency_key ---


def test_confirm_idempotency_key_is_deterministic() -> None:
    k1 = confirm_idempotency_key("ih-abc", 0, None)
    k2 = confirm_idempotency_key("ih-abc", 0, None)
    assert k1 == k2
    assert len(k1) == 64


def test_confirm_idempotency_key_changes_with_generation() -> None:
    k1 = confirm_idempotency_key("ih-abc", 0, None)
    k2 = confirm_idempotency_key("ih-abc", 1, None)
    assert k1 != k2


def test_confirm_idempotency_key_changes_with_nonce() -> None:
    k1 = confirm_idempotency_key("ih-abc", 0, None)
    k2 = confirm_idempotency_key("ih-abc", 0, "nonce-1")
    assert k1 != k2


def test_confirm_idempotency_key_changes_with_intent_hash() -> None:
    k1 = confirm_idempotency_key("ih-abc", 0, None)
    k2 = confirm_idempotency_key("ih-xyz", 0, None)
    assert k1 != k2
```

- [ ] **Step 2: Run the tests; expect collection failure**

Run: `uv run pytest tests/unit/test_intent_hash.py -v`
Expected: ImportError or AttributeError — `intent_hash` not defined; `confirm_idempotency_key` has the wrong signature.

- [ ] **Step 3: Implement `intent_hash` and the new `confirm_idempotency_key` in `core/ids.py`**

Replace the body of `src/agentic_cab/core/ids.py` (keep the ULID factories and `cancel_idempotency_key` unchanged). Drop the old `confirm_idempotency_key(draft_id, fingerprint)`.

```python
"""ULID factories and deterministic idempotency-key derivation helpers.

ULID factories: one per ID kind so call sites read as intent, not 'new_ulid'.

Intent-bound idempotency keys: derived deterministically from intent state via sha256
so that an agent retry — including across process restarts — produces the same key
and collides on the existing claim_or_recall entry (no double-booking / double-cancel).

See `docs/superpowers/specs/2026-05-20-cross-session-idempotency-design.md`.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from ulid import ULID

from agentic_cab.core.fingerprint import canonical_json
from agentic_cab.core.types import RideIntent

_NOW_BUCKET_MINUTES = 10


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


def intent_hash(intent: Any, vehicle_class: str, *, now: datetime) -> str:
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

    when_repr: str
    if intent.when == "now":
        when_repr = _bucketed_now(now)
    else:
        when_repr = intent.when.isoformat()

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
```

- [ ] **Step 4: Update `tests/unit/test_ids.py` to match the new signature**

Replace the three `confirm_idempotency_key` tests (lines 25–46 of the current file) with this block:

```python
# --- confirm_idempotency_key helpers (new signature: intent_hash, generation, nonce) ---


def test_confirm_idempotency_key_is_deterministic() -> None:
    key_a = confirm_idempotency_key("ih-abc", 0, None)
    key_b = confirm_idempotency_key("ih-abc", 0, None)
    assert key_a == key_b
    assert isinstance(key_a, str)
    assert len(key_a) == 64


def test_confirm_idempotency_key_changes_with_intent_hash() -> None:
    key_a = confirm_idempotency_key("ih-abc", 0, None)
    key_b = confirm_idempotency_key("ih-xyz", 0, None)
    assert key_a != key_b


def test_confirm_idempotency_key_changes_with_generation() -> None:
    key_a = confirm_idempotency_key("ih-abc", 0, None)
    key_b = confirm_idempotency_key("ih-abc", 1, None)
    assert key_a != key_b


def test_confirm_idempotency_key_changes_with_nonce() -> None:
    key_a = confirm_idempotency_key("ih-abc", 0, None)
    key_b = confirm_idempotency_key("ih-abc", 0, "nonce-x")
    assert key_a != key_b
```

The cancel-key tests stay as-is.

- [ ] **Step 5: Run all id tests; expect green**

Run: `uv run pytest tests/unit/test_intent_hash.py tests/unit/test_ids.py -v`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/agentic_cab/core/ids.py tests/unit/test_intent_hash.py tests/unit/test_ids.py
git commit -m "feat(ids): intent_hash + intent-bound confirm_idempotency_key

Replaces (draft_id, fingerprint) derivation with (intent_hash, generation,
nonce). Ride-only; AppointmentIntent raises NotImplementedError.

Closes the cross-session double-book gap at the key-derivation layer.
The tool-layer wiring and StateStore changes land in subsequent commits."
```

---

## Task 2: StateStore Protocol — three new methods

**Files:**
- Modify: `src/agentic_cab/core/ports.py`

- [ ] **Step 1: Add three method declarations to the StateStore Protocol**

In `src/agentic_cab/core/ports.py`, insert these three method signatures inside the `StateStore` Protocol class, after `def finalize_idempotency(...)` and before the `# Pending confirmations` comment block:

```python
    # Intent-bound idempotency (cross-session)
    def find_active_booking_by_intent_hash(
        self: Self, intent_hash_hex: str
    ) -> Booking | None: ...

    def count_terminal_bookings_by_intent_hash(self: Self, intent_hash_hex: str) -> int: ...

    def set_booking_intent_hash(self: Self, booking_id: str, intent_hash_hex: str) -> None: ...
```

"Active" = `status NOT IN ('completed','cancelled','failed')`. "Terminal" = the complement. `set_booking_intent_hash` is the post-`finalize_idempotency` write that lets future sessions find this booking by intent.

- [ ] **Step 2: Run pyright to confirm the Protocol surface is well-formed**

Run: `uv run pyright src/agentic_cab/core/ports.py`
Expected: no errors. (Pyright sees no usage yet, so the new methods are just type signatures.)

- [ ] **Step 3: Commit**

```bash
git add src/agentic_cab/core/ports.py
git commit -m "feat(ports): StateStore methods for intent-hash lookup

find_active_booking_by_intent_hash, count_terminal_bookings_by_intent_hash,
set_booking_intent_hash — used by tools/confirm.py for cross-session dedup."
```

---

## Task 3: Schema column + index for `bookings.intent_hash`

**Files:**
- Modify: `src/agentic_cab/adapters/schema.sql`

- [ ] **Step 1: Add the column and index**

Edit `src/agentic_cab/adapters/schema.sql`. Replace the `bookings` block (lines 11–22) with:

```sql
CREATE TABLE IF NOT EXISTS bookings (
    booking_id   TEXT PRIMARY KEY,
    draft_id     TEXT NOT NULL REFERENCES drafts(draft_id),
    provider     TEXT NOT NULL,
    status       TEXT NOT NULL,
    summary      TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    cancelled_at TEXT,
    cancellation TEXT,
    intent_hash  TEXT             -- nullable; populated by tools/confirm after a successful confirm
);
CREATE INDEX IF NOT EXISTS bookings_status_idx ON bookings(status);
CREATE INDEX IF NOT EXISTS bookings_intent_hash_idx ON bookings(intent_hash);
```

The column is nullable so existing rows (and bookings created via the cancel path, which has no intent-hash semantics) are valid. Lookups filter on `intent_hash IS NOT NULL`.

- [ ] **Step 2: Verify schema parses**

Run:
```bash
uv run python -c "import sqlite3, pathlib; sqlite3.connect(':memory:').executescript(pathlib.Path('src/agentic_cab/adapters/schema.sql').read_text())"
```
Expected: exit 0, no output.

- [ ] **Step 3: Commit**

```bash
git add src/agentic_cab/adapters/schema.sql
git commit -m "feat(schema): bookings.intent_hash column + index

Nullable, additive. New column populated by tools/confirm.py for cross-session
idempotency lookup. Existing rows keep NULL; lookups filter intent_hash IS NOT NULL.
make reset is the documented escape hatch for stale databases."
```

---

## Task 4: SqliteStateStore — implement the three new methods

**Files:**
- Modify: `src/agentic_cab/adapters/state_store.py`
- Create: `tests/unit/test_state_store_intent_hash.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_state_store_intent_hash.py`:

```python
"""Tests for the three intent_hash methods on SqliteStateStore."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.core.types import (
    AddressRef,
    BookingEvent,
    Money,
    Option,
    RideOptionDetails,
)
from agentic_cab.core.types import Booking


def _store(tmp_path: Path) -> SqliteStateStore:
    s = SqliteStateStore(tmp_path / "state.db")
    s.apply_schema()
    return s


def _opt(option_id: str = "opt-1") -> Option:
    return Option(
        option_id=option_id,
        provider="uber-sim",
        title="UberX",
        price=Money(amount_cents=1000, currency="USD"),
        eta_seconds=300,
        when=None,
        details=RideOptionDetails(vehicle_class="uberx", capacity=4),
        expires_at=datetime(2026, 5, 20, 13, 0, tzinfo=UTC),
    )


def _draft_row(store: SqliteStateStore, draft_id: str) -> None:
    # Bookings reference drafts via FK; insert a minimal draft row directly.
    with store._write() as conn:  # noqa: SLF001
        conn.execute(
            "INSERT INTO drafts(draft_id, option_snapshot, fingerprint, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                draft_id,
                _opt().model_dump_json(),
                "fp",
                datetime(2026, 5, 20, 12, 0, tzinfo=UTC).isoformat(),
                datetime(2026, 5, 20, 13, 0, tzinfo=UTC).isoformat(),
            ),
        )


def _booking(booking_id: str, draft_id: str, status: str = "confirmed") -> Booking:
    return Booking(
        booking_id=booking_id,
        draft_id=draft_id,
        provider="uber-sim",
        status=status,
        summary=_opt(),
    )


def test_find_active_returns_none_when_no_match(tmp_path: Path) -> None:
    s = _store(tmp_path)
    assert s.find_active_booking_by_intent_hash("ih-abc") is None


def test_find_active_returns_match_after_set(tmp_path: Path) -> None:
    s = _store(tmp_path)
    _draft_row(s, "draft-1")
    s.put_booking(_booking("bk-1", "draft-1", status="confirmed"))
    s.set_booking_intent_hash("bk-1", "ih-abc")
    found = s.find_active_booking_by_intent_hash("ih-abc")
    assert found is not None
    assert found.booking_id == "bk-1"


def test_find_active_skips_terminal(tmp_path: Path) -> None:
    s = _store(tmp_path)
    _draft_row(s, "draft-1")
    s.put_booking(_booking("bk-1", "draft-1", status="completed"))
    s.set_booking_intent_hash("bk-1", "ih-abc")
    assert s.find_active_booking_by_intent_hash("ih-abc") is None


def test_find_active_skips_null_intent_hash(tmp_path: Path) -> None:
    s = _store(tmp_path)
    _draft_row(s, "draft-1")
    s.put_booking(_booking("bk-1", "draft-1", status="confirmed"))
    # intent_hash never set
    assert s.find_active_booking_by_intent_hash("ih-abc") is None


def test_count_terminal_returns_zero_when_no_match(tmp_path: Path) -> None:
    s = _store(tmp_path)
    assert s.count_terminal_bookings_by_intent_hash("ih-abc") == 0


def test_count_terminal_counts_only_terminal(tmp_path: Path) -> None:
    s = _store(tmp_path)
    _draft_row(s, "draft-1")
    _draft_row(s, "draft-2")
    _draft_row(s, "draft-3")
    s.put_booking(_booking("bk-1", "draft-1", status="completed"))
    s.put_booking(_booking("bk-2", "draft-2", status="cancelled"))
    s.put_booking(_booking("bk-3", "draft-3", status="confirmed"))
    for b in ("bk-1", "bk-2", "bk-3"):
        s.set_booking_intent_hash(b, "ih-abc")
    assert s.count_terminal_bookings_by_intent_hash("ih-abc") == 2


def test_set_booking_intent_hash_is_idempotent_overwrite(tmp_path: Path) -> None:
    s = _store(tmp_path)
    _draft_row(s, "draft-1")
    s.put_booking(_booking("bk-1", "draft-1"))
    s.set_booking_intent_hash("bk-1", "ih-aaa")
    s.set_booking_intent_hash("bk-1", "ih-bbb")
    assert s.find_active_booking_by_intent_hash("ih-aaa") is None
    found = s.find_active_booking_by_intent_hash("ih-bbb")
    assert found is not None and found.booking_id == "bk-1"
```

- [ ] **Step 2: Run the tests; expect failure**

Run: `uv run pytest tests/unit/test_state_store_intent_hash.py -v`
Expected: AttributeError on `set_booking_intent_hash` / `find_active_booking_by_intent_hash` / `count_terminal_bookings_by_intent_hash`.

- [ ] **Step 3: Implement the three methods**

In `src/agentic_cab/adapters/state_store.py`, after `def list_active_bookings(...)` (line ~204) and before the `# --- Idempotency ---` block, add:

```python
    def find_active_booking_by_intent_hash(
        self: Self, intent_hash_hex: str
    ) -> Booking | None:
        row = self._conn.execute(
            "SELECT booking_id FROM bookings"
            " WHERE intent_hash = ?"
            "   AND status NOT IN ('completed','cancelled','failed')"
            " ORDER BY created_at ASC"
            " LIMIT 1",
            (intent_hash_hex,),
        ).fetchone()
        if row is None:
            return None
        return self.get_booking(row[0])

    def count_terminal_bookings_by_intent_hash(self: Self, intent_hash_hex: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM bookings"
            " WHERE intent_hash = ?"
            "   AND status IN ('completed','cancelled','failed')",
            (intent_hash_hex,),
        ).fetchone()
        return int(row[0]) if row else 0

    def set_booking_intent_hash(self: Self, booking_id: str, intent_hash_hex: str) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE bookings SET intent_hash = ? WHERE booking_id = ?",
                (intent_hash_hex, booking_id),
            )
```

- [ ] **Step 4: Run the tests; expect green**

Run: `uv run pytest tests/unit/test_state_store_intent_hash.py tests/unit/test_state_store.py -v`
Expected: all green. The existing `test_state_store.py` suite must also pass — the additive column should not break anything.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_cab/adapters/state_store.py tests/unit/test_state_store_intent_hash.py
git commit -m "feat(state-store): find_active/count_terminal/set intent_hash

Three additive methods on SqliteStateStore for cross-session dedup lookup.
Active = status NOT IN terminal set. Idempotent overwrite on set."
```

---

## Task 5: Wire `tools/confirm.py` to the new key derivation + duplicate-intent path

**Files:**
- Modify: `src/agentic_cab/tools/confirm.py`
- Create: `tests/unit/test_confirm_dedup.py`
- Modify: `tests/unit/test_tool_confirm.py`

- [ ] **Step 1: Write failing tests for the duplicate-intent path**

Create `tests/unit/test_confirm_dedup.py`:

```python
"""Tests for confirm_booking's duplicate-intent short-circuit. See spec §4.4 + §6.2."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentic_cab.adapters.action_log import JsonlActionLog
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.confirmation.scripted import ScriptedConfirmationChannel
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.adapters.uber_sim.provider import UberSimAdapter
from agentic_cab.core.ids import new_session_id, new_turn_id
from agentic_cab.core.types import AddressRef, RideIntent
from agentic_cab.tools.confirm import ConfirmArgs, confirm_booking


def _addr(name: str, lat: float, lng: float) -> AddressRef:
    return AddressRef(
        raw=name,
        formatted=name,
        lat=lat,
        lng=lng,
        confidence=0.99,
        provider_metadata={"alternates": []},
    )


T0 = datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def harness(tmp_path: Path):
    store = SqliteStateStore(tmp_path / "state.db")
    store.apply_schema()
    log = JsonlActionLog(tmp_path / "logs" / "session.jsonl")
    clock = FrozenClock(T0)
    sim = UberSimAdapter(store=store, clock=clock, rng_seed=42)
    return store, log, clock, sim


async def _prepare_draft(sim: UberSimAdapter, intent: RideIntent):
    options = await sim.discover(intent)
    uberx = next(o for o in options if o.details.vehicle_class == "uberx")
    return await sim.prepare(uberx.option_id)


@pytest.mark.asyncio
async def test_active_prior_booking_without_nonce_returns_duplicate_intent(harness) -> None:
    store, log, clock, sim = harness
    intent = RideIntent(
        pickup=_addr("A", 37.0, -122.0),
        dropoff=_addr("B", 37.1, -122.1),
        when="now",
    )
    channel = ScriptedConfirmationChannel(approvals=["UberX", "UberX"])
    session_id = new_session_id()

    # First confirm — succeeds.
    d1 = await _prepare_draft(sim, intent)
    r1 = await confirm_booking(
        ConfirmArgs(draft_id=d1.draft_id),
        provider=sim, store=store, log=log, channel=channel,
        clock=clock, session_id=session_id, turn_id=new_turn_id(),
    )
    assert r1["status"] == "confirmed"

    # Second confirm — fresh draft, same intent, no nonce.
    d2 = await _prepare_draft(sim, intent)
    assert d2.draft_id != d1.draft_id
    r2 = await confirm_booking(
        ConfirmArgs(draft_id=d2.draft_id),
        provider=sim, store=store, log=log, channel=channel,
        clock=clock, session_id=session_id, turn_id=new_turn_id(),
    )
    assert r2["status"] == "duplicate_intent"
    assert r2["existing_booking_id"] == r1["booking_id"]


@pytest.mark.asyncio
async def test_active_prior_booking_with_nonce_proceeds(harness) -> None:
    store, log, clock, sim = harness
    intent = RideIntent(
        pickup=_addr("A", 37.0, -122.0),
        dropoff=_addr("B", 37.1, -122.1),
        when="now",
    )
    channel = ScriptedConfirmationChannel(approvals=["UberX", "UberX"])
    session_id = new_session_id()

    d1 = await _prepare_draft(sim, intent)
    r1 = await confirm_booking(
        ConfirmArgs(draft_id=d1.draft_id),
        provider=sim, store=store, log=log, channel=channel,
        clock=clock, session_id=session_id, turn_id=new_turn_id(),
    )
    assert r1["status"] == "confirmed"

    d2 = await _prepare_draft(sim, intent)
    r2 = await confirm_booking(
        ConfirmArgs(draft_id=d2.draft_id, intent_override_nonce="user-said-yes"),
        provider=sim, store=store, log=log, channel=channel,
        clock=clock, session_id=session_id, turn_id=new_turn_id(),
    )
    assert r2["status"] == "confirmed"
    assert r2["booking_id"] != r1["booking_id"]


@pytest.mark.asyncio
async def test_terminal_prior_booking_lets_new_through(harness) -> None:
    store, log, clock, sim = harness
    intent = RideIntent(
        pickup=_addr("A", 37.0, -122.0),
        dropoff=_addr("B", 37.1, -122.1),
        when="now",
    )
    channel = ScriptedConfirmationChannel(approvals=["UberX", "UberX"])
    session_id = new_session_id()

    d1 = await _prepare_draft(sim, intent)
    r1 = await confirm_booking(
        ConfirmArgs(draft_id=d1.draft_id),
        provider=sim, store=store, log=log, channel=channel,
        clock=clock, session_id=session_id, turn_id=new_turn_id(),
    )
    assert r1["status"] == "confirmed"

    # Force the first booking to terminal via the StateStore (no public API needed).
    from agentic_cab.core.types import BookingEvent
    store.update_booking_status(
        r1["booking_id"], "completed",
        BookingEvent(ts=T0 + timedelta(minutes=20), kind="completed", payload={}),
    )

    # Advance clock past the 10-minute bucket so when=now hashes differently?
    # NO — generation must advance independently of bucket. Keep clock fixed.
    d2 = await _prepare_draft(sim, intent)
    r2 = await confirm_booking(
        ConfirmArgs(draft_id=d2.draft_id),
        provider=sim, store=store, log=log, channel=channel,
        clock=clock, session_id=session_id, turn_id=new_turn_id(),
    )
    assert r2["status"] == "confirmed"
    assert r2["booking_id"] != r1["booking_id"]
```

- [ ] **Step 2: Run the tests; expect failure**

Run: `uv run pytest tests/unit/test_confirm_dedup.py -v`
Expected: failure — `ConfirmArgs` rejects `intent_override_nonce`; algorithm doesn't return `duplicate_intent`.

- [ ] **Step 3: Update `ConfirmArgs` and rewire the algorithm**

Edit `src/agentic_cab/tools/confirm.py`:

(a) Update imports — replace the `confirm_idempotency_key` import line with:

```python
from agentic_cab.core.ids import confirm_idempotency_key, intent_hash, new_action_id
```

(b) Replace `ConfirmArgs`:

```python
class ConfirmArgs(BaseModel):
    draft_id: str
    intent_override_nonce: str | None = None
```

(c) Replace the `key_factory` signature and the `key = key_factory(...)` line with the new derivation. Specifically, change the function signature default:

```python
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
```

(remove the `key_factory` parameter entirely — tests that injected it must change; see Step 4).

(d) Insert the dedup block between the re-quote block (ending with the `return _emit_outcome("price_changed", ...)` branch) and the comment `# 4. Idempotency claim (invariant 5).`:

```python
    # 3a. Intent guard — ride-only for now (appointment hashing tracked in FOLLOWUPS).
    from agentic_cab.core.types import RideIntent
    if not isinstance(draft.intent_snapshot, RideIntent):
        _emit_verified(
            {**checks, "intent_snapshot_missing": True}, "rejected",
            "intent_snapshot_missing_or_unsupported",
        )
        return _emit_outcome(
            "rejected", {"reason": "intent_snapshot_missing_or_unsupported"},
        )

    # 3b. Compute the intent hash from stable user inputs + chosen vehicle class.
    if not isinstance(draft.option_snapshot.details, RideOptionDetails):
        # Shouldn't happen for ride intents, but guard for type narrowing.
        _emit_verified(
            {**checks, "intent_snapshot_missing": True}, "rejected",
            "option_details_unsupported",
        )
        return _emit_outcome("rejected", {"reason": "option_details_unsupported"})

    ih = intent_hash(
        draft.intent_snapshot,
        draft.option_snapshot.details.vehicle_class,
        now=clock.now(),
    )

    # 3c. Look up an active prior booking with this intent. If found and the agent
    #     did not supply a nonce, short-circuit with duplicate_intent.
    existing = store.find_active_booking_by_intent_hash(ih)
    if existing is not None and args.intent_override_nonce is None:
        _emit_verified(
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
        return _emit_outcome(
            "duplicate_intent",
            {
                "existing_booking_id": existing.booking_id,
                "existing_status": existing.status,
            },
        )

    # 3d. Compute generation (count of terminal prior bookings with this intent).
    generation = store.count_terminal_bookings_by_intent_hash(ih)
```

(e) Replace the existing key derivation line:

```python
    # 4. Idempotency claim (invariant 5). Key derived from (intent_hash, generation, nonce)
    # so an agent retry — even across process restarts — produces the same key and hits
    # claim_or_recall instead of re-booking.
    key = confirm_idempotency_key(ih, generation, args.intent_override_nonce)
```

(f) After the successful `store.finalize_idempotency(key, booking=booking)` line in step 8, add:

```python
        store.set_booking_intent_hash(booking.booking_id, ih)
```

(So the booking is queryable by future sessions.)

(g) Update the docstring at the top of `confirm.py` step 4 to describe the new key derivation.

- [ ] **Step 4: Update `tests/unit/test_tool_confirm.py`**

The existing tests that import `confirm_idempotency_key` or pass `key_factory=` will break. Run them first:

Run: `uv run pytest tests/unit/test_tool_confirm.py -v`
Expected: failures around `key_factory`, `confirm_idempotency_key(draft.draft_id, draft.fingerprint)`, and possibly the `idempotency_replay_short_circuits_second_provider_call` test.

For each broken test:
- Drop the `key_factory=fixed_key` argument; the function no longer takes one.
- Replace `confirm_idempotency_key(draft.draft_id, draft.fingerprint)` with the new signature, deriving the expected key:
  ```python
  from agentic_cab.core.ids import confirm_idempotency_key, intent_hash
  ih = intent_hash(
      draft.intent_snapshot,
      draft.option_snapshot.details.vehicle_class,
      now=clock.now(),
  )
  expected_key = confirm_idempotency_key(ih, 0, None)
  ```
- For the `test_idempotency_replay_short_circuits_second_provider_call` test (lines ~135–180): two confirms with the *same draft* now collide on the SAME intent_hash + generation=0 + nonce=None. The recall path should still fire. Update assertions to reflect that the recall happens via the new derivation; provider.confirm is called exactly once. The `replayed: True` row remains.

- [ ] **Step 5: Run all confirm-related tests; expect green**

Run: `uv run pytest tests/unit/test_tool_confirm.py tests/unit/test_tool_confirm_in_progress.py tests/unit/test_tool_confirm_re_quote.py tests/unit/test_confirm_dedup.py -v`
Expected: all green.

- [ ] **Step 6: Run the full test-fast suite**

Run: `uv run make test-fast`
Expected: all green. Watch in particular for:
- `tests/contract/` — must pass unchanged (port surface didn't change).
- `tests/contract/test_restart_survival.py` — exercises the StateStore across processes; the additive column should be invisible.

- [ ] **Step 7: Commit**

```bash
git add src/agentic_cab/tools/confirm.py tests/unit/test_confirm_dedup.py tests/unit/test_tool_confirm.py
git commit -m "feat(tools/confirm): intent-bound key + duplicate_intent outcome

ConfirmArgs gains intent_override_nonce. New algorithm step 3a-3d:
guard intent_snapshot, compute intent_hash, look up active prior
booking and short-circuit duplicate_intent (no nonce), or compute
generation + proceed. Existing tests updated to the new key derivation
signature; key_factory injection removed."
```

---

## Task 6: Agent system prompt — duplicate_intent handling

**Files:**
- Modify: `src/agentic_cab/agent/system_prompt.py`

- [ ] **Step 1: Add the handling rule**

Edit `src/agentic_cab/agent/system_prompt.py`. In the `Rules:` block, after the `If a tool returns status='rejected', explain the reason ...` bullet, add:

```
  - If confirm_booking returns status='duplicate_intent', the user already
    has an active booking for this trip. Show the user the existing booking's
    booking_id and status, and ask whether they want to book a second,
    separate ride. If — and only if — the user explicitly confirms, retry
    confirm_booking with intent_override_nonce set to a fresh identifier.
    Do not retry without explicit user confirmation.
```

- [ ] **Step 2: Run the prompt-related tests**

Run: `uv run pytest tests/unit/test_chat_repl.py -v`
Expected: green (the prompt is consumed verbatim; no test currently snapshots it; if any test does and snaps, update the snapshot deliberately).

- [ ] **Step 3: Commit**

```bash
git add src/agentic_cab/agent/system_prompt.py
git commit -m "feat(agent): handle duplicate_intent — surface, ask, retry-with-nonce"
```

---

## Task 7: Invariant checkers for the new scenario

**Files:**
- Modify: `src/agentic_cab/scenarios/invariants.py`

- [ ] **Step 1: Read the existing checker registry**

Open `src/agentic_cab/scenarios/invariants.py` and find the `CHECKERS` dict (around line 107). Confirm the pattern: a function `check_<name>(rows, ...) -> bool` plus a registry entry.

- [ ] **Step 2: Add two new checkers**

In `src/agentic_cab/scenarios/invariants.py`, before the `CHECKERS` dict, add:

```python
def check_provider_confirm_call_count(
    rows: list[dict[str, Any]], tool: str, count: int
) -> bool:
    """Assert the number of provider.confirm calls (executed rows for `tool`) equals `count`.

    Replayed executed rows (replayed=True) are NOT counted — they reflect a recall, not
    a provider call. This is the load-bearing invariant for cross-session dedup:
    two confirm attempts with the same intent should produce exactly one provider call.
    """
    n = sum(
        1
        for r in rows
        if r.get("kind") == "executed"
        and r.get("tool") == tool
        and not r.get("replayed", False)
    )
    return n == count


def check_outcome_present(rows: list[dict[str, Any]], tool: str, status: str) -> bool:
    """Assert there is at least one outcome row for `tool` with the given status."""
    return any(
        r.get("kind") == "outcome"
        and r.get("tool") == tool
        and r.get("status") == status
        for r in rows
    )
```

And register them in `CHECKERS`:

```python
    "provider_confirm_call_count": check_provider_confirm_call_count,
    "outcome_present": check_outcome_present,
```

- [ ] **Step 3: Add unit coverage in the existing checker test file**

Append to `tests/scenarios/test_invariant_checkers.py`:

```python
def test_provider_confirm_call_count_counts_only_non_replayed() -> None:
    from agentic_cab.scenarios.invariants import check_provider_confirm_call_count

    rows = [
        {"kind": "executed", "tool": "confirm_booking", "replayed": False},
        {"kind": "executed", "tool": "confirm_booking", "replayed": True},
        {"kind": "executed", "tool": "confirm_booking", "replayed": False},
    ]
    assert check_provider_confirm_call_count(rows, "confirm_booking", 2)
    assert not check_provider_confirm_call_count(rows, "confirm_booking", 3)


def test_outcome_present_matches_tool_and_status() -> None:
    from agentic_cab.scenarios.invariants import check_outcome_present

    rows = [
        {"kind": "outcome", "tool": "confirm_booking", "status": "duplicate_intent"},
        {"kind": "outcome", "tool": "confirm_booking", "status": "confirmed"},
    ]
    assert check_outcome_present(rows, "confirm_booking", "duplicate_intent")
    assert not check_outcome_present(rows, "confirm_booking", "rejected")
```

- [ ] **Step 4: Run the invariant checker tests**

Run: `uv run pytest tests/scenarios/test_invariant_checkers.py -v`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_cab/scenarios/invariants.py tests/scenarios/test_invariant_checkers.py
git commit -m "feat(invariants): provider_confirm_call_count + outcome_present checkers"
```

---

## Task 8: New scenario `06_cross_session_dedup.yaml`

**Files:**
- Create: `scenarios/06_cross_session_dedup.yaml`

- [ ] **Step 1: Author the scenario**

Create `scenarios/06_cross_session_dedup.yaml`:

```yaml
name: 06_cross_session_dedup
description: Same ride intent, two fresh drafts — second confirm short-circuits as duplicate_intent; agent retries with nonce after explicit user yes.
seed: 42
clock:
  start: "2026-05-20T12:00:00+00:00"
  mode: frozen
sim:
  rng_seed: 42
confirmation:
  channel: scripted
  script:
    - match: "UberX"
      action: approve
    - match: "UberX"
      action: approve
user_turns:
  - "Book me a ride from 37.7749,-122.4194 (Market & Powell) to 37.7849,-122.4094 (Union Square)."
  - "UberX please — go ahead and confirm."
  - "Actually, book the same ride again — I need a second car for my friend."
  - "Yes, I really want another. Go ahead."
invariants:
  - kind: action_log_contains
    where: {kind: "outcome", status: "confirmed"}
  - kind: four_row_pattern
    tool: "confirm_booking"
  - kind: gate_invoked_for_tool
    tool: "confirm_booking"
  - kind: provider_confirm_call_count
    tool: "confirm_booking"
    count: 2
  - kind: outcome_present
    tool: "confirm_booking"
    status: "duplicate_intent"
```

- [ ] **Step 2: Smoke-run the scenario via the CLI entrypoint**

Run: `uv run python -m agentic_cab run-scenario 06_cross_session_dedup`
Expected: completes without raising. Read the resulting `logs/<session>.jsonl` and confirm the row sequence includes a `verified(rejected, "duplicate_intent")` row followed by an `outcome(duplicate_intent)` row, then a second `executed(replayed=false)` after the user's explicit "yes." If the agent doesn't produce the third/fourth turn correctly (e.g., it doesn't supply a nonce), iterate on the system prompt in Task 6 before continuing.

- [ ] **Step 3: Commit**

```bash
git add scenarios/06_cross_session_dedup.yaml
git commit -m "test(scenarios): 06 — cross-session dedup w/ explicit-yes override"
```

---

## Task 9: Scenario test wiring

**Files:**
- Create: `tests/scenarios/test_scenario_06_cross_session_dedup.py`

- [ ] **Step 1: Author the test**

Create `tests/scenarios/test_scenario_06_cross_session_dedup.py`, mirroring `tests/scenarios/test_scenario_04_surge_on_confirm.py` for structure:

```python
"""End-to-end coverage for scenarios/06_cross_session_dedup.yaml."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_cab.scenarios.invariants import evaluate
from agentic_cab.scenarios.runner import run_scenario


@pytest.mark.scenario
@pytest.mark.asyncio
async def test_06_cross_session_dedup(tmp_path: Path) -> None:
    scenario_path = Path("scenarios/06_cross_session_dedup.yaml")
    session_id = await run_scenario(
        scenario_path,
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        chat_dir=tmp_path / "chat",
    )

    log_path = tmp_path / "logs" / f"{session_id}.jsonl"
    rows = [
        __import__("json").loads(line)
        for line in log_path.read_text().splitlines()
        if line.strip()
    ]

    import yaml
    spec = yaml.safe_load(scenario_path.read_text())
    results = evaluate(rows, spec["invariants"])
    failures = [(kind, params) for kind, passed, params in results if not passed]
    assert not failures, f"invariant failures: {failures}"

    # Hard sanity: exactly two non-replayed `executed` rows for confirm_booking.
    confirm_executes = [
        r for r in rows
        if r.get("kind") == "executed"
        and r.get("tool") == "confirm_booking"
        and not r.get("replayed", False)
    ]
    assert len(confirm_executes) == 2

    # Hard sanity: exactly one outcome row with status="duplicate_intent".
    dups = [
        r for r in rows
        if r.get("kind") == "outcome"
        and r.get("tool") == "confirm_booking"
        and r.get("status") == "duplicate_intent"
    ]
    assert len(dups) == 1
```

If the `run_scenario` signature doesn't match (`data_dir`/`log_dir`/`chat_dir` may have different names — check `src/agentic_cab/scenarios/runner.py` line 46), adapt the call accordingly. Inspect `tests/scenarios/test_scenario_04_surge_on_confirm.py` for the canonical invocation.

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/scenarios/test_scenario_06_cross_session_dedup.py -v`
Expected: green.

- [ ] **Step 3: Run the full scenario test suite**

Run: `uv run pytest tests/scenarios/ -v`
Expected: all green; no regressions in 01–05.

- [ ] **Step 4: Commit**

```bash
git add tests/scenarios/test_scenario_06_cross_session_dedup.py
git commit -m "test(scenarios): pytest coverage for 06_cross_session_dedup"
```

---

## Task 10: Re-render all transcripts and verify diff scope

**Files:**
- Modify: `transcripts/01_happy_path.md`, `02_price_comparison.md`, `03_bad_address.md`, `05_cancel_with_fee.md`
- Create: `transcripts/06_cross_session_dedup.md`
- (`04_surge_on_confirm.md` must be byte-identical after re-render.)

- [ ] **Step 1: Snapshot the pre-rerender state for diff comparison**

Run:
```bash
mkdir -p /tmp/transcript-pre-rerender
cp transcripts/*.md /tmp/transcript-pre-rerender/
```

- [ ] **Step 2: Re-render all transcripts**

Run: `uv run make transcripts`
Expected: completes without error. Five existing + one new file present.

- [ ] **Step 3: Verify `04_surge_on_confirm.md` is byte-identical**

Run: `diff /tmp/transcript-pre-rerender/04_surge_on_confirm.md transcripts/04_surge_on_confirm.md`
Expected: no output (byte-identical). If there is any diff, stop — investigate before continuing.

- [ ] **Step 4: Verify the other four transcripts diff ONLY on `idempotency_key`**

For each of `01_happy_path`, `02_price_comparison`, `03_bad_address`, `05_cancel_with_fee`:

```bash
diff /tmp/transcript-pre-rerender/<name>.md transcripts/<name>.md | grep -v 'idempotency_key'
```

Expected: empty output for each (after filtering out idempotency_key changes, nothing else differs). Any other diff is a regression — stop and investigate.

Note: the `grep` filters lines containing the substring `idempotency_key`. If the diff format places the key on a different line from that substring, switch to a more careful inspection (e.g., view the diff and manually confirm each chunk).

- [ ] **Step 5: Commit**

```bash
git add transcripts/
git commit -m "test(transcripts): re-render with intent-bound idempotency keys

01, 02, 03, 05: idempotency_key hex digest changes; all other bytes identical.
04 (price_changed short-circuit): byte-identical.
06: new scenario."
```

---

## Task 11: Documentation sweep — spec, CLAUDE.md, WRITEUP, FOLLOWUPS

**Files:**
- Modify: `docs/superpowers/specs/2026-05-19-agentic-cab-rides-design.md`
- Modify: `CLAUDE.md`
- Modify: `docs/WRITEUP.md`
- Modify: `docs/FOLLOWUPS.md`

- [ ] **Step 1: Update the design spec §10a**

Open `docs/superpowers/specs/2026-05-19-agentic-cab-rides-design.md` and locate §10a (idempotency). Replace the paragraph that describes confirm-key derivation as `sha256({draft_id, fingerprint, op:"confirm"})` with:

```
The confirm idempotency key is derived from a hash of the user's stable intent
(pickup, dropoff, 10-minute-bucketed `when`, party size, vehicle class), a
generation counter (number of prior bookings with this intent that reached a
terminal status), and an optional nonce supplied by the agent on the user's
explicit "yes, book another" path. Formally:

  intent_hash = sha256(canonical_json({pickup, dropoff, when, party_size, vehicle_class}))
  key         = sha256(canonical_json({intent_hash, generation, nonce, op:"confirm"}))

This makes the key invariant across `draft_id` regenerations — including across
process restarts — so an agent re-issuing the same user intent after a crash
collides on the existing `claim_or_recall` entry instead of double-booking.
Cancel keys remain bound to (booking_id, fee_cents, status_at_quote).

See docs/superpowers/specs/2026-05-20-cross-session-idempotency-design.md.
```

Then locate the `confirm_booking` return-shape table (or paragraph) and add the new outcome:

```
| duplicate_intent | {existing_booking_id, existing_status}
                   | The user already has an active booking for this intent.
                   | Agent should ask the user; on explicit yes, retry with
                   | intent_override_nonce set. |
```

(Match the existing table style; if the spec uses prose instead of a table, mirror that prose form.)

- [ ] **Step 2: Update `CLAUDE.md` invariant 5**

Open `CLAUDE.md` and locate invariant 5. Replace it with:

```
5. **Idempotency keys are required.** `BookingProvider.confirm` and `cancel`
   take an `idempotency_key`. SQLite enforces `UNIQUE`. `claim_or_recall`
   short-circuits the second provider call. The confirm key is derived from
   a hash of the user's stable intent (pickup, dropoff, 10-minute-bucketed
   `when`, party size, vehicle class), a generation counter advanced when a
   prior booking with this intent reaches terminal status, and an optional
   nonce supplied by the agent when the user explicitly asks for a second
   identical booking. This keeps the key invariant across `draft_id`
   regenerations and across process restarts. Cancel keys remain bound to
   `(booking_id, fee_cents, status_at_quote)`.
```

- [ ] **Step 3: Update `docs/WRITEUP.md`**

Open `docs/WRITEUP.md`. In §2 ("What breaks first in production"), replace the **Cross-session idempotency** paragraph with a one-sentence "hardened" mention, e.g.:

```
**Cross-session idempotency.** Hardened. The confirm key is now bound to a
hash of the user's stable intent (pickup, dropoff, time bucket, party size,
vehicle class) rather than the per-session `draft_id`, with a generation
counter for legitimate re-bookings and an agent-supplied nonce for the
explicit "yes, book another" path. See
`docs/superpowers/specs/2026-05-20-cross-session-idempotency-design.md`.
```

In §3 ("What I'd build next, in priority order"), drop the first bullet (Cross-session idempotency) and re-number the rest. The new top-of-list is the background `track_booking` polling loop.

- [ ] **Step 4: Update `docs/FOLLOWUPS.md`**

Open `docs/FOLLOWUPS.md`. Under the **Architecture / engineering** section, add:

```
- [ ] **Appointment-intent hashing.** `intent_hash` raises `NotImplementedError`
  for `AppointmentIntent`. When the Zocdoc/appointment swap goes from prose to
  code, mirror the ride hash with appointment-stable fields (subject, location,
  earliest_at/latest_at bucket). — cross-session-idempotency slice.
- [ ] **Duplicate-intent UX polish.** The `duplicate_intent` outcome carries
  only `existing_booking_id` + `existing_status`. The agent then has to call
  `track_booking` or `get_booking` separately to show the user the fare/ETA
  before asking "book another?". Bundle the existing booking's summary into
  the `duplicate_intent` outcome payload so the agent can surface it in one
  turn. — cross-session-idempotency slice.
```

- [ ] **Step 5: Verify durable-doc rules**

Run pre-commit on the changed docs to catch dead links and any stray TODOs:

```bash
uv run pre-commit run --files \
  docs/superpowers/specs/2026-05-19-agentic-cab-rides-design.md \
  CLAUDE.md docs/WRITEUP.md docs/FOLLOWUPS.md
```
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/specs/2026-05-19-agentic-cab-rides-design.md \
        CLAUDE.md docs/WRITEUP.md docs/FOLLOWUPS.md
git commit -m "docs: reflect cross-session intent-bound idempotency

spec §10a + CLAUDE.md invariant 5 rewrite to describe the new key
derivation. WRITEUP §2 marks it hardened; §3 drops the now-resolved
top item. FOLLOWUPS gains the appointment-hash + UX-polish items."
```

---

## Task 12: Final verification

- [ ] **Step 1: Run `make verify`**

Run: `uv run make verify`
Expected: green (fmt-check + lint + typecheck + test-fast).

- [ ] **Step 2: Run the full test suite**

Run: `uv run make test`
Expected: green. This includes the scenario suite and any slow tests.

- [ ] **Step 3: Quick sanity replay of the new scenario**

Find the session ID of the latest `06_cross_session_dedup` run (look in `logs/`) and replay it:

```bash
uv run make replay SESSION=<session_id>
```
Expected: human-readable replay showing two `confirmed` outcomes and one `duplicate_intent` outcome in the correct order.

- [ ] **Step 4: Confirm no regressions in transcript byte-count for `04_surge_on_confirm.md`**

```bash
diff /tmp/transcript-pre-rerender/04_surge_on_confirm.md transcripts/04_surge_on_confirm.md && echo "byte-identical OK"
```
Expected: `byte-identical OK`.

- [ ] **Step 5: Final commit if anything trailing**

If `make verify` or `make test` produced incidental fixes (e.g., a snapshot file update for the system prompt), commit them with a focused message. Otherwise skip.

- [ ] **Step 6: Push & open PR**

```bash
git push -u origin HEAD
gh pr create --title "feat: cross-session intent-bound idempotency" --body "$(cat <<'EOF'
## Summary
- Rebinds `confirm_booking`'s idempotency key from `(draft_id, fingerprint)` to a hash of the user's stable intent + generation counter + optional agent nonce.
- New `duplicate_intent` outcome short-circuits the second confirm of an already-active booking; agent re-prompts the user, who can opt in to a second booking via an opaque nonce.
- Closes the WRITEUP §2 "first thing that breaks in production" gap.

## Test plan
- [x] `make verify` green
- [x] `make test` green (incl. scenario suite)
- [x] New scenario `06_cross_session_dedup` covers the dedup path + explicit-yes override
- [x] Pre-existing transcripts re-rendered: 01/02/03/05 diff only on `idempotency_key`; 04 byte-identical
- [x] Spec, CLAUDE.md, WRITEUP, FOLLOWUPS updated

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Notes for the implementer

- **Run `make help` first** if any Makefile target above doesn't behave as expected — discovery surface lives there.
- **`uv run` everywhere** — never invoke `pytest` / `python` outside `uv run` (per `CLAUDE.md` "Repo conventions").
- **Do not weaken any existing invariant** — if a step seems to require it (e.g., to make a test pass), stop and re-read the spec.
- **No `--no-verify`. Ever.** If a hook fails, fix the underlying issue.
- **`docs/scratch/`** is fine for thinking-out-loud during implementation; gitignored.
- **The slice-delivery discipline** applies: invoke the `slice-delivery` skill if you find yourself horizontally batching layers instead of vertically completing tasks. Each task above is itself a small vertical slice (test → code → commit).
