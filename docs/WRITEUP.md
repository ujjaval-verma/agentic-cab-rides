# Writeup — Marketplace Swap & Production Failure Modes

> **Status: P3 complete (T26–T32 shipped).** This writeup reflects the full P3 state as of 2026-05-20. P4–P6 will add scenario expansion, streaming CLI output, and a real third-party adapter.

---

## 1. Swapping marketplaces

**Lyft (trivial).** Same `RideIntent`, same `Option.details` shape. New `LyftAdapter` implementing `BookingProvider`; passes the same contract suite at `tests/contract/test_booking_provider_contract.py`. Agent, tools, gate, log, fingerprint: unchanged. *Estimated: ~1 day, mostly auth/SDK plumbing.*

**Zocdoc (the abstraction's stress test).** Zocdoc is not a cab service — it books healthcare appointments. The architecture survives because of three deliberate choices:

1. **`Intent` is a tagged union.** Add `AppointmentIntent` alongside `RideIntent`.
2. **`Option.details` is a discriminated union, not a dict.** Add `AppointmentOptionDetails` (provider, slot, location, in-person/telehealth, specialty).
3. **`BookingProvider.supported_intent_kinds: set[str]`** routes intents to capable adapters.

The remaining additions: one intent-router rule in the agent's tool-call resolution, and a new tool surface variant (`reschedule_appointment` replaces `cancel_booking` semantics — Zocdoc rescheduling is the dominant flow). The tool layer's fingerprint logic, the `ConfirmationChannel` design, the four-row action log, and the SQLite idempotency table are unchanged.

**This claim is now tested, not just prose.** `tests/contract/test_booking_provider_contract.py` runs a parametrized suite over `UberSimAdapter` (ride intent) and `MockAppointmentProvider` (appointment intent). Both adapters pass the same Protocol-level contract tests — idempotency, option/draft rejection, cancel no-op, fingerprint stability — without any test reaching into adapter-specific shape. The suite is the swap proof: the abstraction holds for a second, structurally different intent kind.

*The fact that "swap to Zocdoc" requires no edits below the tool layer is the architectural payoff this design was meant to surface.*

## 2. What breaks first in production — and what shipped

**Idempotency under retries — shipped.** The `BookingProvider` port requires `idempotency_key`. SQLite enforces `UNIQUE` on the `idempotency_keys` table. `claim_or_recall` short-circuits the second provider call.

As of T27, keys are **intent-bound**, not call-bound:
- `confirm_booking`: key = `sha256(canonical_json({draft_id, fingerprint, op:"confirm"}))`
- `cancel_booking`: key = `sha256(canonical_json({booking_id, fee_cents, status_at_quote, op:"cancel"}))`

An agent retry after a timeout produces the **same key** → `claim_or_recall` returns the recorded response → no second provider call. The fee and status-at-quote are part of the cancel key because two cancels quoted at different fees are different intents — the user consented to a specific fee.

*Remaining gap (known):* intent-bound keys survive retries within a session, but a fresh process that re-sends the same user intent generates a new `draft_id` (and thus a new key). This is tracked in [`FOLLOWUPS.md`](FOLLOWUPS.md) as the first thing that breaks in production. T30 (restart-survival) closed the intra-session crash gap by persisting `in_progress` claims; cross-session key binding requires storing the intent hash independently of the draft.

**Real re-quote before confirm — shipped (T28).** `confirm_booking` calls `provider.discover()` again after the fingerprint check and before invoking the confirmation gate. If price drift exceeds `RE_QUOTE_DRIFT_THRESHOLD_CENTS`, the tool returns `PriceChanged` with a new draft — the gate is never invoked at a stale price. The `04_surge_on_confirm` scenario drives this path deterministically via `force_surge_on_confirm`.

**Web confirmation surface — shipped (T29).** `WebConfirmationChannel` suspends the agent turn on a `Future` and publishes a `pending_confirmation` SSE event. The browser resolves it via `POST /confirmations/{subject_id}`. The gate is structurally identical to the CLI channel — the agent never sees a `confirmed=True` parameter; the channel decides. Thread safety: `SqliteStateStore` uses a `threading.Lock` on all writes when `check_same_thread=False` (FastAPI path). SSE streaming via `sse-starlette`.

**Restart-survival — shipped (T30).** Pending confirmations are persisted to `pending_confirmations` table before the `Future` is awaited. On process restart, the startup hook calls `list_pending_confirmations()` and re-publishes each row so a reloaded browser tab can re-render the confirmation modal. The `in_progress` claim status prevents a second provider call if the restart race is lost.

**Force-surge scenario — shipped (T28).** `SurgeCurve.force_surge_on_confirm=True` triggers a surge multiplier only on the `on_confirm=True` discover call (re-quote path), not on initial discover. This lets scenario 04 deterministically produce a `price_changed` rejection, proving the re-quote gate fires correctly.

**Second adapter (MockAppointmentProvider) — shipped (T31).** The parametrized contract suite (`@pytest.mark.parametrize` over fixture names) covers both `UberSimAdapter` and `MockAppointmentProvider`. One test (`test_confirm_idempotent`) is marked `not applicable to mock_appointment` because appointment providers have a different booking lifecycle — the skip rationale is explicit in the test.

**PII redaction at the log boundary — extended (T31).** The `summary_title` field (which providers populate with driver names and vehicle plates) is now redacted at the JSONL boundary: `_NAME` regex strips capitalised word pairs; `_PHONE` regex strips phone-shaped strings. This closes the gap where adapter-level `summary_title` values could leak PII into the action log even when pickup/dropoff addresses were already redacted.

**REPL/web parity — proven (T32).** `tests/scenarios/test_repl_web_parity.py` runs the complete happy-path tool sequence (geocode → discover → prepare → confirm) twice: once wired as the REPL/CLI surface and once as the web surface (using `ScriptedConfirmationChannel` in place of `WebConfirmationChannel` — both satisfy the same Protocol). The two action-log outputs are normalised (volatile ULIDs and timestamps stripped) and compared element-by-element. Both bookings carry the same price. If either surface emits an extra log row or deviates in any field, the test fails at the source of divergence.

## 3. Other landmines (one-line mitigations)

- **Price drift quote↔confirm.** Mitigated: `confirm_booking` re-quotes before invoking the gate; returns `PriceChanged` with a new draft if drift exceeds threshold.
- **PII in logs.** Partial: `redact()` applied at the JSONL boundary. Pickup/dropoff addresses, driver names, and phone numbers are covered. Freeform agent text turns are not redacted today (they don't enter the structured schema). Production needs a redaction pass over agent prose as well.
- **Payment authorization scope.** Out of scope: this prototype assumes Uber-style account-level pre-auth. Production needs explicit payment-method selection and SCA confirmation flow.
- **Provider rate limits / backoff.** Not implemented. Production needs a token-bucket per provider and exponential backoff on 429s — without it, agent retries multiply load.
- **State drift agent ↔ reality.** Not implemented. The agent's mental model of "current ride" can diverge from the provider's view. Production needs a background polling loop refreshing `bookings.status` and surfacing transitions to the agent's context.
- **Action log retention/rotation.** Not implemented. Logs grow unbounded in `logs/`. Production needs rotation, retention windows, and (for compliance) signed append-only storage.
- **Cross-session idempotency key binding.** Tracked in FOLLOWUPS. Intent-bound keys work within a session; a fresh session with the same user intent generates a new `draft_id` and thus a new key — double-booking is still possible if the user repeats the request in a new session after a crash.

## 4. What I'd build next, in priority order

1. **Background `track_booking` polling loop** — closes state-drift between agent model and provider reality
2. **Cross-session idempotency** — store the intent hash at the tool layer, not derived from `draft_id`
3. **Webhook/Slack `ConfirmationChannel`** — proves the third channel works after CLI + Web; closes the abstraction
4. **Real `LyftAdapter`** — empirical proof the contract holds against a second real provider
5. **Payment authorization flow** — pre-confirm scope grant
6. **Per-provider token bucket** with retry/backoff
7. **Multi-user / auth layer** — moves out of single-user prototype mode

---

*See [`docs/superpowers/specs/2026-05-19-agentic-cab-rides-design.md`](superpowers/specs/2026-05-19-agentic-cab-rides-design.md) for the full design and [`docs/PHASES.md`](PHASES.md) for what shipped vs. is planned.*
