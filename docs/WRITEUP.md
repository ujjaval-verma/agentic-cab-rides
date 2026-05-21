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

**Cross-session idempotency.** Hardened. The confirm key is now bound to a
hash of the user's stable intent (pickup, dropoff, 10-minute-bucketed `when`,
party size, vehicle class) rather than the per-session `draft_id`, with a
generation counter (incremented when prior bookings for the same intent reach
a terminal status — legitimate re-bookings) and an agent-supplied
`intent_override_nonce` for the explicit "yes, book another" path. The key
survives `draft_id` regeneration and process restart; an active duplicate
intent surfaces as `outcome.status="duplicate_intent"` carrying the existing
booking ID, and the agent must obtain explicit user confirmation before
retrying with a nonce. See `docs/ARCHITECTURE.md` § "Core flow:
`confirm_booking`" for the algorithm, and scenario `06_cross_session_dedup`
for the proof.

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

**Provider option durability — REPL/web parity assumed.** Not yet
mitigated. The take-home assumes production needs both surfaces (REPL +
web) to be first-class — see
[`docs/ARCHITECTURE.md`](ARCHITECTURE.md#runtime-topology) for the
current process model on each. The REPL is a single long-lived process so the
provider's in-memory `_option_cache`/`_intent_cache` survive naturally
across turns; the web app instantiates a fresh adapter per `POST /chat`
and previously lost the cache between `discover_options` and
`prepare_booking` — surfacing as `unknown option_id` on the second turn.
PR #8 memoizes the `UberSimAdapter` per `session_id`, which closes the
within-process gap, but the caches still vanish on restart. Production
fix: persist options to SQLite (mirroring how drafts and bookings
already persist) so `prepare()` re-hydrates from disk on a cache miss
(`docs/FOLLOWUPS.md`).

**Credential management.** Not mitigated. The repo's sole secret —
`ANTHROPIC_API_KEY` — lives in a chmod-600 `.env` symlinked from
`~/.config/agentic-cab-rides/.env` (`.env.example` also documents optional
non-secret overrides like `ANTHROPIC_MODEL` and `NOMINATIM_USER_AGENT`,
which can stay where they are). Fine for one developer on one laptop, but
rotation requires editing a plaintext file in one place, there is no
access audit, and there is no per-user key scoping. Production direction: load the key from a secret manager at
process start. Two ergonomic local options without code changes — 1Password
CLI (`op run --env-file=.env.op -- make repl`, where `.env.op` carries
`ANTHROPIC_API_KEY=op://Private/anthropic/credential`) and Bitwarden CLI
(`ANTHROPIC_API_KEY=$(bw get password anthropic-key) make repl`). A real
deploy needs AWS Secrets Manager / GCP Secret Manager with rotation hooks
and IAM-scoped access. Tracked in `docs/FOLLOWUPS.md`.

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

*See [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) for layer + port structure,
core flow, and type contracts.*
