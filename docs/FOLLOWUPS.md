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
- [ ] **AutoApproveChannel structural guard.** When `adapters/confirmation/auto.py` lands (P1), its `__init__` must assert `os.environ.get("ALLOW_AUTO_APPROVE") == "1"` and raise otherwise. Add a unit test that asserts construction raises by default and succeeds with the env var set. Right now this is only enforced by prose in the spec; an external reviewer will rightly probe it. — P1 confirmation slice.
- [ ] **Mock `AppointmentProvider` in the contract suite.** The Zocdoc-reach claim is currently paper-only — the architecture supports it but no test exercises an `AppointmentIntent` end-to-end. Ship a minimal in-memory `MockAppointmentProvider` that satisfies the contract suite, proving the abstraction holds beyond ride-share. Strong signal for the writeup. — P3 contract suite expansion.
- [ ] **Action-log retention/rotation.** Logs currently grow without bound under `logs/<session>.jsonl`. Add a `make logs-rotate` target and a retention policy doc (likely just "keep last 100 sessions" for the prototype) before any long-running demo. — post-P6.
- [ ] **Scenario YAML top-level `seed` field is unused.** runner.py only consumes `sim.rng_seed`; the top-level `seed` is parsed but ignored. Use it as a single canonical seed source (derive `sim.rng_seed` from it, or remove the top-level field). — T15 Ralph.
- [ ] **Scenario YAML `clock.mode` not validated.** runner.py always constructs FrozenClock regardless of `clock.mode` value. Add a guard that raises if `clock.mode != "frozen"` (interactive mode is a different code path). — T15 Ralph.
- [ ] **`_by_action` raises KeyError on rows missing `action_id`.** All current log rows include `action_id` per invariant 6, but a defensive `.get("action_id", "")` would prevent hard failures if a malformed log is fed to the invariant checkers. — T15 Ralph.
- [ ] **NominatimGeocoder throttle Lock has no concurrency test (T19 review I1).** The current `test_second_call_within_one_second_waits` serializes calls sequentially; a true concurrency test would fire two `asyncio.gather`'d calls and assert the second was delayed ~1s. The Lock could be removed today without test failure. — P3+.
- [ ] **`summary_title` log redaction (T20-T22 review):** `tools/track.py` and `tools/list_active.py` emit `booking.summary.title` directly into action-log outcome rows. The Uber sim's titles are benign ("UberX") but a real provider could include driver name or plate (PII). Before integrating any real adapter, route `summary_title` through `core.privacy.redact()` or pin a sanitized field on `Option`. Tied to invariant 8.
- [ ] **`UberSimAdapter._any_draft_prepared` is a stateful workaround for the surge-on-confirm hook (T28 review).** Once `prepare()` is called, the flag never resets — so a re-prepare-after-rejection on a fresh adapter instance (or a process restart) would not apply peak at re-quote time, creating a behaviour divergence between in-process retries and out-of-process retries. Cleaner: add `on_confirm: bool = False` to `BookingProvider.discover` Protocol (small Protocol rev) or pass context via `Intent.metadata`. The flag works for the `04_surge_on_confirm` scenario today because new drafts are prepared from already-surged options, but the abstraction is fragile. — T28 review.
- [ ] **`UberSimAdapter.confirm()` computes a confirm-time `confirm_price` but persists `draft.option_snapshot` (prepare-time price) on the booking (T28 review).** `last_confirm_price_cents` is a test-only attribute; the booking's stored summary uses the original draft price. For the `price_changed` scenario this is benign (the tool's threshold guard short-circuits before `provider.confirm()` runs when drift exceeds threshold). But if a caller uses the sim directly with `force_surge_on_confirm=True` and the tool's guard is bypassed/disabled, the booking is persisted at the wrong price. Either store `confirm_price` on the booking or remove the dead-code computation. — T28 review.

## Tests / coverage gaps

- [ ] **Fingerprint property test: permutations.** Current test only reverses dict insertion order; replace with st.permutations or hypothesis shuffling to cover arbitrary key orderings. — T3 Ralph N1.
- [ ] **Tighten `fingerprint()` parameter type to Option.** Today accepts any BaseModel; spec §4 only requires Option. — T3 Ralph Q1.
- [ ] **Booking.events round-trip inconsistency.** claim_or_recall's recalled Booking carries the events list as serialised at finalize_idempotency time. get_booking reconstructs a Booking with no events (events table not joined). Decide canonical semantics in P3 contract suite design — possibly: claim_or_recall returns only booking_id and T13 re-fetches via get_booking. — T7 Ralph Q1.
- [ ] **Filter `approved_at: null` from declined verified rows.** When the user declines, the `verified.checks.confirmation` dump includes `approved_at: null`. Spec §8 examples only show the field when approved. Consider filtering on the declined path to keep audit rows tight, or update spec §8 to acknowledge the null. — T13 Ralph A5.
## Product / UX

- (none yet)

## Operational

- [ ] **`quote_cancel_fee` before `claim_or_recall` assumes terminal-safe fee quote (T27 review).** UberSim returns `Money(0, "USD")` for terminal bookings; a real provider may raise. The new cancel algorithm calls `quote_cancel_fee` BEFORE `claim_or_recall` (because the deterministic key includes `fee_cents`). When a real adapter ships, contract-test that `quote_cancel_fee` returns gracefully for `_ALREADY_TERMINAL` statuses, or restructure to short-circuit on terminal status before quoting. — T27 review.
- [ ] **`transcripts/01_happy_path.md` is stale relative to T27's sha256 key shape.** Line 31 still carries `"idempotency_key": "01KS1QTRK867VXS7HDC6TJPGHW"` (a 26-char ULID). After T27, the key should be a 64-char sha256 hex digest. `make transcripts` consistently hangs after discover — the agent stops without proceeding to prepare+confirm (observed across 5+ runs post-T27; pre-T27 run `01KS2H5P7R675YAN37993745B0` shows the old ULID shape). Root cause is likely agent prompt drift causing early stop; investigate runner.py turn-loop exit conditions. Regenerate once scenario flakiness is resolved. — T27 gap 2 / P3 spec-compliance audit.
