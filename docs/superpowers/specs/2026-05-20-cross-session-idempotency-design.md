# Cross-session intent-bound idempotency

**Status:** draft — design approved 2026-05-20
**Owner:** Ujjaval
**Touches:** `core/ids.py`, `core/ports.py` (StateStore), `adapters/state_store.py`, `adapters/schema.sql`, `tools/confirm.py`, agent system prompt, one new scenario
**Spec it amends:** `docs/superpowers/specs/2026-05-19-agentic-cab-rides-design.md` §10a (idempotency)
**Invariant it amends:** CLAUDE.md invariant 5

## 1. Problem

Today the `confirm_booking` idempotency key is

```
sha256({draft_id, fingerprint, op:"confirm"})
```

`draft_id` is a fresh ULID minted by `provider.prepare()`. Within one session an
agent retry after a network timeout reuses the same `draft_id` and the key
collides — `claim_or_recall` short-circuits the second `provider.confirm` call.

Across sessions the guarantee evaporates. A fresh process re-issuing the same
user intent calls `provider.prepare()` again, mints a new `draft_id`, and
produces a new key. The user crashes mid-confirm, restarts, repeats their ask,
and double-books.

`WRITEUP.md` §2 names this as the first thing that breaks in production.
CLAUDE.md invariant 5 acknowledges it as a known gap. This spec closes it.

## 2. Solution shape

Replace the key derivation. The new key is bound to a hash of the **user's
stable intent**, not the `draft_id` produced by the provider.

```
intent_hash = sha256(canonical_json({
    pickup:        {formatted, lat, lng},
    dropoff:       {formatted, lat, lng},
    when:          bucketed_when,
    party_size:    int,
    vehicle_class: str,
}))

confirm_idempotency_key = sha256(canonical_json({
    intent_hash:   str,
    generation:    int,
    nonce:         str | None,
    op:            "confirm",
}))
```

Where:

- **`bucketed_when`**: `RideIntent.when == "now"` → floor `clock.now()` to the
  nearest 10-minute mark, ISO-format. A concrete datetime hashes to its own
  ISO string. Two "now" requests within 10 minutes collide; two an hour apart
  do not.
- **`generation`**: count of prior bookings with this `intent_hash` that
  reached a terminal status (`completed`, `cancelled`, `failed`). Lets the user
  legitimately re-book the same trip after the first one is over.
- **`nonce`**: explicit disambiguator from the agent when the user has been
  shown the existing booking and confirmed "yes, really book another."
  Opaque to the tool layer — any fresh ULID works.

Scope is **ride only.** `intent_hash` on `AppointmentIntent` raises
`NotImplementedError`. Appointment hashing is tracked as a follow-up.

## 3. Invariant changes

CLAUDE.md invariant 5 currently reads:

> Idempotency keys are required. … In this prototype the tool layer mints
> keys per call (a ULID at the top of `confirm_booking`); making keys
> intent-bound — so an agent retry after a timeout reuses the same key — is
> a known gap tracked in `docs/FOLLOWUPS.md`.

After this change:

> Idempotency keys are required. `BookingProvider.confirm` and `cancel` take
> an `idempotency_key`. SQLite enforces `UNIQUE`. `claim_or_recall`
> short-circuits the second provider call. The confirm key is derived from a
> hash of the user's stable intent (pickup, dropoff, time bucket, party size,
> vehicle class), a generation counter (number of prior terminal bookings for
> that intent), and an optional nonce supplied by the agent when the user
> explicitly wants a second identical booking. Cancel keys remain bound to
> `(booking_id, fee_cents, status_at_quote)` as today.

The four other relevant invariants (1, 2, 3, 6) are unchanged. The layer
direction, structural gate, fingerprint check, and four-row log all carry
through untouched.

## 4. Surface changes

### 4.1 `core/ids.py`

New:

```python
def intent_hash(
    intent: RideIntent,
    vehicle_class: str,
    *,
    now: datetime,
) -> str: ...

def confirm_idempotency_key(
    intent_hash: str,
    generation: int,
    nonce: str | None,
) -> str: ...
```

The old `confirm_idempotency_key(draft_id, fingerprint)` is **deleted** — no
deprecation period (prototype, no external callers). `cancel_idempotency_key`
is untouched.

### 4.2 `core/ports.py` (StateStore Protocol)

Two new methods:

```python
def find_active_booking_by_intent_hash(
    self, intent_hash: str
) -> Booking | None: ...

def count_terminal_bookings_by_intent_hash(
    self, intent_hash: str
) -> int: ...
```

"Active" = `status not in {completed, cancelled, failed}`. "Terminal" = the
complement.

### 4.3 `adapters/schema.sql` + `adapters/state_store.py`

Additive: a new `intent_hash TEXT` column on `bookings`, indexed. Populated by
`put_booking` whenever the caller passes a non-null intent hash. Existing
rows keep `NULL`; both new lookup methods skip them, which is acceptable for a
prototype — `make reset` is the documented escape hatch.

`bookings.intent_hash` is **not** a foreign key to anything; it is a derived
field carried for lookup. The authoritative intent lives in
`drafts.intent_snapshot` (already there).

### 4.4 `tools/confirm.py`

`ConfirmArgs` gains:

```python
class ConfirmArgs(BaseModel):
    draft_id: str
    intent_override_nonce: str | None = None
```

Algorithm change, inserted between current step 3 (re-quote) and step 4
(idempotency claim):

```
3a. Guard: draft.intent_snapshot must be non-null and a RideIntent. If not,
    emit verified(rejected, "intent_snapshot_missing") and return outcome
    "rejected". (Prototype invariant: provider.prepare() populates
    intent_snapshot. The guard exists to make the failure mode explicit.)

3b. Compute:
       ih = intent_hash(
              draft.intent_snapshot,
              draft.option_snapshot.details.vehicle_class,
              now=clock.now(),
            )

3c. existing = store.find_active_booking_by_intent_hash(ih)
    if existing is not None and args.intent_override_nonce is None:
        emit verified(
            checks={..., "intent_hash": ih, "existing_booking_id": existing.booking_id},
            result="rejected",
            reason="duplicate_intent",
        )
        return outcome(
            "duplicate_intent",
            {
                "existing_booking_id": existing.booking_id,
                "existing_status": existing.status,
            },
        )

3d. generation = store.count_terminal_bookings_by_intent_hash(ih)
    key = confirm_idempotency_key(ih, generation, args.intent_override_nonce)

[step 4 continues with claim_or_recall(key, scope="confirm")]
```

When persisting the booking (`store.put_booking` via `finalize_idempotency`),
the adapter writes `intent_hash = ih`. Implementation detail: pass `ih` into
`finalize_idempotency` or set it on the `Booking` model — the design leaves
this to the implementation plan; the constraint is that `bookings.intent_hash`
is populated by the time `find_active_booking_by_intent_hash` could see it
from a subsequent session.

### 4.5 Outcomes returned by `confirm_booking`

Existing: `confirmed`, `rejected`, `price_changed`, `in_progress`, `error`.

**New:** `duplicate_intent` with payload
`{existing_booking_id, existing_status}`.

### 4.6 Agent system prompt

One added paragraph (exact wording in the implementation plan):

> When `confirm_booking` returns `duplicate_intent`, the user already has an
> active booking for this trip. Present the existing booking's status to the
> user and ask whether they want to book a second, separate ride. If the user
> explicitly confirms, retry `confirm_booking` with `intent_override_nonce`
> set to a fresh identifier. Do not retry without explicit user confirmation.

## 5. Action log

No new row kind. The `verified` row on a duplicate-intent rejection carries:

```json
{
  "kind": "verified",
  "tool": "confirm_booking",
  "checks": {
    "draft_found": true,
    "fingerprint_match": true,
    "intent_hash": "<hex>",
    "existing_booking_id": "<ulid>"
  },
  "result": "rejected",
  "reason": "duplicate_intent"
}
```

The subsequent `outcome` row carries `status: "duplicate_intent"` and the
same `existing_booking_id` / `existing_status` fields. Per invariant 6, no
`executed` row appears — `verified.result == "rejected"` short-circuits.

## 6. Tests

### 6.1 Unit — `tests/unit/test_intent_hash.py`

- Same `RideIntent` + same vehicle class + same time → same hash.
- `when="now"` collapses to the same hash within a 10-minute bucket; flips
  across the bucket boundary.
- Differing `pickup`, `dropoff`, `party_size`, `vehicle_class` each produce
  different hashes.
- `AppointmentIntent` raises `NotImplementedError`.
- Hash is stable across dict key ordering (rides on `canonical_json`,
  already tested for `fingerprint`).

### 6.2 Unit — `tests/unit/test_confirm_dedup.py`

With a faked `StateStore`:

- Active prior booking, no nonce → `duplicate_intent`, `provider.confirm`
  is never called, four-row log shape is `requested → verified(rejected) →
  outcome(duplicate_intent)`.
- Active prior booking, nonce supplied → proceeds to `claim_or_recall`,
  fresh key, normal four-row flow.
- Terminal prior booking, no nonce → `generation` advances, fresh key,
  normal flow.
- No prior booking → `generation = 0`, normal flow.

### 6.3 Contract — unchanged

The contract suite at `tests/contract/test_booking_provider_contract.py` is
untouched. The `BookingProvider` port surface does not change. This is
deliberate evidence that the fix is tool-layer, not port-layer.

### 6.4 Scenario — `scenarios/NN_cross_session_dedup.yaml`

(Number assigned at implementation time — next free.)

Drives three turns under a single seeded `FrozenClock`:

1. User asks for a ride to the airport. Agent discovers, prepares, confirms.
   Booking A created.
2. Process "restart" simulated by clearing the agent's in-memory state but
   keeping `data/state.db` and `logs/`. User repeats the same ask. Agent
   discovers + prepares (new `draft_id`) and calls `confirm_booking`. Tool
   returns `duplicate_intent` with Booking A's ID. Agent surfaces this to
   the user.
3. User says "yes, book another one." Agent retries `confirm_booking` with
   `intent_override_nonce = <fresh ULID>`. Second `provider.confirm` fires;
   Booking B created.

Invariant assertions:

- Exactly two `provider.confirm` calls across the three turns (proven by
  counting `executed` rows for `confirm_booking`).
- Turn 2's action log contains a `verified(rejected, "duplicate_intent")`
  row and an `outcome(duplicate_intent)` row, with no `executed` row in
  between.
- `bookings.intent_hash` for Booking A and Booking B is **identical** (same
  intent), but their idempotency keys are different (different generation
  or nonce).

## 7. Out of scope

- **Appointment intent hashing.** Tracked in `FOLLOWUPS.md` as a separate
  item.
- **Cancel-key intent binding.** Cancels are already bound to
  `(booking_id, fee_cents, status_at_quote)`, which is stable across
  sessions for a given booking. No change needed.
- **Backfilling `intent_hash` on existing booking rows.** Prototype;
  `make reset` clears state.
- **UX polish on the duplicate-intent prompt** (showing fare, ETA, driver
  status). Follow-up.
- **Background `track_booking` polling.** Explicitly dropped from this
  slice per brainstorming session.

## 8. Documentation updates landing in the same slice

- `docs/superpowers/specs/2026-05-19-agentic-cab-rides-design.md` §10a:
  replace the idempotency-key paragraph; add `duplicate_intent` to the
  `confirm_booking` returns table.
- `CLAUDE.md` invariant 5: rewrite per §3 above.
- `docs/WRITEUP.md` §2: move "Cross-session idempotency" from "what breaks"
  to a one-line "what was hardened" line; promote agent ↔ reality drift to
  top of the §3 priority list.
- `docs/FOLLOWUPS.md`: add (a) appointment-intent hash, (b) duplicate-intent
  UX polish.

## 9. Slice plan (high-level — full plan via `writing-plans`)

1. `core/ids.py` — `intent_hash` + new `confirm_idempotency_key`, TDD.
2. Schema + `StateStore` — additive column + two lookup methods.
3. `tools/confirm.py` — wire it in, delete old key helper.
4. Agent prompt + duplicate-intent handling path.
5. New scenario + invariant checker entries.
6. Docs sweep (spec → CLAUDE.md → WRITEUP → FOLLOWUPS).
7. `make verify`; re-render only the new scenario's transcript; confirm
   the other four are byte-identical (this is the deterministic-build
   regression check).

## 10. Acceptance

- Cross-session double-confirm with identical user intent results in exactly
  one `provider.confirm` call. Proven by `scenarios/NN_cross_session_dedup`.
- The contract suite passes unchanged.
- `make verify` is green.
- All five pre-existing transcripts re-render byte-identical.
- Spec, CLAUDE.md, WRITEUP, and FOLLOWUPS reflect the new state.
