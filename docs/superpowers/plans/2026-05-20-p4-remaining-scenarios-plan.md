# P4 Remaining Scenarios — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship scenarios 02, 03, 05 with green invariants and a parameterized YAML validator, taking the committed scenario set from 2/5 to 5/5.

**Architecture:** Five serial slices, each a single Conventional Commit shipped to `origin/main` with CI green before the next slice starts. Three slices add scenario YAML + rendered transcript pairs; one adds the validator; one refreshes docs. No new tools, ports, or providers — purely scenario YAML, one new test file, one rename, and docs.

**Tech Stack:** Python 3.12 + `uv`, `pytest`, `typer` CLI, Pydantic, Anthropic SDK (`claude-opus-4-7`), `ScriptedConfirmationChannel`, JSONL action log, SQLite state store.

**Spec:** `docs/superpowers/specs/2026-05-20-p4-remaining-scenarios-design.md`

---

## File map

**Create:**
- `scenarios/02_price_comparison.yaml`
- `scenarios/03_bad_address.yaml`
- `scenarios/05_cancel_with_fee.yaml`
- `transcripts/02_price_comparison.md` (generated)
- `transcripts/03_bad_address.md` (generated)
- `transcripts/05_cancel_with_fee.md` (generated)
- `tests/scenarios/test_invariant_checkers.py` (rename target — contents moved from current `test_scenarios.py`)
- `tests/scenarios/test_scenarios.py` (new content — YAML/invariant validator)

**Modify:**
- `docs/WRITEUP.md` (final docs commit — bump shipped scenario count)
- `docs/PHASES.md` *(only if a P4 acceptance line needs sharpening; otherwise leave alone — `git log` is the tracker)*

**No modifications expected to:**
- `src/agentic_cab/scenarios/invariants.py` — `CHECKERS` is already a dict registry the validator can import directly
- `src/agentic_cab/agent/system_prompt.py` — escalate only if two scenario-wording attempts fail (per spec)
- `src/agentic_cab/tools/cancel.py` — `cancel_booking` already emits `outcome.status="cancelled"` and surfaces `"Cancel {title}? Fee: $X.YY USD"` to the channel (verified by reading the file pre-plan)

---

## Pre-flight environment

Every task assumes:

```bash
export $(grep -v '^#' ~/.config/agentic-cab-rides/.env | xargs)   # loads ANTHROPIC_API_KEY
```

If `make transcripts --assert-invariants` is invoked without an API key, the run fails with an Anthropic auth error — that's a setup problem, not a scenario bug.

---

### Task 1: Slice 1 — `02_price_comparison`

**Files:**
- Create: `scenarios/02_price_comparison.yaml`
- Create (generated): `transcripts/02_price_comparison.md`

- [ ] **Step 1: Write the scenario YAML**

Create `scenarios/02_price_comparison.yaml`:

```yaml
name: 02_price_comparison
description: Multiple ride classes surfaced; user picks the cheapest (UberX).
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

- [ ] **Step 2: Render + verify invariants pass**

Run:

```bash
make reset
uv run python -m agentic_cab render-transcripts
```

Expected: tail of output contains `02_price_comparison.yaml → session …` and five `PASS` lines under it (one per declared invariant). Process exits 0.

If any FAIL: read the rendered `transcripts/02_price_comparison.md` Conversation section, identify why the agent stopped or diverged, and adjust the user-turn wording (NOT the system prompt). Re-render. Maximum two wording attempts before escalating per the spec's risk table.

- [ ] **Step 3: Run the test gate**

Run:

```bash
make verify
```

Expected: `225 passed, 1 skipped` (or current baseline) — invariant-loudness tests plus the rest. No new tests in this slice, so the count should be unchanged from `main`.

- [ ] **Step 4: Commit**

```bash
git add scenarios/02_price_comparison.yaml transcripts/02_price_comparison.md
git commit -m "$(cat <<'EOF'
feat(P4): add 02_price_comparison scenario + transcript

Two-turn scenario: agent surfaces four priced classes (UberX, Comfort,
UberXL, Black) for the Market & Powell → Union Square route; user picks
UberX as the cheapest and confirms.  Demonstrates the discover →
prepare → confirm path for an explicit class selection and exercises
the same five invariants as 01_happy_path.

No new invariant kinds, no provider changes.  Uses the existing
fixtures/geocode cache (no live Nominatim).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: Ship**

Run:

```bash
make ship
```

Expected: push lands on `origin/main`; the last three `gh run list` rows show the previous green runs plus a queued run for the new commit. Wait ~90 seconds and re-run `gh run list --limit 3 --branch main`; the new run must be `completed success` before starting Task 2.

---

### Task 2: Slice 2 — `03_bad_address`

**Files:**
- Create: `scenarios/03_bad_address.yaml`
- Create (generated): `transcripts/03_bad_address.md`

- [ ] **Step 1: Write the scenario YAML**

Create `scenarios/03_bad_address.yaml`:

```yaml
name: 03_bad_address
description: >
  Dropoff address "123 Main St" is ambiguous (confidence 0.3, two
  alternates).  Agent surfaces alternates; user picks the Anytown, CA
  one and confirms.  Booking proceeds.
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

- [ ] **Step 2: Render + verify invariants pass**

Run:

```bash
make reset
uv run python -m agentic_cab render-transcripts
```

Expected: tail shows `03_bad_address.yaml → session …` followed by five PASS lines plus the previously-passing 01/02/04 results. Process exits 0.

**Likely failure mode #1:** the agent doesn't call `geocode_address` on "123 Main St" and proceeds with a fabricated lat/lng. Symptom: `four_row_pattern` PASS but the rendered transcript shows no `geocode_address` action and the agent invented a destination. Fix: change turn 1 to *"Book me a ride from 37.7749,-122.4194 (Market & Powell) to 123 Main St — please look that up first."*

**Likely failure mode #2:** the agent geocodes, sees low confidence, and asks for clarification — but stops without taking the user's reply seriously (uses the alternate's text but not the lat/lng). Symptom: confirm goes green but pickup/dropoff coords in the action log don't match the user's chosen alternate. Fix: tighten turn 2 to include the lat/lng explicitly (already does in this draft); if still failing, add `"Use lat=34.0522, lng=-118.2437."`

Re-render after each wording change. Max two attempts before escalating per spec.

- [ ] **Step 3: Run the test gate**

```bash
make verify
```

Expected: same passing count as Task 1.

- [ ] **Step 4: Commit**

```bash
git add scenarios/03_bad_address.yaml transcripts/03_bad_address.md
git commit -m "$(cat <<'EOF'
feat(P4): add 03_bad_address scenario + transcript

Three-turn disambiguation scenario.  User asks for "123 Main St" as
the dropoff; the geocoder returns confidence 0.3 with two alternates
(Anytown CA, Smallville KS).  Agent surfaces the alternates; user
picks Anytown CA by name + lat/lng; booking proceeds.

Exercises the system-prompt rule "if confidence < 0.5 and alternates,
ask the user to clarify" against the committed geocode fixture
(fixtures/geocode/07a22fb7…json).  No new fixtures, no provider
changes.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: Ship**

```bash
make ship
```

Expected: push lands; wait for new CI run to be `completed success` before Task 3.

---

### Task 3: Slice 3 — `05_cancel_with_fee`

**Files:**
- Create: `scenarios/05_cancel_with_fee.yaml`
- Create (generated): `transcripts/05_cancel_with_fee.md`

- [ ] **Step 1: Confirm cancel summary string shape (pre-flight read)**

The `match:` substring for the cancel confirmation depends on what
`cancel_booking` puts in `ConfirmationRequest.summary`.  Already verified
in plan preparation:

```python
# src/agentic_cab/tools/cancel.py:207
summary=(
    f"Cancel {booking.summary.title}? Fee: ${fee.amount_cents / 100:.2f} {fee.currency}"
),
```

So `match: "Cancel "` (note the trailing space to avoid matching the
`"UberX"` summary on a re-entry) is safe.  No source change needed.
Skip this step if the cancel.py file is unchanged from commit `513c4ea`.

- [ ] **Step 2: Write the scenario YAML**

Create `scenarios/05_cancel_with_fee.yaml`:

```yaml
name: 05_cancel_with_fee
description: >
  User books an UberX, then asks to cancel.  Agent quotes the fee,
  user approves, booking is cancelled.  Exercises the second gated
  tool (cancel_booking) and confirms the four-row log pattern is
  emitted for it as well.
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
    - match: "Cancel "
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
    where: {kind: "outcome", tool: "cancel_booking", status: "cancelled"}
```

- [ ] **Step 3: Render + verify invariants pass**

```bash
make reset
uv run python -m agentic_cab render-transcripts
```

Expected: tail shows `05_cancel_with_fee.yaml → session …` followed by five PASS lines.  Process exits 0.

**Likely failure mode:** the scripted channel matches the FIRST script entry whose `match` substring appears in the summary.  If the channel's matching is unordered and the cancel summary's title field includes "UberX" (because the title encodes the ride class), the `"UberX"` entry could match the cancel prompt and approve it — possibly leading to a double-approve of the wrong thing.  Pre-flight check by reading `ScriptedConfirmationChannel` if Step 3 surfaces unexpected behaviour; document any fix in the commit body.

- [ ] **Step 4: Run the test gate**

```bash
make verify
```

Expected: same passing count.

- [ ] **Step 5: Commit**

```bash
git add scenarios/05_cancel_with_fee.yaml transcripts/05_cancel_with_fee.md
git commit -m "$(cat <<'EOF'
feat(P4): add 05_cancel_with_fee scenario + transcript

Three-turn scenario: user books an UberX, then asks to cancel.  Agent
quotes the cancellation fee via cancel_booking's confirmation gate;
the scripted channel approves; cancel_booking emits its own four-row
log pattern (requested → verified → executed → outcome) and the
booking lands with status="cancelled".

Two-entry scripted confirmation script: first match "UberX" (booking),
then match "Cancel " (cancellation).  No source changes — cancel_booking
already emits outcome.status="cancelled" and surfaces a "Cancel {title}?
Fee: $X.YY USD" summary string.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6: Ship**

```bash
make ship
```

Expected: push lands; wait for new CI run to be `completed success` before Task 4.

---

### Task 4: Slice 4 — Scenario validator test

**Files:**
- Create: `tests/scenarios/test_invariant_checkers.py` (rename target — contents moved from current `test_scenarios.py`)
- Create: `tests/scenarios/test_scenarios.py` (new content, replaces existing file)

- [ ] **Step 1: Rename the existing checker unit tests**

The current `tests/scenarios/test_scenarios.py` is pure unit tests of
the checker functions (`check_four_row_pattern`, `check_no_double_booking`,
etc.).  Its purpose is "are the checker functions correct given a
known row list".  Rename to make that purpose explicit:

```bash
git mv tests/scenarios/test_scenarios.py tests/scenarios/test_invariant_checkers.py
```

Do NOT edit the file contents in this step.

- [ ] **Step 2: Run the renamed file to verify it still passes**

```bash
uv run pytest tests/scenarios/test_invariant_checkers.py -v
```

Expected: four PASS lines (`test_four_row_happy_path_passes`,
`test_rejected_path_does_not_require_executed`,
`test_double_booking_detected`,
`test_replayed_executed_does_not_count_as_double_booking`).

- [ ] **Step 3: Write the new parameterized validator (failing first)**

Create `tests/scenarios/test_scenarios.py`:

```python
"""Parameterized validator for `scenarios/*.yaml`.

Catches a scenario added with a typo'd invariant kind, a missing
required schema field, or a non-list `user_turns`.  Runs in
`make test-fast` (no LLM, no provider, no network).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agentic_cab.scenarios.invariants import CHECKERS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCENARIOS_DIR = _REPO_ROOT / "scenarios"

_REQUIRED_TOP_LEVEL_KEYS = (
    "name",
    "clock",
    "sim",
    "confirmation",
    "user_turns",
    "invariants",
)


def _scenario_paths() -> list[Path]:
    return sorted(_SCENARIOS_DIR.glob("*.yaml"))


def _scenario_ids(paths: list[Path]) -> list[str]:
    return [p.name for p in paths]


_PATHS = _scenario_paths()


@pytest.mark.parametrize("scenario_path", _PATHS, ids=_scenario_ids(_PATHS))
def test_scenario_has_required_top_level_keys(scenario_path: Path) -> None:
    scn = yaml.safe_load(scenario_path.read_text())
    assert isinstance(scn, dict), f"{scenario_path.name}: YAML root must be a mapping"
    missing = [k for k in _REQUIRED_TOP_LEVEL_KEYS if k not in scn]
    assert not missing, f"{scenario_path.name} missing required keys: {missing}"


@pytest.mark.parametrize("scenario_path", _PATHS, ids=_scenario_ids(_PATHS))
def test_scenario_user_turns_is_nonempty_string_list(scenario_path: Path) -> None:
    scn = yaml.safe_load(scenario_path.read_text())
    user_turns = scn.get("user_turns")
    assert isinstance(user_turns, list) and user_turns, (
        f"{scenario_path.name}: user_turns must be a non-empty list"
    )
    assert all(isinstance(t, str) for t in user_turns), (
        f"{scenario_path.name}: every user_turns entry must be a string"
    )


@pytest.mark.parametrize("scenario_path", _PATHS, ids=_scenario_ids(_PATHS))
def test_scenario_confirmation_has_channel(scenario_path: Path) -> None:
    scn = yaml.safe_load(scenario_path.read_text())
    conf = scn.get("confirmation")
    assert isinstance(conf, dict) and "channel" in conf, (
        f"{scenario_path.name}: confirmation.channel is required"
    )


@pytest.mark.parametrize("scenario_path", _PATHS, ids=_scenario_ids(_PATHS))
def test_scenario_invariant_kinds_are_known(scenario_path: Path) -> None:
    scn = yaml.safe_load(scenario_path.read_text())
    invariants = scn.get("invariants") or []
    unknown = [inv["kind"] for inv in invariants if inv["kind"] not in CHECKERS]
    assert not unknown, (
        f"{scenario_path.name} references unknown invariant kinds: {unknown}. "
        f"Known kinds: {sorted(CHECKERS)}"
    )


def test_scenarios_dir_is_nonempty() -> None:
    """Sanity check — without this the parametrized tests run zero cases silently."""
    assert _PATHS, "no scenarios found under scenarios/"
```

- [ ] **Step 4: Run the new tests to verify they pass against the four committed scenarios**

```bash
uv run pytest tests/scenarios/test_scenarios.py -v
```

Expected: 4 scenarios × 4 parametrized tests + 1 sanity check = 17 PASS lines (or 16 + 1 depending on parametrize id rendering; the count is `len(_PATHS) * 4 + 1`).

If a FAIL appears here, the corresponding scenario YAML has a real defect — fix the YAML, not the test.

- [ ] **Step 5: Add a negative-path regression test**

To pin the validator's value, add a test that proves a typo'd invariant kind would be caught.  Append to `tests/scenarios/test_scenarios.py`:

```python


def test_validator_catches_unknown_invariant_kind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A scenario referencing a non-existent invariant kind must fail the validator."""
    bad_dir = tmp_path / "scenarios"
    bad_dir.mkdir()
    bad = {
        "name": "bad",
        "clock": {"start": "2026-05-19T12:00:00+00:00", "mode": "frozen"},
        "sim": {"rng_seed": 0},
        "confirmation": {"channel": "scripted", "script": []},
        "user_turns": ["hi"],
        "invariants": [{"kind": "no_such_checker"}],
    }
    bad_path = bad_dir / "99_bad.yaml"
    bad_path.write_text(yaml.safe_dump(bad))

    scn = yaml.safe_load(bad_path.read_text())
    unknown = [inv["kind"] for inv in scn["invariants"] if inv["kind"] not in CHECKERS]
    assert unknown == ["no_such_checker"]
```

This is intentionally not parametrized — it asserts the validator's
machinery (the `not in CHECKERS` predicate) is the right one, even if
no real scenario file currently triggers it.

- [ ] **Step 6: Run the full test gate**

```bash
make verify
```

Expected: all green; new tests counted in the pass total.

- [ ] **Step 7: Commit**

```bash
git add tests/scenarios/test_invariant_checkers.py tests/scenarios/test_scenarios.py
git commit -m "$(cat <<'EOF'
test(P4): split scenario tests into checker units + YAML validator

The old tests/scenarios/test_scenarios.py was pure unit tests of the
invariant checker functions — its name promised more than it
delivered.  Rename it to test_invariant_checkers.py (contents
unchanged) and reclaim the test_scenarios.py name for what P4 §"DOD"
actually asks for: a parameterized validator over scenarios/*.yaml
that asserts schema shape and invariant-kind references are sound.

Four parameterized tests cover every scenario:
  - required top-level keys present
  - user_turns is a non-empty list of strings
  - confirmation.channel is set
  - every invariant.kind references a known CHECKER

Plus one negative-path test that pins the validator catches a
typo'd kind, and a sanity check that the parametrize fixture
never silently runs zero cases.

Runs in make test-fast (no LLM).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 8: Ship**

```bash
make ship
```

Wait for CI green before Task 5.

---

### Task 5: Refresh WRITEUP for P4 completion

**Files:**
- Modify: `docs/WRITEUP.md`

- [ ] **Step 1: Read the current WRITEUP to find the scenario-count claim**

```bash
grep -n -E 'scenario|transcript' docs/WRITEUP.md | head -20
```

Locate the sentence(s) that state how many scenarios are shipped (likely refers to 1–2 of 5).  Identify the exact phrase that needs updating.

- [ ] **Step 2: Edit WRITEUP to reflect 5/5 scenarios shipped**

Use `Edit` to replace the specific sentence.  Example pattern (the actual `old_string` depends on Step 1 output):

```
Old: "Two of five scenarios are rendered today (01_happy_path, 04_surge_on_confirm); the other three are P4."
New: "All five scenarios render end-to-end with green invariants."
```

If the WRITEUP doesn't have a literal count and just lists scenarios qualitatively, refresh that list to include 02_price_comparison, 03_bad_address, and 05_cancel_with_fee in the appropriate section.

Do NOT rewrite the rest of the WRITEUP in this commit — keep the diff to scenario-count claims only.  Other content updates (e.g. new lessons learned during P4) belong in a separate commit.

- [ ] **Step 3: Verify the doc still passes the markdown-link hook**

```bash
make verify
```

Expected: still green (no test changes; the `relative markdown links must resolve` pre-commit hook will run when committing).

- [ ] **Step 4: Commit**

```bash
git add docs/WRITEUP.md
git commit -m "$(cat <<'EOF'
docs: refresh WRITEUP for P4 completion

All five scenarios now render with green invariants (02, 03, 05 landed
in this phase; 01 and 04 were already shipped).  Update the writeup's
scenario-count claims to match.  No other content changes.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: Ship**

```bash
make ship
```

Wait for CI green.  P4 is now complete.

---

## Post-implementation verification

After Task 5 ships, run these to confirm the phase contract is met:

- [ ] `ls scenarios/*.yaml | wc -l` → `5`
- [ ] `ls transcripts/*.md | grep -v README | wc -l` → `5`
- [ ] `make reset && uv run python -m agentic_cab render-transcripts` → all PASS lines, exit 0
- [ ] `git log --oneline --grep='(P4)'` → at least 3 commits (one per scenario slice; slice 4 is `test(P4):`; slice 5 is `docs:` with no phase scope)
- [ ] `gh run list --limit 5 --branch main` → most recent five all `completed success`

If any of these fail, do not declare P4 done.  Open a follow-up entry in `docs/FOLLOWUPS.md` describing the gap.

---

## Follow-ups that may surface during execution

Add to `docs/FOLLOWUPS.md` as they appear; do not pre-commit here.

- Scripted channel `match` substring is positional and case-sensitive; consider a `match_kind: substring|regex|exact` field for richer patterns once a scenario needs it.
- `make transcripts` is end-to-end and slow (~30–60 seconds per scenario at `claude-opus-4-7` latency); a "scenarios-affected" subset target (e.g. `make transcript NAME=05_cancel_with_fee`) would help iteration loops.
- `tests/scenarios/test_scenarios.py` could grow a check that every scenario's transcript exists under `transcripts/` and is at most N minutes older than its YAML (catches a forgotten regen).  Defer until a regen lag actually bites.
