# Agentic Cab Rides — Design Spec

**Date:** 2026-05-19
**Status:** Approved for implementation planning
**Scope:** 48h take-home prototype. Working end-to-end, engineering rigor demonstrable, not production-hardened.

---

## 1. Problem & graded properties

Build a system where a Claude agent books cab rides end-to-end: discover, compare, book, track, cancel.

The brief grades three architectural properties harder than feature completeness:

1. **A swappable platform adapter** — "if we asked you to plug in Lyft or Zocdoc, what changes?" The Zocdoc hint is deliberate; the adapter must abstract over more than ride-share.
2. **Confirmation gates** — the agent must be *structurally unable* to book without explicit user approval.
3. **A replayable action log** — recording, for every attempt: what was requested, what was verified, what executed, what happened.

Deliverables: repo (3-min clone-to-run), 5 transcripts (happy path / price comparison / bad address / surge / cancellation), 1-page writeup on marketplace swap + what breaks first in prod.

## 2. Stack & non-negotiables

- **Python 3.11+**, `uv`-managed
- **Pydantic v2** for all typed contracts
- **Anthropic SDK** native tool use (no LangChain — hides the rigor being graded)
- **FastAPI + vanilla HTML/JS** for web UI (no build step, no npm)
- **Rich** for CLI rendering
- **pytest** with parameterized provider contract suite
- **JSONL** for action log and chat history; `state.json` for live state
- **ULIDs** for all IDs (sortable, no DB needed)
- **No external API keys** beyond `ANTHROPIC_API_KEY` — Nominatim is keyless, cached fixtures committed
- **Deterministic seeds** for all randomness; scenarios pin seeds + frozen clock

## 3. Layered architecture

Four strictly downward-pointing layers:

```
CLI / Web         (Rich + /dev/tty   ||   FastAPI + index.html)
   │
Agent loop        (Anthropic SDK; never touches adapters directly)
   │
Tool layer        (Pydantic-typed functions; owns fingerprinting, action-log emission, gate invocation)
   │
Core ports        (Protocols: BookingProvider, ConfirmationChannel, Clock, ActionLog, StateStore)
   │
Adapters          (UberSimAdapter, CLI/Web/Scripted ConfirmationChannel, System/FrozenClock,
                   JSONLActionLog, JSONStateStore, NominatimGeocoder)
```

**Invariant:** Agent → Tools → Ports → Adapters. No upward edges. Each layer is independently testable.

## 4. Core type contracts

```python
class RideIntent(BaseModel):
    kind: Literal["ride"] = "ride"
    pickup: AddressRef
    dropoff: AddressRef
    when: Literal["now"] | datetime
    party_size: int = 1
    vehicle_class_preference: list[str] | None = None

Intent = RideIntent  # union grows: | AppointmentIntent | ...

class Option(BaseModel):
    option_id: str
    provider: str
    title: str
    price: Money
    eta_seconds: int | None
    when: datetime | None
    details: RideOptionDetails | AppointmentOptionDetails  # discriminated
    provider_metadata: dict
    expires_at: datetime

class Draft(BaseModel):
    draft_id: str                  # ULID
    option_snapshot: Option        # frozen at prepare-time
    fingerprint: str               # sha256(canonical_json(option_snapshot))
    created_at: datetime
    expires_at: datetime

class Booking(BaseModel):
    booking_id: str
    draft_id: str
    status: Literal["confirmed","driver_assigned","en_route","arrived",
                    "in_progress","completed","cancelled","failed"]
    provider: str
    summary: Option
    events: list[BookingEvent]
    cancellation: CancellationInfo | None
```

**Notes:**
- `Option.details` is a discriminated union (`kind` discriminator) — keeps the agent prompt typed across intent variants.
- `expires_at` exists on both `Option` (re-quote on surge) and `Draft` (user walk-away).
- `Money` is `{amount_cents: int, currency: str}` — no floats for money.

## 5. The BookingProvider port

```python
class BookingProvider(Protocol):
    name: str
    supported_intent_kinds: set[str]

    async def discover(self, intent: Intent) -> list[Option]: ...
    async def prepare(self, option_id: str) -> Draft: ...
    async def confirm(self, draft_id: str, idempotency_key: str) -> Booking: ...
    async def track(self, booking_id: str) -> Booking: ...
    async def cancel(self, booking_id: str, idempotency_key: str) -> Booking: ...
```

**`idempotency_key` is required by the contract.** This is the production-realism hook that pays off in the writeup. The sim dedupes via in-memory dict; prod implementations would persist.

**Lyft swap:** new `LyftAdapter` implementing the same contract. Agent/tools/gate/log unchanged.
**Zocdoc swap:** add `AppointmentIntent` to the union, add `AppointmentOptionDetails`, write `ZocdocAdapter`, add an intent router. Tool layer, confirmation gate, action log unchanged.

## 6. ConfirmationChannel port

```python
class ConfirmationRequest(BaseModel):
    draft_id: str
    summary: str               # human-readable, one screen
    fingerprint: str           # sha256 of canonical(draft.option_snapshot)
    details: dict              # structured: price, eta, pickup, dropoff, vehicle
    expires_at: datetime

class ConfirmationResult(BaseModel):
    approved: bool
    reason: Literal["approved","declined","expired","fingerprint_mismatch","timeout"]
    approved_at: datetime | None
    approver: str | None       # "cli-tty" | "web:<session>" | "scripted"

class ConfirmationChannel(Protocol):
    async def request(self, req: ConfirmationRequest) -> ConfirmationResult: ...
```

**Implementations:**
- `CLIConfirmationChannel` — reads from `/dev/tty`, not stdin. Agent stdin cannot inject "y".
- `WebConfirmationChannel` — stores pending `asyncio.Future` keyed by `draft_id`, publishes via SSE, resolved by `POST /confirmations/{id}`.
- `ScriptedConfirmationChannel` — for scenarios; matches summary against script rules.
- `AutoApproveChannel` — tests only; guarded by env var assertion.

The fingerprint check is performed by the **tool layer**, not the channel. The channel only displays + collects.

## 7. Tool surface (what the agent sees)

Seven narrow tools, all Pydantic-validated:

| Tool | Gated | Provider call | Purpose |
|---|---|---|---|
| `geocode_address(query)` | no | no (adapter) | Resolve address text → lat/lng + confidence. Low confidence triggers clarification. |
| `discover_options(intent)` | no | yes | Returns ranked list of `Option`. |
| `prepare_booking(option_id)` | no | yes | Validates non-expired, creates Draft, computes fingerprint, persists. |
| `confirm_booking(draft_id)` | **yes** | yes | Verifies → re-quotes → invokes gate → executes. |
| `track_booking(booking_id)` | no | yes | Read-only status. |
| `cancel_booking(booking_id, reason)` | **yes** | yes | Quotes fee, gate, executes. |
| `list_active_bookings()` | no | no (state store) | Helps agent recover IDs across turns. |

**`confirm_booking` algorithm (the heart of the system):**

1. Load draft from state. Reject if unknown or expired.
2. Recompute fingerprint over `draft.option_snapshot`. Reject on mismatch.
3. Call `provider.discover()` for re-quote. If price drift > $1 OR > 5%, re-prepare and return `PriceChanged{new_draft_id, new_summary}` — do NOT invoke the gate with stale price.
4. Mint `idempotency_key` (ULID).
5. `await channel.request(ConfirmationRequest(...))`.
6. If approved → `provider.confirm(draft_id, idempotency_key)`; persist Booking.
7. Emit `requested`, `verified`, `executed`, `outcome` rows throughout.

**`cancel_booking` is one tool, two-phase under the hood.** Internal: compute fee → gate on fee → execute. Same gate guarantee.

## 8. Action log

JSONL, one file per session: `logs/<session_id>.jsonl`. Append-only.

Row kinds, all linked by `action_id`:

```json
// requested
{"ts":"...","session_id":"...","turn_id":"...","action_id":"...",
 "kind":"requested","tool":"confirm_booking","args":{...},"actor":"agent"}

// verified  (preconditions: fingerprint, expiry, requote, confirmation result)
{"ts":"...","action_id":"...","kind":"verified",
 "checks":{"draft_found":true,"draft_expired":false,"fingerprint_match":true,
           "requote_price_drift_cents":0,
           "confirmation":{"approved":true,"approver":"cli-tty","approved_at":"..."}},
 "result":"ok"}      // or "rejected" with "reason"

// executed  (the provider call we made)
{"ts":"...","action_id":"...","kind":"executed",
 "provider":"uber-sim","method":"confirm","idempotency_key":"...",
 "request":{...},"latency_ms":143}

// outcome
{"ts":"...","action_id":"...","kind":"outcome",
 "status":"ok","result":{...},"error":null}
```

**Rules:**
- Every tool emits at least `requested` + `outcome`. Gated tools emit all four.
- `verified.result == "rejected"` short-circuits — no `executed` row will follow.
- PII redaction at log boundary: raw address text replaced with `{formatted, lat, lng}`; phone/name through `redact()`.
- `replay.py <session_id>` reconstructs the full session deterministically.

## 9. UberSimAdapter

**Pricing** (in-process, fully deterministic):
```
price = base_fare[class]
      + per_km[class] * straight_line_km(pickup, dropoff) * 1.35
      + per_min[class] * estimated_minutes(time_of_day)
      + surge_multiplier(time_of_day, zone, seed) * dynamic_component
```

**Classes:** `uberx`, `comfort`, `uberxl`, `black` (each with own coefficients + party-size limits).

**Driver availability:** seeded per (class, zone, time-bucket). Probability table; scenarios can force `[]` for "no drivers" cases.

**State machine:** `confirmed → driver_assigned → en_route → arrived → in_progress → completed` with `cancelled` exit from any state. Transitions advance on `Clock` ticks; `FrozenClock` in scenario mode for determinism.

**Failure hooks for scenarios:**
- `force_no_availability(class)`
- `force_surge_on_confirm(multiplier)` — causes the surge transcript's price drift between prepare and confirm

**Contract test suite** (`tests/contract/test_booking_provider_contract.py`) — parameterized over all registered providers. Tests idempotency, expiry, cancel-after-cancel no-op, fingerprint stability, etc. This is the swap-in proof.

## 10. Geocoding

`NominatimGeocoder` calls public Nominatim. Responses cached in `fixtures/geocode/<sha1(query)>.json` and **committed**. Cache-first lookup means the 5 scenario transcripts run fully offline. Interactive mode hits the network with 1 req/sec throttle (Nominatim's policy).

`AddressRef` carries `{raw, formatted, lat, lng, confidence}`. Low confidence (<0.5) is the signal the agent uses to ask for clarification in the bad-address scenario.

## 11. Scenarios & transcripts

**Scenario YAML schema** (`scenarios/<name>.yaml`):

```yaml
name: <slug>
description: <one line>
seed: <int>
clock:
  start: "<iso8601>"
  mode: frozen
sim:
  force_classes: [...]
  surge_zones: {...}
  no_availability: [...]
confirmation:
  channel: scripted
  script:
    - match: "<substring of summary>"
      action: approve | decline
user_turns:
  - "<message>"
invariants:
  - kind: action_log_contains
    where: {tool: "...", "outcome.status": "..."}
  - kind: no_double_booking
  - kind: fingerprint_verified_before_execute
  - kind: gate_invoked_for_tool
    tool: "confirm_booking"
```

**The five scenarios:**

1. `01_happy_path` — single ride, defaults, end-to-end
2. `02_price_comparison` — three classes surfaced, user picks cheapest
3. `03_bad_address` — Nominatim returns low confidence → agent clarifies → user disambiguates → succeeds
4. `04_surge_on_confirm` — prepare at base price, sim surges between prepare & confirm, tool returns `price_changed`, agent re-prompts, user approves new price
5. `05_cancel_with_fee` — book → driver_assigned → user cancels → fee surfaced through gate → user approves → cancellation completes

**Render pipeline** (`scripts/render_transcript.py`):
- Reads chat history JSONL + action log JSONL
- Emits `transcripts/<name>.md` with sections: Scenario header (name/seed/repro command), Conversation, Action log highlights (collapsed), Invariants (pass/fail)
- `make transcripts` regenerates all 5. Rendered MDs are **committed** so the grader sees them without running.

**Determinism:** Claude with `temperature=0`, pinned model ID, stable versioned system prompt. ~5% wording drift accepted; invariants are what's asserted.

## 12. Web UI (minimal, no build step)

**Backend** (FastAPI, one process):
- `POST /chat` — user message in
- `GET /events?session_id=...` — SSE stream: agent text deltas, tool-call summaries, `confirmation_pending` events
- `POST /confirmations/{draft_id}` — `{approved: bool}` → resolves the pending gate
- `GET /sessions/{id}/log` — raw JSONL action log for the side panel

**Frontend** — single `index.html` served by FastAPI. Vanilla HTML + ~150 lines vanilla JS. Three panes:
1. **Chat** — user/agent messages, SSE-streamed
2. **Confirmation modal** — pops on `confirmation_pending`, shows the same summary the CLI would print plus short fingerprint hash. Approve / Decline buttons.
3. **Action log** — collapsible side panel, tails JSONL in real time, color-coded by `kind`.

**The point:** the web UI is a new `ConfirmationChannel` + new output renderer. *Nothing else changes.* Same agent loop, same tools, same fingerprint logic, same action log. Same three channels (CLI/Web/Scripted) drive one agent.

**Run modes:**
- `make repl` → CLI (CLIConfirmationChannel)
- `make web` → uvicorn at `:8000` (WebConfirmationChannel)
- `make transcripts` → scenarios (ScriptedConfirmationChannel)

## 13. Repo layout

```
agentic-cab-rides/
├── README.md                    # 3-min quickstart
├── Makefile                     # install / repl / web / transcripts / test
├── pyproject.toml               # uv-managed, locked
├── .env.example                 # ANTHROPIC_API_KEY=
├── src/agentic_cab/
│   ├── __main__.py              # repl | run-scenario | web
│   ├── core/
│   │   ├── types.py
│   │   ├── ports.py
│   │   ├── fingerprint.py
│   │   └── ids.py
│   ├── tools/
│   │   ├── registry.py          # Pydantic → Anthropic tool schema
│   │   ├── geocode.py
│   │   ├── discover.py
│   │   ├── prepare.py
│   │   ├── confirm.py           # the gated one
│   │   ├── track.py
│   │   ├── cancel.py
│   │   └── list_active.py
│   ├── agent/
│   │   ├── loop.py
│   │   ├── system_prompt.py
│   │   └── chat.py              # Rich REPL
│   ├── web/
│   │   ├── app.py               # FastAPI
│   │   ├── sse.py
│   │   └── static/index.html
│   ├── adapters/
│   │   ├── uber_sim/
│   │   │   ├── provider.py
│   │   │   ├── pricing.py
│   │   │   ├── surge.py
│   │   │   ├── state_machine.py
│   │   │   └── fixtures/
│   │   ├── confirmation/
│   │   │   ├── cli.py
│   │   │   ├── web.py
│   │   │   ├── scripted.py
│   │   │   └── auto.py
│   │   ├── geocoding/
│   │   │   ├── nominatim.py
│   │   │   └── cache.py
│   │   ├── clock.py
│   │   ├── action_log.py
│   │   └── state_store.py
│   └── render/transcript.py
├── scenarios/                   # 5 yaml files
├── transcripts/                 # 5 generated .md files (committed)
├── logs/                        # gitignored
├── fixtures/geocode/            # cached Nominatim responses
├── tests/
│   ├── contract/test_booking_provider_contract.py
│   ├── unit/
│   └── scenarios/test_scenarios.py
├── docs/
│   ├── ARCHITECTURE.md
│   └── WRITEUP.md               # the 1-pager deliverable
└── scripts/
    ├── render_transcript.py
    └── replay.py
```

## 14. Time budget (42h of 48)

| Block | Hours |
|---|---|
| Core types + ports + fingerprint + ids | 2 |
| UberSimAdapter (pricing, surge, state machine) | 5 |
| Contract test suite (parameterized) | 2 |
| ConfirmationChannel (CLI + scripted) | 2 |
| Action log + state store | 2 |
| Geocoding (Nominatim + cache) | 1.5 |
| 7 tools | 4 |
| Agent loop + system prompt + CLI REPL | 3 |
| Scenario runner + 5 YAMLs | 3 |
| Transcript renderer + replay.py | 2 |
| FastAPI + SSE + endpoints | 1.5 |
| index.html + vanilla JS | 2 |
| WebConfirmationChannel + output renderer | 1.5 |
| Web polish + screenshot | 1 |
| Polish + README + Makefile | 2 |
| Writeup (1-pager) | 1.5 |
| **Subtotal** | **36** |
| Buffer | 6 |
| **Total** | **42** |

**Cut order if behind:**
1. Drop `uberxl` + `black` classes (-1h)
2. Drop action-log side pane in web UI (keep CLI-only via `jq`) (-0.5h)
3. Drop SSE; web UI polls instead (-1h)
4. Drop `replay.py` (-1h; log greppable with `jq`, README shows queries)
5. Drop web UI entirely (-6h, ships CLI-only)
6. Drop Nominatim, use pure fake geocoder (-1.5h)
7. Drop contract-test parameterization (-1h, big writeup cost)

**Never cut:** fingerprint check, four-row log, scenarios runner, the 5 transcripts.

## 15. Writeup outline (`docs/WRITEUP.md`)

Single page, four blocks:

1. **Swapping marketplaces** (½ page) — Lyft = trivial (same `RideIntent`, new adapter passes contract). Zocdoc = new `AppointmentIntent` variant + adapter + intent router; tool layer, gate, log, fingerprint all unchanged. This is the architectural payoff.
2. **What breaks first in production** (¼ page) — pick **idempotency under retries**. Port already takes `idempotency_key` but sim dedupes via dict; prod needs persistent dedupe with TTL surviving restart, and agent retry policy must reuse the same key (currently mints fresh). 3-line fix sketch.
3. **Other landmines** (¼ page) — bullet list w/ one-line mitigation: price drift quote↔confirm (mitigated), PII in logs (partial), payment scope (out of scope), provider rate limits (need token bucket), state drift agent↔reality (needs polling loop).
4. **Next** — persistent state store (SQLite), webhook ConfirmationChannel, real Lyft adapter proving the contract, payment authorization flow.

## 16. Out of scope (explicit)

- Persistent DB (SQLite or otherwise) — `state.json` + JSONL only
- Auth / multi-tenant — single user assumed
- Payment method selection — assume account-level pre-auth
- Real ride routing — straight-line × fudge factor
- Real Lyft/Zocdoc adapters — only Uber sim ships; swap is described in writeup
- Production observability (metrics, tracing) — action log only
- Map UI / driver pin tracking — text status only

## 17. Open questions resolved during brainstorm

| # | Question | Decision |
|---|---|---|
| 1 | Simulator realism | Deterministic sim (B) + scenario mode with pinned seeds (C) |
| 2 | Confirmation gate mechanism | Inline CLI prompt via /dev/tty, abstracted as `ConfirmationChannel` |
| 3 | Adapter scope | Generic `BookingProvider` over `Intent → Option → Draft → Booking` |
| 4 | Agent interaction model | Persistent REPL session + 7 narrow tools |
| 5 | Action log shape | Four-row-per-gated-action JSONL, linked by `action_id` |
| 6 | Geocoding | Nominatim + committed cache fixtures; in-process pricing math |
| 7 | Transcript format | Auto-generated markdown from log + chat, with scenario YAMLs and invariants |
| +1 | Web UI in scope? | Yes — minimal, no build step, demonstrates ConfirmationChannel abstraction |
