# Agentic Cab Rides — Design Spec

**Date:** 2026-05-19
**Status:** Approved for implementation planning
**Scope:** Prototype. Working end-to-end, engineering rigor demonstrable, not production-hardened.

---

## 1. Problem & target properties

Build a system where a Claude agent books cab rides end-to-end: discover, compare, book, track, cancel.

Three architectural properties matter more than feature completeness:

1. **A swappable platform adapter** — "if we asked you to plug in Lyft or Zocdoc, what changes?" The Zocdoc hint is deliberate; the adapter must abstract over more than ride-share.
2. **Confirmation gates** — the agent must be *structurally unable* to book without explicit user approval.
3. **A replayable action log** — recording, for every attempt: what was requested, what was verified, what executed, what happened.

Deliverables: repo (3-min clone-to-run), 5 transcripts (happy path / price comparison / bad address / surge / cancellation), 1-page writeup on marketplace swap + what breaks first in prod.

## 2. Stack & non-negotiables

- **Python 3.12+**, `uv`-managed
- **Pydantic v2** for all typed contracts
- **Anthropic SDK** native tool use (no LangChain — hides the rigor we want visible)
- **FastAPI + vanilla HTML/JS** for web UI (no build step, no npm)
- **Rich** for CLI rendering
- **pytest** with parameterized provider contract suite
- **SQLite** (stdlib `sqlite3`, no ORM) for live state (drafts, bookings, idempotency keys)
- **JSONL** for action log, chat history, and geocode cache (append-only / replay-friendly artifacts stay flat files)
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
                   JSONLActionLog, SqliteStateStore, NominatimGeocoder)
```

**Invariant:** Agent → Tools → Ports → Adapters. No upward edges. Each layer is independently testable.

<!-- spec-amendment: 2026-05-20 --> `GeocoderPort` is part of the port layer (`core/ports.py`); adapters such as `NominatimGeocoder` implement it, and the tool layer depends only on the Protocol.

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
    async def quote_cancel_fee(self, booking_id: str) -> Money: ...  # <!-- spec-amendment: 2026-05-20 -->
```

**`idempotency_key` is required by the contract.** Dedupe is enforced at the `StateStore` boundary via a `UNIQUE` constraint on the `idempotency_keys` table (see §11a). The provider checks the store before issuing a new booking; a duplicate key returns the previously-recorded booking. This survives process restart — the prod-realism story that the writeup leans on.

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
    reason: Literal["approved", "declined", "expired", "timeout"]
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

The fingerprint check is performed by the **tool layer**, not the channel. The channel only displays + collects — it cannot reject for fingerprint reasons. Fingerprint mismatch surfaces as a tool-layer error before the channel is invoked.

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

1. Load draft from state. Reject if unknown, expired, or already consumed (`consumed_at IS NOT NULL`) — drafts are single-use even though the idempotency key is the durable barrier against double-booking.
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

<!-- spec-amendment: 2026-05-20 -->
Every row (requested/verified/executed/outcome) carries a `tool` field — added 2026-05-20 to make plain-grep diagnostics work without an action_id join.

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

## 10a. State persistence (SQLite)

Live mutable state — drafts, bookings, booking events, idempotency keys — lives in `data/state.db`. Stdlib `sqlite3`, WAL mode, no ORM. One `schema.sql` file applied on startup (idempotent `CREATE TABLE IF NOT EXISTS`). Append-only artifacts (action log, chat, geocode cache) stay as JSONL/JSON files — moving them into SQLite would harm the audit story, not help it.

**Schema** (`src/agentic_cab/adapters/schema.sql`):

```sql
CREATE TABLE IF NOT EXISTS drafts (
    draft_id        TEXT PRIMARY KEY,                  -- ULID
    option_snapshot TEXT NOT NULL,                     -- canonical JSON
    fingerprint     TEXT NOT NULL,                     -- sha256 hex
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    consumed_at     TEXT                               -- set when confirm succeeds; NULL otherwise
);

CREATE TABLE IF NOT EXISTS bookings (
    booking_id   TEXT PRIMARY KEY,                     -- provider-issued
    draft_id     TEXT NOT NULL REFERENCES drafts(draft_id),
    provider     TEXT NOT NULL,
    status       TEXT NOT NULL,
    summary      TEXT NOT NULL,                        -- canonical JSON
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    cancelled_at TEXT,
    cancellation TEXT                                  -- JSON or NULL
);
CREATE INDEX IF NOT EXISTS bookings_status_idx ON bookings(status);

CREATE TABLE IF NOT EXISTS booking_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id TEXT NOT NULL REFERENCES bookings(booking_id),
    ts         TEXT NOT NULL,
    kind       TEXT NOT NULL,
    payload    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS booking_events_booking_idx ON booking_events(booking_id, ts);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key             TEXT PRIMARY KEY,                  -- ULID minted by tool layer
    scope           TEXT NOT NULL,                     -- "confirm" | "cancel"
    booking_id      TEXT,                              -- resolved booking, if any
    response_blob   TEXT NOT NULL,                     -- canonical JSON of provider response
    created_at      TEXT NOT NULL
);
```

**`SqliteStateStore` port methods:**

```python
class StateStore(Protocol):
    # Drafts
    def put_draft(self, draft: Draft) -> None: ...
    def get_draft(self, draft_id: str) -> Draft | None: ...
    def consume_draft(self, draft_id: str, at: datetime) -> bool: ...   # atomic mark-used

    # Bookings
    def put_booking(self, booking: Booking) -> None: ...
    def get_booking(self, booking_id: str) -> Booking | None: ...
    def update_booking_status(self, booking_id: str, status: str, event: BookingEvent) -> None: ...
    def list_active_bookings(self) -> list[Booking]: ...

    # Idempotency
    def claim_or_recall(self, key: str, scope: Literal["confirm", "cancel"]) -> ClaimResult: ...
        # If key exists: returns ClaimResult(status="recalled", booking=<Booking>).
        # If not: claims it with no booking, returns ClaimResult(status="new") — caller must
        # finalize() with the provider response.
    def finalize_idempotency(self, key: str, booking: Booking) -> None: ...
```

**Idempotency flow inside `confirm_booking`:**

1. Mint `idempotency_key` (ULID) — or reuse if the tool layer is retrying.
2. `store.claim_or_recall(key, "confirm")`. If recalled, short-circuit: emit `executed` row with `latency_ms=0, replayed=true`, return the recalled booking. **No second provider call.**
3. Otherwise, call `provider.confirm(draft_id, idempotency_key)`. The sim provider also checks its own dedupe (mirroring real prod where the provider is the source of truth) — but the store is the durable barrier.
4. `store.finalize_idempotency(key, booking)`. Same transaction as `put_booking`. The adapter serialises the Booking internally to the `response_blob` column.

**Idempotency replay vs. structural gate.** When `claim_or_recall` returns
`status="recalled"`, the tool layer short-circuits without re-invoking the
`ConfirmationChannel`. This is intentional: the channel approval that produced
the original booking is the canonical consent for this idempotency_key, and
the recalled response is a deterministic replay of that approved action — not
a new state mutation. CLAUDE.md invariant 2 ("every state-mutating action goes
through a ConfirmationChannel") is preserved at the *idempotency-key* level,
not the function-call level.

**Concurrency:** WAL mode means CLI REPL and FastAPI web can both touch the DB without locking issues. Both share `data/state.db`; the action log is per-session so no contention there.

**Restart survival** is tested explicitly in `tests/contract/test_restart_survival.py`: book → kill process → restart → call `confirm` with the same idempotency key → assert same booking returned, no duplicate provider call.

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
- `make transcripts` regenerates all 5. Rendered MDs are **committed** so reviewers can inspect them without running.

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
│   ├── __main__.py              # repl | run-scenario | render-transcripts | web
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
│   │   ├── state_store.py       # SqliteStateStore
│   │   └── schema.sql           # applied on startup, idempotent
│   └── render/transcript.py
├── scenarios/                   # 5 yaml files
├── transcripts/                 # 5 generated .md files (committed)
├── data/                        # gitignored; state.db lives here
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
| Action log (JSONL) | 1 |
| SQLite schema + `SqliteStateStore` + idempotency flow | 2 |
| Restart-survival contract test | 0.5 |
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
| **Subtotal** | **37.5** |
| Buffer | 4.5 |
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
2. **What breaks first in production** (¼ page) — the obvious answer is **idempotency under retries**, and I built it: SQLite `UNIQUE` on `idempotency_keys`, `claim_or_recall` short-circuits the second provider call, restart-survival test proves it. With that in hand, the *next* thing that breaks is **agent-side retry policy**: the agent must reuse the same idempotency key across retries, not mint a new one. Today the tool layer mints per-call; production needs key-bound-to-intent (e.g., `sha256(draft_id + "confirm")`) so that an agent retry after a timeout doesn't bypass the dedupe barrier. 3-line fix sketch.
3. **Other landmines** (¼ page) — bullet list w/ one-line mitigation: price drift quote↔confirm (mitigated), PII in logs (partial — addresses redacted at log boundary), payment scope (out of scope), provider rate limits (need token bucket + backoff), state drift agent↔reality (needs background polling loop refreshing `bookings.status`), action log retention/rotation (not implemented).
4. **Next** — webhook/Slack `ConfirmationChannel`, real `LyftAdapter` proving the contract suite holds, payment authorization flow, agent-bound idempotency keys, background status poller, multi-user / auth layer.

## 16. Out of scope (explicit)

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
| +2 | Persistent state via SQLite? | Yes — stdlib `sqlite3`, no daemon, doesn't violate 3-min clone-to-run. Upgrades the idempotency story from hand-waved to implemented. Action log / chat / geocode cache stay JSONL. |
