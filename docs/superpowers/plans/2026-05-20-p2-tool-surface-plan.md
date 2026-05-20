# P2 — Tool Surface Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to implement this plan slice-by-slice. Steps use checkbox (`- [ ]`) syntax for tracking. Each slice closes with a `feat(P2):` / `refactor(P2):` commit + `make verify` + `make ship`. The repo's `slice-delivery` skill owns the Ralph-loop, SOLID-scan, and start-lane rules; consult it before claiming any slice done.

**Goal:** Ship every tool from spec §7, the `CLIConfirmationChannel`, the Nominatim geocoder with committed fixtures, and a Rich-rendered REPL that exercises book → track → cancel end-to-end.

**Architecture.** Four refactor-precursor slices land first to clear known FOLLOWUPS that new tools would otherwise inherit (`StateStore` reach-in, missing `GeocoderPort`, missing `db_path` accessor, missing `quote_cancel_fee` on `BookingProvider`). Then six feature slices: Nominatim + cache, four tools (`geocode`, `track`, `list_active`, `cancel`), the `/dev/tty`-bound CLI gate, and the Rich REPL. The three read-only tools and the geocoder are independent and parallelizable in worktrees once the precursors land; `cancel` and the CLI gate land sequentially because they share structural invariants with `confirm`.

**Tech Stack.** Python 3.12, Pydantic v2, Anthropic SDK (no `temperature` — `claude-opus-4-7` is extended-thinking), `httpx` (already pinned), stdlib `hashlib` + `sqlite3`, Rich, pytest with `contract`/`scenario`/`slow` markers, ruff, pyright strict on `core/`+`tools/`.

**Out of scope.** Real re-quote-on-confirm against `provider.discover()` (stays stubbed; P3 ships the surge fixture + `force_surge_on_confirm` hook the real re-quote needs to exercise). `AutoApproveChannel` (no P2 caller needs it). Streamed Rich output (defer to P5 where SSE is the canonical streaming surface). Restart-survival, intent-bound idempotency keys, multi-class pricing, `MockAppointmentProvider`, web channel — all P3+ per `docs/PHASES.md`.

**Ralph dispositions per slice** (per the post-P1 retro lever — declare up front, not after the fact):

| Slice | Ralph |
|---|---|
| T16 `StateStore.get_draft_consumed_at` + `db_path` property | none (trivial refactor) |
| T17 `GeocoderPort` Protocol | none (one-method Protocol) |
| T18 `BookingProvider.quote_cancel_fee` Protocol extension | none (one-method Protocol + sim impl) |
| T19 `NominatimGeocoder` + cache + fixtures | standalone (network adapter + cache invariants) |
| T20 `tools/geocode.py` | family-batched with T21/T22 |
| T21 `tools/track.py` | family-batched with T20/T22 |
| T22 `tools/list_active.py` | family-batched with T20/T21 |
| T23 `adapters/confirmation/cli.py` (`/dev/tty`) | standalone (touches the gate; structural-proof test) |
| T24 `tools/cancel.py` | standalone (gate + idempotency + fee re-quote — parallels T13) |
| T25 `agent/chat.py` + `make repl` wiring | standalone (E2E smoke protocol) |

**Parallelization plan.** After T16–T19 land sequentially on `main`, dispatch T20/T21/T22 in three worktrees concurrently (cap: 3 active). T23 and T24 follow sequentially on `main` because both touch the confirmation/idempotency seam. T25 follows T24. Pre-flight every worktree: `git fetch origin && git worktree add ../agentic-cab-rides-<slug> origin/main`.

**Spec amendments this plan introduces.** Two: (a) `BookingProvider` gains `async def quote_cancel_fee(self, booking_id: str) -> Money` — spec §5 contract extension, must be added in the same commit as T18; (b) `GeocoderPort` is added to `core/ports.py` with one method — already implied by spec §3 layering but never made explicit. Both are recorded in the spec file as part of T17/T18.

---

## Definition of Done — applies to every slice

Inherits from `docs/PHASES.md` "Definition of Done (every slice)" verbatim. Adds for P2:

- [ ] If the slice adds a tool, it is registered in the agent's tool list in `src/agentic_cab/scenarios/runner.py` AND `src/agentic_cab/agent/chat.py` (the REPL) — the registry is per-entrypoint, not global, by design.
- [ ] If the slice adds a gated tool, the **four-row pattern** is exercised by at least one unit test that walks the real JSONL the tool emitted (parallel to `tests/unit/test_action_log_integrity.py`).
- [ ] If the slice touches `core/ports.py`, every adapter implementing the protocol type-checks under pyright strict.
- [ ] No `store._conn` reach-ins. If a new tool needs DB introspection beyond the StateStore Protocol, extend the Protocol in the same commit as the tool.

---

## T16 — `StateStore.get_draft_consumed_at` + `db_path` property *(refactor precursor)*

**Why first.** `tools/confirm.py` reaches into `store._conn` with `# type: ignore[attr-defined]` to read `consumed_at`. `tools/cancel.py` will be tempted to do the same to introspect bookings/drafts. Land the accessor before any new tool writes the same pattern. Also exposes `db_path` so future contract tests can open a second connection without reaching into `_db_path`.

**Files:**
- Modify: `src/agentic_cab/core/ports.py` — add `get_draft_consumed_at(self, draft_id: str) -> datetime | None` to the `StateStore` Protocol.
- Modify: `src/agentic_cab/adapters/state_store.py` — implement `get_draft_consumed_at`; add `db_path` as a `@property` returning `self._db_path` (read-only).
- Modify: `src/agentic_cab/tools/confirm.py` — replace the `store._conn.execute(...)` block in `_validate_draft` with `store.get_draft_consumed_at(draft_id)`; delete the `# type: ignore` and the inline NOTE.
- Modify: `tests/unit/test_state_store.py` — add `test_get_draft_consumed_at_returns_none_until_consumed` and `test_get_draft_consumed_at_returns_timestamp_after_consume`.
- Modify: `tests/unit/test_tool_confirm.py` — ensure existing tests still pass; no behaviour change.

**Tasks:**
- [ ] Step 1: Write the two unit tests against `SqliteStateStore` and run them — expect FAIL with `AttributeError`.
- [ ] Step 2: Add the Protocol method to `core/ports.py` and the implementation to `adapters/state_store.py`. Add the `db_path` property.
- [ ] Step 3: Rerun the two unit tests — expect PASS. Run `make test-fast` — expect PASS (no regressions; `tools/confirm.py` still uses the old path).
- [ ] Step 4: Switch `tools/confirm.py` to call `store.get_draft_consumed_at`. Run `make test-fast` — expect PASS, including `tests/unit/test_action_log_integrity.py`.
- [ ] Step 5: Update the FOLLOWUPS.md entries (T13 Ralph F2 and H2) — strike them through with a one-line "shipped in T16" pointer, or delete entirely (per CLAUDE.md "delete when done — git log is the archive"). Prefer delete.
- [ ] Step 6: `make verify` + `make transcripts-ci` (must stay green — this is a pure refactor). Commit: `refactor(P2): expose draft.consumed_at accessor on StateStore Protocol`.
- [ ] Step 7: `make ship`. Verify `gh run list --limit 1 --branch main` is green.

**Ralph:** none. Trivial structural refactor with two new tests.

---

## T17 — `GeocoderPort` Protocol *(refactor precursor)*

**Why now.** Adding the Protocol after `NominatimGeocoder` exists invites `tools/geocode.py` to import the concrete adapter (invariant 1 violation). Establishing the Protocol first means the geocoder slice has a target type to satisfy.

**Files:**
- Modify: `src/agentic_cab/core/ports.py` — add `GeocoderPort` Protocol with one method: `async def geocode(self, query: str) -> AddressRef`. `@runtime_checkable`. Self-type annotated (`self: Self`) per the pinned ruff `ANN101` rule.
- Modify: `docs/superpowers/specs/2026-05-19-agentic-cab-rides-design.md` §3/§10 — append a one-sentence note that `GeocoderPort` is part of the port layer. Use a `<!-- spec-amendment: 2026-05-20 -->` HTML comment so the edit is grep-able.
- Modify: `docs/FOLLOWUPS.md` — strike/delete the "GeocoderPort Protocol" entry (T4 Ralph N1).

**Tasks:**
- [ ] Step 1: Add the Protocol. Run `uv run pyright src/agentic_cab/core/ports.py` — expect PASS.
- [ ] Step 2: Patch the spec § with the amendment marker.
- [ ] Step 3: Remove the FOLLOWUPS entry.
- [ ] Step 4: `make verify` — expect PASS (Protocol with no implementer is fine; nothing references it yet).
- [ ] Step 5: Commit: `refactor(P2): introduce GeocoderPort Protocol in core/ports`.
- [ ] Step 6: `make ship`.

**Ralph:** none.

---

## T18 — `BookingProvider.quote_cancel_fee` Protocol extension *(refactor precursor)*

**Why.** `tools/cancel.py` must surface a fee through the gate BEFORE invoking `provider.cancel()`. Today there is no port method to quote the fee — only `cancel()` itself, which mutates. Adding a quote method is the cleanest fix and mirrors the discover/prepare/confirm split for confirmation.

**Decision recorded.** This is a spec amendment to §5. The Lyft-swap claim still holds: any cancel-fee-supporting provider can implement it; providers that never charge a fee return `Money(amount_cents=0, currency="USD")`.

**Files:**
- Modify: `src/agentic_cab/core/ports.py` — add to `BookingProvider`: `async def quote_cancel_fee(self, booking_id: str) -> Money`.
- Modify: `src/agentic_cab/adapters/uber_sim/provider.py` — implement `quote_cancel_fee`. Sim policy (P2 minimal; P3 thickens):
  - `confirmed` → `Money(0, "USD")`
  - `driver_assigned` | `en_route` | `arrived` → `Money(500, "USD")` ($5 flat)
  - `in_progress` | `completed` → raise `ValueError("cannot cancel; ride already in progress")`
  - `cancelled` | `failed` → `Money(0, "USD")` (cancel-after-cancel is a no-op; T24 will route accordingly)
- Modify: `docs/superpowers/specs/2026-05-19-agentic-cab-rides-design.md` §5 — add `quote_cancel_fee` to the Protocol snippet with the `<!-- spec-amendment: 2026-05-20 -->` marker.
- Modify: `tests/unit/test_uber_sim.py` — add `test_quote_cancel_fee_by_status` covering the four branches above.

**Tasks:**
- [ ] Step 1: Write `test_quote_cancel_fee_by_status` driving each branch (parameterized over status). Expect FAIL — method not defined.
- [ ] Step 2: Add the Protocol method + UberSim implementation.
- [ ] Step 3: Rerun the test — expect PASS.
- [ ] Step 4: Patch the spec.
- [ ] Step 5: `make verify` — expect PASS (pyright strict: `tools/` doesn't reference `quote_cancel_fee` yet, so no fan-out failures).
- [ ] Step 6: Commit: `feat(P2): add BookingProvider.quote_cancel_fee port + uber-sim impl`. NOTE: this is `feat`, not `refactor`, because the sim grows a real method.
- [ ] Step 7: `make ship`.

**Ralph:** none. The sim policy is a placeholder by spec — P3 will widen it.

---

## T19 — `NominatimGeocoder` + `GeocodeCache` + committed fixtures

**Files:**
- Create: `src/agentic_cab/adapters/geocoding/__init__.py`
- Create: `src/agentic_cab/adapters/geocoding/cache.py` — `class GeocodeCache` with `get(query: str) -> AddressRef | None` and `put(query: str, ref: AddressRef) -> None`. Storage layout: `fixtures/geocode/<sha1(query.strip().lower()).hexdigest()>.json` containing `AddressRef.model_dump_json()`. Cache key derivation must be deterministic — `hashlib.sha1` (cache, not crypto), lowercased + stripped query, ASCII-fold not required for the P2 fixture set but documented as a P4 follow-up if any non-ASCII queries get seeded.
- Create: `src/agentic_cab/adapters/geocoding/nominatim.py` — `class NominatimGeocoder` implementing `GeocoderPort`. Constructor takes a `cache: GeocodeCache` and an `http: httpx.AsyncClient` (DI for tests). `geocode(query)` is cache-first; on miss, calls Nominatim with a User-Agent header (Nominatim's policy requires one — use `"agentic-cab-rides/0.1 (https://github.com/ujjaval-verma/agentic-cab-rides)"`), throttles to 1 req/sec via an internal `asyncio.Lock` + `asyncio.sleep(1.0)` after each network call (NOT before — first call is immediate), parses the top result, maps it to `AddressRef`, writes the cache, returns.
- Create: `fixtures/geocode/.gitkeep` + committed fixtures for the addresses used in scenario `01_happy_path.yaml` (Market & Powell, Union Square). Use the SHA-1 layout above. Seed two extra ambiguous-query fixtures so T24's bad-address path can later be exercised in P4 without hitting the network:
  - `"123 Main St"` — synthetic low-confidence fixture (`confidence: 0.3`, two candidate results merged into one with a `provider_metadata: {"alternates": [...]}`); see "Low-confidence structural signal" below.
  - One high-confidence counterpart, e.g. `"123 Main St, Anytown, USA"` (`confidence: 0.95`).
- Create: `tests/unit/test_geocode_cache.py` — covers: cache hit returns the stored `AddressRef`; cache miss returns `None`; key derivation is case- and whitespace-insensitive; round-trip preserves all `AddressRef` fields including `provider_metadata`.
- Create: `tests/unit/test_nominatim_geocoder.py` — covers, using a mocked `httpx.AsyncClient`: cache hit avoids the HTTP call; cache miss triggers exactly one HTTP call; User-Agent header is set; Nominatim response → `AddressRef` mapping is correct (`importance` or `place_rank` maps to `confidence` — document the chosen Nominatim field at the top of `nominatim.py`); the throttle Lock is exercised (two concurrent calls serialize ≥1s apart — use a fake clock for the sleep so the test is fast).

**Low-confidence structural signal — design decision.** Spec §10 says "Low confidence (<0.5) is the signal the agent uses to ask for clarification". The handoff says it must be "structurally distinguishable from a high-confidence one — not just numerically different." Decision:

- `AddressRef.confidence < 0.5` is necessary but not sufficient.
- A low-confidence result MUST also carry `provider_metadata={"alternates": [{"formatted": ..., "lat": ..., "lng": ...}, ...]}` listing at least two candidates from Nominatim. A high-confidence result has `provider_metadata={"alternates": []}` (empty list, present and typed).
- The `tools/geocode.py` outcome row exposes this directly so the agent sees both signals (confidence + alternates) without parsing the adapter return.
- The bad-address scenario (P4 `03_bad_address.yaml`) will key off the presence of non-empty `alternates`, NOT just the float comparison.

**Tasks:**
- [ ] Step 1: Write `tests/unit/test_geocode_cache.py` (all assertions). Expect FAIL — `GeocodeCache` not defined.
- [ ] Step 2: Implement `cache.py` to make those tests pass.
- [ ] Step 3: Write `tests/unit/test_nominatim_geocoder.py`. Expect FAIL — `NominatimGeocoder` not defined.
- [ ] Step 4: Implement `nominatim.py` until tests pass. Verify `isinstance(g, GeocoderPort)` returns True at the end of one test (runtime_checkable proves the port shape).
- [ ] Step 5: Hand-craft the four committed fixtures. Their content must round-trip through `AddressRef.model_validate_json`. Verify with a one-shot script or REPL session before committing; do NOT commit fixtures the parser can't load.
- [ ] Step 6: `make verify` + `make transcripts-ci` — green.
- [ ] Step 7: Commit: `feat(P2): Nominatim geocoder + cache + committed fixtures`. Include the two structurally-distinct fixtures and reference §10 in the body.
- [ ] Step 8: `make ship`.

**Ralph:** **standalone**. The adapter has network IO, throttle semantics, a non-trivial cache layout, AND owns the low-confidence structural signal. Worth a single self-Ralph pass before commit.

---

## T20 / T21 / T22 — Three read-only tools (parallel)

After T19 ships, dispatch these three slices in concurrent worktrees. Each lands as one `feat(P2):` commit.

### T20 `tools/geocode.py`

**Files:**
- Create: `src/agentic_cab/tools/geocode.py` — non-gated, read-only. Emits **`requested` + `outcome` only** (invariant 6 — read-only tools never emit `verified` or `executed`). Takes `GeocodeArgs(query: str)`; depends on `GeocoderPort` (NOT `NominatimGeocoder`).
- Create: `tests/unit/test_tool_geocode.py` — covers: happy-path returns `AddressRef`-shaped result; cache hit + cache miss both produce identical action-log shape; the outcome row carries `confidence` and `alternates_count` (a derived integer, not the raw alternates list — avoid PII expansion in logs per invariant 8).

**Outcome row shape (must match in test):**
```
{kind: "outcome", status: "ok",
 result: {formatted, lat, lng, confidence, alternates_count: int}, error: null}
```

**Tasks:**
- [ ] Step 1: Write `test_tool_geocode.py` against a stub `GeocoderPort` (no Nominatim in unit tests — DI a fake). Expect FAIL.
- [ ] Step 2: Implement `tools/geocode.py`. Pattern: copy the `requested`/`outcome` skeleton from `tools/discover.py` verbatim — do NOT invent a new shape.
- [ ] Step 3: Tests pass. `make verify` green.
- [ ] Step 4: Register in `src/agentic_cab/scenarios/runner.py` tool list with the description: `"Resolve an address query to {formatted, lat, lng, confidence}. confidence<0.5 + non-zero alternates_count means the address is ambiguous — ask the user to clarify."`
- [ ] Step 5: Commit: `feat(P2): add geocode_address tool`. `make ship`.

### T21 `tools/track.py`

**Files:**
- Create: `src/agentic_cab/tools/track.py` — non-gated, read-only. `TrackArgs(booking_id: str)`. Calls `provider.track(booking_id)`, returns `{booking_id, status, summary_title, price_cents}`. Same `requested`/`outcome`-only log shape.
- Create: `tests/unit/test_tool_track.py` — covers: returns booking when present; outcome row has `status: "ok"`; if `provider.track` raises `ValueError("unknown booking")`, the tool catches, emits `outcome` with `status: "error", error: <msg>`, and returns `{status: "error", error: <msg>}` (never raises out of the tool).

**Tasks:**
- [ ] Step 1: Tests first (both happy and unknown-booking).
- [ ] Step 2: Implement. Mirror `tools/discover.py` shape.
- [ ] Step 3: Tests pass. `make verify`.
- [ ] Step 4: Register in `runner.py` tool list. Description: `"Read-only: returns current status of an existing booking_id."`
- [ ] Step 5: Commit: `feat(P2): add track_booking tool`. `make ship`.

### T22 `tools/list_active.py`

**Files:**
- Create: `src/agentic_cab/tools/list_active.py` — non-gated, read-only. `ListActiveArgs(BaseModel): pass` (empty schema; Pydantic v2 accepts this). Calls `store.list_active_bookings()` (NOT through the provider — this is a state-store query, per spec §7 table "no (state store)"). Outcome carries `bookings: list[{booking_id, status, summary_title}]`.
- Create: `tests/unit/test_tool_list_active.py` — covers: empty list when no bookings; multiple bookings filtered by `status NOT IN ('completed','cancelled','failed')` (relies on existing `SqliteStateStore.list_active_bookings`).

**Tasks:**
- [ ] Step 1: Tests.
- [ ] Step 2: Implement.
- [ ] Step 3: Tests pass. `make verify`.
- [ ] Step 4: Register in `runner.py`. Description: `"Read-only: list bookings not yet completed/cancelled/failed. Use to recover booking_ids the user references obliquely (\"my ride\")."`
- [ ] Step 5: Commit: `feat(P2): add list_active_bookings tool`. `make ship`.

**Ralph for T20/T21/T22:** **family-batched** after all three land on `main`. One reviewer pass over the three tools as a unit, looking for log-shape drift, registration-pattern drift, and read-only/gated boundary leakage. Capture dispositions in a single follow-up commit if needed (`refactor(P2): align <…>` per disposition), or a single review note in the body of the last of the three if nothing actionable surfaces.

**Parallelization protocol.** Each task runs in `../agentic-cab-rides-<t20|t21|t22>` worktrees off `origin/main`. They edit no shared file EXCEPT `scenarios/runner.py` (tool registry). Resolve that conflict by:
- The first slice to land registers its tool only.
- Each subsequent slice rebases on `origin/main` immediately before its registration commit and adds its own line.
- If two land within the same merge window, the second resolves trivially (append to the list).

`tools/__init__.py` and pyproject changes: none expected — confirm before opening any worktree.

---

## T23 — `CLIConfirmationChannel` reading from `/dev/tty`

**Why standalone.** Invariant 2 is structurally enforced by the channel; the test that proves it is the deliverable. Spec §6: "reads from `/dev/tty`, not stdin. Agent stdin cannot inject 'y'."

**Files:**
- Create: `src/agentic_cab/adapters/confirmation/cli.py` — `class CLIConfirmationChannel`. Constructor takes `clock: Clock` and an optional `tty_path: str = "/dev/tty"` (DI for the test). `request()` opens `tty_path` in `"r+"` mode, writes the summary + fingerprint prefix (`fingerprint[:12]`) + prompt (`"Approve? [y/N]: "`), reads one line, strips, lowercases. `"y" | "yes"` → approved; anything else → declined. Sets `approver="cli-tty"` on approval. On EOFError or OSError opening the tty (e.g., test contexts with no tty), raise — do NOT silently fall back to stdin (that is the bug the test guards against).
- Create: `tests/unit/test_cli_confirmation.py` — three tests:
  - `test_reads_from_tty_path_not_stdin`: pass a `tty_path=<tmp_path>/fake_tty` containing `"y\n"`; redirect `sys.stdin` (via `monkeypatch.setattr(sys, "stdin", io.StringIO("n\n"))`) to "n". Assert `approved=True`. This is the structural proof — stdin says no, tty says yes, channel honors tty.
  - `test_declines_on_non_y`: tty contents `"n\n"`, then `"\n"`, then `"maybe\n"` (parameterized). Assert `approved=False, reason="declined"` for each.
  - `test_raises_when_tty_unopenable`: pass `tty_path="/nonexistent/tty"`. Assert raises (no silent fallback). Use `pytest.raises(OSError)`.
- Modify: `pyproject.toml` — no change expected; `io`/`pathlib` are stdlib.

**Tasks:**
- [ ] Step 1: Write all three tests with the fake-tty fixture pattern. Expect FAIL — class not defined.
- [ ] Step 2: Implement `CLIConfirmationChannel`.
- [ ] Step 3: Run the three tests. Expect PASS.
- [ ] Step 4: Run `make test-fast` — full unit suite green.
- [ ] Step 5: `make verify` + `make transcripts-ci` (scripted scenarios unaffected — green).
- [ ] Step 6: Commit: `feat(P2): CLIConfirmationChannel reads from /dev/tty`. Body must explicitly cite invariant 2 and reference `test_reads_from_tty_path_not_stdin` by name as the structural proof.
- [ ] Step 7: `make ship`.

**Ralph:** **standalone**. Channel is the highest-stakes structural component shipped in P2. One review pass focused on: (a) is there any code path that falls back to stdin?, (b) is the fingerprint prefix actually shown to the user?, (c) does the test really redirect stdin (not just leave it untouched)?

---

## T24 — `tools/cancel.py` (gated, fee-quoted)

**Architecture.** Same gate guarantee as `confirm_booking`. Two-phase under the hood, one tool surface:

1. Load booking; reject if missing or already cancelled/completed/failed (no-op path returns `outcome:status="rejected", reason=...`).
2. `quote_cancel_fee = await provider.quote_cancel_fee(booking_id)` — populates the fee that goes into the gate summary.
3. Mint idempotency key via injected `key_factory: Callable[[], str] = new_idempotency_key` (same DI as `confirm_booking`; intent-bound keys remain a P3 follow-up).
4. `store.claim_or_recall(key, scope="cancel")` — if recalled, short-circuit with `executed.replayed=True` and the recalled booking; do NOT re-invoke the channel (mirrors confirm's idempotency-replay-vs-gate semantics per spec §10a).
5. Build `ConfirmationRequest(summary=f"Cancel {booking.summary.title}? Fee: ${fee/100:.2f} {currency}", fingerprint=fingerprint_of_cancel_intent, details={"booking_id", "fee_cents", "status_at_quote"}, expires_at=clock.now() + 60s)`.
   - **Fingerprint for cancel** is `sha256(canonical_json({booking_id, fee_cents, status_at_quote}))` — NOT the original draft fingerprint, which is irrelevant post-confirm. Document this with a one-line comment in `tools/cancel.py` (this is exactly the kind of "non-obvious WHY" CLAUDE.md says is comment-worthy).
6. Gate. Decline → `verified.result="rejected", reason="user declined"`, `outcome:status="rejected"`. NO `executed` row (invariant 6).
7. Approve → emit `verified(ok)`, call `provider.cancel(booking_id, idempotency_key=key)`, emit `executed`, persist via `store.put_booking(updated_booking)` (or a status update if the adapter returns a status-mutated Booking), `finalize_idempotency(key, booking)`, emit `outcome:status="cancelled"`.

**Files:**
- Create: `src/agentic_cab/tools/cancel.py`.
- Modify: `src/agentic_cab/adapters/uber_sim/provider.py` — `cancel()` currently returns the booking unchanged. Make it set `status="cancelled"` and attach a `CancellationInfo(cancelled_at=clock.now(), fee=<same as quote_cancel_fee>, reason=None)` to the returned `Booking`, then `put_booking` it. The fee must match what `quote_cancel_fee` would have returned for the same status — extract a private `_compute_fee(status)` helper so both methods call into one source of truth.
- Create: `tests/unit/test_tool_cancel.py` — covers ALL paths below:
  - `test_cancel_happy_path_emits_four_rows`: book → cancel → assert exactly `requested → verified(ok) → executed → outcome(cancelled)` rows.
  - `test_cancel_declined_emits_three_rows_no_executed`: scripted channel returns `approved=False` → assert NO `executed` row, `outcome:status="rejected"`. This is the cancel-side mirror of the action-log integrity invariant.
  - `test_cancel_unknown_booking`: `outcome:status="rejected", reason="unknown booking"`. No gate invocation.
  - `test_cancel_already_cancelled_short_circuits`: a booking already in `cancelled` state → `outcome:status="rejected", reason="already cancelled"`. No gate.
  - `test_cancel_idempotency_replay`: call cancel twice with the same `key_factory`; second call returns the recalled booking, gate is NOT re-invoked (assert by passing a channel that raises if called more than once), `executed.replayed=True` on the second.
  - `test_cancel_fee_in_gate_summary`: spy channel captures `req.summary`; assert it contains the dollar amount the sim's `_compute_fee` would return for the booking's current status.
- Modify: `src/agentic_cab/scenarios/runner.py` — register `cancel_booking` in the tool list. Description: `"Cancel an existing booking_id. Gated — user will be asked to approve the cancellation fee. The fee depends on current ride status."`
- Modify: `tests/unit/test_action_log_integrity.py` — extend the "rejected→no-executed" invariant test to walk JSONLs from cancel scenarios as well, not just confirm. (The existing test takes a `tool` parameter; add a parameterization for `cancel_booking`.)

**Tasks:**
- [ ] Step 1: Write all six unit tests first. Expect FAIL — tool not defined.
- [ ] Step 2: Implement `_compute_fee` in the sim adapter; update `cancel()` to use it and return the mutated booking.
- [ ] Step 3: Implement `tools/cancel.py`. Use `tools/confirm.py` as the template — copy the action-log emission structure verbatim to keep four-row shape consistent.
- [ ] Step 4: All six tests pass.
- [ ] Step 5: Extend `test_action_log_integrity.py` parameterization. Run — PASS.
- [ ] Step 6: `make verify`. Green.
- [ ] Step 7: Commit: `feat(P2): cancel_booking tool — gated, fee re-quoted, idempotent`. Body cites invariants 2, 5, 6 and references the six test names.
- [ ] Step 8: `make ship`.

**Ralph:** **standalone**. Cancel touches gate + fingerprint + idempotency + log shape — the same surface area as T13 (confirm). Worth its own pass.

---

## T25 — `agent/chat.py` + `make repl` interactive E2E

**Files:**
- Create: `src/agentic_cab/agent/chat.py` — module-level `async def run_repl()` entry point.
  - Wires `SystemClock`, `random.Random(os.urandom(8))`, `SqliteStateStore(data/state.db)`, `JSONLActionLog`, `NominatimGeocoder(GeocodeCache(fixtures/geocode/), httpx.AsyncClient(timeout=10))`, `CLIConfirmationChannel(clock=SystemClock())`, `UberSimAdapter(store, clock, rng_seed=int.from_bytes(os.urandom(4)))`.
  - Registers all six tools: `discover_options`, `prepare_booking`, `confirm_booking`, `track_booking`, `cancel_booking`, `geocode_address`, `list_active_bookings`. (Seven if you count `list_active_bookings` separately — match spec §7's table exactly.)
  - Rich rendering: `from rich.console import Console; from rich.panel import Panel`. User input via `Console.input(">>> ")`. Agent output rendered in a `Panel(text, title="agent", border_style="cyan")`. Tool calls rendered as a dim one-liner `[dim]→ discover_options(...)[/dim]`. Non-streamed (streaming deferred to P5; spec §11 doesn't require streamed CLI).
  - Exit: typing `/quit` or Ctrl-D.
  - Session lifecycle: one `session_id` for the whole REPL session. New `turn_id` per user input.
- Modify: `src/agentic_cab/__main__.py` — wire a CLI subcommand `python -m agentic_cab repl` to `chat.run_repl()`. The existing `run-scenario` subcommand stays untouched.
- Modify: `Makefile` — confirm `make repl` already points at `python -m agentic_cab repl` (it does per the existing target on line 26; re-verify before committing).
- Update: `agent/system_prompt.py` — extend the rules section to cover the new tools:
  - `track_booking` is read-only — agent may call it freely without asking.
  - `geocode_address` should be called before `discover_options` when the user provides natural-language addresses, NOT lat/lng pairs.
  - `list_active_bookings` is the recovery path when the user says "my ride" without an ID.
  - `cancel_booking` is gated — same y/n contract as `confirm_booking`. The fee will be shown to the user; the agent must quote it before invoking.
  - Add: "Be terse" stays. Add: "When the user provides an address that geocodes with confidence<0.5 OR has non-empty alternates, ask which they meant before discovering."

**Manual smoke-test protocol (book → track → cancel).** Run these by hand AFTER `make verify` is green and BEFORE the commit. Document the actual session output in the commit body (paraphrased — not a full transcript paste).

1. `make reset && make repl`.
2. Type: `"Book a ride from 37.7749,-122.4194 to 37.7849,-122.4094"`.
3. Agent calls `discover_options` → `prepare_booking` → `confirm_booking`. CLI prompts via `/dev/tty`. Type `y`. Expect: `outcome:confirmed` with a `booking_id`.
4. Type: `"What's the status of my ride?"`. Agent should call `list_active_bookings` (or `track_booking` if it remembered the ID) and report `confirmed` or whatever the sim's state machine says by now (P2 sim is stationary at `confirmed`; P3 will move it).
5. Type: `"Cancel it"`. Agent calls `cancel_booking`. CLI prompts with the fee in the summary. Type `y`. Expect: `outcome:cancelled` with the fee surfaced.
6. Type: `/quit`. Inspect `logs/<session_id>.jsonl` and verify the four-row pattern for both `confirm_booking` and `cancel_booking`. Run `make replay SESSION=<id>` and verify it pretty-prints cleanly.

**Automated coverage.** The above is manual because it exercises a real TTY. The automatable parts are:
- T23's `test_reads_from_tty_path_not_stdin` covers the gate.
- T20–T24 unit tests cover each tool independently.
- A new `scenarios/02_book_then_cancel.yaml` would automate steps 2–5 above end-to-end with `ScriptedConfirmationChannel`. **Defer to P4** per `docs/PHASES.md` (P4 owns the remaining four scenarios; `05_cancel_with_fee.yaml` will subsume this). Do NOT add a new scenario in P2 — that's scope creep.

**Tasks:**
- [ ] Step 1: Implement `agent/chat.py`. No unit test for the REPL itself (interactive surface — the smoke protocol is the test).
- [ ] Step 2: Update `system_prompt.py` per the bullets above.
- [ ] Step 3: Wire `__main__.py` subcommand.
- [ ] Step 4: `make verify` + `make transcripts-ci` — green (scripted scenarios still pass; the REPL is additive).
- [ ] Step 5: Run the manual smoke protocol. Save a one-paragraph paraphrase of the run for the commit body. If anything diverges from expected, do NOT mark complete — debug via `superpowers:systematic-debugging`.
- [ ] Step 6: Commit: `feat(P2): Rich REPL + book→track→cancel wired end-to-end`. Body lists the seven registered tools and includes the smoke-test paraphrase.
- [ ] Step 7: `make ship`.

**Ralph:** **standalone**. The REPL is the user-facing surface for everything P2 ships; one review pass focused on tool registration completeness, system-prompt coverage of the new tools, and that no scenario-mode-only assumptions leaked in (e.g., `FrozenClock` references — the REPL must use `SystemClock`).

---

## Self-review checklist (run before handing the plan to the user)

- [ ] Every P2 acceptance bullet from `docs/PHASES.md` maps to at least one slice:
  - `track_booking, cancel_booking, list_active_bookings, geocode_address` → T20, T21, T22, T24
  - `CLIConfirmationChannel /dev/tty` → T23
  - `make repl` interactive book→track→cancel → T25 (manual smoke protocol)
  - Nominatim adapter + fixtures → T19
  - Low-confidence structural distinguishability → T19 (provider_metadata.alternates pattern) + T20 (outcome row exposes alternates_count)
- [ ] Every P2-adjacent FOLLOWUPS item from the handoff is either pulled in or explicitly deferred:
  - GeocoderPort → T17 ✔
  - StateStore.get_draft_consumed_at → T16 ✔
  - SqliteStateStore.db_path → T16 ✔
  - AutoApproveChannel structural guard → deferred (no P2 caller needs it; note in plan ✔)
  - Restart-survival, FastAPI thread-safety, intent-bound keys, MockAppointmentProvider, fingerprint permutations, fingerprint type tightening, ConfirmationResult validator, typed ConfirmationRequest.details, scenario YAML hygiene, action-log retention → deferred (called out as out-of-scope ✔)
- [ ] No placeholders. Spot-checked.
- [ ] Type signatures consistent across slices:
  - `GeocoderPort.geocode(query: str) -> AddressRef` — T17 defines, T19 implements, T20 consumes ✔
  - `BookingProvider.quote_cancel_fee(booking_id: str) -> Money` — T18 defines + implements, T24 consumes ✔
  - `StateStore.get_draft_consumed_at(draft_id: str) -> datetime | None` — T16 defines + implements, `tools/confirm.py` switched in T16 ✔
- [ ] Spec-amendment markers (`<!-- spec-amendment: 2026-05-20 -->`) called out for T17 and T18 — both edit `docs/superpowers/specs/2026-05-19-agentic-cab-rides-design.md` in their own commits.
- [ ] Velocity levers honored: Ralph dispositions declared per-slice up front; parallelizable slices flagged with a worktree protocol; no Pydantic-shape smoke tests demanded.
- [ ] `make ship` checkpoint at end of every slice. No slice is "done" until `gh run list --limit 1 --branch main` is green.
- [ ] No `temperature` parameter introduced anywhere — the model rejects it.
- [ ] No `--no-verify` usage.
- [ ] All new tests under `tests/unit/` (no contract or scenario tests added in P2 — those are P3/P4).

---

**After the user approves the plan:**

1. Commit this file with `docs:` scope (no phase scope per CLAUDE.md commit convention for docs-only).
2. `make ship` and confirm CI green.
3. Then invoke `superpowers:subagent-driven-development` to execute T16 → T17 → T18 → T19 sequentially, then dispatch T20/T21/T22 in parallel worktrees, then T23 → T24 → T25 sequentially. Two-stage review between each slice; the parallel triplet gets one batched family-Ralph after all three land.
