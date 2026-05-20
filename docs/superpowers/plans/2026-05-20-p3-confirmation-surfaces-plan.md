# P3 — Confirmation Surfaces, Sim Depth, and Contract Suite — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to implement this plan slice-by-slice. Each slice is a vertical tracer through ports → adapters → tools → tests → scenario evidence, per `slice-delivery`. Steps use checkbox (`- [ ]`) syntax.
>
> **Plan-style override (per `tmp/handoff-agentic-cab-p3-entry.md`, 2026-05-20):** This plan specifies file paths, function signatures, test names, expected log shapes, and exact commit messages — but NOT line-level implementation code. The plan is reviewable; execution lives in a separate session. The "complete code in every step" guideline from `writing-plans` is intentionally overridden here. Where a contract is non-obvious (new types, log row shapes), the plan pins the *shape*, not the bytes.

**Goal:** Ship phase P3 — the `BookingProvider` port is provably swappable (parameterized contract suite + second adapter), the sim is deep enough for the remaining scenarios (multi-class pricing, surge, state machine, force-hooks), the structural confirmation gate works over the web (FastAPI + SSE), the gate survives a process restart, and idempotency keys are intent-bound so a retry after a network timeout collides on the existing claim.

**Architecture:**
- Two **refactor precursors** land first (T26, T27) so the feature slices don't fight a stale type surface or non-deterministic keys.
- One **sim-depth** slice (T28) ships everything `tools/confirm.py` needs to do a real `re_quote_on_confirm`, plus the force-hooks the remaining scenarios will exercise in P4.
- The **web slice** (T29) adds a third `ConfirmationChannel` adapter (`WebConfirmationChannel`) behind FastAPI + SSE, sharing the existing `ConfirmationChannel` Protocol — proving the gate is structural, not CLI-shaped.
- **Restart survival** (T30) introduces a third idempotency status (`in_progress`) and a startup-time rehydration of pending confirmations, eliminating the in-memory-future single point of failure.
- The **contract suite** (T31) parameterises every test over every registered `BookingProvider`, and a new `MockAppointmentProvider` proves the Protocol holds beyond ride-share (writeup signal).
- **Parity** (T32) is the proof: `make web` drives the same `book → track → cancel` flow `make repl` already does, against the same scenario fixture, without any agent-loop changes.

**Tech Stack:** Python 3.12, `uv`, FastAPI, `sse-starlette` (or vanilla `EventSourceResponse`), `pyright` strict on `core/` + `tools/`, `pytest` markers `contract`/`scenario`/`slow`, vanilla HTML/JS for the browser surface (no framework).

**Phase contract:** `docs/PHASES.md` §P3.
**Spec sections P3 touches:** §5 (`BookingProvider`), §6 (`ConfirmationChannel`), §7 (tools), §8 (action log shape), §10 (`GeocoderPort` — only via `summary_title` redaction touchup), §10a (`StateStore`, idempotency replay).

---

## Slice queue (T26–T32)

Sequence is **partly fixed**: T26 and T27 are refactor precursors and MUST land before T29 (web) and T30 (restart) respectively. T28, T31 are mostly independent of T29/T30 and may parallelise via subagent dispatch once T26/T27 are on `origin/main`. T32 is the integration capstone and lands last.

```
T26 ──┐
       ├── T29 ── T30 ──┐
T27 ──┤                  ├── T32
       ├── T28 ──────────┤
       └── T31 ──────────┘
```

Each slice closes with the DOD checklist in `PHASES.md` §"Definition of Done" and a Ralph-loop review for any non-trivial change.

---

## Pre-flight (do once, no commit)

- [ ] `make status` — confirm branch `main`, clean, fully pushed, HEAD = `0366ec6` or descendant.
- [ ] `gh run list --limit 3 --branch main` — three green runs.
- [ ] Re-read `docs/PHASES.md` §P3 and the P3-relevant entries in `docs/FOLLOWUPS.md` (already linked in the handoff).
- [ ] Re-read spec §6 (channel), §5 (provider), §10a (state-store / idempotency). Note any `<!-- spec-amendment: 2026-05-20 -->` markers — the P2 amendments (`GeocoderPort`, `quote_cancel_fee`, action-log `tool` field) are upstream of this plan.
- [ ] For each slice in this plan, the implementer subagent must invoke `slice-delivery` at slice entry and `superpowers:test-driven-development` for every gated change.

---

## T26 — Resolve `ConfirmationRequest.draft_id` overload  *(refactor precursor; MUST precede T29)*

**Why first:** `WebConfirmationChannel` (T29) routes `POST /confirmations/{draft_id}` and keys its pending-future map on the same field. Today `tools/cancel.py:188` stuffs a `booking_id` into `draft_id` (documented in the in-file comment and in `FOLLOWUPS.md` as the T24-review followup). If T29 lands first, web routing will accidentally key on the wrong namespace and the bug will be invisible until a cancel-from-web path is added in P5.

**Decision (locked at plan-review, 2026-05-20):** option (b) — rename `ConfirmationRequest.draft_id` → `subject_id: str` AND add `subject_kind: Literal["draft", "booking"]`. Smallest change that gives T29's router a typed key, keeps the existing flat-`BaseModel` shape, and survives future intent kinds without a Protocol rev. The `subject_kind` is also a useful audit field on the SSE payload.

**Files:**
- Modify: `src/agentic_cab/core/ports.py` — rename `ConfirmationRequest.draft_id` → `subject_id`; add `subject_kind: Literal["draft", "booking"]`.
- Modify: `src/agentic_cab/tools/confirm.py` — emit `subject_id=draft.draft_id`, `subject_kind="draft"`.
- Modify: `src/agentic_cab/tools/cancel.py` — emit `subject_id=args.booking_id`, `subject_kind="booking"`; delete the in-file overload comment (lines ~183–187 of current file) and the bracketed apology.
- Modify: `src/agentic_cab/adapters/confirmation/cli.py` — read `req.subject_id` in summary; the field is still opaque to the channel.
- Modify: `src/agentic_cab/adapters/confirmation/scripted.py` — same.
- Modify: `src/agentic_cab/render/transcript.py` (if it touches the field — grep first; if it doesn't, skip).
- Modify: all unit tests that construct a `ConfirmationRequest` literal: `tests/unit/test_tool_confirm.py`, `tests/unit/test_tool_cancel.py`, `tests/unit/test_scripted_channel.py`, `tests/unit/test_cli_confirmation.py` (and any others surfaced by `grep -rn 'draft_id=' tests/`).
- Delete: the corresponding FOLLOWUPS entry once the slice is on `origin/main`.

**TDD steps:**
- [ ] **Step 1: Failing test (cancel path).** In `tests/unit/test_tool_cancel.py`, add `test_cancel_request_uses_subject_kind_booking()` that drives `cancel_booking` against a stub channel which captures the request, asserts `req.subject_kind == "booking"` and `req.subject_id == booking_id`. Expected fail: `AttributeError` on `subject_kind`.
- [ ] **Step 2: Failing test (confirm path).** In `tests/unit/test_tool_confirm.py`, add `test_confirm_request_uses_subject_kind_draft()`. Same shape.
- [ ] **Step 3: Run.** `uv run pytest tests/unit/test_tool_confirm.py::test_confirm_request_uses_subject_kind_draft tests/unit/test_tool_cancel.py::test_cancel_request_uses_subject_kind_booking -v`. Expect 2 failures.
- [ ] **Step 4: Implement the port change** in `ports.py`. Field order: `subject_id`, `subject_kind`, `summary`, `fingerprint`, `details`, `expires_at`.
- [ ] **Step 5: Cascade through `tools/confirm.py` and `tools/cancel.py`.** Replace every `draft_id=` literal in the `ConfirmationRequest(...)` construction with the two new fields. Delete the overload comment in `cancel.py`.
- [ ] **Step 6: Cascade through channel adapters and any callsite reading `req.draft_id`.** Run `grep -rn 'req\.draft_id\|request\.draft_id' src/ tests/` — every hit becomes `subject_id`.
- [ ] **Step 7: Run the targeted tests green, then `make test-fast`.** Expected: all green.
- [ ] **Step 8: `make verify`.** Expected: green.
- [ ] **Step 9: Re-render the touched scenario.** `python -m agentic_cab run-scenario 01_happy_path --assert-invariants`. The action log row keys do not change here (the `verified.checks.confirmation` dump is `ConfirmationResult`, not `ConfirmationRequest`) — but re-rendering proves it.
- [ ] **Step 10: Ralph review.** Spec-faithfulness pass + code-quality pass. Distil to commit body or `docs/scratch/.review-T26-*.md`.
- [ ] **Step 11: Commit.**
  ```
  refactor(P3): ConfirmationRequest.subject_id + subject_kind discriminator

  Resolves the draft_id-overloaded-as-booking-id hazard tracked in
  FOLLOWUPS (T24 review). Precursor to WebConfirmationChannel routing.
  ```
- [ ] **Step 12: `make ship`.** Push, then `gh run list --limit 1 --branch main` — green. Delete the FOLLOWUPS entry in a separate `docs:` commit.

**DOD:**
- [ ] All callsites typecheck (`make typecheck`).
- [ ] No grep hit for `req\.draft_id` outside of `ports.py` (the model definition itself is gone; the new field is `subject_id`).
- [ ] FOLLOWUPS entry deleted.

---

## T27 — Intent-bound idempotency keys  *(refactor precursor; MUST precede T30)*

**Why first:** Today `tools/confirm.py:164` and `tools/cancel.py:129` mint a fresh ULID *every call* via the injected `key_factory`. An agent retry after a network timeout therefore mints a NEW key and bypasses `claim_or_recall`. `WRITEUP.md` already names this as "what breaks first in production." T30's restart-survival story is only coherent if a re-driven retry collides on the existing claim — which requires deterministic keys.

**Design:**
- Replace the `key_factory: Callable[[], str]` parameter shape with a deterministic derivation from intent state:
  - `confirm`: `sha256(f"confirm:{draft_id}:{draft.fingerprint}")[:26]` (or full 64 — pick once and pin in `core/ids.py`).
  - `cancel`: `sha256(f"cancel:{booking_id}:{status_at_quote}:{fee_cents}")[:26]`.
- Status-at-quote and fee are in the cancel key because two cancels minted against different fee quotes are *different intents*; the user gave consent for a specific fee. The cancel fingerprint already covers these — the key derivation re-uses the fingerprint logic.
- New helpers in `src/agentic_cab/core/ids.py`:
  - `def confirm_idempotency_key(draft_id: str, fingerprint: str) -> str: ...`
  - `def cancel_idempotency_key(booking_id: str, fee_cents: int, status_at_quote: str) -> str: ...`
- Keep the `key_factory` parameter as a *test seam* but default it to a partial of the helper above. Tests that want to force collisions can still override; production code never overrides.

**Files:**
- Modify: `src/agentic_cab/core/ids.py` — add the two helpers; export from `__all__`.
- Modify: `src/agentic_cab/tools/confirm.py` — replace `key = key_factory()` with `key = key_factory(draft.draft_id, draft.fingerprint)`; default the factory parameter to `confirm_idempotency_key`. Change signature: `key_factory: Callable[[str, str], str] = confirm_idempotency_key`.
- Modify: `src/agentic_cab/tools/cancel.py` — replace `key = key_factory()` with `key = key_factory(args.booking_id, fee.amount_cents, status_at_quote)`. Default factory `cancel_idempotency_key`.
- Modify: `tests/unit/test_tool_confirm.py` — add `test_retry_after_synthetic_failure_reuses_idempotency_key()`. Drive `confirm_booking` twice with the same args + same state; assert `provider.confirm` invoked once, second call returns `recalled`, both action-log `executed` rows carry identical `idempotency_key`. (Today this would fail because two ULIDs are minted.)
- Modify: `tests/unit/test_tool_cancel.py` — symmetric `test_cancel_retry_reuses_idempotency_key()`.
- Modify: `WRITEUP.md` — flip the "first thing that breaks" paragraph from a known-gap entry to a shipped-mitigation entry. (Keep the prose honest: "deterministic keys close the timeout-retry race; the remaining gap is *cross-process retries beyond restart-survival's window*", which T30 also addresses.)
- Delete: the FOLLOWUPS entry "Intent-bound idempotency keys".

**TDD steps:**
- [ ] **Step 1: Failing tests** as named above. Run, expect failure (each call mints a fresh ULID, so the second call mints a new key and the assertion `keys[0] == keys[1]` fails).
- [ ] **Step 2: Add helpers to `core/ids.py`.** Pure functions, no clock, no I/O.
- [ ] **Step 3: Add unit tests for the helpers** in `tests/unit/test_ids.py`: `test_confirm_idempotency_key_is_deterministic()`, `test_confirm_idempotency_key_changes_with_fingerprint()`, `test_cancel_idempotency_key_is_deterministic()`, `test_cancel_idempotency_key_changes_with_fee()`. Property: same input → same output; different fingerprint/fee → different output.
- [ ] **Step 4: Wire into `confirm.py` and `cancel.py`.** Change the factory signature; production callers don't pass the factory so they get the new default.
- [ ] **Step 5: Run the failing tool tests green.** Plus `make test-fast`.
- [ ] **Step 6: `make verify`.**
- [ ] **Step 7: Re-render `01_happy_path`** with `--assert-invariants`. The `executed` row's `idempotency_key` value changes shape (sha256 prefix instead of ULID) — update the snapshot test if one exists (grep for `idempotency_key` under `tests/unit/__snapshots__/`).
- [ ] **Step 8: Ralph review.** Specifically prompt the reviewer to look at "what if the agent calls confirm, the network drops, the agent retries with the same draft, the second call mints the same key, claim_or_recall short-circuits — is that the intended behaviour, and does the second call's verified row still surface the `idempotency: 'recalled'` check?" (Yes; the existing recall branch in `confirm.py:166-191` handles it.)
- [ ] **Step 9: Commit.**
  ```
  refactor(P3): intent-bound idempotency keys for confirm and cancel

  Derive the key deterministically from (draft_id, fingerprint) and
  (booking_id, fee_cents, status_at_quote) so an agent retry after a
  timeout collides on the existing claim. Precursor to T30 (restart
  survival). Closes FOLLOWUPS entry "Intent-bound idempotency keys".
  ```
- [ ] **Step 10: `make ship`.** Verify CI green. Delete the FOLLOWUPS entry in a follow-on `docs:` commit. Update `WRITEUP.md` in the same commit or a `docs:` commit, the user's call.

**DOD:**
- [ ] All callsites use the deterministic helper.
- [ ] Tests prove same-intent retry → same key → single provider call.
- [ ] `WRITEUP.md` reflects the change.
- [ ] FOLLOWUPS entry deleted.

---

## T28 — Sim depth + real `re_quote_on_confirm`  *(feature; parallel with T29/T31 after T27 lands)*

**Why now:** P3 acceptance bullet 3 (uberx/comfort/uberxl/black priced and surged), bullet 4 (`force_surge_on_confirm`), bullet 5 (state-machine ticks under `FrozenClock`), and bullet 6 (driver-availability table + `force_no_availability`). Plus the stub `requote_drift_cents = 0` line in `tools/confirm.py:161` becomes a real `provider.discover()` call — which only works once the sim can *actually surge* between prepare and confirm. So the sim depth is a hard prerequisite for the real re-quote.

**New files:**
- `src/agentic_cab/adapters/uber_sim/pricing.py` — pure functions:
  - `def base_price(vehicle_class: VehicleClass, distance_km: float, duration_min: float) -> Money: ...`
  - `def surge_multiplier(scenario_state: SimState, now: datetime) -> Decimal: ...` (returns 1.0–4.0; deterministic given `scenario_state`).
  - Vehicle classes typed as `Literal["uberx", "comfort", "uberxl", "black"]` aliased in `core/types.py` as `VehicleClass`.
- `src/agentic_cab/adapters/uber_sim/surge.py` — surge curve parameterisation:
  - `class SurgeCurve(BaseModel)` with fields `base_multiplier: Decimal`, `peak_multiplier: Decimal`, `peak_start: datetime`, `peak_duration_min: int`, plus the `force_surge_on_confirm: bool` hook.
  - `def evaluate(curve: SurgeCurve, now: datetime, *, on_confirm: bool) -> Decimal: ...`. When `on_confirm and curve.force_surge_on_confirm`, returns `peak_multiplier` regardless of time.
- `src/agentic_cab/adapters/uber_sim/state_machine.py` — typed transitions:
  - States: `Literal["requested", "matched", "en_route", "in_progress", "completed", "cancelled", "failed"]` (aliased as `BookingStatus` in `core/types.py` — closes the BookingStatus FOLLOWUP).
  - `def advance(booking: Booking, clock: Clock, table: DriverAvailability) -> Booking: ...` — pure; called by the sim provider at `track()` time to compute the booking's *current* status given how much time has elapsed.
  - Driver availability table: `class DriverAvailability(BaseModel)` with `available: bool` and `force_no_availability: bool`.

**Modified files:**
- `src/agentic_cab/adapters/uber_sim/provider.py`:
  - Replace inline `discover` pricing with a call into `pricing.base_price` + `surge.evaluate(..., on_confirm=False)`.
  - In `confirm`, surface `force_surge_on_confirm` via the surge module (the sim has its own `SimState` already; this is where the on-confirm surge price differs from the prepare-time price).
  - `track()` invokes `state_machine.advance(...)`.
  - New constructor parameter: `surge_curve: SurgeCurve`, `availability: DriverAvailability`, both injected so scenarios can pin them via the runner.
- `src/agentic_cab/tools/confirm.py`:
  - Replace `requote_drift_cents = 0` (line ~161) with a real `provider.discover(intent)` call. The drift is `new_option.price.amount_cents - draft.option_snapshot.price.amount_cents`. If drift exceeds the threshold (constant `RE_QUOTE_DRIFT_THRESHOLD_CENTS` in `core/constants.py`), return a `PriceChanged` outcome with a *new* draft (call `provider.prepare(new_option.option_id)`) and emit a verified row with `result="rejected"` and `reason="price_changed"`. The draft replacement returns to the agent so it can re-decide.
  - Note: `Draft.intent_snapshot` must be available on the `Draft` to drive the discover call — verify by reading `core/types.py`. If `Draft` doesn't carry the `Intent` already, this is a Draft schema change (add `intent_snapshot: Intent`); call it out in the Ralph review.
- `src/agentic_cab/core/constants.py`: add `RE_QUOTE_DRIFT_THRESHOLD_CENTS = 100` (matches spec).
- `scenarios/01_happy_path.yaml`: no change (the happy-path runs at `surge=1.0` and re-quote drift is 0 — the action-log row gains `"requote_price_drift_cents": 0` from a real call instead of a stub).
- `scenarios/04_surge_on_confirm.yaml`: new file. Pins `force_surge_on_confirm: true` in the sim config; transcript shows the agent surfacing the new price and the user declining the re-quote.
- `src/agentic_cab/scenarios/runner.py`: thread the new sim config (`surge_curve`, `availability`) from YAML through to the provider constructor.

**New tests:**
- `tests/unit/test_uber_sim_pricing.py` — per-class base prices, distance scaling.
- `tests/unit/test_uber_sim_surge.py` — `force_surge_on_confirm` flips price between `discover()` calls only when `on_confirm=True`.
- `tests/unit/test_uber_sim_state_machine.py` — ticks: `requested → matched → en_route → in_progress → completed` over deterministic `FrozenClock` advances; `force_no_availability` keeps the booking in `requested` forever.
- `tests/unit/test_tool_confirm_re_quote.py` — `test_re_quote_within_threshold_proceeds`, `test_re_quote_above_threshold_returns_price_changed_with_new_draft`. The second asserts the action-log shape:
  ```
  verified.result == "rejected"
  verified.reason == "price_changed"
  verified.checks.requote_price_drift_cents == <delta>
  verified.checks.new_draft_id == <new ULID>
  outcome.status == "price_changed"
  outcome.result == {"new_draft_id": "...", "new_price_cents": ...}
  ```
  And asserts NO `executed` row exists for the original `action_id` (invariant 6 holds: rejected → no executed).
- `tests/scenarios/test_scenario_04_surge_on_confirm.py` — `--dry-run --assert-invariants`. Scenario file: `scenarios/04_surge_on_confirm.yaml` (drafted here; landed in P4 owns the *transcript*, but the scenario *file* lands now because T28 needs it to exercise the path).

**TDD steps (high level — each test is its own red-green-commit microcycle inside the slice):**
- [ ] **Step 1:** Pricing tests + `pricing.py` module.
- [ ] **Step 2:** Surge tests + `surge.py` module.
- [ ] **Step 3:** State-machine tests + `state_machine.py` module.
- [ ] **Step 4:** Provider wiring tests — `UberSimAdapter` accepts the new injected config.
- [ ] **Step 5:** Re-quote tests in `tools/confirm.py` — start by writing the "drift > threshold → PriceChanged" failing test; implement; then the "drift = 0 → unchanged" regression test against `01_happy_path`.
- [ ] **Step 6:** Scenario `04_surge_on_confirm.yaml` + its dry-run invariant test.
- [ ] **Step 7:** Address the `Draft.intent_snapshot` question — if `Draft` doesn't already carry `Intent`, ship a minimal `core/types.py` change in this slice's first commit (refactor:) BEFORE the feature commits.
- [ ] **Step 8:** `make verify`, `make test`, `make transcripts-ci` for `01_happy_path` and `04_surge_on_confirm`.
- [ ] **Step 9:** Ralph review with specific focus on: deterministic surge curve under `FrozenClock`; no module-level randomness; pricing in `Money(amount_cents: int, currency: str)` end-to-end (no `float`); the new `executed`/`verified` row shapes match spec §8 (look for the `<!-- spec-amendment: 2026-05-20 -->` markers — none are added here unless the reviewer finds a divergence; if so, amend the spec in the same PR).
- [ ] **Step 10: Commits (split — one concern per commit):**
  1. `refactor(P3): Draft carries intent_snapshot for re-quote` (if needed by step 7)
  2. `feat(P3): UberSim multi-class pricing module`
  3. `feat(P3): UberSim surge curve with force_surge_on_confirm hook`
  4. `feat(P3): UberSim state machine + driver availability`
  5. `feat(P3): real re_quote_on_confirm replaces P1 stub`
  6. `feat(P3): scenarios/04_surge_on_confirm.yaml + dry-run invariant test`
- [ ] **Step 11: `make ship` after each commit.** Watch CI between commits — these are independent enough that a green build between them is a meaningful checkpoint.

**DOD:**
- [ ] `requote_drift_cents` line is a real `discover()` call, not a stub.
- [ ] `PriceChanged` returns a new `draft_id` to the agent (verified by snapshot + by test).
- [ ] All 4 vehicle classes priced; all surge.
- [ ] `force_surge_on_confirm` and `force_no_availability` are scenario-driven, not module-level.
- [ ] No `float` for money anywhere new (`pyright strict` on `core/` + `tools/` enforces).
- [ ] Action-log rows for re-quote rejection match the shape in this plan's test bullets; integrity test `tests/unit/test_action_log_integrity.py` still green.

---

## T29 — `WebConfirmationChannel` + FastAPI + SSE  *(feature; depends on T26)*

**Why now:** P3 acceptance bullet 1. The structural gate must survive a different I/O surface — that's the point of `ConfirmationChannel` being a Protocol. A web surface also unlocks P5 (the screenshot deliverable) and lets the writeup point to a *running* artifact, not just "the abstraction permits it."

**Design:**
- `WebConfirmationChannel.request(req)` creates an `asyncio.Future`, stores it in a dict keyed on `req.subject_id` (T26 dependency), and `await`s the future. The future resolves when the browser POSTs `/confirmations/{subject_id}` with the user's decision.
- The FastAPI app exposes:
  - `GET /` → static `index.html` (chat + log pane).
  - `POST /chat` → drives a turn through `agent.chat` and streams tool outcomes via the SSE bus.
  - `GET /events?session_id=...` → SSE stream of action-log rows + pending confirmation prompts.
  - `POST /confirmations/{subject_id}` body `{"approved": bool, "approver": str}` → resolves the pending future.
  - `GET /sessions/{session_id}/log` → JSONL dump for offline replay.
- SSE bus: one `asyncio.Queue` per session, lives in `web/sse.py`. Tail-the-JSONL implementation is the v1; a pub-sub upgrade is a P5/P6 concern if needed.
- SQLite thread-safety: `SqliteStateStore` constructor gains `check_same_thread: bool = True` but the web entrypoint constructs it with `check_same_thread=False` + a single asyncio `Lock` wrapping every write. Closes the FOLLOWUPS entry "SqliteStateStore thread-safety for FastAPI web path."

**New files:**
- `src/agentic_cab/web/__init__.py`
- `src/agentic_cab/web/app.py` — FastAPI app + routes.
- `src/agentic_cab/web/sse.py` — `class SessionEventBus` with `publish(row)` / `subscribe() -> AsyncIterator[row]`.
- `src/agentic_cab/adapters/confirmation/web.py` — `class WebConfirmationChannel` implementing `ConfirmationChannel`. Pending-future map keyed on `req.subject_id`. Constructor takes the session bus so it can also publish a `"pending_confirmation"` event for the UI.
- `src/agentic_cab/web/static/index.html` — vanilla HTML.
- `src/agentic_cab/web/static/app.js` — ~150 lines vanilla JS: chat post + EventSource + modal on `pending_confirmation` + POST to `/confirmations/{subject_id}`.
- `src/agentic_cab/web/static/style.css` — minimal.
- `Makefile`: add target `web: uv run uvicorn agentic_cab.web.app:app --host 127.0.0.1 --port 8000 --reload`.

**Modified files:**
- `src/agentic_cab/adapters/state_store.py`: add `check_same_thread: bool = True` to constructor; document that the web path passes `False`.
- `src/agentic_cab/agent/chat.py`: if currently coupled to CLI rendering, factor the pure handler builder out so the web app reuses it (the T25 review already moved `chat.build_handlers` toward port protocols — confirm by reading `agent/chat.py`).

**New tests:**
- `tests/unit/test_web_confirmation_channel.py`:
  - `test_request_blocks_until_post()` — fire `request()` in a task; assert it's pending; POST to the channel's resolve method; assert the future returns `ConfirmationResult(approved=True, ...)`.
  - `test_request_publishes_pending_confirmation_event()` — subscribe to the bus; call `request()`; assert one `pending_confirmation` event arrives with the `subject_id`, `subject_kind`, `summary`.
  - `test_request_returns_declined_on_post_false()`.
  - `test_request_returns_timeout_when_expires_at_passes_without_post()` — use `FrozenClock` + `asyncio.wait_for`. Asserts the `ConfirmationResult.reason == "timeout"`.
- `tests/unit/test_web_app_routes.py` — FastAPI `TestClient`:
  - `test_get_index_serves_html()`.
  - `test_post_confirmations_resolves_pending_future()`.
  - `test_get_events_streams_log_rows()` — POST a chat turn that triggers a tool, assert the SSE stream contains the four expected log rows.
- `tests/unit/test_sqlite_state_store_thread_safety.py` — open the store with `check_same_thread=False`, drive a concurrent write from two threads via `asyncio.to_thread`, assert both succeed under the lock. Closes the FOLLOWUPS thread-safety entry.

**Scenario evidence:** `make web`, hit `http://127.0.0.1:8000`, run the happy-path conversation manually, screenshot the confirmation modal. The screenshot is for `README.md` in P5 — for P3 we capture it to `docs/scratch/p3-web-confirmation.png` (gitignored) so the user can review without it committing yet.

**TDD steps (each test is its own red-green-commit microcycle):**
- [ ] **Step 1:** SSE bus tests + `web/sse.py`. No FastAPI yet — pure asyncio.
- [ ] **Step 2:** `WebConfirmationChannel` tests + `adapters/confirmation/web.py`. Still no FastAPI — the channel is pure protocol implementation.
- [ ] **Step 3:** `SqliteStateStore` thread-safety test + `check_same_thread` parameter.
- [ ] **Step 4:** FastAPI route tests + `web/app.py`. Routes wired in dependency order: `/` → `/events` → `/chat` → `/confirmations/{subject_id}` → `/sessions/{session_id}/log`.
- [ ] **Step 5:** Static frontend — write `index.html` + `app.js` + `style.css`. No test (rendering is manual); the test for the frontend is the manual flow at the end of T32.
- [ ] **Step 6:** Makefile target.
- [ ] **Step 7:** `make verify`, `make test`. `make web` runs and serves a page.
- [ ] **Step 8:** Ralph review — focus on: pending-future lifecycle (no leak if the user closes the tab; `request()` must honour `expires_at` via `asyncio.wait_for`); SSE backpressure (queue overflow behaviour); CSP / XSS (HTML is rendered from JSON in the chat pane — escape it).
- [ ] **Step 9: Commits (split):**
  1. `feat(P3): SessionEventBus for SSE`
  2. `feat(P3): WebConfirmationChannel — gate over SSE pending-future`
  3. `fix(P3): SqliteStateStore check_same_thread injection for web path`
  4. `feat(P3): FastAPI app + routes for chat, events, confirmations, log`
  5. `feat(P3): web static frontend (vanilla HTML/JS) + make web target`
- [ ] **Step 10: `make ship` per commit.**

**DOD:**
- [ ] `make web` runs and serves a working confirmation flow.
- [ ] `WebConfirmationChannel` implements `ConfirmationChannel` (`runtime_checkable` passes; `isinstance(channel, ConfirmationChannel)` returns `True`).
- [ ] Thread-safety test passes; the FOLLOWUPS entry is deleted.
- [ ] No agent-loop changes — the new surface plugs in at the channel/store/render seams only.

---

## T30 — Restart-survival for in-flight confirmations  *(feature; depends on T27 and T29)*

**Why now:** P3 acceptance "restart-survival test" bullet. Today the `WebConfirmationChannel`'s pending-future map is in-memory only. If the process restarts between `claim_or_recall` (which writes to SQLite) and `provider.confirm` returning, the in-flight confirm dies and the user is stuck: the agent loop is gone, the future never resolves, and a retry mints a *new* idempotency key (until T27 lands) which double-books. With T27 deterministic keys, a retry collides on the existing claim — but `claim_or_recall` currently returns `status="new"` for a claimed-but-not-finalised row (see FOLLOWUPS "Restart-survival semantics for claim_or_recall"). That's the second hole.

**Design:**
- Introduce a third `ClaimResult.status` value: `"in_progress"`. Returned when the row is claimed but not finalised. The caller can either await the resolution from a sibling process (out of scope here) or, in the same-process restart case, rehydrate the pending request from durable state.
- Durable pending-request state: add a new SQLite table `pending_confirmations`:
  ```
  CREATE TABLE pending_confirmations (
    subject_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    request_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
  );
  ```
  Written when `WebConfirmationChannel.request()` creates a pending future, deleted when it resolves (approved/declined/timeout).
- Web app startup hook: scan `pending_confirmations`; for each row that hasn't expired, re-publish the `pending_confirmation` event to the bus so the (re-loaded) browser tab can show the modal. On POST to `/confirmations/{subject_id}`, even after restart, finalise the idempotency row and call `provider.confirm` once.
- The `confirm_booking` tool's recall branch (already in place) does the right thing on a third `status="in_progress"` value: treat as a `verified.checks.idempotency = "in_progress"` rejection, return `outcome.status = "in_progress"`, surface to the agent so it can re-prompt or wait. (Plan calls this out explicitly; the implementer must pick a behavior in code review.)

**New files:**
- Migration in `src/agentic_cab/adapters/schema.sql` — add the `pending_confirmations` table.
- `tests/contract/test_restart_survival.py` — kills and restarts the web app mid-confirm; asserts no double-book.

**Modified files:**
- `src/agentic_cab/core/ports.py` — extend `ClaimResult.status` Literal: `Literal["new", "recalled", "in_progress"]`.
- `src/agentic_cab/adapters/state_store.py` — `claim_or_recall` distinguishes claimed-but-not-finalised. Two new methods: `put_pending_confirmation(subject_id, session_id, request_json, expires_at)` and `pop_pending_confirmation(subject_id) -> ConfirmationRequest | None` and `list_pending_confirmations(now) -> list[ConfirmationRequest]`.
- `src/agentic_cab/adapters/confirmation/web.py` — write durable row in `request()`; delete on resolution; on `__init__`, take a callback that flushes existing pendings via the bus.
- `src/agentic_cab/web/app.py` — startup hook calls `store.list_pending_confirmations(clock.now())` and re-publishes each via the bus.
- `src/agentic_cab/tools/confirm.py` — handle `claim.status == "in_progress"` branch explicitly. Don't double-call the provider. Emit `verified` with `checks.idempotency = "in_progress"`, `outcome.status = "in_progress"`.
- Delete: FOLLOWUPS entry "Restart-survival semantics for claim_or_recall".

**New tests:**
- `tests/unit/test_state_store_pending_confirmations.py` — put/list/pop, expiry filtering.
- `tests/unit/test_state_store_claim_or_recall_in_progress.py` — claim once without finalising, claim again, expect `status="in_progress"`.
- `tests/unit/test_tool_confirm_in_progress.py` — `claim_or_recall` returns `"in_progress"`; assert `outcome.status == "in_progress"` and NO `executed` row.
- `tests/contract/test_restart_survival.py` — uses two `UberSimAdapter` instances backed by the same SQLite file; "kills" the first mid-confirm by destroying its in-memory bus + future map; constructs the second; replays the same confirm with the deterministic key from T27; asserts only ONE `provider.confirm` call (the first instance's) — the second sees `recalled` and returns the same booking. Marked `@pytest.mark.contract`.

**TDD steps:**
- [ ] **Step 1:** State-store pending-confirmation tests + schema migration + methods.
- [ ] **Step 2:** `claim_or_recall` extends to `"in_progress"`. Tests first.
- [ ] **Step 3:** `tools/confirm.py` handles the new branch. Test first.
- [ ] **Step 4:** `WebConfirmationChannel` writes/clears durable rows. Test first.
- [ ] **Step 5:** FastAPI startup hook flushes pendings. Integration test in `test_web_app_routes.py` extends to `test_startup_flushes_pending_confirmations`.
- [ ] **Step 6:** Contract test `test_restart_survival.py`. Run with `pytest -m contract`.
- [ ] **Step 7:** `make verify`, `make test` (includes contract by default — confirm in `pyproject.toml` markers).
- [ ] **Step 8:** Ralph review — focus on: race between `put_pending_confirmation` and the SQLite `claim_or_recall` (must be in the same transaction or have a clear ordering); the `outcome.status = "in_progress"` semantic is novel — confirm the agent loop handles it gracefully (the agent should either ask the user or wait, not crash).
- [ ] **Step 9: Commits:**
  1. `feat(P3): ClaimResult.in_progress status for unfinalised claims`
  2. `feat(P3): pending_confirmations table + StateStore methods`
  3. `feat(P3): WebConfirmationChannel persists pending requests across restart`
  4. `feat(P3): confirm_booking emits in_progress outcome under unfinalised claim`
  5. `test(P3): restart survival contract test`
- [ ] **Step 10:** `make ship` per commit.

**DOD:**
- [ ] Restart-survival contract test passes.
- [ ] `outcome.status = "in_progress"` is documented in spec §8 (add a `<!-- spec-amendment: 2026-05-20 -->` marker if §8 doesn't already permit it).
- [ ] FOLLOWUPS entry deleted.

---

## T31 — `MockAppointmentProvider` + parameterized contract suite  *(feature; parallel with T29/T30 after T27 lands)*

**Why now:** P3 acceptance bullet "parameterized contract suite covers …" + the FOLLOWUPS "Mock AppointmentProvider in the contract suite" entry. The Zocdoc-reach claim in the writeup is currently paper-only — the architecture supports a non-ride-share intent but no test exercises an `AppointmentIntent` end-to-end. This slice is the strongest writeup signal in P3.

**Design:**
- New intent kind: `AppointmentIntent` in `core/types.py` (a sibling of `RideIntent`). Fields: `provider_kind: Literal["appointment"]`, `subject` (e.g. "GP check-up"), `earliest_at: datetime`, `latest_at: datetime`, `location: AddressRef`.
- New adapter: `src/agentic_cab/adapters/mock_appointment/provider.py`. In-memory; supports `supported_intent_kinds = {"appointment"}`. `discover` returns 1–3 `Option`s with `Option.title = "<clinic> @ <time>"`. `prepare`, `confirm`, `track`, `cancel`, `quote_cancel_fee` all implemented.
- Parameterized contract suite: `tests/contract/test_booking_provider_contract.py`. `@pytest.fixture(params=[...])` over every registered provider. Tests:
  - `test_idempotency_replay_same_key_same_booking`
  - `test_idempotency_different_key_different_booking`
  - `test_option_expiry_honored`
  - `test_draft_expiry_honored`
  - `test_cancel_after_cancel_is_no_op`
  - `test_fingerprint_stable_under_no_op_re_quote`
  - `test_force_surge_on_confirm_produces_price_changed` (skipped for providers that don't expose the hook — `pytest.skip` is fine)
- `summary_title` PII redaction at log boundary — close the FOLLOWUPS entry. **Decision (locked at plan-review, 2026-05-20):** route `summary_title` through `core.privacy.redact()` at log emission. Single chokepoint; doesn't trust adapters to sanitize.

**New files:**
- `src/agentic_cab/adapters/mock_appointment/__init__.py`
- `src/agentic_cab/adapters/mock_appointment/provider.py`
- `tests/contract/test_booking_provider_contract.py`
- `tests/contract/conftest.py` — registers the fixture matrix.

**Modified files:**
- `src/agentic_cab/core/types.py` — add `AppointmentIntent`; widen `Intent` to discriminated union.
- `src/agentic_cab/adapters/action_log.py` — apply `redact()` to `summary_title` fields. Test additions.
- Delete: FOLLOWUPS entries "Mock AppointmentProvider in the contract suite" and "summary_title log redaction".

**New tests beyond the contract suite:**
- `tests/unit/test_mock_appointment_provider.py` — adapter-specific tests; the contract suite covers Protocol conformance.
- `tests/unit/test_action_log_summary_title_redaction.py` — feed a `summary_title` with a fake driver name; assert redacted in the emitted row.

**TDD steps:**
- [ ] **Step 1:** Spec the contract suite — write the parametrize fixture and ONE failing contract test (`test_idempotency_replay_same_key_same_booking`) against `UberSimAdapter` only. Run, expect green (this is a regression test for behaviour P1 already shipped).
- [ ] **Step 2:** Add `MockAppointmentProvider` to the fixture matrix. Re-run; expect failure (no adapter).
- [ ] **Step 3:** Stub `MockAppointmentProvider`. Make `test_idempotency_replay` green.
- [ ] **Step 4:** Add each contract test one at a time, fixing both adapters as needed.
- [ ] **Step 5:** Booking.events round-trip: when writing `test_idempotency_replay`, the FOLLOWUPS Booking.events round-trip inconsistency surfaces (`claim_or_recall` returns a `Booking` with events, but `get_booking` returns one without). Decide canonical semantics here — recommended: `claim_or_recall` returns only `booking_id`, the caller re-fetches via `get_booking`. Close the FOLLOWUPS entry.
- [ ] **Step 6:** `summary_title` redaction at log boundary + test.
- [ ] **Step 7:** `make verify`, `make test` (contract suite included by default).
- [ ] **Step 8:** Ralph review with the specific prompt: "Is the contract suite *protocol*-only, or does any test reach into `UberSimAdapter`-specific shape? If so, that's a leak." (Should be Protocol-only.)
- [ ] **Step 9: Commits:**
  1. `feat(P3): AppointmentIntent + Intent discriminated union`
  2. `feat(P3): MockAppointmentProvider — second adapter for swap proof`
  3. `test(P3): parameterized BookingProvider contract suite`
  4. `refactor(P3): claim_or_recall returns booking_id, callers re-fetch`
  5. `fix(P3): summary_title redacted at action-log boundary`
- [ ] **Step 10:** `make ship` per commit.

**DOD:**
- [ ] Contract suite runs over ≥2 providers, all tests green.
- [ ] `WRITEUP.md` updated: the marketplace-swap paragraph now points at the contract test, not just the prose.
- [ ] FOLLOWUPS entries deleted (4 entries: Mock AppointmentProvider, summary_title redaction, Booking.events round-trip, BookingStatus alias — if that one is closed under T28).

---

## T32 — REPL + web parity demo  *(integration capstone; depends on T28, T29, T30, T31)*

**Why last:** P3 isn't truly done until both surfaces drive the same flow against the same fixture without divergence. This slice does NOT add new ports/adapters/tools — it's an integration test + a tiny rendering polish.

**Files:**
- `tests/scenarios/test_repl_web_parity.py` — runs the happy-path scenario twice: once with the headless REPL render path, once with a FastAPI `TestClient` driving the web endpoints. Asserts both produce identical action-log JSONL (modulo timestamps, which both use `FrozenClock`) and identical final booking state.
- `Makefile` target `make demo`: spins up `make web`, opens a browser via `python -m webbrowser http://127.0.0.1:8000`, prints a banner. Marked `# manual smoke` in `make help`.
- `docs/WRITEUP.md` polish — fold in the now-shipped items (intent-bound keys, second adapter, restart-survival, force_surge, web surface).

**TDD steps:**
- [ ] **Step 1:** Write the parity test, expect failure (likely a tiny divergence: one side emits an extra log row or formats a timestamp differently).
- [ ] **Step 2:** Fix the divergence at the source (this is where any final renderer asymmetry surfaces).
- [ ] **Step 3:** `make verify`, `make test`, `make transcripts-ci`.
- [ ] **Step 4:** Manual smoke: `make web`, run book → track → cancel in the browser, screenshot to `docs/scratch/p3-web-demo.png`.
- [ ] **Step 5:** Update `WRITEUP.md`.
- [ ] **Step 6:** Ralph review — "Has the WRITEUP claim survived its tests?"
- [ ] **Step 7: Commits:**
  1. `test(P3): REPL + web parity for happy path`
  2. `feat(P3): make demo target` (optional; can be folded with prior or skipped)
  3. `docs: refresh WRITEUP for P3 shipped items`
- [ ] **Step 8:** `make ship`.

**DOD:**
- [ ] Parity test green.
- [ ] `make demo` works on a clean clone.
- [ ] `WRITEUP.md` truthfully reflects shipped state.

---

## Out of scope for P3 (still deferred to later phases)

- New scenarios `02_*`, `03_*`, `05_*` — P4 owns scenario expansion. `04_surge_on_confirm.yaml` lands in T28 because T28 needs it as a test fixture, but the *transcript* of `04_*` is P4's.
- Streaming Rich CLI output → P5.
- `AutoApproveChannel` — no current caller; FOLLOWUPS keeps it.
- `NominatimGeocoder` throttle Lock concurrency test (FOLLOWUPS minor) — optional sprinkle if T28/T31 have headroom; otherwise defer.
- Naive-datetime defensive UTC for `get_draft` and other writers (FOLLOWUPS minor) — same; one-line each, but no P3 acceptance bullet depends on it.

---

## Self-review

**1. Spec coverage:**
- P3 acceptance bullet 1 (parameterized contract suite over Option/Draft expiry, cancel-after-cancel, fingerprint stability, force_surge): T31 + T28.
- P3 acceptance bullet "restart-survival test": T30.
- P3 acceptance bullet (4 classes priced + surged): T28.
- P3 acceptance bullet (surge curve parameterised + force_surge_on_confirm): T28.
- P3 acceptance bullet (state machine on FrozenClock ticks): T28.
- P3 acceptance bullet (driver availability table + force_no_availability): T28.
- Handoff bullets 1 (WebConfirmationChannel + FastAPI + SSE): T29.
- Handoff bullet 2 (real re_quote_on_confirm): T28.
- Handoff bullet 3 (intent-bound idempotency keys): T27.
- Handoff bullet 4 (restart-survival): T30.
- Handoff bullet 5 (MockAppointmentProvider): T31.
- FOLLOWUPS T24 (ConfirmationRequest.draft_id overload): T26.
- FOLLOWUPS summary_title redaction: T31.
- FOLLOWUPS Booking.events round-trip: T31.
- FOLLOWUPS BookingStatus alias: T28 (state machine introduces it).
- FOLLOWUPS SqliteStateStore thread-safety: T29.
- FOLLOWUPS Restart-survival semantics for claim_or_recall: T30.
- FOLLOWUPS Intent-bound idempotency keys: T27.
- FOLLOWUPS Mock AppointmentProvider: T31.
- Minor FOLLOWUPS (NominatimGeocoder concurrency, naive-datetime defensive UTC, ConfirmationResult coherence validator, typed ConfirmationRequest.details, scenario YAML top-level seed, scenario clock.mode validation, `_by_action` defensive `.get`): deferred or absorbed into "Out of scope" — call out at plan-review time if any should be promoted.

**2. Placeholder scan:** No TBD / "implement later" / "add validation" / "similar to Task N". Plan deliberately omits line-level code per the handoff's override; every step pins a file path, a function signature, a test name, a log shape, or a commit message.

**3. Type consistency:**
- `subject_id` and `subject_kind` are used consistently from T26 onward in T29, T30.
- `VehicleClass` / `BookingStatus` are introduced in T28's `state_machine.py` description and re-used in T31 (`Intent`, `Booking`).
- `ClaimResult.status` literal `"in_progress"` is introduced in T30 and consumed in T30 only — no callsite in T26–T29 reads it.
- `confirm_idempotency_key(draft_id, fingerprint)` / `cancel_idempotency_key(booking_id, fee_cents, status_at_quote)` signatures are consistent between T27's helper definition and T30's restart-survival contract test.

---

## Execution handoff

This plan is for **subagent-driven execution** in a fresh session, per the handoff:

> Hand to user for review BEFORE executing.

When the user signs off, the next session should:
1. Invoke `superpowers:subagent-driven-development`.
2. Dispatch a fresh implementer subagent per slice (T26 first, since it gates T29).
3. Run the two-stage review (spec-faithfulness + code-quality) between each slice.
4. `make ship` between commits within a slice — CI green on `origin/main` is the only "done" signal.

**Execution order (locked at plan-review, 2026-05-20):** strictly sequential — T26 → T27 → T28 → T29 → T30 → T31 → T32. No parallel dispatch. Easier to review, easier to bisect if CI breaks. P2 parallelism is intentionally not repeated here.
