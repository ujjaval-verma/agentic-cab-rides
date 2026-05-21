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
- [ ] **Action-log retention/rotation.** Logs currently grow without bound under `logs/<session>.jsonl`. Add a `make logs-rotate` target and a retention policy doc (likely just "keep last 100 sessions" for the prototype) before any long-running demo. — post-P6.
- [ ] **Scenario YAML top-level `seed` field is unused.** runner.py only consumes `sim.rng_seed`; the top-level `seed` is parsed but ignored. Use it as a single canonical seed source (derive `sim.rng_seed` from it, or remove the top-level field). — T15 Ralph.
- [ ] **Scenario YAML `clock.mode` not validated.** runner.py always constructs FrozenClock regardless of `clock.mode` value. Add a guard that raises if `clock.mode != "frozen"` (interactive mode is a different code path). — T15 Ralph.
- [ ] **`_by_action` raises KeyError on rows missing `action_id`.** All current log rows include `action_id` per invariant 6, but a defensive `.get("action_id", "")` would prevent hard failures if a malformed log is fed to the invariant checkers. — T15 Ralph.
- [ ] **NominatimGeocoder throttle Lock has no concurrency test (T19 review I1).** The current `test_second_call_within_one_second_waits` serializes calls sequentially; a true concurrency test would fire two `asyncio.gather`'d calls and assert the second was delayed ~1s. The Lock could be removed today without test failure. — P3+.
- [ ] **`UberSimAdapter._any_draft_prepared` is a stateful workaround for the surge-on-confirm hook (T28 review).** Once `prepare()` is called, the flag never resets — so a re-prepare-after-rejection on a fresh adapter instance (or a process restart) would not apply peak at re-quote time, creating a behaviour divergence between in-process retries and out-of-process retries. Cleaner: add `on_confirm: bool = False` to `BookingProvider.discover` Protocol (small Protocol rev) or pass context via `Intent.metadata`. The flag works for the `04_surge_on_confirm` scenario today because new drafts are prepared from already-surged options, but the abstraction is fragile. — T28 review.
- [ ] **`UberSimAdapter.confirm()` computes a confirm-time `confirm_price` but persists `draft.option_snapshot` (prepare-time price) on the booking (T28 review).** `last_confirm_price_cents` is a test-only attribute; the booking's stored summary uses the original draft price. For the `price_changed` scenario this is benign (the tool's threshold guard short-circuits before `provider.confirm()` runs when drift exceeds threshold). But if a caller uses the sim directly with `force_surge_on_confirm=True` and the tool's guard is bypassed/disabled, the booking is persisted at the wrong price. Either store `confirm_price` on the booking or remove the dead-code computation. — T28 review.
- [ ] **Appointment-intent hashing.** `intent_hash` raises `NotImplementedError` for `AppointmentIntent`. When the Zocdoc/appointment swap goes from prose to code, mirror the ride hash with appointment-stable fields (subject, location, earliest_at/latest_at bucket). — cross-session-idempotency slice.
- [ ] **Duplicate-intent UX polish.** The `duplicate_intent` outcome carries only `existing_booking_id` + `existing_status`. The agent then has to call `track_booking` or `get_booking` separately to show the user the fare/ETA before asking "book another?". Bundle the existing booking's summary into the `duplicate_intent` outcome payload so the agent can surface it in one turn. — cross-session-idempotency slice.
- [ ] **`MockAppointmentProvider.prepare()` produces drafts without `intent_snapshot`.** The new `confirm_booking` step-3a guard rejects any draft missing a `RideIntent` intent_snapshot. The mock appointment adapter is therefore non-functional through `confirm_booking` until either (a) `MockAppointmentProvider.prepare` is updated to populate `intent_snapshot` with an `AppointmentIntent`, AND `intent_hash` is extended to support appointments, OR (b) the 3a guard is relaxed to a soft fall-back for non-ride intents. The provider-contract suite doesn't exercise `confirm_booking` so the regression is dormant. — cross-session-idempotency slice, code review of T5.

- [ ] **`_PHONE` regex in `action_log.py` false-matches ISO date prefixes.** `_PHONE = re.compile(r"\+?\d[\d\-\(\)\s]{6,}\d")` matches `2026-05-20`, so every leaf-string `ts` field gets corrupted to `[redacted-phone]T12:00:00+00:00` when run through `redact()`. Pre-existing; A3 expanded the surface area by adding six more `clock.now().isoformat()` log emissions. Tighten the regex (e.g. require at least 7 consecutive digits, or anchor on `\b` + a minimum digit count) so dates are not redacted. — A3 review (commit 8a45cda).

- [ ] **`SqliteStateStore` writes its own `created_at`/`updated_at` via `datetime.now(UTC)` instead of the injected `Clock`.** Three call sites: `claim_or_recall` (state_store.py:218 — hot path on every booking), `put_booking` (~line 137), `put_pending_confirmation` (~line 299). The adapter sits below the tool layer correctly, but invariant 9 says reads of `datetime.now()` should go through the `Clock` port. Under `FrozenClock` scenarios the stored timestamps drift from scenario time. Fix: inject `Clock` into `SqliteStateStore.__init__` (mirror the tool-layer pattern; do **not** add `_override` knobs). — PR #2 review.

## Tests / coverage gaps

- [ ] **Fingerprint property test: permutations.** Current test only reverses dict insertion order; replace with st.permutations or hypothesis shuffling to cover arbitrary key orderings. — T3 Ralph N1.
- [ ] **Tighten `fingerprint()` parameter type to Option.** Today accepts any BaseModel; spec §4 only requires Option. — T3 Ralph Q1.
- [ ] **Filter `approved_at: null` from declined verified rows.** When the user declines, the `verified.checks.confirmation` dump includes `approved_at: null`. Spec §8 examples only show the field when approved. Consider filtering on the declined path to keep audit rows tight, or update spec §8 to acknowledge the null. — T13 Ralph A5.
## Product / UX

- [ ] **Web confirm modal: structured route card (pickup/dropoff dots, vertical connector, price + ETA chips).** Today the modal renders an opaque `summary: str` from `ConfirmationRequest`. Promote `ConfirmationRequest.details` to a typed `ConfirmationDetails` (see the dup'd Architecture entry above), then thread `pickup`, `dropoff`, `vehicle_class`, `price`, `eta_seconds`, `fingerprint_short` through `WebConfirmationChannel` so the modal can render structured fields. Markup sketch in `docs/superpowers/plans/2026-05-20-review-followups-plan.md` §C6. — Phase C deferred (no port changes inside docs polish).
- [ ] **Web option-comparison card strip (C7).** Currently `agent_reply` SSE events carry only `text`; rendering `discover_options` results as a scannable three-column card strip requires the agent loop or the discover-tool surface to also publish a structured `options` payload. Plan sketch in §C7. — Phase C deferred.

## Operational

- [ ] **`quote_cancel_fee` before `claim_or_recall` assumes terminal-safe fee quote (T27 review).** UberSim returns `Money(0, "USD")` for terminal bookings; a real provider may raise. The new cancel algorithm calls `quote_cancel_fee` BEFORE `claim_or_recall` (because the deterministic key includes `fee_cents`). When a real adapter ships, contract-test that `quote_cancel_fee` returns gracefully for `_ALREADY_TERMINAL` statuses, or restructure to short-circuit on terminal status before quoting. — T27 review.
- [ ] **Narrow crash window between `consume_draft` and `finalize_idempotency` in confirm_booking (T30 review).** If the process crashes between these two sequential writes, `consumed_at` is set but the idempotency key is unfinalised (`response_blob` is null). On retry, `_validate_draft` returns "draft already consumed" BEFORE `claim_or_recall` can return `"in_progress"` — the agent gets a confusing error instead of the proper in-progress recovery path. No double-booking risk (the key is still claimed), but recovery UX is worse. Fix: make consume_draft + finalize_idempotency atomic in a single transaction, or reorder so finalize_idempotency runs first. — T30 review.
