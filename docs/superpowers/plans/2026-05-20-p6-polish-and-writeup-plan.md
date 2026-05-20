# P6 — Polish + Writeup + Final Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close P6 — write `docs/ARCHITECTURE.md`, rewrite `docs/WRITEUP.md` as a pure 1-pager, polish `README.md` with clone-to-run validation, re-render transcripts, and finalize CI green on `main`.

**Architecture:** Five sequential slices, each one a single `make ship`-able commit. ARCHITECTURE.md and WRITEUP.md are independent (do 1 then 2); README polish depends on both; transcript re-render runs last before the closeout gate. Each slice's verification command is given inline.

**Tech Stack:** Markdown + mermaid (rendered inline by GitHub). Docker for clone-to-run proxy. `make ship` for push + CI evidence.

**Spec:** `docs/superpowers/specs/2026-05-20-p6-polish-and-writeup-design.md`

---

## Pre-flight (one-time)

- [ ] **Step 0.1: Confirm clean working tree on `main`**

  Run: `git status --short && git rev-parse --abbrev-ref HEAD`
  Expected: empty modified list (or only the pre-P6 transcript stash, which stays stashed); branch `main`.

  If non-empty besides the stash: stop, surface to user.

- [ ] **Step 0.2: Confirm CI green for HEAD**

  Run: `gh run list --limit 1 --branch main`
  Expected: `completed	success	docs(P6): switch ARCHITECTURE.md layer diagram to mermaid…` (or later) — most recent run is green.

---

## Task 1: Write `docs/ARCHITECTURE.md`

**Files:**
- Create: `docs/ARCHITECTURE.md`

Pulls structure straight from §4.1 of the spec. Mermaid layer diagram + two markdown tables + persistence-split paragraph + pointers. Target 150–250 lines.

- [ ] **Step 1.1: Read the port surface so file paths and responsibilities are accurate**

  Run: `grep -n '^class' src/agentic_cab/core/ports.py`
  Note: file paths and Protocol names you'll reference (`BookingProvider`, `ConfirmationChannel`, `Clock`, `ActionLog`, `StateStore` — the first five Protocols; skip `GeocoderPort` for the headline table or include as a sixth row, see Step 1.3).

  Run: `ls src/agentic_cab/adapters/`
  Note: today's adapter directories (`confirmation/`, `geocoding/`, `mock_appointment/`, `uber_sim/`, plus `action_log.py`, `clock.py`, `state_store.py`).

- [ ] **Step 1.2: Create `docs/ARCHITECTURE.md` with the full content below**

  Use this exact skeleton verbatim. Two encoding notes:
  - Outer fence is four backticks because the inner mermaid block uses three.
  - Every `](` in markdown links inside the skeleton is written as `]&#40;` (and the matching `)` as `&#41;`) so our `scripts/check_links.py` hook — which scans raw text and does not respect code fences — does not parse the skeleton's example links as live. **When you paste this into `docs/ARCHITECTURE.md`, replace `&#40;` with `(` and `&#41;` with `)` in every link.** GitHub will render the plan correctly either way; only the final committed `ARCHITECTURE.md` needs the literal parens.

  ````markdown
  # Architecture

  Agentic Cab Rides is a layered Python service: a thin CLI/web shell drives an
  Anthropic-SDK-powered agent, which calls deterministic tool functions, which
  talk to swappable adapters through narrow ports. Every layer depends only on
  the layer below it; nothing reaches across. This document maps that structure,
  the ports that separate the layers, and where each invariant in `CLAUDE.md`
  is enforced.

  For the full design rationale see
  [`docs/superpowers/specs/2026-05-19-agentic-cab-rides-design.md`]&#40;superpowers/specs/2026-05-19-agentic-cab-rides-design.md&#41;.
  For the 1-pager deliverable see [`docs/WRITEUP.md`]&#40;WRITEUP.md&#41;.

  ## Layer diagram

  ```mermaid
  graph TD
      CLI["CLI shell<br/><code>src/agentic_cab/__main__.py</code>"]
      Web["Web shell<br/><code>src/agentic_cab/web/</code>"]
      Agent["Agent loop<br/><code>src/agentic_cab/agent/chat.py</code>"]
      Tools["Tools<br/><code>src/agentic_cab/tools/*</code>"]
      Ports["Ports (Protocols)<br/><code>src/agentic_cab/core/ports.py</code>"]
      Adapters["Adapters<br/><code>src/agentic_cab/adapters/*</code>"]

      CLI --> Agent
      Web --> Agent
      Agent --> Tools
      Tools --> Ports
      Ports --> Adapters
  ```

  Arrows point only down. The agent never imports an adapter directly; it only
  calls tools, and tools take ports (not concrete classes) as parameters.

  ## Ports

  | Port | File | Responsibility | Implementations today |
  |------|------|----------------|----------------------|
  | `BookingProvider` | `core/ports.py` | Marketplace adapter: `discover` / `prepare` / `confirm` / `cancel`. Idempotency key required on every mutation. | `UberSimAdapter`, `MockAppointmentProvider` |
  | `ConfirmationChannel` | `core/ports.py` | Owns the y/n decision for every state-mutating action. The agent cannot self-approve. | `CLIConfirmationChannel` (TTY), `WebConfirmationChannel` (SSE + POST), `ScriptedConfirmationChannel` (scenarios) |
  | `ActionLog` | `core/ports.py` | Append-only JSONL emission, four rows per gated action. PII redacted at the boundary. | `JSONLActionLog` |
  | `StateStore` | `core/ports.py` | SQLite-backed durable state: drafts, bookings, booking_events, idempotency_keys, pending_confirmations. | `SqliteStateStore` |
  | `Clock` | `core/ports.py` | Now-source. Real time in interactive mode, frozen in scenario mode for determinism. | `SystemClock`, `FrozenClock` |
  | `GeocoderPort` | `core/ports.py` | Address → `{formatted, lat, lng, confidence}`. Cached. | `InMemoryGeocoder`, `MapboxGeocoderAdapter` |

  ## Invariants and where they live

  Each row maps a `CLAUDE.md` invariant to the layer that enforces it.
  This is the audit map: if you want to verify the project still honours
  an invariant, read the file in the "Enforced in" column.

  | # | Invariant (abbrev.) | Enforced in |
  |---|---------------------|-------------|
  | 1 | Strict layer dependency direction | This document + code review; no import lint today |
  | 2 | Structural confirmation gate | `tools/confirm.py`, `tools/cancel.py`, `core/ports.py::ConfirmationChannel`, `adapters/confirmation/*` |
  | 3 | Fingerprint check before every gated provider call | `tools/confirm.py` (`prepare`→`confirm` pair), `core/fingerprint.py` |
  | 4 | Re-quote before confirm; drift threshold | `tools/confirm.py` (post-fingerprint, pre-gate) |
  | 5 | Idempotency keys required | `core/ports.py::BookingProvider`, `adapters/state_store.py::claim_or_recall`, schema UNIQUE constraint |
  | 6 | Four-row action log per gated action | `tools/confirm.py`, `tools/cancel.py`, `adapters/action_log.py` |
  | 7 | Persistence split (SQLite vs JSONL) | `adapters/state_store.py`, `adapters/action_log.py` |
  | 8 | PII redaction at the log boundary | `adapters/action_log.py::redact` |
  | 9 | Determinism in scenarios | `adapters/clock.py::FrozenClock`, `core/constants.py::MODEL_ID`, seeded RNGs |
  | 10 | `Money(amount_cents, currency)`, never float | `core/types.py::Money` |
  | 11 | ULIDs for all IDs | `core/ids.py` |
  | 12 | No `--no-verify` | `.claude/settings.json` deny list, pre-push hook |
  | 13 | No `TODO`/`FIXME`/`XXX` in code | Ruff `FIX` rule, pre-commit |
  | 14 | No `print()` outside CLI/web/scripts | Ruff `T20` rule |

  ## Persistence split

  Two durable surfaces, deliberately not merged:

  - **SQLite (`data/state.db`)** holds anything that needs relational lookups
    or transactional guarantees: drafts, bookings, booking_events,
    idempotency_keys, pending_confirmations. The UNIQUE constraint on
    `idempotency_keys` is the load-bearing guarantee against double-bookings;
    keeping it in SQL means it survives crashes and is enforced by the
    database, not application code.
  - **JSONL (`logs/<session_id>.jsonl`, `chat/`, geocode cache)** holds
    append-only audit artifacts. Flat files because the audit story depends
    on being trivially `grep`-able, `tail -f`-able, and replayable without
    a database. The action log is a deliverable surface — its format and
    flatness matter externally, not just internally.

  Moving the action log into SQLite would lose this. The split is intentional
  and described in `CLAUDE.md` invariant 7.

  ## See also

  - [`docs/WRITEUP.md`]&#40;WRITEUP.md&#41; — the 1-pager deliverable.
  - [`docs/PHASES.md`]&#40;PHASES.md&#41; — phase contract.
  - [`docs/superpowers/specs/2026-05-19-agentic-cab-rides-design.md`]&#40;superpowers/specs/2026-05-19-agentic-cab-rides-design.md&#41;
    — full design spec.
  ````

- [ ] **Step 1.3: If `GeocoderPort` is present in `core/ports.py` (it is), include it as a sixth port row; otherwise drop the row**

  The skeleton above already includes it. If your `grep` from step 1.1 shows it missing, delete that row. Otherwise no change.

- [ ] **Step 1.4: Verify markdown links resolve**

  Run: `uv run pre-commit run relative-markdown-links --files docs/ARCHITECTURE.md`
  Expected: `Passed`.

  If it fails, fix the broken link before continuing. The most likely cause is the spec link — confirm `docs/superpowers/specs/2026-05-19-agentic-cab-rides-design.md` exists.

- [ ] **Step 1.5: Eyeball the mermaid block locally**

  Run: `grep -n 'graph TD' docs/ARCHITECTURE.md`
  Expected: one match. Mermaid in markdown is rendered by GitHub on push; we don't need a local renderer.

- [ ] **Step 1.6: Commit**

  ```bash
  git add docs/ARCHITECTURE.md
  git commit -m "$(cat <<'EOF'
  docs(P6): add ARCHITECTURE.md — layer diagram + port table + invariants map

  Terse architecture doc per the P6 spec.  Mermaid layer diagram (rendered
  inline by GitHub), markdown tables for ports and invariants-to-layer map,
  one paragraph on the persistence split.  No per-port walkthroughs or
  sequence diagrams — that depth was explicitly rejected in brainstorming.

  Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
  EOF
  )"
  ```

- [ ] **Step 1.7: Ship**

  Run: `make ship`
  Expected: `Passed` on every pre-push hook; `gh run list` shows the new run starting.

---

## Task 2: Rewrite `docs/WRITEUP.md`

**Files:**
- Modify: `docs/WRITEUP.md` (full rewrite)

Drop phase scaffolding. Target ≤ 60 lines (≤ 1 printed page). Sections per spec §4.2: marketplace swap, what breaks first, what I'd build next.

- [ ] **Step 2.1: Read the current writeup so you keep load-bearing nuance**

  Run: `cat docs/WRITEUP.md`
  Note: keep the contract-suite-as-proof sentence, the Lyft-vs-Zocdoc argument, the cross-session idempotency gap, the priority-ordered "what I'd build next" list.

- [ ] **Step 2.2: Overwrite `docs/WRITEUP.md` with the new content**

  Replace the file's full contents with:

  ```markdown
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

  **Cross-session idempotency.** Inside a session, intent-bound keys
  (`sha256({draft_id, fingerprint, op:"confirm"})`) make agent retries
  safe — `claim_or_recall` short-circuits the second provider call. But a
  fresh process that re-sends the same user intent generates a new
  `draft_id` and thus a new key. A user who repeats their request after a
  crash can double-book. The fix is to store the intent hash independently
  of the draft, derived from the user's stable inputs (pickup, dropoff,
  ride class). Tracked in `docs/FOLLOWUPS.md`; this is the first thing I
  would harden.

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

  1. **Cross-session idempotency** — store the intent hash at the tool
     layer, not derived from `draft_id`. Closes the double-booking gap.
  2. **Background `track_booking` polling loop** — closes agent ↔ reality
     state drift.
  3. **Webhook / Slack `ConfirmationChannel`** — proves the third channel
     works after CLI + web; closes the abstraction.
  4. **Real `LyftAdapter`** — empirical proof the contract holds against a
     second real provider, not just a second mock.
  5. **Payment authorization flow** — pre-confirm scope grant.

  ---

  *See [`docs/ARCHITECTURE.md`]&#40;ARCHITECTURE.md&#41; for layer + port structure
  and [`docs/superpowers/specs/2026-05-19-agentic-cab-rides-design.md`]&#40;superpowers/specs/2026-05-19-agentic-cab-rides-design.md&#41;
  for the full design.*
  ```

- [ ] **Step 2.3: Sanity-check line count and link resolution**

  Run: `wc -l docs/WRITEUP.md`
  Expected: ≤ 80 lines (target ~60; small overage acceptable).

  Run: `uv run pre-commit run relative-markdown-links --files docs/WRITEUP.md`
  Expected: `Passed`.

- [ ] **Step 2.4: Verify no load-bearing nuance was dropped**

  Run: `git diff docs/WRITEUP.md | grep '^-' | grep -iE 'contract|intent|fingerprint|idempot' | head -20`
  Expected: nothing surprising. If a removed line mentions one of those four concepts but the new version doesn't, restore the substance (not the prose).

- [ ] **Step 2.5: Commit**

  ```bash
  git add docs/WRITEUP.md
  git commit -m "$(cat <<'EOF'
  docs(P6): rewrite WRITEUP.md as pure 1-pager — drop phase scaffolding

  Remove the "Status: P4 complete; P5 shipped" framing and per-subsystem
  "shipped (Tnn)" callouts.  By P6 everything is shipped; the callouts
  added noise and dated the doc.  Preserves the load-bearing technical
  content: Lyft-trivial / Zocdoc-stress argument, contract-suite-as-proof,
  cross-session idempotency as the first production gap, priority-ordered
  next-steps list.

  Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
  EOF
  )"
  ```

- [ ] **Step 2.6: Ship**

  Run: `make ship`
  Expected: green.

---

## Task 3: Polish `README.md` + clone-to-run validation

**Files:**
- Modify: `README.md`

Two surgical edits in the file (Status section + quickstart heading), plus a separate command that validates the quickstart end-to-end. The Docker proxy command's timing goes into the commit message, not the README.

- [ ] **Step 3.1: Replace the stale Status section**

  Edit `README.md`. Replace the block:

  ```markdown
  ## Status

  **P0 — Harness:** shipped. Tooling, hooks, skills, invariants, phase contract.
  **P1 — Tracer bullet:** next. See [`docs/PHASES.md`]&#40;docs/PHASES.md&#41;.

  Tracker = `git log --oneline --grep='(P[0-9])'`. There is no status file; status lives in commits.
  ```

  with:

  ```markdown
  ## Status

  Working today: REPL agent (`make repl`), web UI (`make web`), 5 end-to-end
  scenarios with replayable action logs, a contract suite that proves the
  `BookingProvider` port survives a non-ride second adapter, and a green
  `make verify` gate on every push.

  Tracker = `git log --oneline --grep='(P[0-9])'`. There is no status file; status lives in commits.
  ```

- [ ] **Step 3.2: Strip the P6 qualifier from the quickstart heading**

  Edit `README.md`. Change:

  ```markdown
  ## Quickstart *(will be validated end-to-end in P6)*
  ```

  to:

  ```markdown
  ## Quickstart
  ```

- [ ] **Step 3.3: Run the Docker-proxy clone-to-run validation**

  Run (substitute `$(pwd)` for your repo root):

  ```bash
  docker run --rm -v "$(pwd)":/repo -w /repo python:3.12-slim bash -c '
    set -e
    apt-get update -qq && apt-get install -y -qq curl git make
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="/root/.local/bin:$PATH"
    time uv sync
    uv run python -m agentic_cab --help
  '
  ```

  Expected: `uv sync` completes; `python -m agentic_cab --help` prints a usage banner; total elapsed wall clock under 3 minutes on a normal network. **Record the actual elapsed time** — you'll cite it in the commit message at step 3.5.

  If the container can't reach PyPI or the apt mirror, do not fudge — record the failure mode in the commit message instead and stop here for user input.

- [ ] **Step 3.4: Final link check**

  Run: `uv run pre-commit run relative-markdown-links --files README.md`
  Expected: `Passed`.

- [ ] **Step 3.5: Commit**

  Replace `<ELAPSED>` with the actual `real` wall-clock from step 3.3.

  ```bash
  git add README.md
  git commit -m "$(cat <<'EOF'
  docs(P6): refresh README Status section + validate clone-to-run

  Status section was stale ("P0 shipped, P1 next").  Replaced with a
  2-line description of what works today; phase tracker pointer kept.
  Stripped "(will be validated end-to-end in P6)" qualifier from the
  Quickstart heading — P6 is the validation pass.

  Clone-to-run validated via Docker proxy
  (python:3.12-slim + uv install + uv sync + module import):
  wall-clock <ELAPSED>, well under the 3-minute acceptance bullet.

  Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
  EOF
  )"
  ```

- [ ] **Step 3.6: Ship**

  Run: `make ship`
  Expected: green.

---

## Task 4: Final transcript re-render

**Files:**
- Possibly modify: `transcripts/01_happy_path.md`, `transcripts/02_price_comparison.md`, `transcripts/03_bad_address.md`, `transcripts/04_surge_on_confirm.md`, `transcripts/05_cancel_with_fee.md`

Re-render. Commit only material content differences. Pure session-id / ULID churn is discarded.

- [ ] **Step 4.1: Confirm the pre-P6 transcript stash is still parked**

  Run: `git stash list | grep -i 'uncommitted transcripts (pre-flake-fix workspace state)'`
  Expected: one match. Leave it stashed. It is not the canonical re-render.

- [ ] **Step 4.2: Re-render all transcripts**

  Run: `make transcripts`
  Expected: each scenario renders without error; transcripts under `transcripts/NN_*.md` are rewritten in place. This invokes the live LLM via `ANTHROPIC_API_KEY` — make sure your env is wired (`make link-env`).

  If `make transcripts` fails on a transient LLM error, retry up to twice. Persistent failure → stop and surface to user; do not "no-op-close" through a broken render.

- [ ] **Step 4.3: Classify each diff**

  Run: `git diff --stat transcripts/`
  Then for each modified file:

  ```bash
  git diff transcripts/<file>
  ```

  For each file, classify the diff as:
  - **id-churn only** — only `session_id=01J…`, timestamps, ULID-shaped substrings, or random-tail noise changed. Discard: `git checkout -- transcripts/<file>`.
  - **material content change** — different wording, different option ordering, different invariant assertion, different number of conversation turns. Keep for the commit.

  Material = "would a reader form a different impression of what the agent does". When in doubt, **prefer discard** — churn pollutes the audit trail.

- [ ] **Step 4.4a: If any material diffs survived → commit them**

  ```bash
  git add transcripts/
  git commit -m "$(cat <<'EOF'
  docs(P6): final transcript re-render — keep material diffs only

  Ran `make transcripts` end-to-end against the pinned model.  Files
  staged here had content (not id/timestamp) changes — kept.  Files
  with id-churn only were discarded per the P6 spec policy
  (same lesson as 8492eb6).

  Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
  EOF
  )"
  ```

  Run: `make ship`
  Expected: green; CI run kicks off.

- [ ] **Step 4.4b: If no material diffs survived → no-op closeout commit**

  ```bash
  git commit --allow-empty -m "$(cat <<'EOF'
  docs(P6): final transcript re-render — no material diffs

  Re-ran `make transcripts` end-to-end; every diff was id/timestamp churn
  only and was discarded per the P6 spec policy.  Recording this empty
  commit as the audit trail entry that the re-render pass was performed.

  Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
  EOF
  )"
  ```

  Run: `make ship`
  Expected: green.

- [ ] **Step 4.5: Drop the pre-P6 transcript stash**

  Run: `git stash drop $(git stash list | grep 'uncommitted transcripts (pre-flake-fix workspace state)' | head -1 | cut -d: -f1)`
  Expected: one stash dropped. If `git stash list` no longer shows that entry, skip.

---

## Task 5: Closeout — CI green gate

**Files:**
- None (verification only).

This is the meta-gate. The push from Task 4 should have produced a fresh CI run; confirm it goes green.

- [ ] **Step 5.1: Wait for the latest run to complete**

  Run: `gh run list --limit 1 --branch main`
  If `status` is `in_progress`, wait (do not poll in a tight loop). When complete:
  Expected: most recent run is `completed	success`.

- [ ] **Step 5.2: Verify `make verify` is clean on the up-to-date `main`**

  Run: `make verify`
  Expected: every step (`fmt-check`, `lint`, `typecheck`, `test-fast`) passes; final line is the test summary with zero failures.

- [ ] **Step 5.3: Cross-check P6 acceptance against `docs/PHASES.md`**

  Open `docs/PHASES.md` §P6. For each acceptance bullet, confirm it is satisfied:
  - [ ] Fresh clone + `uv sync` + `make repl` ≤ 3 min — verified by Task 3 step 3.3.
  - [ ] `docs/ARCHITECTURE.md` written — verified by Task 1.
  - [ ] `docs/WRITEUP.md` written — verified by Task 2.
  - [ ] `README.md` quickstart validated — verified by Task 3.
  - [ ] All 5 transcripts re-rendered — verified by Task 4 (material commits or no-op evidence commit).
  - [ ] `make verify` clean on `main` — verified by step 5.2.
  - [ ] `make transcripts-ci` green in CI — verified by step 5.1.

  If any bullet fails, return to the relevant task. Do not declare P6 done with a red bullet.

- [ ] **Step 5.4: Surface completion to the user**

  Plain text: "P6 acceptance contract satisfied. HEAD = `<sha>`. CI = green
  (`<run-id>`). All 7 bullets in `docs/PHASES.md` §P6 verified."

---

## Self-review notes (author check, not for the executor)

- **Spec coverage:** §4.1 → Task 1; §4.2 → Task 2; §4.3 → Task 3 steps 3.1–3.2; §4.4 → Task 3 step 3.3 (Docker proxy); §4.5 → Task 4; §4.6 → Task 5.
- **No placeholders:** every code block is concrete; every command has expected output.
- **Type/name consistency:** the port names (`BookingProvider`, `ConfirmationChannel`, `ActionLog`, `StateStore`, `Clock`, `GeocoderPort`) match `core/ports.py` line scrape; invariant numbering matches `CLAUDE.md`.
- **Out-of-scope guard:** no plan task adds tests, features, or scenarios — P6 is documentation + verification per the spec.
