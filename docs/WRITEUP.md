# Writeup — Marketplace Swap & Production Failure Modes

> **Status: skeleton (ships at P6).** The brief asks for a 1-page writeup; this file is the stub so links resolve and P6 has a concrete artifact to fill. The structure below mirrors what `docs/superpowers/specs/2026-05-19-agentic-cab-rides-design.md` §15 commits to.

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

*Remaining gap:* **cross-process retries beyond restart-survival's window.** If the process crashes between `confirm` returning and the agent recording the outcome, a restart picks up a fresh session with no memory of the in-flight key. T30 (restart-survival) closes this by persisting `in_progress` claims so recovery sees the prior key. That gap is tracked in [`FOLLOWUPS.md`](FOLLOWUPS.md) under "Architecture / engineering."

## 3. Other landmines (one-line mitigations)

- **Price drift quote↔confirm.** Mitigated: `confirm_booking` re-quotes before invoking the gate; returns `PriceChanged` with a new draft if drift exceeds threshold.
- **PII in logs.** Partial: `redact()` applied at the JSONL boundary. Names and phone numbers covered; freeform user notes are not (they don't enter the structured schema today). Production needs a redaction pass over agent text turns as well.
- **Payment authorization scope.** Out of scope: this prototype assumes Uber-style account-level pre-auth. Production needs explicit payment-method selection and SCA confirmation flow.
- **Provider rate limits / backoff.** Not implemented. Production needs a token-bucket per provider and exponential backoff on 429s — without it, agent retries multiply load.
- **State drift agent ↔ reality.** Not implemented. The agent's mental model of "current ride" can diverge from the provider's view. Production needs a background polling loop refreshing `bookings.status` and surfacing transitions to the agent's context.
- **Action log retention/rotation.** Not implemented. Logs grow unbounded in `logs/`. Production needs rotation, retention windows, and (for compliance) signed append-only storage.

## 4. What I'd build next, in priority order

1. **Restart-survival for in-flight claims** (T30; closes the cross-process retry gap noted above)
2. **Background `track_booking` polling loop** for state-drift mitigation
3. **Webhook/Slack `ConfirmationChannel`** — proves the third channel works after CLI + Web; closes the abstraction
4. **Real `LyftAdapter`** — empirical proof the contract holds against a second real provider
5. **Payment authorization flow** — pre-confirm scope grant
6. **Per-provider token bucket** with retry/backoff
7. **Multi-user / auth layer** — moves out of single-user prototype mode

---

*See [`docs/superpowers/specs/2026-05-19-agentic-cab-rides-design.md`](superpowers/specs/2026-05-19-agentic-cab-rides-design.md) for the full design and [`docs/PHASES.md`](PHASES.md) for what shipped vs. is planned.*
