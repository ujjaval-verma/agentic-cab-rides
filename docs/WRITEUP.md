# Writeup — Marketplace Swap & Production Failure Modes

## 1. Swapping marketplaces

**Lyft (trivial).** Same `RideIntent`, same `Option.details` shape. A new
`LyftAdapter` implementing `BookingProvider` passes the same contract suite
at `tests/contract/test_booking_provider_contract.py`. Agent, tools,
confirmation gate, action log, fingerprint logic: unchanged. Estimate: ~1
day, mostly auth/SDK plumbing.

**Zocdoc (the abstraction's stress test).** Zocdoc is not a cab service —
it books healthcare appointments. The architecture survives because of
three deliberate choices:

1. **`Intent` is a tagged union.** Add `AppointmentIntent` alongside
   `RideIntent`.
2. **`Option.details` is a discriminated union.** Add
   `AppointmentOptionDetails` (provider, slot, location, in-person /
   telehealth, specialty).
3. **`BookingProvider.supported_intent_kinds: set[str]`** routes intents
   to capable adapters.

The remaining additions: one intent-router rule in the agent's tool-call
resolution, and a new tool surface variant (`reschedule_appointment`
replaces `cancel_booking` semantics — Zocdoc rescheduling is the
dominant flow). The tool layer's fingerprint logic, the
`ConfirmationChannel` design, the four-row action log, and the SQLite
idempotency table are unchanged.

**This claim is tested, not just prose.**
`tests/contract/test_booking_provider_contract.py` runs a parametrized
suite over `UberSimAdapter` (ride intent) and `MockAppointmentProvider`
(appointment intent). Both adapters pass the same Protocol-level contract
tests — idempotency, option/draft rejection, cancel no-op, fingerprint
stability — without any test reaching into adapter-specific shape. The
suite is the swap proof: the abstraction holds for a second, structurally
different intent kind.

## 2. What breaks first in production

**Cross-session idempotency.** Hardened. The confirm key is now bound to a hash of the user's stable intent (pickup, dropoff, time bucket, party size, vehicle class) rather than the per-session `draft_id`, with a generation counter for legitimate re-bookings and an agent-supplied nonce for the explicit "yes, book another" path. See `docs/superpowers/specs/2026-05-20-cross-session-idempotency-design.md`.

**Price drift quote ↔ confirm.** Mitigated. `confirm_booking` re-quotes
via `provider.discover()` after the fingerprint check and before invoking
the gate. If drift exceeds `RE_QUOTE_DRIFT_THRESHOLD_CENTS`, the tool
returns `PriceChanged` with a new draft — the gate is never invoked at
a stale price. The `04_surge_on_confirm` scenario drives this path
deterministically.

**Agent ↔ reality state drift.** Not mitigated. The agent's mental model
of "current ride" can diverge from the provider's view (the driver
cancels, the ride starts, ETAs change). Production needs a background
polling loop refreshing `bookings.status` and surfacing transitions to
the agent's context. Today the agent only learns about state changes
when it asks.

Other landmines, one-line: payment auth scope is assumed (Uber-style
pre-auth) — production needs SCA. Per-provider rate limits aren't
implemented — without a token bucket and backoff, agent retries
multiply load. Action-log retention/rotation is unbounded — production
needs windows and (for compliance) signed append-only storage. Freeform
agent prose is not redacted today; only structured fields are.

## 3. What I'd build next, in priority order

1. **Background `track_booking` polling loop** — closes agent ↔ reality
   state drift.
2. **Webhook / Slack `ConfirmationChannel`** — proves the third channel
   works after CLI + web; closes the abstraction.
3. **Real `LyftAdapter`** — empirical proof the contract holds against a
   second real provider, not just a second mock.
4. **Payment authorization flow** — pre-confirm scope grant.

---

*See [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) for layer + port structure
and [`docs/superpowers/specs/2026-05-19-agentic-cab-rides-design.md`](superpowers/specs/2026-05-19-agentic-cab-rides-design.md)
for the full design.*
