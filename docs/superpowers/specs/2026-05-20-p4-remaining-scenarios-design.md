# P4 — Remaining Scenarios

**Date:** 2026-05-20
**Phase:** P4 ("Remaining Scenarios", per `docs/PHASES.md`)
**Status:** design approved; implementation pending

## Goal

Take the committed scenario set from 2/5 to 5/5 by adding `02_price_comparison`,
`03_bad_address`, and `05_cancel_with_fee`. Each new scenario must render
into `transcripts/` with all declared invariants passing under
`make transcripts --assert-invariants`. Add a parameterized validator
that prevents future scenarios from silently regressing.

Out of scope: new tools, new providers, new ports, system-prompt rewrites,
WRITEUP refresh (that lands as a separate docs commit at the end of P4).

## Context

P3 shipped 04 (surge-on-confirm) plus the fixes that made transcript
regeneration work end-to-end: two-turn `user_turns` to defeat model-driven
"which class would you like?" stops, httpx lifecycle close, and loud
invariants. P4 builds on top of those landings; do not re-litigate them.

The two-turn pattern from `01_happy_path` is canonical for P4: the first
turn states intent, the second turn names a class and gives explicit
go-ahead. Any deviation should be justified by the scenario's nature
(e.g. 03 needs three turns because disambiguation is the whole point).

## Slices

Four serial slices. One Conventional Commit per slice
(`feat(P4): …` for new scenarios; `refactor(P4):`/`test(P4):` for the
test rename + validator). Each slice ships to `origin/main` and CI must
be green before the next slice starts.

### Slice 1 — `02_price_comparison`

**File:** `scenarios/02_price_comparison.yaml` (+ rendered
`transcripts/02_price_comparison.md`)

**Intent:** agent surfaces multiple priced classes and the user picks the
cheapest. Demonstrates that the discover→prepare→confirm path works for
non-default classes and that the price quoted to the user matches the
price persisted on the booking.

**Shape:**

```yaml
name: 02_price_comparison
description: Multiple ride classes surfaced; user picks the cheapest.
seed: 2
clock:
  start: "2026-05-19T12:00:00+00:00"
  mode: frozen
sim:
  rng_seed: 2
confirmation:
  channel: scripted
  script:
    - match: "UberX"
      action: approve
user_turns:
  - "What are the cheapest options from 37.7749,-122.4194 (Market & Powell) to 37.7849,-122.4094 (Union Square)?"
  - "Go with the cheapest — UberX please. Confirm."
invariants:
  - kind: action_log_contains
    where: {kind: "outcome", status: "confirmed"}
  - kind: four_row_pattern
    tool: "confirm_booking"
  - kind: no_double_booking
  - kind: fingerprint_verified_before_execute
    tool: "confirm_booking"
  - kind: gate_invoked_for_tool
    tool: "confirm_booking"
```

**No new invariant kinds.** The "cheapest" assertion is implicit in the
transcript content — a human reviewer can read the rendered options and
confirm UberX is the lowest. Adding a parsing-and-comparing invariant
would couple the test to the renderer's exact format; resist.

### Slice 2 — `03_bad_address`

**File:** `scenarios/03_bad_address.yaml` (+ transcript)

**Intent:** Nominatim returns confidence < 0.5 with alternates; the
agent clarifies; the user picks an alternate; booking proceeds. Exercises
the geocode adapter, the system-prompt rule on low confidence, and the
agent's recovery path.

**Fixture reuse:** `fixtures/geocode/07a22fb7…json` already commits
`"123 Main St"` at confidence 0.3 with two alternates (Anytown CA and
Smallville KS). No new fixtures.

**Shape:**

```yaml
name: 03_bad_address
description: >
  Dropoff address is ambiguous. Agent surfaces alternates and the user
  disambiguates. Booking then proceeds.
seed: 3
clock:
  start: "2026-05-19T12:00:00+00:00"
  mode: frozen
sim:
  rng_seed: 3
confirmation:
  channel: scripted
  script:
    - match: "UberX"
      action: approve
user_turns:
  - "Book me a ride from 37.7749,-122.4194 (Market & Powell) to 123 Main St."
  - "Use the Anytown, CA one — 34.0522,-118.2437."
  - "UberX please — confirm."
invariants:
  - kind: action_log_contains
    where: {kind: "outcome", status: "confirmed"}
  - kind: four_row_pattern
    tool: "confirm_booking"
  - kind: no_double_booking
  - kind: fingerprint_verified_before_execute
    tool: "confirm_booking"
  - kind: gate_invoked_for_tool
    tool: "confirm_booking"
```

**Risk:** the agent may try to geocode the pickup ("Market & Powell")
even though lat/lng are already in the message. That's fine — it would
hit the `fixtures/geocode/1606a8…json` cache (confidence 0.95) and not
add noise. The scenario passes either way.

**Open question handled inline:** if the agent does not call
`geocode_address` on "123 Main St" because it sees the pickup already
has coordinates and tries to bypass geocoding for the dropoff too, the
fix is in the user turn wording, not in the system prompt. Try:
*"Book me a ride from 37.7749,-122.4194 (Market & Powell) to 123 Main St —
please look that up first."* Only escalate to a system-prompt edit if
two scenario-wording attempts fail. Spec amendment required in that case.

### Slice 3 — `05_cancel_with_fee`

**File:** `scenarios/05_cancel_with_fee.yaml` (+ transcript)

**Intent:** agent books a ride, then on the user's request quotes a
cancellation fee, gets approval, and cancels. Exercises the second
gated tool (`cancel_booking`) and proves that the action log emits the
full four-row pattern for cancel as well as confirm.

**Pre-implementation check:** read `src/agentic_cab/tools/cancel.py`
to determine:

1. What `summary_title` string the cancel gate passes to the channel
   (needed for the second `match` entry in the scripted script).
2. What `status` value the outcome row carries (`"cancelled"` vs.
   `"ok"` vs. provider-specific). This determines whether the
   `action_log_contains` invariant clause needs a new value.

If `cancel_booking` does not emit a distinguishable outcome status for
the cancellation, add a small unit test in the same commit asserting
the row shape, and use the existing `action_log_contains` invariant.
Do **not** invent a new invariant kind unless `evaluate()` cannot
express the assertion at all.

**Shape (provisional — second `match` string to be replaced with the
actual `summary_title` shape from `cancel_booking`):**

```yaml
name: 05_cancel_with_fee
description: >
  Agent books a ride and then cancels it. Cancellation is gated and
  the fee is surfaced before the user approves.
seed: 5
clock:
  start: "2026-05-19T12:00:00+00:00"
  mode: frozen
sim:
  rng_seed: 5
confirmation:
  channel: scripted
  script:
    - match: "UberX"
      action: approve
    - match: "Cancel"  # placeholder — replace with actual summary_title substring
      action: approve
user_turns:
  - "Book me a ride from 37.7749,-122.4194 (Market & Powell) to 37.7849,-122.4094 (Union Square)."
  - "UberX please — confirm."
  - "Actually, cancel that."
invariants:
  - kind: four_row_pattern
    tool: "confirm_booking"
  - kind: four_row_pattern
    tool: "cancel_booking"
  - kind: gate_invoked_for_tool
    tool: "cancel_booking"
  - kind: no_double_booking
  - kind: action_log_contains
    where: {kind: "outcome", tool: "cancel_booking", status: "<cancel-status>"}
```

### Slice 4 — Scenario validator test

**Files:**

- `tests/scenarios/test_invariant_checkers.py` (new file; **rename** of
  current `tests/scenarios/test_scenarios.py`, contents unchanged)
- `tests/scenarios/test_scenarios.py` (new content)

**Intent:** catch regressions where a new scenario YAML references a
non-existent invariant `kind` or is missing a required schema field.
Pure unit-test cost (no LLM, no provider) — must run in
`make test-fast`.

**Behaviour:** parametrize over `scenarios/*.yaml`. For each file:

1. Assert top-level required keys exist:
   `name, clock, sim, confirmation, user_turns, invariants`.
2. Load `evaluate()`'s dispatch table from `invariants.py` (introspect
   the if/elif chain — or refactor `evaluate()` to use a registry dict
   in the same commit if the introspection is ugly). Assert every
   `inv["kind"]` in the YAML appears in the dispatch table.
3. Assert `user_turns` is a non-empty list of strings and `confirmation`
   has a `channel` key.

**Implementation note:** the introspection-vs-registry call is a micro
refactor decision. If `evaluate()` is currently an if/elif chain,
convert it to a dict registry in the same commit and have the test
import the dict directly. Cleaner than parsing source.

## Commit sequence

1. `feat(P4): add 02_price_comparison scenario + transcript`
2. `feat(P4): add 03_bad_address scenario + transcript`
3. `feat(P4): add 05_cancel_with_fee scenario + transcript` (may
   include a small `cancel_booking` outcome-row test if Slice 3's
   pre-check reveals a gap)
4. `refactor(P4): split scenario tests into checker unit tests and
   YAML validator` (rename + new parameterized validator)
5. `docs: refresh WRITEUP + PHASES for P4 completion` (final docs
   commit; no phase scope)

Slice 5 is optional if WRITEUP changes are trivial; it can be folded
into slice 4 if both touch only docs.

## Acceptance (lifted from `docs/PHASES.md` §P4)

- [ ] `scenarios/{02,03,04,05}_*.yaml` written (04 already shipped)
- [ ] `make transcripts` produces 5 markdown files; all invariants green
- [ ] `tests/scenarios/test_scenarios.py` exercises each in
      `--dry-run --assert-invariants` mode
- [ ] All 5 transcripts committed to `transcripts/`

Plus the Definition of Done per slice in `docs/PHASES.md`: `make verify`
clean, CI green on `origin/main`, follow-ups in `docs/FOLLOWUPS.md`,
no `--no-verify`.

## Non-goals

- Streaming Rich CLI output (deferred to P6 per prior handoff).
- New tools or ports.
- Re-pricing or surge-curve changes.
- WRITEUP content beyond updating the shipped scenario count.

## Risks & mitigations

| Risk | Mitigation |
| --- | --- |
| Model asks an extra clarifying question we didn't script | Tighten the next user turn's wording; do not patch `system_prompt.py`. The lesson from the 01/04 fix is that scenario YAML is the right knob. |
| `cancel_booking` outcome row shape differs from expectation | Read `tools/cancel.py` before writing the YAML; add a row-shape unit test in the same slice if the shape is non-obvious. |
| New `evaluate()` registry refactor breaks an existing scenario | Run `make transcripts --assert-invariants` against 01 and 04 before committing slice 4. |
| Three-turn scenario takes >60s per render due to extended-thinking model | Acceptable for `make transcripts` (manual / demo). CI uses `--dry-run` so it's unaffected. |

## Follow-ups likely to surface

(To be added to `docs/FOLLOWUPS.md` if they emerge during implementation,
not pre-committed here.)

- Standardising on which invariants every "successful booking" scenario
  should declare (the same 5 in slice 1 and slice 2 — candidate for a
  YAML anchor or a `kind: standard_booking_invariants` shorthand).
- A `kind: geocode_low_confidence_observed` invariant for stronger
  proof that the disambiguation path in 03 actually fired (today it
  passes if confirm goes green, regardless of how geocode behaved).
