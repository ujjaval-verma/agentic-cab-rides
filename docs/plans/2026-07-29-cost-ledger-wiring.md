# Plan: per-action cost ledger — seam, wiring, and drain (P8)

Branch: `feat/cost-ledger`. Phase `P8` in `docs/PHASES.md`.

Slice 1's core artifacts already landed (`e121c93`): `LLMCall` in `core/types.py`,
`CostMeter` Protocol in `core/ports.py`, `price_call` in `core/pricing.py`,
`InMemoryCostMeter` in `adapters/cost_meter.py`, and unit tests for both.

What remains is the part that makes them load-bearing: threading a meter from the
one `client.messages.create` call site out to the gated tools, and draining it onto
the `outcome` row so cost is bound to an `action_id`.

## Global Constraints

These bind every task. They come from the root `CLAUDE.md` invariants and from
decisions already made on this branch — none are open for relitigation.

1. **`meter: CostMeter` is a REQUIRED keyword-only parameter everywhere it appears.**
   Never `CostMeter | None`, never defaulted. A defaulted meter lets a missed call
   site silently lose cost, which is the exact failure this phase exists to prevent.
   If a call site is missed, the type checker must fail.

2. **Layer direction is strict (invariant 1).** `agent/` and `tools/` import
   `CostMeter` from `agentic_cab.core.ports` — never `agentic_cab.adapters.cost_meter`.
   `InMemoryCostMeter` is constructed only at the three wiring sites, which already
   sit above the agent layer. No new upward edge.

3. **`LLMCall.latency_ms` is held in memory but NEVER emitted to the action log.**
   Wall-clock latency is machine noise that would make action-log rows irreproducible
   across re-runs (invariant 9). Documented in `docs/ARCHITECTURE.md`. The meter
   records it; the log drops it.

4. **The action log takes flat JSON only.** `JSONLActionLog.emit` serializes with
   `default=str`, so a pydantic object would stringify into garbage. Emit
   `model_dump()` dicts and plain ints.

5. **Invariant 6 is unchanged.** This adds fields to the existing `outcome` row kind.
   It does NOT add a fifth row kind. Read-only tools still emit `requested` +
   `outcome` only and never take a meter.

6. **No `TODO`/`FIXME`/`XXX` anywhere (invariant 13, ruff `FIX`).** Deferred work goes
   to `docs/FOLLOWUPS.md`.

7. **No `print()` outside CLI/web rendering or `scripts/` (invariant 14, ruff `T20`).**

8. **No `--no-verify`, ever (invariant 12).**

9. **Money and cost are integers.** Microcents, `StrictInt`. No float, no `Decimal`.

10. **Verification is evidence, not assertion.** Every task reports the actual command
    output of `uv run ruff format --check .`, `uv run ruff check .`,
    `uv run pyright`, and `uv run pytest -m "not scenario and not slow"`. Pasting
    real output is required; "tests pass" is not a report.

## Task 1 — the seam and the three wiring sites

Thread a single per-session `CostMeter` from the Anthropic call out to the two gated
tools. No log rows change in this task.

### The seam

`src/agentic_cab/agent/loop.py:37` holds the only `client.messages.create` call in
`src/`. Around it:

- Time the call and build an `LLMCall` from `response.usage` plus `MODEL_ID`.
- `meter.record(...)` it **before** the `stop_reason` branch, so every iteration is
  recorded — including the final, non-`tool_use` one. A `record` placed inside the
  `tool_use` branch would silently drop the last call of every turn.
- `provider` on the `LLMCall` is `"anthropic"`.
- `cost_microcents` comes from `core.pricing.price_call`.
- `response.usage` may not carry the cache fields on every SDK version; read them
  defensively (`getattr(usage, "cache_read_input_tokens", 0) or 0`) rather than
  assuming presence. They are `int | None` in some SDK versions — `None` must become
  `0`, not crash `StrictInt`.

For `latency_ms`: `Clock` (`core/ports.py`) exposes only `now()`, which is a wall
clock and, under `FrozenClock`, does not advance. Add a `monotonic_ns()` method to
the `Clock` Protocol and implement it in both `SystemClock` and `FrozenClock`
(`adapters/clock.py`) — `FrozenClock` returns a counter that advances by a fixed
step per call, so it stays deterministic. `run_turn` therefore also needs a
`clock: Clock` keyword. Do NOT call `time.perf_counter()` directly in `agent/loop.py`;
that is an un-injected non-determinism source and violates invariant 9.

Only `SystemClock` and `FrozenClock` implement `Clock`, so the Protocol addition is
about six lines total. Decision, so it is not re-litigated in review: `latency_ms` is
recorded **truthfully** even though Global Constraint 3 keeps it out of the log. It is
a required `StrictInt` on an already-committed frozen type, so it must hold something,
and a field named `latency_ms` that always holds a hardcoded `0` is a worse artifact
than one port method — it reads as a measurement while being a fixed lie. The
in-memory value is what a future `scripts/` cost report would consume.

### Signatures (exact — these are the contract)

```python
# agent/loop.py
async def run_turn(
    client: Anthropic,
    *,
    user_message: str,
    history: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    handlers: dict[str, ToolHandler],
    meter: CostMeter,
    clock: Clock,
) -> tuple[str, list[dict[str, Any]]]: ...

# agent/chat.py
def build_handlers(
    *, sim, store, log, clock, channel, geocoder,
    meter: CostMeter, session_id, turn_id_ref,
) -> dict[str, ToolHandler]: ...

# tools/confirm.py
async def confirm_booking(
    args, *, provider, store, log, channel, clock,
    meter: CostMeter, session_id, turn_id,
) -> dict[str, Any]: ...

# tools/cancel.py — same addition
async def cancel_booking(
    args, *, provider, store, log, channel, clock,
    meter: CostMeter, session_id, turn_id,
) -> dict[str, Any]: ...
```

In this task `confirm_booking` and `cancel_booking` accept `meter` and do not yet use
it. Task 2 consumes it. Accepting an unused parameter for one task is deliberate: it
lets the signature churn and the behavior change land as separate reviewable commits.

`build_handlers` passes `meter` to the `confirm_booking` and `cancel_booking` closures
**only**. The other five handlers do not take it.

### The three wiring sites

Each constructs **one** `InMemoryCostMeter` per session and passes the **same
instance** to both `run_turn` and the handlers. Two meters in one session means the
tools drain a buffer the loop never filled.

- `agent/chat.py:run_repl` (~280-334) — uses `build_handlers`. Note that
  `_repl_loop` (~222) calls `run_turn` at ~261 but currently receives only
  `handlers`, `console`, `input_source`, `session_id`, `turn_id_ref`,
  `chat_log_path`. It needs `meter` and `clock` threaded in as well.
- `scenarios/runner.py:run_scenario` (~53-211) — **does NOT use `build_handlers`.**
  It duplicates the handler dict inline (~129-198) with a shared `common` kwargs
  dict. Add `meter` to the `confirm_booking` (~155) and `cancel_booking` (~163)
  handlers **specifically**. Do NOT add it to `common` — that would push a meter at
  all seven tools, five of which do not accept one.
- `web/app.py` (~199-267) — uses `build_handlers`; `run_turn` is called at ~250
  inside `_run_turn`.

### Test churn

46 direct `await confirm_booking(...)` / `await cancel_booking(...)` call sites across
9 test files:

```
tests/unit/test_tool_confirm.py            tests/unit/test_tool_cancel.py
tests/unit/test_tool_confirm_re_quote.py   tests/unit/test_tool_confirm_in_progress.py
tests/unit/test_confirm_dedup.py           tests/unit/test_action_log_tool_field.py
tests/unit/test_cancel_decline_releases_claim.py
tests/contract/test_restart_survival.py    tests/scenarios/test_repl_web_parity.py
```

Add a `meter` fixture to a shared `tests/conftest.py` returning a fresh
`InMemoryCostMeter()` per test, and thread that fixture through — do NOT hand-write 46
inline constructions. `tests/conftest.py` does not exist yet; `tests/contract/conftest.py`
does. Create the top-level one.

### Tests to write

- `run_turn` records exactly one `LLMCall` per model call, including on a turn whose
  final response has no `tool_use` block. Drive it with a stubbed Anthropic client —
  `tests/unit/test_chat_repl.py` already stubs `messages.create` around lines 114,
  127, and 282; copy that shape.
- The recorded `LLMCall` carries the token counts from `response.usage` and a
  `cost_microcents` matching `price_call` for those counts.
- `usage` objects missing or nulling the cache fields yield `0`, not a crash.
- A `FakeCostMeter` recording into a list is fine for assertions; it must satisfy the
  `CostMeter` Protocol.

### Done when

- `uv run pyright` is clean, which — because `meter` is required and keyword-only —
  is what proves no call site was missed.
- `uv run pytest -m "not scenario and not slow"` is green, at or above the current
  baseline of 312 passed / 2 skipped / 1 deselected.
- No log row shape changed anywhere in this task.

## Task 2 — bind cost to `action_id`

Drain the meter at the single `_emit_outcome` site in each gated tool and attach the
result to the `outcome` row.

Commit `618b842` already collapsed each of `confirm.py` and `cancel.py` from two
`kind:"outcome"` emit points down to one, so this is a two-site change, not a
four-site change. That refactor is what makes the provider-raises path fall out for
free — do not reintroduce a second emit point.

### The change

In `_emit_outcome` in `tools/confirm.py` (~283) and `tools/cancel.py` (~120), drain
the meter and add two fields to the emitted row:

```json
"llm_calls": [ ... ],
"total_cost_microcents": 41250
```

- `llm_calls` is a list of `LLMCall.model_dump()` dicts **with `latency_ms` removed**
  (Global Constraint 3). Every other field stays.
- `total_cost_microcents` is the integer sum of `cost_microcents` across the drained
  calls.
- A drain returning an empty list emits `"llm_calls": []` and
  `"total_cost_microcents": 0`. Do not omit the fields — a reader must be able to
  distinguish "no calls attributed" from "this build predates the ledger".
- Drain exactly once per `_emit_outcome` invocation. `_emit_outcome` returns the tool
  result and is the terminal step of every path.

### Tests to write

- The two fields land on a gated `outcome` row on the happy path.
- **The two fields land on the provider-raises path.** Drive it with a provider
  fixture whose `confirm` raises after the gate approves. This is the test that
  matters; it is the reason the refactor commit came first. Write it for both
  `confirm_booking` and `cancel_booking`.
- `total_cost_microcents` equals the sum over `llm_calls[*].cost_microcents`.
- No emitted row contains a `latency_ms` key inside `llm_calls`.
- Rows for read-only tools are unchanged and gain no cost fields.
- A second gated action in the same session sees only the calls recorded since the
  previous drain — not the first action's calls again.

### Done when

- `make verify` is green.
- No transcript re-render. This task does not change the default model path; if a
  transcript diff appears, stop and report rather than re-rendering.

## Out of scope

Tail-loss measurement and the `docs/WRITEUP.md` section are NOT part of this plan.
They require real scenario runs against the paid API and are gated on a separate
decision. Do not attempt them, and do not stub a number for them.
