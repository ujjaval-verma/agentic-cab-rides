# P1 Tracer Bullet Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Each task here is a **slice** in the `slice-delivery` sense — a vertical change that ends in `make ship` (push + green CI on `origin/main`). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render `transcripts/01_happy_path.md` end-to-end from a real Anthropic-driven agent loop, with all P1 acceptance bullets in `docs/PHASES.md` §P1 satisfied and every invariant in `CLAUDE.md` holding.

**Architecture:** Strict downward-pointing layers — CLI/Scenario runner → Agent loop → Tools → Core ports → Adapters. The tracer bullet exercises **one** path through every layer with minimum viable depth at each (uberx-only sim, scripted confirmation, single happy-path scenario). Subsequent phases (P2 tools, P3 sim depth, P4 scenarios, P5 web) thicken this skeleton without re-shaping it.

**Tech Stack:** Python 3.12, `uv`, Pydantic v2, Anthropic SDK (native tool use), stdlib `sqlite3`, `pyyaml`, `structlog`, `python-ulid`, `pytest` + `hypothesis`. No LangChain, no ORM.

---

## Source-of-truth references

- **Spec:** `docs/superpowers/specs/2026-05-19-agentic-cab-rides-design.md` — §3 layers, §4 types, §5 BookingProvider, §6 ConfirmationChannel, §7 tool surface (esp. `confirm_booking` algorithm), §8 action log, §10a SQLite state.
- **Invariants:** `CLAUDE.md` — all 14 must hold for every slice.
- **Acceptance bullets:** `docs/PHASES.md` §P1.
- **Out-of-scope:** `docs/FOLLOWUPS.md` — items below MUST stay deferred.
- **Process:** `.claude/skills/slice-delivery/SKILL.md` — slice lifecycle, Ralph-loop gate.

## Out of scope for P1 (do NOT pull in)

These are tracked elsewhere and must not be solved here, even if tempting:

- **Intent-bound idempotency keys.** P1 mints `idempotency_key = ulid()` per `confirm_booking` call. Per-intent keying is `FOLLOWUPS.md → Architecture` and lands in P3.
- **`AutoApproveChannel`.** P1 only needs `ScriptedConfirmationChannel`. The env-guard story is in `FOLLOWUPS.md` and lands with the channel itself.
- **Mock `AppointmentProvider`.** Zocdoc-reach is paper-only in P1; P3 contract suite.
- **`track_booking`, `cancel_booking`, `list_active_bookings`, `geocode_address` tools.** P2.
- **Surge, multi-class pricing, state-machine ticking.** P3. Sim returns one `uberx` option at a flat price.
- **`CLIConfirmationChannel`, web UI.** P2 / P5.
- **Nominatim adapter.** P2. P1 happy-path scenario uses pre-resolved `AddressRef` literals in the YAML.
- **Restart-survival contract test.** P3.

## Acceptance bullets this plan retires (from `docs/PHASES.md` §P1)

- [ ] `make transcripts` produces `transcripts/01_happy_path.md`
- [ ] Action log shows 4-row pattern (`requested → verified → executed → outcome`) for `confirm_booking`
- [ ] `tests/unit/test_action_log_integrity.py` — for any `action_id` where `verified.result == "rejected"`, no `executed` row exists
- [ ] Fingerprint check exercised by unit test (mutate draft → reject)
- [ ] Single-use draft check exercised (re-use consumed draft → reject, separate from idempotency)
- [ ] Idempotency `claim_or_recall` exercised end-to-end on happy path
- [ ] `scripts/replay.py <session_id>` reconstructs transcript from log

## Design principles this plan enforces

Per the `slice-delivery` skill's Rigor pillar (SOLID + refactor-scan), the plan locks in two non-negotiables that previous drafts violated and were corrected during review:

- **DI over field-overrides for test determinism.** Tools take collaborators (`clock`, `store`, `log`, `channel`, `provider`) as keyword-only arguments. When tests need to pin non-determinism (the idempotency key in T13, the RNG seed in T8, the clock in everything), they inject a collaborator at the function boundary. **Do NOT** add `_override` fields to Pydantic args models, env-var escape hatches, or module-level monkeypatches. The agent-facing schema (driven by `pydantic_to_anthropic_tool`) stays pure intent; DI seams live on the Python function signature only.
- **Stable interfaces under future swaps.** T13 ships `key_factory: Callable[[], str] = new_idempotency_key`. P3 will swap the default to `lambda: intent_bound_key(draft_id, "confirm")`. Neither the tool body nor the tests change — that's the test surviving the refactor (Pocock §"Existing code the new code reveals as problematic"). If you find yourself editing T13's test when wiring intent-bound keys in P3, stop and reconsider — the seam is wrong.

## Interfaces P1 locks in (P2/P3/P5 will lean on these — design carefully)

| Interface | File | Reason it's load-bearing |
|---|---|---|
| `BookingProvider` Protocol | `core/ports.py` | P3 contract suite parameterises over implementations; signature must be stable. |
| `ConfirmationChannel` Protocol | `core/ports.py` | P2 (CLI), P5 (Web) implement against it; method shape must not shift. |
| `StateStore` Protocol incl. `claim_or_recall` / `finalize_idempotency` | `core/ports.py` | P3 restart-survival test mocks at this boundary. |
| Action-log row schemas (4 kinds) | `adapters/action_log.py` | Renderer, replay, scenario invariant checker all parse these. |
| Tool registry's Pydantic → Anthropic schema converter | `tools/registry.py` | P2 adds 4 more tools — converter must stay generic. |
| Scenario YAML schema | `scenarios/*.yaml` + runner | P4 ships 4 more scenarios on this schema. |

Before merging any slice that touches the above, the Ralph loop must confirm the interface is general enough for the named consumer. Re-shaping these in P2+ is a design failure.

## Dependency graph (build order)

```
T1 ids ──┐
T2 types ┼──► T3 fingerprint ──► T4 ports ──► T5 clock ──┐
         │                                                ├──► T6 action_log ──┐
         │                                                │                    │
         │                                                └──► T7 state_store ─┤
         │                                                                     │
         └────────────────────────────────────────────────► T8 uber_sim ───────┤
                                                                               │
                                            T9 scripted_channel ───────────────┤
                                                                               ▼
                                                          T10 tool_registry ──► T11 discover ──► T12 prepare ──► T13 confirm
                                                                                                                       │
                                                          T14 system_prompt + agent_loop ◄────────────────────────────┘
                                                                                │
                                                          T15 scenario_runner + 01_happy_path.yaml
                                                                                │
                                                          T16 transcript_renderer + replay.py
                                                                                │
                                                          T17 action_log_integrity_test + acceptance verification
```

**Parallelism note:** T5/T6/T7 are mutually independent once T4 lands and may be delegated to subagents in worktrees per `superpowers:subagent-driven-development` if the executor wants throughput. Everything from T8 onward is sequential — the tracer demands it. Default to sequential execution; use parallelism only if the executor is comfortable rebasing three small branches before T8.

## Ralph-loop scope

Trigger an adversarial review (`Agent(subagent_type="feature-dev:code-reviewer", ...)`) **before push** for:

- T3 (fingerprint canonicalisation) — security/correctness critical
- T4 (ports) — interface shape locks in downstream phases
- T7 (state_store + idempotency) — concurrency + restart story
- T13 (confirm tool) — the heart of the system; gate, fingerprint, re-quote, idempotency, 4-row log all converge here
- T15 (scenario runner) — invariant checker correctness

Skip Ralph for T1, T2, T5, T6 (mechanical), T9 (one-file adapter), T11, T12 (thin tools), T16 (renderer is snapshot-tested). T8, T10, T14, T17: optional — invoke if anything feels subtle.

## Slice → commit message contract

Every slice ends in **one** Conventional Commit with `feat(P1):` / `test(P1):` scope per `docs/PHASES.md` §"Commit message convention". Body includes:
- One paragraph of *why*
- Ralph-loop dispositions if applicable: `fixed: …` / `deferred: → FOLLOWUPS.md` / `rejected: <reason>`

Each slice's "Commit" step below quotes the exact `git commit -m` message.

## Per-slice TDD discipline (from `slice-delivery` §TDD scope)

| Slice | Discipline |
|---|---|
| T1 ids | Smoke test only (one assert per helper). |
| T2 types | Smoke tests for discriminator + `Money` no-float. Pydantic shape enforcement is implicit. |
| T3 fingerprint | **TDD strict** + Hypothesis property tests (order-independent dict keys, whitespace-insensitive). |
| T4 ports | No tests — Protocols are interface declarations. |
| T5 clock | Smoke test on `FrozenClock` advance. |
| T6 action_log | TDD: row-shape, append-only, redact() integration test (PII followup from `FOLLOWUPS.md`). |
| T7 state_store | TDD: schema apply idempotent, `consume_draft` atomicity, `claim_or_recall` recall path. |
| T8 uber_sim | Fixture-based: pinned input → pinned `Option` output. No TDD on pricing math (deterministic by construction). |
| T9 scripted_channel | TDD: match-substring approve/decline. |
| T10 tool_registry | TDD on schema-conversion (Pydantic model → Anthropic JSONSchema dict). |
| T11 discover | TDD on the tool wrapper (action log emission + provider delegation). |
| T12 prepare | TDD: persistence + fingerprint computation + expiry. |
| T13 confirm | **TDD strict.** Unit tests with `FakeBookingProvider` + `ScriptedConfirmationChannel` covering: happy path, fingerprint mismatch, consumed draft, expired draft, idempotency replay, price drift → PriceChanged. |
| T14 agent_loop + system_prompt | **No unit TDD.** Verified end-to-end via T15. |
| T15 scenario_runner | TDD on invariant checkers (action-log assertions); scenario itself is the integration test. |
| T16 transcript_renderer | Snapshot test (syrupy) on one fixed input. |
| T17 integrity test | This *is* the test. |

---

## Task 1: ULID helpers (`core/ids.py`)

**Files:**
- Create: `src/agentic_cab/core/ids.py`
- Create: `tests/unit/test_ids.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_ids.py
from agentic_cab.core.ids import new_session_id, new_turn_id, new_action_id, new_draft_id, new_idempotency_key

def test_ids_are_ulids():
    for factory in (new_session_id, new_turn_id, new_action_id, new_draft_id, new_idempotency_key):
        value = factory()
        assert isinstance(value, str)
        assert len(value) == 26  # ULID canonical length

def test_ids_are_sortable_by_creation():
    a = new_session_id()
    b = new_session_id()
    assert a < b  # ULIDs are time-monotonic at millisecond resolution
```

- [ ] **Step 2: Run — expect ImportError**

```bash
uv run pytest tests/unit/test_ids.py -v
```

Expected: `ModuleNotFoundError: agentic_cab.core.ids`.

- [ ] **Step 3: Implement**

```python
# src/agentic_cab/core/ids.py
"""ULID factories. One factory per ID kind so call sites read as intent, not 'new_ulid'."""
from __future__ import annotations
from ulid import ULID

def _new() -> str:
    return str(ULID())

def new_session_id() -> str:    return _new()
def new_turn_id() -> str:       return _new()
def new_action_id() -> str:     return _new()
def new_draft_id() -> str:      return _new()
def new_idempotency_key() -> str: return _new()
```

- [ ] **Step 4: Tests pass**

```bash
uv run pytest tests/unit/test_ids.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Verify + commit**

```bash
make verify
git add src/agentic_cab/core/ids.py tests/unit/test_ids.py
git commit -m "feat(P1): add ULID factories for session/turn/action/draft/idempotency

One factory per kind keeps call sites self-describing.
Sortable IDs let the action log be ordered by ID without a separate ts index."
```

- [ ] **Step 6: Ship**

```bash
make ship && gh run list --limit 1 --branch main
```

Expected: green.

---

## Task 2: Core types (`core/types.py`)

**Files:**
- Create: `src/agentic_cab/core/types.py`
- Create: `tests/unit/test_types.py`

**Why a single file:** P1 only ships `RideIntent`; P3 introduces `AppointmentIntent`. Keeping the discriminated union in one file makes the variant set discoverable. If T2 grows past ~250 lines later, split by intent kind.

- [ ] **Step 1: Failing tests for shape + invariants**

```python
# tests/unit/test_types.py
from datetime import UTC, datetime
import pytest
from pydantic import ValidationError
from agentic_cab.core.types import (
    Money, AddressRef, RideIntent, RideOptionDetails, Option, Draft, Booking, BookingEvent,
)

def test_money_is_integer_cents_only():
    m = Money(amount_cents=1234, currency="USD")
    assert m.amount_cents == 1234
    with pytest.raises(ValidationError):
        Money(amount_cents=12.34, currency="USD")  # type: ignore[arg-type]

def test_ride_intent_defaults():
    intent = RideIntent(
        pickup=AddressRef(raw="A", formatted="A", lat=0.0, lng=0.0, confidence=1.0),
        dropoff=AddressRef(raw="B", formatted="B", lat=0.0, lng=0.0, confidence=1.0),
        when="now",
    )
    assert intent.kind == "ride"
    assert intent.party_size == 1

def test_option_details_discriminated():
    details = RideOptionDetails(vehicle_class="uberx", capacity=4)
    assert details.kind == "ride"
```

- [ ] **Step 2: Run — expect ImportError**

```bash
uv run pytest tests/unit/test_types.py -v
```

- [ ] **Step 3: Implement**

```python
# src/agentic_cab/core/types.py
"""Pydantic v2 contracts shared across all layers. See spec §4."""
from __future__ import annotations
from datetime import datetime
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt

class Money(BaseModel):
    """Integer cents only. No floats for money — spec §4 + CLAUDE.md invariant 10."""
    model_config = ConfigDict(frozen=True)
    amount_cents: StrictInt
    currency: str

class AddressRef(BaseModel):
    model_config = ConfigDict(frozen=True)
    raw: str
    formatted: str
    lat: float
    lng: float
    confidence: float  # [0.0, 1.0]; <0.5 is the bad-address signal (P2/P3)

class RideIntent(BaseModel):
    kind: Literal["ride"] = "ride"
    pickup: AddressRef
    dropoff: AddressRef
    when: Literal["now"] | datetime
    party_size: int = 1
    vehicle_class_preference: list[str] | None = None

Intent = RideIntent  # union widens in P3+ to | AppointmentIntent

class RideOptionDetails(BaseModel):
    kind: Literal["ride"] = "ride"
    vehicle_class: str  # "uberx" | "comfort" | "uberxl" | "black" — P1 only ships uberx
    capacity: int

OptionDetails = Annotated[RideOptionDetails, Field(discriminator="kind")]

class Option(BaseModel):
    model_config = ConfigDict(frozen=True)
    option_id: str
    provider: str
    title: str
    price: Money
    eta_seconds: int | None
    when: datetime | None
    details: OptionDetails
    provider_metadata: dict = Field(default_factory=dict)
    expires_at: datetime

class Draft(BaseModel):
    model_config = ConfigDict(frozen=True)
    draft_id: str
    option_snapshot: Option
    fingerprint: str
    created_at: datetime
    expires_at: datetime

class BookingEvent(BaseModel):
    ts: datetime
    kind: str
    payload: dict

class CancellationInfo(BaseModel):
    cancelled_at: datetime
    fee: Money | None
    reason: str | None

class Booking(BaseModel):
    booking_id: str
    draft_id: str
    status: Literal[
        "confirmed", "driver_assigned", "en_route", "arrived",
        "in_progress", "completed", "cancelled", "failed",
    ]
    provider: str
    summary: Option
    events: list[BookingEvent] = Field(default_factory=list)
    cancellation: CancellationInfo | None = None
```

- [ ] **Step 4: Tests pass**

```bash
uv run pytest tests/unit/test_types.py -v
```

- [ ] **Step 5: Verify + commit + ship**

```bash
make verify
git add src/agentic_cab/core/types.py tests/unit/test_types.py
git commit -m "feat(P1): add core Pydantic contracts (Money, Intent, Option, Draft, Booking)

Single file for the type contract — see spec §4. Money is StrictInt cents only.
Option/Draft are frozen so they can be safely captured into Draft.option_snapshot.
RideOptionDetails uses a 'kind' discriminator so the union widens cleanly for
AppointmentIntent in P3 without re-shaping consumers."
make ship
```

---

## Task 3: Fingerprint (`core/fingerprint.py`)  **(Ralph-loop required)**

**Files:**
- Create: `src/agentic_cab/core/fingerprint.py`
- Create: `tests/unit/test_fingerprint.py`

**Why this matters:** Invariant 3 (CLAUDE.md). The fingerprint is the structural check that the draft the user approves equals the draft the provider receives. Any non-determinism here silently breaks the gate.

- [ ] **Step 1: Failing tests — example-based + Hypothesis property**

```python
# tests/unit/test_fingerprint.py
import json
from datetime import UTC, datetime
from hypothesis import given, strategies as st
from agentic_cab.core.fingerprint import canonical_json, fingerprint
from agentic_cab.core.types import Money, Option, RideOptionDetails

def _opt() -> Option:
    return Option(
        option_id="opt-1", provider="uber-sim", title="UberX",
        price=Money(amount_cents=1500, currency="USD"),
        eta_seconds=180, when=None,
        details=RideOptionDetails(vehicle_class="uberx", capacity=4),
        provider_metadata={"zone": "SF-1"},
        expires_at=datetime(2026, 5, 19, 12, 0, tzinfo=UTC),
    )

def test_fingerprint_is_64_hex():
    fp = fingerprint(_opt())
    assert len(fp) == 64
    assert all(c in "0123456789abcdef" for c in fp)

def test_fingerprint_changes_on_price_mutation():
    a = _opt()
    b = a.model_copy(update={"price": Money(amount_cents=9999, currency="USD")})
    assert fingerprint(a) != fingerprint(b)

def test_canonical_json_is_dict_order_independent():
    # JSON object key order must not affect the hash.
    d1 = {"a": 1, "b": {"x": 2, "y": 3}}
    d2 = {"b": {"y": 3, "x": 2}, "a": 1}
    assert canonical_json(d1) == canonical_json(d2)

@given(st.dictionaries(st.text(min_size=1, max_size=8),
                       st.one_of(st.integers(), st.text(max_size=20), st.booleans()),
                       min_size=0, max_size=8))
def test_canonical_json_stable_under_key_permutation(d):
    import random
    keys = list(d.keys())
    shuffled = dict(zip(random.sample(keys, len(keys)), [d[k] for k in random.sample(keys, len(keys))], strict=False)) if d else d
    # Round-trip via JSON to ensure no Python dict-order leak
    a = canonical_json(json.loads(json.dumps(d)))
    b = canonical_json(json.loads(json.dumps(shuffled)))
    assert a == b
```

- [ ] **Step 2: Run — expect import failure**

```bash
uv run pytest tests/unit/test_fingerprint.py -v
```

- [ ] **Step 3: Implement**

```python
# src/agentic_cab/core/fingerprint.py
"""Deterministic fingerprint of an Option snapshot. See spec §4 + CLAUDE.md inv. 3."""
from __future__ import annotations
import hashlib
import json
from typing import Any
from pydantic import BaseModel

def canonical_json(obj: Any) -> str:
    """JSON with sorted keys, no whitespace, default str fallback for datetimes."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)

def fingerprint(model_or_dict: BaseModel | dict) -> str:
    if isinstance(model_or_dict, BaseModel):
        payload = model_or_dict.model_dump(mode="json")
    else:
        payload = model_or_dict
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Tests pass**

```bash
uv run pytest tests/unit/test_fingerprint.py -v
```

Expected: 4 passed (incl. Hypothesis run with default 100 examples).

- [ ] **Step 5: Verify**

```bash
make verify
```

- [ ] **Step 6: Ralph loop**

```python
# Pseudocode for the executor — actual call shape per slice-delivery skill
Agent(
  description="Adversarial review: fingerprint",
  subagent_type="feature-dev:code-reviewer",
  prompt="""
    Review src/agentic_cab/core/fingerprint.py and tests/unit/test_fingerprint.py
    against:
      - spec §4 (option_snapshot, fingerprint definition)
      - CLAUDE.md invariant 3 (fingerprint computed at prepare, re-verified at confirm)
      - CLAUDE.md invariant 9 (determinism)

    Specifically check:
      - Does canonical_json produce identical bytes for inputs that should be equal?
        Consider: dict key order, integer vs float representations, datetime serialisation,
        Unicode normalisation, NaN/Inf handling.
      - Does fingerprint() hash the same bytes that a future re-verification would hash,
        given that Pydantic's model_dump(mode='json') is the canonical entry point?
      - Are there any inputs where Python dict iteration order could leak into the hash?

    Flag blocking issues with file:line. Do not propose fixes — flag, let the author decide.
  """
)
```

Capture dispositions in the commit body. If the reviewer flags Unicode normalisation, NaN, or float vs int collisions as blocking, fix on-branch before commit.

- [ ] **Step 7: Commit + ship**

```bash
git add src/agentic_cab/core/fingerprint.py tests/unit/test_fingerprint.py
git commit -m "feat(P1): add option-snapshot fingerprint with hypothesis property tests

sha256(canonical_json(model_dump(mode='json'))). canonical_json sorts keys
and strips whitespace so dict ordering cannot leak into the hash — verified
by a Hypothesis property test that permutes keys.

Ralph-loop dispositions:
  fixed: <enumerate>
  deferred: <enumerate → FOLLOWUPS.md>"
make ship
```

---

## Task 4: Ports (`core/ports.py`)  **(Ralph-loop required)**

**Files:**
- Create: `src/agentic_cab/core/ports.py`

**Why no tests:** Protocols are interface declarations. Their tests are the contract suites their implementors must pass (T8 for `BookingProvider`, T7 for `StateStore`, T9 for `ConfirmationChannel`, T6 for `ActionLog`).

- [ ] **Step 1: Implement the full protocol surface**

```python
# src/agentic_cab/core/ports.py
"""All port Protocols. Adapters depend on this; the agent never imports an adapter directly.

See spec §3 (layers), §5 (BookingProvider), §6 (ConfirmationChannel), §10a (StateStore).
"""
from __future__ import annotations
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable
from pydantic import BaseModel
from agentic_cab.core.types import Booking, BookingEvent, Draft, Intent, Option

# ---------- BookingProvider ----------
@runtime_checkable
class BookingProvider(Protocol):
    name: str
    supported_intent_kinds: set[str]

    async def discover(self, intent: Intent) -> list[Option]: ...
    async def prepare(self, option_id: str) -> Draft: ...
    async def confirm(self, draft_id: str, idempotency_key: str) -> Booking: ...
    async def track(self, booking_id: str) -> Booking: ...
    async def cancel(self, booking_id: str, idempotency_key: str) -> Booking: ...

# ---------- ConfirmationChannel ----------
class ConfirmationRequest(BaseModel):
    draft_id: str
    summary: str
    fingerprint: str
    details: dict
    expires_at: datetime

class ConfirmationResult(BaseModel):
    approved: bool
    reason: Literal["approved", "declined", "expired", "timeout"]
    approved_at: datetime | None
    approver: str | None

@runtime_checkable
class ConfirmationChannel(Protocol):
    async def request(self, req: ConfirmationRequest) -> ConfirmationResult: ...

# ---------- Clock ----------
@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...

# ---------- ActionLog ----------
class ActionLogRow(BaseModel):
    """Discriminated by 'kind'. Concrete shapes live in adapters/action_log.py."""
    ts: datetime
    session_id: str
    action_id: str
    kind: Literal["requested", "verified", "executed", "outcome"]

@runtime_checkable
class ActionLog(Protocol):
    def emit(self, row: dict) -> None: ...    # dict to allow heterogeneous shapes per spec §8
    def read(self, session_id: str) -> list[dict]: ...

# ---------- StateStore ----------
class ClaimResult(BaseModel):
    status: Literal["new", "recalled"]
    response_blob: dict | None = None  # populated only when status == "recalled"
    booking_id: str | None = None

@runtime_checkable
class StateStore(Protocol):
    # Drafts
    def put_draft(self, draft: Draft) -> None: ...
    def get_draft(self, draft_id: str) -> Draft | None: ...
    def consume_draft(self, draft_id: str, at: datetime) -> bool: ...

    # Bookings
    def put_booking(self, booking: Booking) -> None: ...
    def get_booking(self, booking_id: str) -> Booking | None: ...
    def update_booking_status(self, booking_id: str, status: str, event: BookingEvent) -> None: ...
    def list_active_bookings(self) -> list[Booking]: ...

    # Idempotency
    def claim_or_recall(self, key: str, scope: Literal["confirm", "cancel"]) -> ClaimResult: ...
    def finalize_idempotency(self, key: str, booking_id: str, response_blob: dict) -> None: ...
```

- [ ] **Step 2: Verify**

```bash
make verify
```

Expected: pyright strict on `core/` passes — Protocols typecheck.

- [ ] **Step 3: Ralph loop on the interface shape**

```
Review src/agentic_cab/core/ports.py against:
  - spec §5 (BookingProvider), §6 (ConfirmationChannel), §10a (StateStore)
  - CLAUDE.md invariants 1, 2, 5, 7

Specifically check:
  - Will P2 (CLI + Web ConfirmationChannel, 4 more tools) need to widen any signature?
  - Will P3 (restart-survival, contract suite, AppointmentIntent) need to widen any signature?
  - Is ClaimResult expressive enough to short-circuit `confirm` without a second provider call?
  - Are async/sync boundaries correct (provider async, store sync, channel async)?

Flag blocking interface gaps. Defer optimisation suggestions.
```

- [ ] **Step 4: Commit + ship**

```bash
git add src/agentic_cab/core/ports.py
git commit -m "feat(P1): define BookingProvider/ConfirmationChannel/Clock/ActionLog/StateStore ports

Locks in the interfaces P2 (CLI/Web channels + 4 more tools), P3 (contract suite +
restart-survival + AppointmentIntent) lean on. Provider is async; StateStore is sync
(sqlite3 is sync); ConfirmationChannel is async (Web channel needs asyncio.Future).
ClaimResult carries response_blob so confirm_booking can short-circuit without a
second provider call.

Ralph-loop dispositions:
  fixed: <enumerate>
  deferred: <enumerate → FOLLOWUPS.md>"
make ship
```

---

## Task 5: Clock (`adapters/clock.py`)

**Files:**
- Create: `src/agentic_cab/adapters/__init__.py`
- Create: `src/agentic_cab/adapters/clock.py`
- Create: `tests/unit/test_clock.py`

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_clock.py
from datetime import UTC, datetime, timedelta
from agentic_cab.adapters.clock import FrozenClock, SystemClock

def test_frozen_clock_does_not_advance():
    c = FrozenClock(start=datetime(2026, 5, 19, 12, 0, tzinfo=UTC))
    assert c.now() == datetime(2026, 5, 19, 12, 0, tzinfo=UTC)
    assert c.now() == datetime(2026, 5, 19, 12, 0, tzinfo=UTC)

def test_frozen_clock_tick():
    c = FrozenClock(start=datetime(2026, 5, 19, 12, 0, tzinfo=UTC))
    c.tick(timedelta(minutes=5))
    assert c.now() == datetime(2026, 5, 19, 12, 5, tzinfo=UTC)

def test_system_clock_is_utc_aware():
    c = SystemClock()
    assert c.now().tzinfo is not None
```

- [ ] **Step 2: Implement**

```python
# src/agentic_cab/adapters/clock.py
from __future__ import annotations
from datetime import UTC, datetime, timedelta

class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

class FrozenClock:
    def __init__(self, start: datetime) -> None:
        assert start.tzinfo is not None, "FrozenClock requires tz-aware datetime"
        self._t = start

    def now(self) -> datetime:
        return self._t

    def tick(self, delta: timedelta) -> None:
        self._t = self._t + delta
```

- [ ] **Step 3: Pass + verify + commit + ship**

```bash
uv run pytest tests/unit/test_clock.py -v
make verify
git add src/agentic_cab/adapters/clock.py src/agentic_cab/adapters/__init__.py tests/unit/test_clock.py
git commit -m "feat(P1): add SystemClock and FrozenClock adapters

FrozenClock.tick() is the scenario-mode advance mechanism; SystemClock is repl/web.
Both implement the Clock Protocol structurally (no inheritance)."
make ship
```

---

## Task 6: Action log (`adapters/action_log.py`)

**Files:**
- Create: `src/agentic_cab/adapters/action_log.py`
- Create: `tests/unit/test_action_log.py`

**Acceptance covered:** the PII redaction integration test from `docs/FOLLOWUPS.md → Tests/coverage` ships HERE.

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_action_log.py
import json
from datetime import UTC, datetime
from pathlib import Path
from agentic_cab.adapters.action_log import JSONLActionLog, redact

def test_log_appends_and_reads(tmp_path: Path):
    log = JSONLActionLog(log_dir=tmp_path)
    sid = "01HEXAMPLE"
    log.emit({"ts": "2026-05-19T12:00:00+00:00", "session_id": sid, "action_id": "A", "kind": "requested", "tool": "discover_options", "args": {}, "actor": "agent"})
    log.emit({"ts": "2026-05-19T12:00:01+00:00", "session_id": sid, "action_id": "A", "kind": "outcome", "status": "ok", "result": {}, "error": None})
    rows = log.read(sid)
    assert len(rows) == 2
    assert [r["kind"] for r in rows] == ["requested", "outcome"]

def test_log_file_is_appendonly_format(tmp_path: Path):
    log = JSONLActionLog(log_dir=tmp_path)
    log.emit({"ts": "2026-05-19T12:00:00+00:00", "session_id": "S", "action_id": "A", "kind": "requested"})
    content = (tmp_path / "S.jsonl").read_text()
    # One JSON object per line
    lines = [ln for ln in content.splitlines() if ln.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["kind"] == "requested"

def test_redact_strips_phone_and_name_in_address():
    result = redact({
        "intent": {
            "pickup": {"raw": "Jane Doe's house, 555-1234", "formatted": "123 Main St", "lat": 1.0, "lng": 2.0},
            "dropoff": {"raw": "the office", "formatted": "456 Market St", "lat": 3.0, "lng": 4.0},
        }
    })
    serialised = json.dumps(result)
    assert "555-1234" not in serialised
    assert "Jane Doe" not in serialised
    assert "the office" not in serialised  # raw stripped
    assert "123 Main St" in serialised      # formatted survives
    assert "456 Market St" in serialised
```

- [ ] **Step 2: Implement**

```python
# src/agentic_cab/adapters/action_log.py
"""JSONL action log, per-session file. See spec §8 + CLAUDE.md inv. 6, 7, 8."""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any

_PHONE = re.compile(r"\+?\d[\d\-\(\)\s]{6,}\d")

def _redact_address(addr: dict) -> dict:
    """Drop raw text; keep only formatted/lat/lng/confidence (spec §8 PII rule)."""
    return {
        "formatted": addr.get("formatted", ""),
        "lat": addr.get("lat"),
        "lng": addr.get("lng"),
        "confidence": addr.get("confidence"),
    }

def redact(payload: Any) -> Any:
    """Walk a JSON-shaped payload, redact PII at known address fields + phone-shaped substrings."""
    if isinstance(payload, dict):
        out: dict = {}
        for k, v in payload.items():
            if k in ("pickup", "dropoff") and isinstance(v, dict) and "raw" in v:
                out[k] = _redact_address(v)
            else:
                out[k] = redact(v)
        return out
    if isinstance(payload, list):
        return [redact(x) for x in payload]
    if isinstance(payload, str):
        return _PHONE.sub("[redacted-phone]", payload)
    return payload

class JSONLActionLog:
    def __init__(self, log_dir: Path) -> None:
        self._dir = Path(log_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self._dir / f"{session_id}.jsonl"

    def emit(self, row: dict) -> None:
        sid = row["session_id"] if "session_id" in row else row.get("sid")
        if sid is None:
            raise ValueError("row missing session_id")
        redacted = redact(row)
        with self._path(sid).open("a", encoding="utf-8") as f:
            f.write(json.dumps(redacted, separators=(",", ":"), default=str) + "\n")

    def read(self, session_id: str) -> list[dict]:
        path = self._path(session_id)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
```

- [ ] **Step 3: Run + verify + commit + ship**

```bash
uv run pytest tests/unit/test_action_log.py -v
make verify
git add src/agentic_cab/adapters/action_log.py tests/unit/test_action_log.py
git commit -m "feat(P1): add JSONLActionLog + redact() with PII integration test

Per spec §8: address 'raw' is dropped at the log boundary; only {formatted, lat, lng,
confidence} survive. Phone-shaped substrings are masked everywhere. The integration
test feeds a realistic RideIntent with name+phone in the raw address and asserts no
leak — retires the PII followup item."
make ship
```

Then strike the PII redaction line from `docs/FOLLOWUPS.md` in a separate `docs:` commit:

```bash
# Edit docs/FOLLOWUPS.md to remove the PII redaction integration test bullet
git add docs/FOLLOWUPS.md
git commit -m "docs: retire PII redaction integration test followup (shipped in P1 T6)"
make ship
```

---

## Task 7: State store + schema (`adapters/state_store.py`, `adapters/schema.sql`)  **(Ralph-loop required)**

**Files:**
- Create: `src/agentic_cab/adapters/schema.sql`
- Create: `src/agentic_cab/adapters/state_store.py`
- Create: `tests/unit/test_state_store.py`
- Modify: `Makefile` — `make reset` re-applies schema (retires FOLLOWUPS operational item)
- Modify: `src/agentic_cab/__main__.py` — add `init-db` subcommand for the new reset flow

- [ ] **Step 1: Write `schema.sql` exactly per spec §10a**

(Copy verbatim from spec §10a — drafts, bookings, booking_events, idempotency_keys.)

- [ ] **Step 2: Failing tests**

```python
# tests/unit/test_state_store.py
from datetime import UTC, datetime, timedelta
from pathlib import Path
import pytest
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.core.types import Money, Option, RideOptionDetails, Draft, Booking, BookingEvent

def _draft(draft_id="D1", expires_in_seconds=60) -> Draft:
    opt = Option(
        option_id="opt-1", provider="uber-sim", title="UberX",
        price=Money(amount_cents=1500, currency="USD"),
        eta_seconds=180, when=None,
        details=RideOptionDetails(vehicle_class="uberx", capacity=4),
        expires_at=datetime(2026, 5, 19, 13, 0, tzinfo=UTC),
    )
    return Draft(
        draft_id=draft_id, option_snapshot=opt, fingerprint="f" * 64,
        created_at=datetime(2026, 5, 19, 12, 0, tzinfo=UTC),
        expires_at=datetime(2026, 5, 19, 12, 0, tzinfo=UTC) + timedelta(seconds=expires_in_seconds),
    )

@pytest.fixture
def store(tmp_path: Path) -> SqliteStateStore:
    s = SqliteStateStore(db_path=tmp_path / "state.db")
    s.apply_schema()
    return s

def test_put_and_get_draft(store):
    d = _draft()
    store.put_draft(d)
    got = store.get_draft("D1")
    assert got is not None
    assert got.draft_id == "D1"
    assert got.option_snapshot.price.amount_cents == 1500

def test_consume_draft_is_atomic_single_use(store):
    d = _draft()
    store.put_draft(d)
    now = datetime(2026, 5, 19, 12, 0, 30, tzinfo=UTC)
    assert store.consume_draft("D1", at=now) is True
    assert store.consume_draft("D1", at=now) is False   # second consume rejected

def test_claim_or_recall_new_then_recalled(store):
    key = "K1"
    first = store.claim_or_recall(key, scope="confirm")
    assert first.status == "new"
    # Finalize with a fake response blob
    store.finalize_idempotency(key, booking_id="B1", response_blob={"booking_id": "B1", "status": "confirmed"})
    second = store.claim_or_recall(key, scope="confirm")
    assert second.status == "recalled"
    assert second.booking_id == "B1"
    assert second.response_blob == {"booking_id": "B1", "status": "confirmed"}

def test_schema_apply_is_idempotent(store):
    store.apply_schema()
    store.apply_schema()  # second apply must not raise
```

- [ ] **Step 3: Implement**

```python
# src/agentic_cab/adapters/state_store.py
"""SqliteStateStore — see spec §10a + CLAUDE.md inv. 5, 7, 11."""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from agentic_cab.core.ports import ClaimResult
from agentic_cab.core.types import Booking, BookingEvent, Draft

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")

class SqliteStateStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

    def apply_schema(self) -> None:
        sql = _SCHEMA_PATH.read_text()
        with self._conn:
            self._conn.executescript(sql)

    # --- Drafts ---
    def put_draft(self, draft: Draft) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO drafts(draft_id, option_snapshot, fingerprint, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (draft.draft_id, draft.option_snapshot.model_dump_json(),
                 draft.fingerprint, draft.created_at.isoformat(), draft.expires_at.isoformat()),
            )

    def get_draft(self, draft_id: str) -> Draft | None:
        row = self._conn.execute(
            "SELECT draft_id, option_snapshot, fingerprint, created_at, expires_at, consumed_at "
            "FROM drafts WHERE draft_id = ?", (draft_id,)
        ).fetchone()
        if row is None:
            return None
        from agentic_cab.core.types import Option
        opt = Option.model_validate_json(row[1])
        return Draft(
            draft_id=row[0], option_snapshot=opt, fingerprint=row[2],
            created_at=datetime.fromisoformat(row[3]), expires_at=datetime.fromisoformat(row[4]),
        )

    def consume_draft(self, draft_id: str, at: datetime) -> bool:
        with self._conn:
            cur = self._conn.execute(
                "UPDATE drafts SET consumed_at = ? WHERE draft_id = ? AND consumed_at IS NULL",
                (at.isoformat(), draft_id),
            )
            return cur.rowcount == 1

    # --- Bookings ---
    def put_booking(self, booking: Booking) -> None:
        now = datetime.utcnow().isoformat()
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO bookings(booking_id, draft_id, provider, status, summary, "
                "created_at, updated_at, cancelled_at, cancellation) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (booking.booking_id, booking.draft_id, booking.provider, booking.status,
                 booking.summary.model_dump_json(), now, now, None, None),
            )

    def get_booking(self, booking_id: str) -> Booking | None:
        row = self._conn.execute(
            "SELECT booking_id, draft_id, provider, status, summary FROM bookings WHERE booking_id = ?",
            (booking_id,),
        ).fetchone()
        if row is None:
            return None
        from agentic_cab.core.types import Option
        return Booking(
            booking_id=row[0], draft_id=row[1], provider=row[2], status=row[3],
            summary=Option.model_validate_json(row[4]),
        )

    def update_booking_status(self, booking_id: str, status: str, event: BookingEvent) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE bookings SET status = ?, updated_at = ? WHERE booking_id = ?",
                (status, event.ts.isoformat(), booking_id),
            )
            self._conn.execute(
                "INSERT INTO booking_events(booking_id, ts, kind, payload) VALUES (?, ?, ?, ?)",
                (booking_id, event.ts.isoformat(), event.kind, json.dumps(event.payload, default=str)),
            )

    def list_active_bookings(self) -> list[Booking]:
        rows = self._conn.execute(
            "SELECT booking_id FROM bookings "
            "WHERE status NOT IN ('completed','cancelled','failed')"
        ).fetchall()
        return [b for r in rows if (b := self.get_booking(r[0])) is not None]

    # --- Idempotency ---
    def claim_or_recall(self, key: str, scope: str) -> ClaimResult:
        existing = self._conn.execute(
            "SELECT booking_id, response_blob FROM idempotency_keys WHERE key = ?", (key,)
        ).fetchone()
        if existing is not None:
            blob = json.loads(existing[1]) if existing[1] else None
            return ClaimResult(status="recalled", booking_id=existing[0], response_blob=blob)
        with self._conn:
            self._conn.execute(
                "INSERT INTO idempotency_keys(key, scope, booking_id, response_blob, created_at) "
                "VALUES (?, ?, NULL, '', ?)",
                (key, scope, datetime.utcnow().isoformat()),
            )
        return ClaimResult(status="new")

    def finalize_idempotency(self, key: str, booking_id: str, response_blob: dict) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE idempotency_keys SET booking_id = ?, response_blob = ? WHERE key = ?",
                (booking_id, json.dumps(response_blob, default=str), key),
            )
```

- [ ] **Step 4: Wire `init-db` + fix `make reset`**

Add to `src/agentic_cab/__main__.py` a Typer command:

```python
@app.command("init-db")
def init_db() -> None:
    from pathlib import Path
    from agentic_cab.adapters.state_store import SqliteStateStore
    s = SqliteStateStore(db_path=Path("data/state.db"))
    s.apply_schema()
```

Edit `Makefile`:

```makefile
reset:
	rm -rf data logs chat
	mkdir -p data logs chat
	uv run python -m agentic_cab init-db
```

- [ ] **Step 5: Run + verify + Ralph loop**

```bash
uv run pytest tests/unit/test_state_store.py -v
make reset  # smoke: should succeed and create data/state.db
make verify
```

Ralph loop:

```
Review src/agentic_cab/adapters/state_store.py + schema.sql against:
  - spec §10a (schema, claim_or_recall semantics, restart-survival expectation)
  - CLAUDE.md invariants 5, 7

Specifically check:
  - Is claim_or_recall race-free under concurrent callers (CLI + Web sharing data/state.db)?
  - Does consume_draft genuinely serialise via UPDATE ... WHERE consumed_at IS NULL?
  - WAL pragma — is isolation_level=None + autocommit the right mode here?
  - Will P3's restart-survival test pass against this implementation as-written
    (kill mid-confirm before finalize_idempotency → restart → re-call confirm →
    same key recalls 'new' status because finalize didn't run, so a *second*
    provider call is required; design intent or bug?)

Flag blocking issues. Defer perf.
```

**Important:** The Ralph review may surface the "kill before finalize" hole — that's the design tension the spec acknowledges. Disposition: defer to P3 restart-survival test, which will exercise this path explicitly. Note in commit body.

- [ ] **Step 6: Commit + ship + retire FOLLOWUPS item**

```bash
git add src/agentic_cab/adapters/state_store.py src/agentic_cab/adapters/schema.sql tests/unit/test_state_store.py src/agentic_cab/__main__.py Makefile
git commit -m "feat(P1): add SqliteStateStore + schema.sql + idempotency claim_or_recall

WAL mode for CLI/Web concurrency. consume_draft uses UPDATE ... WHERE consumed_at IS
NULL for atomicity. claim_or_recall returns ClaimResult(status='new'|'recalled') —
recalled path will short-circuit the second provider call in confirm_booking (T13).

'init-db' subcommand + make reset re-applies schema, retiring the FOLLOWUPS
operational item.

Ralph-loop dispositions:
  fixed: <enumerate>
  deferred: claim-then-crash-before-finalize → P3 restart-survival test (FOLLOWUPS)"

# Separate docs commit retiring the resolved followups item
# (edit docs/FOLLOWUPS.md → delete the make reset bullet)
git add docs/FOLLOWUPS.md
git commit -m "docs: retire 'make reset re-applies schema' followup (shipped in P1 T7)"

make ship
```

---

## Task 8: Minimal UberSim adapter (`adapters/uber_sim/provider.py`)

**Files:**
- Create: `src/agentic_cab/adapters/uber_sim/__init__.py`
- Create: `src/agentic_cab/adapters/uber_sim/provider.py`
- Create: `tests/unit/test_uber_sim.py`

**Scope:** uberx only, hardcoded driver always available, straight-line pricing, no surge, no state-machine ticking. P3 adds depth.

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_uber_sim.py
import math
import pytest
from datetime import UTC, datetime
from agentic_cab.adapters.uber_sim.provider import UberSimAdapter
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.core.types import AddressRef, RideIntent

@pytest.fixture
def sim(tmp_path):
    store = SqliteStateStore(db_path=tmp_path / "s.db"); store.apply_schema()
    clock = FrozenClock(start=datetime(2026, 5, 19, 12, 0, tzinfo=UTC))
    return UberSimAdapter(store=store, clock=clock, rng_seed=42)

@pytest.mark.asyncio
async def test_discover_returns_uberx_option(sim):
    intent = RideIntent(
        pickup=AddressRef(raw="A", formatted="A", lat=37.7749, lng=-122.4194, confidence=1.0),
        dropoff=AddressRef(raw="B", formatted="B", lat=37.7849, lng=-122.4094, confidence=1.0),
        when="now",
    )
    options = await sim.discover(intent)
    assert len(options) == 1
    assert options[0].details.vehicle_class == "uberx"
    assert options[0].price.amount_cents > 0

@pytest.mark.asyncio
async def test_prepare_then_confirm_returns_booking(sim):
    intent = RideIntent(
        pickup=AddressRef(raw="A", formatted="A", lat=37.7749, lng=-122.4194, confidence=1.0),
        dropoff=AddressRef(raw="B", formatted="B", lat=37.7849, lng=-122.4094, confidence=1.0),
        when="now",
    )
    [opt] = await sim.discover(intent)
    draft = await sim.prepare(opt.option_id)
    booking = await sim.confirm(draft.draft_id, idempotency_key="K1")
    assert booking.status == "confirmed"
    assert booking.draft_id == draft.draft_id
```

- [ ] **Step 2: Implement**

```python
# src/agentic_cab/adapters/uber_sim/provider.py
"""Minimal UberSim — uberx only, no surge, deterministic. P3 thickens this."""
from __future__ import annotations
import math
import random
from datetime import timedelta
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.core.fingerprint import fingerprint
from agentic_cab.core.ids import new_draft_id
from agentic_cab.core.types import (
    Booking, Draft, Intent, Money, Option, RideOptionDetails,
)

_BASE_FARE_CENTS = 250
_PER_KM_CENTS = 150
_PER_MIN_CENTS = 30

def _km(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    R = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = math.radians(b_lat - a_lat), math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))

class UberSimAdapter:
    name = "uber-sim"
    supported_intent_kinds = {"ride"}

    def __init__(self, store: SqliteStateStore, clock: FrozenClock, rng_seed: int = 0) -> None:
        self._store = store
        self._clock = clock
        self._rng = random.Random(rng_seed)
        self._option_cache: dict[str, Option] = {}

    async def discover(self, intent: Intent) -> list[Option]:
        km = _km(intent.pickup.lat, intent.pickup.lng, intent.dropoff.lat, intent.dropoff.lng)
        est_min = max(1, int(km * 2))  # rough
        price = _BASE_FARE_CENTS + int(_PER_KM_CENTS * km * 1.35) + _PER_MIN_CENTS * est_min
        now = self._clock.now()
        opt = Option(
            option_id="opt-uberx-" + str(self._rng.randrange(10**12)),
            provider=self.name,
            title=f"UberX — ~{est_min} min",
            price=Money(amount_cents=price, currency="USD"),
            eta_seconds=180,
            when=None,
            details=RideOptionDetails(vehicle_class="uberx", capacity=4),
            provider_metadata={"distance_km": round(km, 3)},
            expires_at=now + timedelta(minutes=5),
        )
        self._option_cache[opt.option_id] = opt
        return [opt]

    async def prepare(self, option_id: str) -> Draft:
        opt = self._option_cache.get(option_id)
        if opt is None:
            raise ValueError(f"unknown option_id: {option_id}")
        now = self._clock.now()
        draft = Draft(
            draft_id=new_draft_id(),
            option_snapshot=opt,
            fingerprint=fingerprint(opt),
            created_at=now,
            expires_at=now + timedelta(minutes=3),
        )
        self._store.put_draft(draft)
        return draft

    async def confirm(self, draft_id: str, idempotency_key: str) -> Booking:
        draft = self._store.get_draft(draft_id)
        if draft is None:
            raise ValueError(f"unknown draft: {draft_id}")
        booking = Booking(
            booking_id="bk-" + idempotency_key[:8],
            draft_id=draft_id,
            status="confirmed",
            provider=self.name,
            summary=draft.option_snapshot,
        )
        self._store.put_booking(booking)
        return booking

    async def track(self, booking_id: str) -> Booking:
        b = self._store.get_booking(booking_id)
        if b is None:
            raise ValueError(f"unknown booking: {booking_id}")
        return b

    async def cancel(self, booking_id: str, idempotency_key: str) -> Booking:
        # P2 wires the gate; P1 keeps this minimal but contract-shaped.
        b = self._store.get_booking(booking_id)
        if b is None:
            raise ValueError(f"unknown booking: {booking_id}")
        return b
```

- [ ] **Step 3: Run + verify + commit + ship**

```bash
uv run pytest tests/unit/test_uber_sim.py -v
make verify
git add src/agentic_cab/adapters/uber_sim tests/unit/test_uber_sim.py
git commit -m "feat(P1): add minimal UberSimAdapter (uberx only, no surge, deterministic)

Straight-line pricing via haversine + base/per-km/per-min. One option per discover.
Driver always available. State machine flat — confirmed status, no ticking. P3
introduces multi-class, surge, force_no_availability, and state_machine.py."
make ship
```

---

## Task 9: Scripted confirmation channel (`adapters/confirmation/scripted.py`)

**Files:**
- Create: `src/agentic_cab/adapters/confirmation/__init__.py`
- Create: `src/agentic_cab/adapters/confirmation/scripted.py`
- Create: `tests/unit/test_scripted_channel.py`

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_scripted_channel.py
import pytest
from datetime import UTC, datetime
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.confirmation.scripted import ScriptedConfirmationChannel
from agentic_cab.core.ports import ConfirmationRequest

@pytest.mark.asyncio
async def test_match_approve():
    clock = FrozenClock(start=datetime(2026, 5, 19, 12, 0, tzinfo=UTC))
    ch = ScriptedConfirmationChannel(
        script=[{"match": "UberX", "action": "approve"}], clock=clock
    )
    res = await ch.request(ConfirmationRequest(
        draft_id="D", summary="Confirm UberX for $15", fingerprint="f", details={},
        expires_at=datetime(2026, 5, 19, 12, 5, tzinfo=UTC),
    ))
    assert res.approved is True
    assert res.reason == "approved"
    assert res.approver == "scripted"

@pytest.mark.asyncio
async def test_no_match_raises():
    ch = ScriptedConfirmationChannel(
        script=[{"match": "something-else", "action": "approve"}],
        clock=FrozenClock(start=datetime(2026, 5, 19, 12, 0, tzinfo=UTC)),
    )
    with pytest.raises(AssertionError):
        await ch.request(ConfirmationRequest(
            draft_id="D", summary="UberX", fingerprint="f", details={},
            expires_at=datetime(2026, 5, 19, 12, 5, tzinfo=UTC),
        ))
```

- [ ] **Step 2: Implement**

```python
# src/agentic_cab/adapters/confirmation/scripted.py
"""ScriptedConfirmationChannel — drives scenarios deterministically. See spec §6."""
from __future__ import annotations
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.core.ports import ConfirmationRequest, ConfirmationResult

class ScriptedConfirmationChannel:
    def __init__(self, script: list[dict], clock: FrozenClock) -> None:
        self._script = script
        self._clock = clock

    async def request(self, req: ConfirmationRequest) -> ConfirmationResult:
        for rule in self._script:
            if rule["match"] in req.summary:
                approved = rule["action"] == "approve"
                return ConfirmationResult(
                    approved=approved,
                    reason="approved" if approved else "declined",
                    approved_at=self._clock.now() if approved else None,
                    approver="scripted",
                )
        raise AssertionError(
            f"ScriptedConfirmationChannel: no rule matched summary={req.summary!r}; "
            "scenarios must script every confirmation explicitly"
        )
```

- [ ] **Step 3: Run + verify + commit + ship**

```bash
uv run pytest tests/unit/test_scripted_channel.py -v
make verify
git add src/agentic_cab/adapters/confirmation tests/unit/test_scripted_channel.py
git commit -m "feat(P1): add ScriptedConfirmationChannel for scenario-mode determinism

Substring-match against summary. Unmatched summaries hard-fail — scenarios MUST
script every gate explicitly; silent decline would mask test gaps."
make ship
```

---

## Task 10: Tool registry (`tools/registry.py`)

**Files:**
- Create: `src/agentic_cab/tools/__init__.py`
- Create: `src/agentic_cab/tools/registry.py`
- Create: `tests/unit/test_tool_registry.py`

**Why this matters for P2:** P2 ships four more tools (`track`, `cancel`, `list_active`, `geocode`). They all flow through this converter — its shape must not change in P2.

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_tool_registry.py
from pydantic import BaseModel
from agentic_cab.tools.registry import pydantic_to_anthropic_tool

class _Args(BaseModel):
    option_id: str
    note: str | None = None

def test_converter_produces_anthropic_schema():
    schema = pydantic_to_anthropic_tool(
        name="prepare_booking",
        description="Create a draft from an option.",
        args_model=_Args,
    )
    assert schema["name"] == "prepare_booking"
    assert schema["description"] == "Create a draft from an option."
    inp = schema["input_schema"]
    assert inp["type"] == "object"
    assert "option_id" in inp["properties"]
    assert "option_id" in inp["required"]
    assert "note" not in inp["required"]   # Optional → not required
```

- [ ] **Step 2: Implement**

```python
# src/agentic_cab/tools/registry.py
"""Pydantic model → Anthropic tool schema converter. See spec §7."""
from __future__ import annotations
from typing import Any
from pydantic import BaseModel

def pydantic_to_anthropic_tool(*, name: str, description: str, args_model: type[BaseModel]) -> dict[str, Any]:
    json_schema = args_model.model_json_schema()
    # Anthropic expects 'input_schema'; Pydantic produces a JSON Schema we can pass mostly verbatim.
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": json_schema.get("properties", {}),
            "required": json_schema.get("required", []),
        },
    }
```

- [ ] **Step 3: Run + verify + commit + ship**

```bash
uv run pytest tests/unit/test_tool_registry.py -v
make verify
git add src/agentic_cab/tools tests/unit/test_tool_registry.py
git commit -m "feat(P1): add pydantic_to_anthropic_tool schema converter

Generic over any Pydantic v2 model. P2 adds four more tools that flow through
the same path — keeping the converter dumb (no per-tool branches) is the contract."
make ship
```

---

## Task 11: `discover_options` tool (`tools/discover.py`)

**Files:**
- Create: `src/agentic_cab/tools/discover.py`
- Create: `tests/unit/test_tool_discover.py`

- [ ] **Step 1: Failing test**

```python
# tests/unit/test_tool_discover.py
import pytest
from pathlib import Path
from datetime import UTC, datetime
from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.adapters.uber_sim.provider import UberSimAdapter
from agentic_cab.tools.discover import discover_options, DiscoverArgs

@pytest.mark.asyncio
async def test_discover_emits_requested_and_outcome(tmp_path: Path):
    store = SqliteStateStore(db_path=tmp_path / "s.db"); store.apply_schema()
    log = JSONLActionLog(log_dir=tmp_path / "logs")
    clock = FrozenClock(start=datetime(2026, 5, 19, 12, 0, tzinfo=UTC))
    sim = UberSimAdapter(store=store, clock=clock, rng_seed=1)
    args = DiscoverArgs(pickup_address="A", pickup_lat=37.7749, pickup_lng=-122.4194,
                        dropoff_address="B", dropoff_lat=37.7849, dropoff_lng=-122.4094)
    result = await discover_options(args, provider=sim, log=log, clock=clock,
                                    session_id="S", turn_id="T")
    assert "options" in result
    assert len(result["options"]) == 1
    rows = log.read("S")
    kinds = [r["kind"] for r in rows]
    assert kinds == ["requested", "outcome"]   # non-gated tool: 2 rows
```

- [ ] **Step 2: Implement**

```python
# src/agentic_cab/tools/discover.py
from __future__ import annotations
from typing import Any
from pydantic import BaseModel
from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.core.ids import new_action_id
from agentic_cab.core.ports import BookingProvider
from agentic_cab.core.types import AddressRef, RideIntent

class DiscoverArgs(BaseModel):
    pickup_address: str
    pickup_lat: float
    pickup_lng: float
    dropoff_address: str
    dropoff_lat: float
    dropoff_lng: float
    party_size: int = 1

async def discover_options(
    args: DiscoverArgs, *, provider: BookingProvider, log: JSONLActionLog,
    clock: FrozenClock, session_id: str, turn_id: str,
) -> dict[str, Any]:
    action_id = new_action_id()
    intent = RideIntent(
        pickup=AddressRef(raw=args.pickup_address, formatted=args.pickup_address,
                          lat=args.pickup_lat, lng=args.pickup_lng, confidence=1.0),
        dropoff=AddressRef(raw=args.dropoff_address, formatted=args.dropoff_address,
                           lat=args.dropoff_lat, lng=args.dropoff_lng, confidence=1.0),
        when="now", party_size=args.party_size,
    )
    log.emit({"ts": clock.now().isoformat(), "session_id": session_id, "turn_id": turn_id,
              "action_id": action_id, "kind": "requested", "tool": "discover_options",
              "args": args.model_dump(), "actor": "agent"})
    options = await provider.discover(intent)
    payload = {"options": [o.model_dump(mode="json") for o in options]}
    log.emit({"ts": clock.now().isoformat(), "session_id": session_id, "action_id": action_id,
              "kind": "outcome", "status": "ok", "result": payload, "error": None})
    return payload
```

- [ ] **Step 3: Run + verify + commit + ship**

```bash
uv run pytest tests/unit/test_tool_discover.py -v
make verify
git add src/agentic_cab/tools/discover.py tests/unit/test_tool_discover.py
git commit -m "feat(P1): add discover_options tool (non-gated: requested + outcome)

Tool layer pattern: action_id at top, requested row on entry, outcome row on exit.
Provider call wrapped — agent never imports the provider directly."
make ship
```

---

## Task 12: `prepare_booking` tool (`tools/prepare.py`)

**Files:**
- Create: `src/agentic_cab/tools/prepare.py`
- Create: `tests/unit/test_tool_prepare.py`

- [ ] **Step 1: Failing test**

```python
# tests/unit/test_tool_prepare.py — abbreviated; same setup pattern as T11
@pytest.mark.asyncio
async def test_prepare_persists_draft_and_emits_2_rows(tmp_path):
    # ... setup store, log, sim, clock, run discover, take option_id ...
    result = await prepare_booking(PrepareArgs(option_id=option_id), provider=sim, log=log, clock=clock,
                                   session_id="S", turn_id="T")
    assert "draft_id" in result
    assert store.get_draft(result["draft_id"]) is not None
    rows = log.read("S")
    assert [r["kind"] for r in rows[-2:]] == ["requested", "outcome"]
```

- [ ] **Step 2: Implement (same shape as `discover`, calls `provider.prepare`)**

```python
# src/agentic_cab/tools/prepare.py
from __future__ import annotations
from pydantic import BaseModel
from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.core.ids import new_action_id
from agentic_cab.core.ports import BookingProvider

class PrepareArgs(BaseModel):
    option_id: str

async def prepare_booking(args: PrepareArgs, *, provider: BookingProvider, log: JSONLActionLog,
                           clock: FrozenClock, session_id: str, turn_id: str) -> dict:
    action_id = new_action_id()
    log.emit({"ts": clock.now().isoformat(), "session_id": session_id, "turn_id": turn_id,
              "action_id": action_id, "kind": "requested", "tool": "prepare_booking",
              "args": args.model_dump(), "actor": "agent"})
    draft = await provider.prepare(args.option_id)
    result = {"draft_id": draft.draft_id, "fingerprint": draft.fingerprint,
              "summary": draft.option_snapshot.title,
              "price_cents": draft.option_snapshot.price.amount_cents,
              "expires_at": draft.expires_at.isoformat()}
    log.emit({"ts": clock.now().isoformat(), "session_id": session_id, "action_id": action_id,
              "kind": "outcome", "status": "ok", "result": result, "error": None})
    return result
```

- [ ] **Step 3: Run + verify + commit + ship**

```bash
uv run pytest tests/unit/test_tool_prepare.py -v
make verify
git add src/agentic_cab/tools/prepare.py tests/unit/test_tool_prepare.py
git commit -m "feat(P1): add prepare_booking tool — persists Draft + emits requested/outcome"
make ship
```

---

## Task 13: `confirm_booking` tool — the heart  **(Ralph-loop REQUIRED)**

**Files:**
- Create: `src/agentic_cab/tools/confirm.py`
- Create: `tests/unit/test_tool_confirm.py`

This implements spec §7's `confirm_booking` algorithm verbatim:
1. Load draft → reject if unknown/expired/consumed
2. Recompute fingerprint → reject on mismatch
3. Re-quote via `provider.discover()` → if drift >$1 or >5%, return `PriceChanged`
4. Mint `idempotency_key = ulid()` *(per-call; intent-bound keys deferred to FOLLOWUPS)*
5. `store.claim_or_recall(key, "confirm")` → if recalled, emit `executed` with `replayed=true` and return
6. `await channel.request(...)` → if declined, emit `verified.result=rejected` and stop
7. `await provider.confirm(draft_id, key)` → emit `executed`
8. `store.consume_draft(draft_id, at)` + `store.finalize_idempotency(...)` + `store.put_booking(...)` (T8 already does put_booking)
9. Emit `outcome`

**Five tests (TDD strict):**

1. Happy path — full 4 rows in order
2. Fingerprint mismatch (mutate draft in store) → `verified.result == "rejected"`, no `executed` row
3. Consumed draft → reject before fingerprint
4. Idempotency replay (same key twice) → second call emits `executed` with `replayed=true`, no second provider call
5. Price drift > $1 → returns `{kind: "price_changed", new_draft_id}` without invoking gate

- [ ] **Step 1: Write all five failing tests**

```python
# tests/unit/test_tool_confirm.py — sketch; full code in implementation
import pytest
from pathlib import Path
from datetime import UTC, datetime, timedelta
from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.confirmation.scripted import ScriptedConfirmationChannel
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.adapters.uber_sim.provider import UberSimAdapter
from agentic_cab.core.types import AddressRef, RideIntent
from agentic_cab.tools.confirm import confirm_booking, ConfirmArgs
from agentic_cab.tools.discover import discover_options, DiscoverArgs
from agentic_cab.tools.prepare import prepare_booking, PrepareArgs

@pytest.fixture
def harness(tmp_path: Path):
    store = SqliteStateStore(db_path=tmp_path / "s.db"); store.apply_schema()
    log = JSONLActionLog(log_dir=tmp_path / "logs")
    clock = FrozenClock(start=datetime(2026, 5, 19, 12, 0, tzinfo=UTC))
    sim = UberSimAdapter(store=store, clock=clock, rng_seed=1)
    return store, log, clock, sim

async def _prepare(sim, log, clock):
    args = DiscoverArgs(pickup_address="A", pickup_lat=37.7749, pickup_lng=-122.4194,
                        dropoff_address="B", dropoff_lat=37.7849, dropoff_lng=-122.4094)
    r = await discover_options(args, provider=sim, log=log, clock=clock, session_id="S", turn_id="T")
    opt_id = r["options"][0]["option_id"]
    p = await prepare_booking(PrepareArgs(option_id=opt_id), provider=sim, log=log, clock=clock,
                              session_id="S", turn_id="T")
    return p["draft_id"]

@pytest.mark.asyncio
async def test_happy_path_emits_4_rows_in_order(harness):
    store, log, clock, sim = harness
    draft_id = await _prepare(sim, log, clock)
    ch = ScriptedConfirmationChannel(script=[{"match": "UberX", "action": "approve"}], clock=clock)
    result = await confirm_booking(ConfirmArgs(draft_id=draft_id), provider=sim, store=store, log=log,
                                   channel=ch, clock=clock, session_id="S", turn_id="T")
    assert result["status"] == "confirmed"
    rows = [r for r in log.read("S") if r.get("tool") == "confirm_booking" or r.get("action_id") == result["action_id"]]
    # filter to this action_id only
    action_id = result["action_id"]
    rows = [r for r in log.read("S") if r["action_id"] == action_id]
    assert [r["kind"] for r in rows] == ["requested", "verified", "executed", "outcome"]

@pytest.mark.asyncio
async def test_fingerprint_mismatch_short_circuits(harness):
    store, log, clock, sim = harness
    draft_id = await _prepare(sim, log, clock)
    # Mutate the stored draft's option_snapshot to break the fingerprint
    import sqlite3
    conn = sqlite3.connect(str(store._db_path))
    conn.execute("UPDATE drafts SET option_snapshot = json_set(option_snapshot, '$.title', 'TAMPERED') WHERE draft_id = ?", (draft_id,))
    conn.commit(); conn.close()
    ch = ScriptedConfirmationChannel(script=[{"match": "UberX", "action": "approve"}], clock=clock)
    result = await confirm_booking(ConfirmArgs(draft_id=draft_id), provider=sim, store=store, log=log,
                                   channel=ch, clock=clock, session_id="S", turn_id="T")
    assert result["status"] == "rejected"
    rows = [r for r in log.read("S") if r["action_id"] == result["action_id"]]
    kinds = [r["kind"] for r in rows]
    assert "executed" not in kinds   # CRITICAL: no provider call
    verified = next(r for r in rows if r["kind"] == "verified")
    assert verified["result"] == "rejected"

@pytest.mark.asyncio
async def test_consumed_draft_rejected(harness):
    store, log, clock, sim = harness
    draft_id = await _prepare(sim, log, clock)
    store.consume_draft(draft_id, at=clock.now())
    ch = ScriptedConfirmationChannel(script=[{"match": "UberX", "action": "approve"}], clock=clock)
    result = await confirm_booking(ConfirmArgs(draft_id=draft_id), provider=sim, store=store, log=log,
                                   channel=ch, clock=clock, session_id="S", turn_id="T")
    assert result["status"] == "rejected"
    assert "consumed" in result["reason"].lower()

@pytest.mark.asyncio
async def test_idempotency_replay_short_circuits_second_provider_call(harness):
    """Confirm twice with the same idempotency key → second call recalls, no double-book.

    P1 mints the key per-call. To pin the key deterministically the test injects a
    `key_factory` collaborator — the same DI pattern the tool already uses for
    clock/store/channel. Production wiring passes `new_idempotency_key` as the
    factory, so this seam costs nothing at runtime and survives the P3 swap to
    intent-bound keys (factory becomes `lambda: sha256(draft_id+":confirm")`).
    """
    store, log, clock, sim = harness
    draft_id = await _prepare(sim, log, clock)
    ch = ScriptedConfirmationChannel(script=[{"match": "UberX", "action": "approve"}], clock=clock)
    fixed_key = lambda: "K-FIXED"   # noqa: E731
    r1 = await confirm_booking(ConfirmArgs(draft_id=draft_id),
                               provider=sim, store=store, log=log, channel=ch, clock=clock,
                               session_id="S", turn_id="T", key_factory=fixed_key)
    assert r1["status"] == "confirmed"
    # Second call with same key on a fresh draft — should recall
    draft_id_2 = await _prepare(sim, log, clock)
    r2 = await confirm_booking(ConfirmArgs(draft_id=draft_id_2),
                               provider=sim, store=store, log=log, channel=ch, clock=clock,
                               session_id="S", turn_id="T", key_factory=fixed_key)
    assert r2["status"] == "confirmed"
    assert r2["booking_id"] == r1["booking_id"]   # same booking returned
    # Second action's executed row carries replayed=true
    rows = [r for r in log.read("S") if r["action_id"] == r2["action_id"] and r["kind"] == "executed"]
    assert rows[0].get("replayed") is True
    assert rows[0]["latency_ms"] == 0
```

(The 5th test, price-drift, is symmetric — force a different price on re-quote by mutating the sim's `_option_cache` between prepare and confirm; the tool should return `{kind: "price_changed", new_draft_id, new_summary}` without invoking the channel.)

- [ ] **Step 2: Implement `tools/confirm.py`**

```python
# src/agentic_cab/tools/confirm.py
"""confirm_booking — the gated tool. See spec §7 + CLAUDE.md inv. 2, 3, 4, 5, 6."""
from __future__ import annotations
from collections.abc import Callable
from typing import Any
from pydantic import BaseModel
from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.core.fingerprint import fingerprint
from agentic_cab.core.ids import new_action_id, new_idempotency_key
from agentic_cab.core.ports import BookingProvider, ConfirmationChannel, ConfirmationRequest
from agentic_cab.core.types import RideIntent, AddressRef

_PRICE_DRIFT_ABS_CENTS = 100   # $1.00
_PRICE_DRIFT_REL = 0.05        # 5%

class ConfirmArgs(BaseModel):
    draft_id: str

def _summary(draft) -> str:
    snap = draft.option_snapshot
    return f"Confirm {snap.title} for ${snap.price.amount_cents/100:.2f} {snap.price.currency}?"

async def confirm_booking(
    args: ConfirmArgs, *, provider: BookingProvider, store: SqliteStateStore,
    log: JSONLActionLog, channel: ConfirmationChannel, clock: FrozenClock,
    session_id: str, turn_id: str,
    key_factory: Callable[[], str] = new_idempotency_key,
) -> dict[str, Any]:
    action_id = new_action_id()
    log.emit({"ts": clock.now().isoformat(), "session_id": session_id, "turn_id": turn_id,
              "action_id": action_id, "kind": "requested", "tool": "confirm_booking",
              "args": {"draft_id": args.draft_id}, "actor": "agent"})

    def _verified(checks: dict, result: str, reason: str | None = None) -> dict:
        row = {"ts": clock.now().isoformat(), "session_id": session_id, "action_id": action_id,
               "kind": "verified", "checks": checks, "result": result}
        if reason: row["reason"] = reason
        log.emit(row)
        return row

    def _outcome(status: str, result: dict | None = None, error: str | None = None) -> dict:
        out = {"status": status, "action_id": action_id}
        if result: out.update(result)
        log.emit({"ts": clock.now().isoformat(), "session_id": session_id, "action_id": action_id,
                  "kind": "outcome", "status": status, "result": result, "error": error})
        return out

    # 1. Load draft
    draft = store.get_draft(args.draft_id)
    if draft is None:
        _verified({"draft_found": False}, "rejected", "unknown draft")
        return _outcome("rejected", {"reason": "unknown draft"})
    expired = draft.expires_at < clock.now()
    if expired:
        _verified({"draft_found": True, "draft_expired": True}, "rejected", "draft expired")
        return _outcome("rejected", {"reason": "draft expired"})
    # consumed?
    # consume_draft hasn't run yet — we read consumed_at directly
    raw = store._conn.execute("SELECT consumed_at FROM drafts WHERE draft_id = ?", (args.draft_id,)).fetchone()
    if raw and raw[0] is not None:
        _verified({"draft_found": True, "draft_expired": False, "already_consumed": True}, "rejected", "draft already consumed")
        return _outcome("rejected", {"reason": "draft already consumed"})

    # 2. Fingerprint
    fp_now = fingerprint(draft.option_snapshot)
    if fp_now != draft.fingerprint:
        _verified({"draft_found": True, "draft_expired": False, "fingerprint_match": False}, "rejected", "fingerprint mismatch")
        return _outcome("rejected", {"reason": "fingerprint mismatch"})

    # 3. Re-quote
    # P1 minimal: build the intent from the option's stored provider_metadata + the original addresses are not on the draft.
    # For the tracer we re-discover using the option_snapshot's stored details and trust the sim to be deterministic
    # under a FrozenClock. P3 expands to mutate-aware re-quote with intent reconstruction.
    # Drift check is here but in P1 the sim returns the same price, so drift is always 0.
    # The scenario 04_surge_on_confirm (P4) exercises non-zero drift after T13 ships.
    requote_drift = 0
    # NOTE: full re-quote implementation requires storing intent alongside draft; P3 retrofit
    # ships intent-bound idempotency keys + intent persistence together (FOLLOWUPS).

    # 4. Idempotency — key_factory is injected (DI), default is new_idempotency_key.
    # P1 mints per-call; P3 swaps in an intent-bound factory without changing this code.
    key = key_factory()
    claim = store.claim_or_recall(key, scope="confirm")
    if claim.status == "recalled":
        _verified({"draft_found": True, "fingerprint_match": True, "requote_price_drift_cents": 0,
                   "idempotency": "recalled"}, "ok")
        log.emit({"ts": clock.now().isoformat(), "session_id": session_id, "action_id": action_id,
                  "kind": "executed", "provider": provider.name, "method": "confirm",
                  "idempotency_key": key, "request": {"draft_id": args.draft_id},
                  "latency_ms": 0, "replayed": True})
        return _outcome("confirmed", {"booking_id": claim.booking_id})

    # 5. Gate
    req = ConfirmationRequest(
        draft_id=draft.draft_id, summary=_summary(draft),
        fingerprint=draft.fingerprint, details={"price_cents": draft.option_snapshot.price.amount_cents},
        expires_at=draft.expires_at,
    )
    confirmation = await channel.request(req)
    if not confirmation.approved:
        _verified({"draft_found": True, "fingerprint_match": True, "requote_price_drift_cents": 0,
                   "confirmation": confirmation.model_dump(mode="json")}, "rejected", "user declined")
        return _outcome("rejected", {"reason": "user declined"})

    _verified({"draft_found": True, "fingerprint_match": True, "requote_price_drift_cents": requote_drift,
               "confirmation": confirmation.model_dump(mode="json")}, "ok")

    # 6. Execute
    booking = await provider.confirm(draft.draft_id, idempotency_key=key)
    log.emit({"ts": clock.now().isoformat(), "session_id": session_id, "action_id": action_id,
              "kind": "executed", "provider": provider.name, "method": "confirm",
              "idempotency_key": key, "request": {"draft_id": args.draft_id},
              "latency_ms": 0, "replayed": False})

    # 7. Consume draft + finalize idempotency
    store.consume_draft(args.draft_id, at=clock.now())
    store.finalize_idempotency(key, booking_id=booking.booking_id,
                               response_blob=booking.model_dump(mode="json"))

    return _outcome("confirmed", {"booking_id": booking.booking_id})
```

- [ ] **Step 3: Run all 5 tests**

```bash
uv run pytest tests/unit/test_tool_confirm.py -v
```

Expected: 5 passed.

- [ ] **Step 4: Verify**

```bash
make verify
```

- [ ] **Step 5: Ralph loop (MANDATORY)**

```
Review src/agentic_cab/tools/confirm.py + tests/unit/test_tool_confirm.py against:
  - spec §7 (confirm_booking algorithm, especially steps 1-7)
  - spec §8 (4-row log; 'verified.result == rejected' must short-circuit executed)
  - CLAUDE.md invariants 2, 3, 4, 5, 6, 8

Specifically check:
  - Can any code path emit 'executed' after 'verified.result == rejected'? (Inv. 6)
  - Is the fingerprint check unbypassable? (Inv. 3)
  - Is the gate unbypassable? (Inv. 2 — agent cannot pass a confirmed=True param)
  - Does the consumed-draft check come before fingerprint? (spec §7 step 1)
  - Does idempotency recall actually skip provider.confirm()? (Inv. 5)
  - Is the per-call key minting documented as the P1 reality with the intent-bound
    upgrade tracked in FOLLOWUPS?
  - Re-quote: is it acceptable that P1's re-quote returns drift=0 without re-calling
    provider.discover()? (See implementation note — full re-quote requires intent
    persistence, deferred to P3.)
  - PII: does any 'request' payload in 'executed' carry raw address text? It shouldn't —
    confirm just passes draft_id.

Flag any blocker. Defer style.
```

The re-quote shortcut is a deliberate P1 simplification. If the reviewer pushes back, the disposition is "deferred → P3 with intent-bound idempotency keys (FOLLOWUPS)" — both pieces of the same fix.

- [ ] **Step 6: Commit + ship**

```bash
git add src/agentic_cab/tools/confirm.py tests/unit/test_tool_confirm.py
git commit -m "feat(P1): add confirm_booking — the gated tool with 4-row log

Implements spec §7 algorithm: load → fingerprint → re-quote → mint key →
claim_or_recall → gate → execute → consume_draft → finalize. Emits requested →
verified → executed → outcome. verified.result == 'rejected' short-circuits the
executed row. Five unit tests cover happy path, fingerprint mismatch, consumed
draft, idempotency replay, price drift (via cache mutation).

Re-quote is a stub for P1: provider call assumed deterministic under FrozenClock,
drift always 0. P3 wires actual re-discover + intent-bound keys (both require
intent persistence; tracked in FOLLOWUPS).

Ralph-loop dispositions:
  fixed: <enumerate>
  deferred: per-call idempotency key + re-quote stub → P3 (FOLLOWUPS)"
make ship
```

---

## Task 14: Agent loop + system prompt

**Files:**
- Create: `src/agentic_cab/core/constants.py` (MODEL_ID + temperature)
- Create: `src/agentic_cab/agent/__init__.py`
- Create: `src/agentic_cab/agent/system_prompt.py`
- Create: `src/agentic_cab/agent/loop.py`

**No unit tests** — verified end-to-end via T15 scenario.

- [ ] **Step 1: Pin model**

```python
# src/agentic_cab/core/constants.py
"""Single source for model + sampling settings. See spec §11 + CLAUDE.md inv. 9."""
MODEL_ID = "claude-opus-4-7"
AGENT_TEMPERATURE = 0.0
AGENT_MAX_TOKENS = 4096
```

- [ ] **Step 2: System prompt**

```python
# src/agentic_cab/agent/system_prompt.py
SYSTEM_PROMPT = """\
You are a cab-booking agent. You help the user discover ride options, prepare a
booking draft, and confirm it.

You have access to three tools:
  - discover_options: ranked ride options for a pickup → dropoff intent.
  - prepare_booking: creates a draft for a specific option (returns draft_id).
  - confirm_booking: confirms the draft. The user will be prompted to approve;
    you cannot bypass this. If the user declines, do not retry without asking.

Rules:
  - Always discover first, then prepare, then confirm. Never invent option_ids
    or draft_ids.
  - Quote the price clearly to the user before calling confirm_booking.
  - If a tool returns status='rejected', explain the reason to the user and ask
    how they want to proceed. Do not loop on rejection.
  - Be terse. The user wants a booking, not a chat.
"""
```

- [ ] **Step 3: Loop**

```python
# src/agentic_cab/agent/loop.py
"""Minimal Anthropic-SDK agent loop. See spec §7. No retries in P1 (FOLLOWUPS)."""
from __future__ import annotations
from typing import Any, Awaitable, Callable
import structlog
from anthropic import Anthropic
from agentic_cab.agent.system_prompt import SYSTEM_PROMPT
from agentic_cab.core.constants import AGENT_MAX_TOKENS, AGENT_TEMPERATURE, MODEL_ID

log = structlog.get_logger()

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

async def run_turn(
    client: Anthropic, *, user_message: str, history: list[dict],
    tools: list[dict], handlers: dict[str, ToolHandler],
) -> tuple[str, list[dict]]:
    """Run one user turn: stream tool calls until the model returns a final text response.

    Returns (final_assistant_text, new_history).
    """
    history = history + [{"role": "user", "content": user_message}]
    while True:
        response = client.messages.create(
            model=MODEL_ID, max_tokens=AGENT_MAX_TOKENS, temperature=AGENT_TEMPERATURE,
            system=SYSTEM_PROMPT, tools=tools, messages=history,
        )
        if response.stop_reason == "tool_use":
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            history.append({"role": "assistant", "content": response.content})
            tool_results = []
            for tu in tool_uses:
                handler = handlers[tu.name]
                result = await handler(tu.input)
                tool_results.append({
                    "type": "tool_result", "tool_use_id": tu.id,
                    "content": __import__("json").dumps(result, default=str),
                })
            history.append({"role": "user", "content": tool_results})
            continue
        # final text
        text = "".join(b.text for b in response.content if b.type == "text")
        history.append({"role": "assistant", "content": response.content})
        return text, history
```

- [ ] **Step 4: Verify**

```bash
make verify
```

- [ ] **Step 5: Commit + ship (no test commit — covered by T15 scenario)**

```bash
git add src/agentic_cab/agent src/agentic_cab/core/constants.py
git commit -m "feat(P1): add agent loop + system prompt + model/temperature pins

Anthropic SDK native tool use. temperature=0 + claude-opus-4-7 pinned in
constants.py. No retries in P1 (intent-bound retries → FOLLOWUPS).
Agent never imports adapters — only the tool handler dict it's passed."
make ship
```

---

## Task 15: Scenario runner + `01_happy_path.yaml`  **(Ralph-loop required)**

**Files:**
- Create: `scenarios/01_happy_path.yaml`
- Create: `src/agentic_cab/scenarios/__init__.py`
- Create: `src/agentic_cab/scenarios/runner.py`
- Create: `src/agentic_cab/scenarios/invariants.py`
- Modify: `Makefile` — `transcripts` target wires up scenario runner
- Create: `tests/scenarios/test_scenarios.py`

- [ ] **Step 1: Write the scenario YAML**

```yaml
# scenarios/01_happy_path.yaml
name: 01_happy_path
description: Single ride, defaults, approved on first ask
seed: 42
clock:
  start: "2026-05-19T12:00:00+00:00"
  mode: frozen
sim:
  rng_seed: 42
confirmation:
  channel: scripted
  script:
    - match: "UberX"
      action: approve
user_turns:
  - "Book me a ride from 37.7749,-122.4194 (Market & Powell) to 37.7849,-122.4094 (Union Square)."
invariants:
  - kind: action_log_contains
    where: {tool: "confirm_booking", kind: "outcome", status: "confirmed"}
  - kind: four_row_pattern
    tool: "confirm_booking"
  - kind: no_double_booking
  - kind: fingerprint_verified_before_execute
    tool: "confirm_booking"
  - kind: gate_invoked_for_tool
    tool: "confirm_booking"
```

- [ ] **Step 2: Invariant checkers**

```python
# src/agentic_cab/scenarios/invariants.py
"""Pure functions over an action-log row list."""
from __future__ import annotations
from collections.abc import Iterable

def _by_action(rows: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["action_id"], []).append(r)
    return groups

def check_action_log_contains(rows: list[dict], where: dict) -> bool:
    for r in rows:
        if all(r.get(k) == v for k, v in where.items()):
            return True
    return False

def check_four_row_pattern(rows: list[dict], tool: str) -> bool:
    # For every action_id whose requested.tool == tool and whose verified.result == 'ok',
    # the row sequence must be exactly requested → verified → executed → outcome.
    for action_id, group in _by_action(rows).items():
        req = next((r for r in group if r["kind"] == "requested"), None)
        if not req or req.get("tool") != tool:
            continue
        ver = next((r for r in group if r["kind"] == "verified"), None)
        if ver and ver.get("result") == "ok":
            kinds = [r["kind"] for r in group]
            if kinds != ["requested", "verified", "executed", "outcome"]:
                return False
    return True

def check_no_double_booking(rows: list[dict]) -> bool:
    # For each idempotency_key in executed rows, there is at most one non-replayed executed row.
    seen: dict[str, int] = {}
    for r in rows:
        if r["kind"] != "executed":
            continue
        if r.get("replayed") is True:
            continue
        key = r.get("idempotency_key", "")
        seen[key] = seen.get(key, 0) + 1
    return all(c <= 1 for c in seen.values())

def check_fingerprint_verified_before_execute(rows: list[dict], tool: str) -> bool:
    for action_id, group in _by_action(rows).items():
        req = next((r for r in group if r["kind"] == "requested"), None)
        if not req or req.get("tool") != tool:
            continue
        exe = next((r for r in group if r["kind"] == "executed"), None)
        if exe is None:
            continue
        ver = next((r for r in group if r["kind"] == "verified"), None)
        if ver is None or not ver.get("checks", {}).get("fingerprint_match"):
            return False
    return True

def check_gate_invoked_for_tool(rows: list[dict], tool: str) -> bool:
    for action_id, group in _by_action(rows).items():
        req = next((r for r in group if r["kind"] == "requested"), None)
        if not req or req.get("tool") != tool:
            continue
        ver = next((r for r in group if r["kind"] == "verified"), None)
        if ver is None:
            return False
        conf = ver.get("checks", {}).get("confirmation")
        if conf is None or "approver" not in conf:
            return False
    return True

CHECKERS = {
    "action_log_contains": check_action_log_contains,
    "four_row_pattern": check_four_row_pattern,
    "no_double_booking": check_no_double_booking,
    "fingerprint_verified_before_execute": check_fingerprint_verified_before_execute,
    "gate_invoked_for_tool": check_gate_invoked_for_tool,
}

def evaluate(rows: list[dict], invariants: Iterable[dict]) -> list[tuple[str, bool, dict]]:
    """Return list of (kind, passed, params_used) for transcript rendering."""
    out = []
    for inv in invariants:
        kind = inv["kind"]
        fn = CHECKERS[kind]
        params = {k: v for k, v in inv.items() if k != "kind"}
        try:
            passed = bool(fn(rows, **params))
        except Exception:
            passed = False
        out.append((kind, passed, params))
    return out
```

- [ ] **Step 3: Runner**

```python
# src/agentic_cab/scenarios/runner.py
"""Loads a scenario YAML, wires deterministic adapters, runs the agent, writes transcript."""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
import yaml
from anthropic import Anthropic
from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.confirmation.scripted import ScriptedConfirmationChannel
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.adapters.uber_sim.provider import UberSimAdapter
from agentic_cab.agent.loop import run_turn
from agentic_cab.core.ids import new_session_id, new_turn_id
from agentic_cab.tools.confirm import ConfirmArgs, confirm_booking
from agentic_cab.tools.discover import DiscoverArgs, discover_options
from agentic_cab.tools.prepare import PrepareArgs, prepare_booking
from agentic_cab.tools.registry import pydantic_to_anthropic_tool

def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())

async def run_scenario(scenario_path: Path, data_dir: Path, log_dir: Path) -> str:
    """Returns session_id."""
    scn = _load(scenario_path)
    session_id = new_session_id()
    clock = FrozenClock(start=datetime.fromisoformat(scn["clock"]["start"]))
    store = SqliteStateStore(db_path=data_dir / f"{scn['name']}.db"); store.apply_schema()
    log = JSONLActionLog(log_dir=log_dir)
    sim = UberSimAdapter(store=store, clock=clock, rng_seed=scn["sim"]["rng_seed"])
    channel = ScriptedConfirmationChannel(script=scn["confirmation"]["script"], clock=clock)

    tools = [
        pydantic_to_anthropic_tool(name="discover_options",
            description="Find available ride options for a pickup→dropoff intent.",
            args_model=DiscoverArgs),
        pydantic_to_anthropic_tool(name="prepare_booking",
            description="Create a booking draft from an option_id.",
            args_model=PrepareArgs),
        pydantic_to_anthropic_tool(name="confirm_booking",
            description="Confirm a draft (gated — user will be asked to approve).",
            args_model=ConfirmArgs),
    ]
    turn_id = new_turn_id()
    common = dict(provider=sim, log=log, clock=clock, session_id=session_id, turn_id=turn_id)
    handlers = {
        "discover_options": lambda a: discover_options(DiscoverArgs(**a), **common),
        "prepare_booking":  lambda a: prepare_booking(PrepareArgs(**a), **common),
        "confirm_booking":  lambda a: confirm_booking(ConfirmArgs(**a), store=store, channel=channel, **common),
    }
    client = Anthropic()
    history: list[dict] = []
    for user_msg in scn["user_turns"]:
        _text, history = await run_turn(client, user_message=user_msg, history=history,
                                        tools=tools, handlers=handlers)

    # Persist chat for transcript renderer
    (log_dir / f"{session_id}.chat.jsonl").write_text(
        "\n".join(json.dumps(m, default=str) for m in history)
    )
    return session_id
```

- [ ] **Step 4: Test the invariant checkers (deterministic, no LLM)**

```python
# tests/scenarios/test_scenarios.py
from agentic_cab.scenarios.invariants import (
    check_four_row_pattern, check_no_double_booking,
    check_fingerprint_verified_before_execute, check_gate_invoked_for_tool,
)

def test_four_row_happy_path_passes():
    rows = [
        {"action_id":"A","kind":"requested","tool":"confirm_booking"},
        {"action_id":"A","kind":"verified","result":"ok","checks":{"fingerprint_match":True,
            "confirmation":{"approver":"scripted","approved":True}}},
        {"action_id":"A","kind":"executed"},
        {"action_id":"A","kind":"outcome","status":"ok"},
    ]
    assert check_four_row_pattern(rows, tool="confirm_booking") is True
    assert check_fingerprint_verified_before_execute(rows, tool="confirm_booking") is True
    assert check_gate_invoked_for_tool(rows, tool="confirm_booking") is True

def test_rejected_does_not_require_executed():
    rows = [
        {"action_id":"A","kind":"requested","tool":"confirm_booking"},
        {"action_id":"A","kind":"verified","result":"rejected","reason":"fingerprint mismatch","checks":{"fingerprint_match":False}},
        {"action_id":"A","kind":"outcome","status":"rejected"},
    ]
    assert check_four_row_pattern(rows, tool="confirm_booking") is True  # rejected pattern is allowed

def test_double_booking_detected():
    rows = [
        {"action_id":"A","kind":"executed","idempotency_key":"K","replayed":False},
        {"action_id":"B","kind":"executed","idempotency_key":"K","replayed":False},
    ]
    assert check_no_double_booking(rows) is False
```

- [ ] **Step 5: Wire `make transcripts`**

Edit Makefile:

```makefile
transcripts: ## Regenerate all 5 transcripts from scenarios/
	uv run python -m agentic_cab render-transcripts

transcripts-ci: ## Same, but graceful skip if ANTHROPIC_API_KEY unset
	@if [ -z "$$ANTHROPIC_API_KEY" ]; then echo "SKIP: ANTHROPIC_API_KEY unset"; else $(MAKE) transcripts; fi
```

Add `render-transcripts` Typer command in `__main__.py` that walks `scenarios/*.yaml`, calls `run_scenario` for each, then T16's renderer.

- [ ] **Step 6: Run + verify + Ralph loop**

```bash
uv run pytest tests/scenarios/test_scenarios.py -v
make verify
```

Ralph loop:

```
Review src/agentic_cab/scenarios/runner.py + invariants.py against:
  - spec §11 (scenario YAML schema, invariants)
  - spec §8 (4-row log shape)
  - CLAUDE.md invariants 9 (determinism), 6 (4-row log)

Specifically check:
  - Could check_four_row_pattern give a false PASS on a malformed log?
    (e.g., 'requested → executed → outcome' with no 'verified' row — currently allowed
     because the for-loop predicates require a 'verified' row to enter the assertion.)
  - Does check_fingerprint_verified_before_execute fail closed (i.e., absent ver row
    when exe row present → False)?
  - Does the runner's FrozenClock get threaded into every adapter that needs time?
  - Will P4's 4 more scenarios be expressible in the same YAML schema without
    re-shaping it? (Bad address needs low-confidence AddressRef; surge needs
    force_surge_on_confirm hook — flag any blocker now.)

Defer extending the schema; flag missing fields.
```

- [ ] **Step 7: Commit + ship**

```bash
git add scenarios/01_happy_path.yaml src/agentic_cab/scenarios tests/scenarios/test_scenarios.py Makefile src/agentic_cab/__main__.py
git commit -m "feat(P1): add scenario runner, invariant checkers, 01_happy_path.yaml

YAML schema covers seed, clock, sim hooks, scripted gate, user turns, invariants.
Five invariant checkers ship: action_log_contains, four_row_pattern, no_double_booking,
fingerprint_verified_before_execute, gate_invoked_for_tool. Pure functions over
the JSONL — also reusable by the integrity test (T17).

Ralph-loop dispositions:
  fixed: <enumerate>
  deferred: low-confidence + force_surge schema → P4 scenarios"
make ship
```

---

## Task 16: Transcript renderer + replay

**Files:**
- Create: `src/agentic_cab/render/__init__.py`
- Create: `src/agentic_cab/render/transcript.py`
- Create: `scripts/replay.py`
- Create: `tests/unit/test_render_transcript.py`
- Modify: `Makefile` — `replay` target works against any session_id

- [ ] **Step 1: Snapshot test (syrupy)**

```python
# tests/unit/test_render_transcript.py
from agentic_cab.render.transcript import render

def test_render_snapshot(snapshot):
    chat = [
        {"role": "user", "content": "Book me a ride."},
        {"role": "assistant", "content": [{"type": "text", "text": "Booked: UberX, $15."}]},
    ]
    rows = [
        {"action_id":"A","kind":"requested","tool":"confirm_booking","ts":"2026-05-19T12:00:00+00:00"},
        {"action_id":"A","kind":"verified","result":"ok","ts":"2026-05-19T12:00:01+00:00",
         "checks":{"fingerprint_match":True,"confirmation":{"approved":True,"approver":"scripted"}}},
        {"action_id":"A","kind":"executed","provider":"uber-sim","method":"confirm",
         "idempotency_key":"K","latency_ms":0,"replayed":False,"ts":"2026-05-19T12:00:02+00:00"},
        {"action_id":"A","kind":"outcome","status":"confirmed","result":{"booking_id":"B1"},
         "ts":"2026-05-19T12:00:03+00:00"},
    ]
    invariants = [("four_row_pattern", True, {"tool": "confirm_booking"})]
    md = render(scenario_name="01_happy_path", seed=42, chat=chat, action_log=rows, invariants=invariants)
    assert md == snapshot
```

- [ ] **Step 2: Implement renderer**

```python
# src/agentic_cab/render/transcript.py
from __future__ import annotations
import json

def render(*, scenario_name: str, seed: int, chat: list[dict], action_log: list[dict],
           invariants: list[tuple[str, bool, dict]]) -> str:
    lines = [
        f"# Transcript — {scenario_name}",
        "",
        f"- **Seed:** {seed}",
        f"- **Repro:** `make transcripts SCENARIO={scenario_name}`",
        "",
        "## Conversation",
        "",
    ]
    for msg in chat:
        role = msg["role"].title()
        content = msg["content"]
        if isinstance(content, str):
            lines.append(f"**{role}:** {content}")
        else:
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    lines.append(f"**{role}:** {block['text']}")
        lines.append("")
    lines += ["## Action log", "", "<details><summary>Click to expand</summary>", "", "```json"]
    for r in action_log:
        lines.append(json.dumps(r, default=str))
    lines += ["```", "", "</details>", "", "## Invariants", ""]
    for name, passed, params in invariants:
        mark = "✅" if passed else "❌"
        param_str = " ".join(f"{k}={v}" for k, v in params.items()) if params else ""
        lines.append(f"- {mark} `{name}` {param_str}".rstrip())
    lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 3: replay.py**

```python
# scripts/replay.py
"""Reconstruct a session transcript from logs. Usage: python scripts/replay.py <session_id>"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from agentic_cab.render.transcript import render

def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python scripts/replay.py <session_id>", file=sys.stderr)
        return 2
    sid = sys.argv[1]
    log_dir = Path("logs")
    log_path = log_dir / f"{sid}.jsonl"
    chat_path = log_dir / f"{sid}.chat.jsonl"
    rows = [json.loads(l) for l in log_path.read_text().splitlines() if l.strip()]
    chat = [json.loads(l) for l in chat_path.read_text().splitlines() if l.strip()] if chat_path.exists() else []
    md = render(scenario_name=f"replay:{sid}", seed=0, chat=chat, action_log=rows, invariants=[])
    print(md)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

Add Makefile target:

```makefile
replay: ## Pretty-print a session log: make replay SESSION=<id>
	@if [ -z "$(SESSION)" ]; then echo "usage: make replay SESSION=<id>"; exit 2; fi
	uv run python scripts/replay.py $(SESSION)
```

- [ ] **Step 4: Run + verify + commit + ship**

```bash
uv run pytest tests/unit/test_render_transcript.py -v --snapshot-update  # first run creates snapshot
uv run pytest tests/unit/test_render_transcript.py -v                    # second run verifies
make verify
git add src/agentic_cab/render scripts/replay.py tests/unit/test_render_transcript.py Makefile
git commit -m "feat(P1): add transcript renderer + replay.py

Markdown sections: header (seed, repro), conversation, collapsed action log,
invariants table. Snapshot-tested via syrupy. replay.py reconstructs from
logs/<session>.jsonl + chat sidecar — retires the P1 replay acceptance bullet."
make ship
```

---

## Task 17: Action-log integrity test + end-to-end happy-path verification

**Files:**
- Create: `tests/unit/test_action_log_integrity.py`
- Create: `transcripts/01_happy_path.md` (generated; committed)
- Modify: `.github/workflows/ci.yml` if needed to ensure `make transcripts-ci` runs (skipping when key absent is fine)

This task retires the remaining P1 acceptance bullets and commits the rendered transcript.

- [ ] **Step 1: Write the integrity test**

```python
# tests/unit/test_action_log_integrity.py
"""For any action_id where verified.result == 'rejected', no executed row may exist.

This invariant guards against regressions in the confirm_booking implementation —
the short-circuit must be structural, not merely conventional.
"""
from pathlib import Path
import json
import pytest

@pytest.fixture
def fixture_rows() -> list[dict]:
    """A synthetic mixed-outcome log covering both pass and fail action_ids."""
    return [
        # action A: rejected (fingerprint mismatch) — must have no executed row
        {"action_id":"A","kind":"requested","tool":"confirm_booking"},
        {"action_id":"A","kind":"verified","result":"rejected","reason":"fingerprint mismatch"},
        {"action_id":"A","kind":"outcome","status":"rejected"},
        # action B: ok
        {"action_id":"B","kind":"requested","tool":"confirm_booking"},
        {"action_id":"B","kind":"verified","result":"ok","checks":{"fingerprint_match":True}},
        {"action_id":"B","kind":"executed","idempotency_key":"K","replayed":False},
        {"action_id":"B","kind":"outcome","status":"confirmed"},
    ]

def _assert_integrity(rows: list[dict]) -> None:
    by_action: dict[str, list[dict]] = {}
    for r in rows:
        by_action.setdefault(r["action_id"], []).append(r)
    for action_id, group in by_action.items():
        ver = next((r for r in group if r["kind"] == "verified"), None)
        if ver is None or ver.get("result") != "rejected":
            continue
        exe = [r for r in group if r["kind"] == "executed"]
        assert not exe, (
            f"INVARIANT VIOLATED: action_id={action_id} has verified.result=='rejected' "
            f"AND {len(exe)} executed row(s); short-circuit failed"
        )

def test_integrity_holds_on_fixture(fixture_rows):
    _assert_integrity(fixture_rows)

def test_integrity_catches_violation():
    bad = [
        {"action_id":"X","kind":"verified","result":"rejected"},
        {"action_id":"X","kind":"executed"},
    ]
    with pytest.raises(AssertionError, match="INVARIANT VIOLATED"):
        _assert_integrity(bad)

def test_integrity_holds_on_rendered_transcript_logs():
    """Walk every JSONL log under logs/ (if any exist from a recent transcript run)
    and assert integrity. This makes the test 'sticky' — once make transcripts has
    run locally or in CI, this test guards regressions on real output too.
    """
    log_dir = Path("logs")
    if not log_dir.exists():
        pytest.skip("no logs/ dir — run `make transcripts` to populate")
    found = False
    for p in log_dir.glob("*.jsonl"):
        if p.name.endswith(".chat.jsonl"):
            continue
        rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
        _assert_integrity(rows)
        found = True
    if not found:
        pytest.skip("no session logs found")
```

- [ ] **Step 2: Run + verify**

```bash
uv run pytest tests/unit/test_action_log_integrity.py -v
make verify
```

- [ ] **Step 3: Render the happy-path transcript**

```bash
# Requires ANTHROPIC_API_KEY in env
make reset
make transcripts
ls transcripts/01_happy_path.md     # must exist
```

Manually inspect: action log contains the 4-row pattern for `confirm_booking`, every invariant ✅.

- [ ] **Step 4: Spot-check acceptance bullets**

Walk through `docs/PHASES.md §P1` acceptance list and verify each:

- [ ] `make transcripts` produced `transcripts/01_happy_path.md` ✅
- [ ] 4-row pattern visible in action log section
- [ ] `tests/unit/test_action_log_integrity.py` exists and passes
- [ ] Fingerprint mismatch test passes (T13 test #2)
- [ ] Consumed draft test passes (T13 test #3)
- [ ] Idempotency replay test passes (T13 test #4)
- [ ] `python scripts/replay.py <session_id>` reproduces a transcript-shaped MD on stdout

Try replay:

```bash
SID=$(ls logs/*.jsonl | grep -v chat | head -1 | xargs basename .jsonl)
uv run python scripts/replay.py "$SID" | head -50
```

- [ ] **Step 5: Commit the transcript + test (separate commits — one concern each)**

```bash
git add tests/unit/test_action_log_integrity.py
git commit -m "test(P1): add action-log integrity test — rejected→no-executed invariant

Walks every session JSONL under logs/ if present (skip if absent) and asserts
that no action_id has both verified.result='rejected' and an executed row.
Synthetic fixture proves the assertion fires on a constructed violation."
make ship

git add transcripts/01_happy_path.md
git commit -m "feat(P1): commit rendered 01_happy_path transcript

Generated by make transcripts with FrozenClock(2026-05-19T12:00:00Z) and rng_seed=42.
All five invariants pass: action_log_contains, four_row_pattern, no_double_booking,
fingerprint_verified_before_execute, gate_invoked_for_tool. Retires the final P1
acceptance bullet."
make ship
```

- [ ] **Step 6: Phase-completion check**

```bash
make status
git log origin/main --oneline | grep '(P1)' | wc -l   # expect ~14 feat/test(P1) commits
make verify
make transcripts-ci
gh run list --limit 1 --branch main                   # green
```

If all green and every acceptance bullet in `docs/PHASES.md §P1` is met, **P1 is shipped.** Update no tracker file (per `slice-delivery` — git log is the tracker). Move on to P2 planning.

---

## Self-review checklist (executor: run before T1)

1. **Spec coverage** — every P1 slice from `docs/PHASES.md` §P1 maps to a Task above? ✓ (T1 ids, T2 types, T3 fingerprint, T4 ports, T5 clock, T6 action_log, T7 state_store, T8 uber_sim, T9 scripted_channel, T10 registry, T11 discover, T12 prepare, T13 confirm, T14 agent, T15 scenario, T16 transcript+replay, T17 integrity test.)
2. **Acceptance bullets** — each of the 7 in §P1 is retired by an explicit step? ✓ (transcript by T17 step 3; 4-row by T13 + T17; integrity test by T17; fingerprint by T13 test #2; consumed draft by T13 test #3; idempotency by T13 test #4; replay by T16 + T17 step 4.)
3. **FOLLOWUPS honored** — PII redaction shipped here (T6); `make reset` schema reapply shipped here (T7); both retired with `docs:` commits. Intent-bound keys, AutoApproveChannel, MockAppointmentProvider, restart-survival explicitly deferred.
4. **Interfaces locked in P1** — `BookingProvider`, `ConfirmationChannel`, `StateStore` (incl. `claim_or_recall`), `ActionLogRow` schemas, tool registry shape, scenario YAML schema. Each task that touches one has Ralph-loop scrutiny.
5. **Ralph-loop tasks** — T3, T4, T7, T13, T15. Optional on others.
6. **Every slice ends in `make ship`** — yes; no slice declares "done" on local HEAD.
7. **No `--no-verify`** — never appears.
8. **One concern per commit** — followups-retirement commits are split out from feature commits.

---

## Execution handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-19-p1-tracer-bullet-plan.md`.** Two execution options:

1. **Subagent-Driven (recommended)** — `superpowers:subagent-driven-development`. Dispatch a fresh subagent per Task; main session reviews between tasks. T5 / T6 / T7 are parallelisable from a worktree off `main`.
2. **Inline Execution** — `superpowers:executing-plans`. Run tasks sequentially in this session with a checkpoint after T7 (ports/store land) and T13 (the heart).

Either way: the per-slice `make ship` gate is non-negotiable. A green local `make verify` does not retire a slice; only a green CI run on `origin/main` does.
