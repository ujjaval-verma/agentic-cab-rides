# Follow-ups

This file is the **only** place "TODO"-class items live in this repo. The lint rule (`ruff FIX` + `no-todo-in-code` hook) forbids `TODO`/`FIXME`/`XXX`/`HACK` in source. Promote durable items here instead of leaving them inline.

## Format

```
- [ ] <one-line description> — <why deferred> — <slice-or-commit-ref>
```

Group by category. Keep entries actionable; delete when done (don't archive — git log is the archive).

---

## Architecture / engineering

- [ ] **ConfirmationResult coherence validator.** Add a `model_validator(mode="after")` asserting `(approved and reason == "approved") or (not approved and reason != "approved")`. Today the model permits incoherent states like `approved=True, reason="declined"`. Low blast radius for prototype but a latent inconsistency. — T4 Ralph N2.
- [ ] **Typed ConfirmationRequest.details.** Replace `details: dict[str, Any]` with a `ConfirmationDetails` Pydantic model (price: Money, eta_seconds, pickup, dropoff, vehicle_class) so the web channel's SSE payload has a typed contract. — T4 Ralph Q2.
- [ ] **`BookingStatus` type alias for `StateStore.update_booking_status`.** The `status: str` parameter is loose — ideally a `Literal[...]` alias exported from `types.py` so callers are type-checked. Non-blocking for P2/P3 (adapter will be strict internally); promote when `Booking.status` type is referenced in a third location. — T4 Ralph review.
- [ ] **Intent-bound idempotency keys.** Today the tool layer mints a fresh ULID at the top of `confirm_booking` / `cancel_booking`, so an agent retry after a network timeout mints a NEW key and bypasses the dedupe barrier. Real fix: derive the key deterministically from intent state, e.g. `sha256(draft_id + ":confirm")`. Tracked in `WRITEUP.md` as "what breaks first in production." — P3 contract suite + retrofit.
- [ ] **AutoApproveChannel structural guard.** When `adapters/confirmation/auto.py` lands (P1), its `__init__` must assert `os.environ.get("ALLOW_AUTO_APPROVE") == "1"` and raise otherwise. Add a unit test that asserts construction raises by default and succeeds with the env var set. Right now this is only enforced by prose in the spec; an external reviewer will rightly probe it. — P1 confirmation slice.
- [ ] **Mock `AppointmentProvider` in the contract suite.** The Zocdoc-reach claim is currently paper-only — the architecture supports it but no test exercises an `AppointmentIntent` end-to-end. Ship a minimal in-memory `MockAppointmentProvider` that satisfies the contract suite, proving the abstraction holds beyond ride-share. Strong signal for the writeup. — P3 contract suite expansion.
- [ ] **Action-log retention/rotation.** Logs currently grow without bound under `logs/<session>.jsonl`. Add a `make logs-rotate` target and a retention policy doc (likely just "keep last 100 sessions" for the prototype) before any long-running demo. — post-P6.
- [ ] **Restart-survival semantics for claim_or_recall.** P1 returns status='new' for an in-progress (claimed-but-not-finalised) idempotency_keys row, which lets the caller retry the provider call with the same key. P3 should introduce status='in_progress' alongside intent-bound keys so a single canonical recovery path exists. — T7 Ralph B1.
- [ ] **SqliteStateStore thread-safety for FastAPI web path.** Connection opened with default check_same_thread=True. Web path (P4/P5) will hit "objects can only be used in same thread" if a shared store is used across asyncio handlers. Fix: connection-per-request or check_same_thread=False with locking. — T7 Ralph N7.
- [ ] **Scenario YAML top-level `seed` field is unused.** runner.py only consumes `sim.rng_seed`; the top-level `seed` is parsed but ignored. Use it as a single canonical seed source (derive `sim.rng_seed` from it, or remove the top-level field). — T15 Ralph.
- [ ] **Scenario YAML `clock.mode` not validated.** runner.py always constructs FrozenClock regardless of `clock.mode` value. Add a guard that raises if `clock.mode != "frozen"` (interactive mode is a different code path). — T15 Ralph.
- [ ] **`_by_action` raises KeyError on rows missing `action_id`.** All current log rows include `action_id` per invariant 6, but a defensive `.get("action_id", "")` would prevent hard failures if a malformed log is fed to the invariant checkers. — T15 Ralph.

## Tests / coverage gaps

- [ ] **Fingerprint property test: permutations.** Current test only reverses dict insertion order; replace with st.permutations or hypothesis shuffling to cover arbitrary key orderings. — T3 Ralph N1.
- [ ] **Tighten `fingerprint()` parameter type to Option.** Today accepts any BaseModel; spec §4 only requires Option. — T3 Ralph Q1.
- [ ] **Booking.events round-trip inconsistency.** claim_or_recall's recalled Booking carries the events list as serialised at finalize_idempotency time. get_booking reconstructs a Booking with no events (events table not joined). Decide canonical semantics in P3 contract suite design — possibly: claim_or_recall returns only booking_id and T13 re-fetches via get_booking. — T7 Ralph Q1.
- [ ] **Filter `approved_at: null` from declined verified rows.** When the user declines, the `verified.checks.confirmation` dump includes `approved_at: null`. Spec §8 examples only show the field when approved. Consider filtering on the declined path to keep audit rows tight, or update spec §8 to acknowledge the null. — T13 Ralph A5.
## Product / UX

- (none yet)

## Operational

- (none yet)
