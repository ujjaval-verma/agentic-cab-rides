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

*The fact that "swap to Zocdoc" requires no edits below the tool layer is the architectural payoff this design was meant to surface.*

## 2. What breaks first in production

**Idempotency under retries — and we already built half of it.** The `BookingProvider` port requires `idempotency_key`. SQLite enforces `UNIQUE` on the `idempotency_keys` table. `claim_or_recall` short-circuits the second provider call, and a restart-survival contract test (`tests/contract/test_restart_survival.py`) proves it.

*What still breaks:* the **agent-side retry policy**. Today the tool layer mints a fresh ULID at the top of `confirm_booking`. If the agent's first call times out and it retries, it mints a *new* key and bypasses the dedupe barrier — the SQLite UNIQUE constraint is then defending against a problem that no longer exists, because each retry has a fresh key.

**The 3-line fix:**
```python
# tools/confirm.py
idempotency_key = hashlib.sha256(
    f"{draft_id}:confirm".encode()
).hexdigest()[:26]   # ULID-shaped, deterministic per intent
```
The key becomes intent-bound, not call-bound. An agent retry produces the same key → `claim_or_recall` returns the recorded response → no second provider call.

This gap is tracked in [`FOLLOWUPS.md`](FOLLOWUPS.md) under "Architecture / engineering."

## 3. Other landmines (one-line mitigations)

- **Price drift quote↔confirm.** Mitigated: `confirm_booking` re-quotes before invoking the gate; returns `PriceChanged` with a new draft if drift exceeds threshold.
- **PII in logs.** Partial: `redact()` applied at the JSONL boundary. Names and phone numbers covered; freeform user notes are not (they don't enter the structured schema today). Production needs a redaction pass over agent text turns as well.
- **Payment authorization scope.** Out of scope: this prototype assumes Uber-style account-level pre-auth. Production needs explicit payment-method selection and SCA confirmation flow.
- **Provider rate limits / backoff.** Not implemented. Production needs a token-bucket per provider and exponential backoff on 429s — without it, agent retries multiply load.
- **State drift agent ↔ reality.** Not implemented. The agent's mental model of "current ride" can diverge from the provider's view. Production needs a background polling loop refreshing `bookings.status` and surfacing transitions to the agent's context.
- **Action log retention/rotation.** Not implemented. Logs grow unbounded in `logs/`. Production needs rotation, retention windows, and (for compliance) signed append-only storage.

## 4. What I'd build next, in priority order

1. **Intent-bound idempotency keys** (3-line fix above; biggest production gap)
2. **Background `track_booking` polling loop** for state-drift mitigation
3. **Webhook/Slack `ConfirmationChannel`** — proves the third channel works after CLI + Web; closes the abstraction
4. **Real `LyftAdapter`** — empirical proof the contract holds against a second real provider
5. **Payment authorization flow** — pre-confirm scope grant
6. **Per-provider token bucket** with retry/backoff
7. **Multi-user / auth layer** — moves out of single-user prototype mode

---

*See [`docs/superpowers/specs/2026-05-19-agentic-cab-rides-design.md`](superpowers/specs/2026-05-19-agentic-cab-rides-design.md) for the full design and [`docs/PHASES.md`](PHASES.md) for what shipped vs. is planned.*
