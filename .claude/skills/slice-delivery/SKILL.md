---
name: slice-delivery
description: Disciplined vertical-slice delivery for this repo. Use when planning, implementing, reviewing, or merging any non-trivial change. Each slice crosses every layer end-to-end (port → adapter → tool → test → scenario evidence) rather than horizontally completing one layer at a time. Triggers on "slice", "tracer bullet", "deliver", "implement", "build-progress", "Ralph loop".
---

# slice-delivery

The execution wrapper for this repo. Read `CLAUDE.md` and `docs/superpowers/specs/2026-05-19-agentic-cab-rides-design.md` before invoking; they own the contracts. This skill owns the *process*.

## Source-of-truth order

1. `docs/superpowers/specs/2026-05-19-agentic-cab-rides-design.md` — design contract. The shape of types, ports, tools, log, gate, scenarios.
2. `CLAUDE.md` — invariants every session must respect.
3. `docs/PHASES.md` — **static** contract: Definition of Done + phase acceptance bullets. Does not track status; status lives in `git log` and `make verify`.
4. `docs/FOLLOWUPS.md` — durable follow-ups; the only place "TODO"-class items are allowed to live.

If a slice's plan conflicts with the spec, **update the spec first** (in a separate commit) or explicitly defer and note in `FOLLOWUPS.md`.

## Three pillars

### 1. Velocity — slices, not layers

- **Slice = one PR-sized commit (or short branch) that crosses every layer.** Not "all the types this week, all the adapters next week." A slice produces working behavior end-to-end, however thin.
- **Tracer bullet first.** P1 goes core types → minimal sim → scripted gate → 3 tools → action log → one happy-path scenario rendering. End-to-end before depth. Everything afterward thickens this skeleton.
- **Phase + DOD is the contract; git log is the tracker.** `docs/PHASES.md` is static. Status comes from `git log --oneline | grep '(P1)'` and `make verify`'s exit code. Do not create a tracker file — it will drift.
- **Commit messages carry the phase scope.** `feat(P3): wire surge curves into pricing` lets `git log --grep='(P3)'` reconstruct progress in 1 second.
- **Do not create GitHub Issues for slices.** Only for durable external blockers or post-launch backlog.
- **No idle green work.** If local gates + CI imply LGTM, land. Don't sit on green branches.

### 2. Rigor — prove the change

- **Deep modules over shallow.** The `BookingProvider` port is the canonical example: 5 methods hide pricing, surge, state machines, idempotency, expiry. Before expanding any interface, ask "can this be one method instead of three?"
- **Within a slice, vertical tracer bullets.** One test → minimal code to pass → next test. Do NOT write all the implementation then all the tests. Confirm the happy path first; expand incrementally.
- **TDD where it pays, fixtures where it doesn't, no tests where it would be ceremony.** See `## TDD scope` below.
- **Adversarial review (Ralph loop) for any non-trivial slice.** Before claiming done, spawn a subagent with `Agent(subagent_type=feature-dev:code-reviewer)` (or `general-purpose` if specialist unavailable) and a prompt naming the slice's spec section + invariants. Address blocking feedback on-branch. Distill dispositions into the commit message body (or `docs/scratch/.review-<slug>.md` if verbose). Merge confidence requires Ralph evidence, not just green CI.
- **Never `--no-verify`.** If a hook fails, fix the cause. If the hook is wrong, change the hook in a separate commit with a one-line rationale. This is enforced by `.claude/settings.json` deny list.
- **Determinism.** `temperature=0`, pinned model ID, seeded RNG, `FrozenClock` in scenarios. If a test flakes, fix the cause; do not retry.
- **Evidence required to mark a slice ✓.** For tool/adapter slices: contract test green + unit tests green + relevant scenario(s) still render with invariants passing. For UI slices: screenshot + console/network clean. For agent/prompt slices: at least one scenario re-rendered with passing invariants.

### 3. Hygiene — keep the repo operable

- **Worktrees for parallel work.** If multiple slices are in flight (e.g., human + delegated subagent), use `git worktree add ../agentic-cab-rides-<slug> -b <branch> origin/main`. Keep the main checkout as integration/reference. Cap active worktrees at 3 unless explicitly approved.
- **One concern per commit.** No bundling refactors with features. Refactor-then-feature is two commits.
- **Clean up after verification.** Don't leave dev servers running. `make reset` between scenario runs to wipe `data/` and `logs/`.
- **No raw PII in logs.** Addresses go through `redact()` at the log boundary (only `formatted/lat/lng` survive). Phone numbers and names redacted similarly.

## TDD scope (where rigor pays vs. ceremony)

| Surface | Discipline |
|---|---|
| `core/fingerprint.py` | **TDD strict.** Includes hypothesis property tests (order-independent, whitespace-insensitive). |
| `core/types.py` (Pydantic models) | Smoke tests only; Pydantic enforces shape. |
| `BookingProvider` contract suite | **TDD strict.** Every new adapter must pass without modification. |
| `tools/confirm.py` (gate, re-quote, fingerprint check) | **TDD strict.** Unit tests with fake `BookingProvider` + scripted `ConfirmationChannel` + assertion on action log rows. |
| `tools/cancel.py` | Same as confirm. |
| `adapters/uber_sim/pricing.py`, `surge.py` | Fixture-based tests. Don't TDD a pricing curve; verify outputs against pinned tables. |
| `adapters/state_store.py` (SQLite) | Restart-survival contract test + idempotency dedupe test. |
| `agent/loop.py`, `agent/system_prompt.py` | **No unit TDD.** Verified end-to-end via scenarios + invariants. |
| `web/app.py`, `web/static/index.html` | Smoke test only (one route check). Verified by manual screenshot. |
| `render/transcript.py` | Snapshot test (syrupy) on one known input. |

## Start-lane gate (before opening a slice)

1. `make status` — orientation: branch, dirty files, recent commits, per-phase counts.
2. Read `docs/PHASES.md` for the current phase's acceptance bullets and unmet ones. Pick the next slice that moves a bullet.
3. Confirm `main` is clean and CI is green.
4. Design the slice's public interface before writing implementation. Prefer fewer methods, simpler params, more hidden complexity.
5. Worktree if parallel work; otherwise branch from latest `main`.

## Slice lifecycle gate

1. Implement vertically (test → minimal code → test).
2. `make test-fast` during iteration. `make verify` before push.
3. Scan for refactor candidates the new code revealed (duplication, shallow modules, primitive obsession). Tests after each refactor step.
4. Run `make verify` and at least the smallest affected scenario via `make transcripts`.
5. Commit. Conventional Commits format with **phase scope**: `feat(P1):`, `fix(P3):`, `refactor(P2):`, `test(P3):`. Docs/chore commits don't need a phase scope.
6. For non-trivial slices: **Ralph loop.** Spawn an adversarial-review subagent (see below). Distill dispositions into the commit message body. Address blocking feedback on-branch before the commit lands.
7. Verify phase acceptance bullet(s) the slice was supposed to move: `make verify && make transcripts-ci`.
8. Move to next slice.

## Ralph loop — how to invoke

```
Agent({
  description: "Adversarial review of slice <id>",
  subagent_type: "feature-dev:code-reviewer",
  prompt: """
    Review commit <sha> on branch <name> against:
      - spec §<X> (path: docs/superpowers/specs/...)
      - CLAUDE.md invariants
      - slice-delivery rigor pillar

    Specifically check:
      - <slice-specific invariants>

    Report blocking issues, non-blocking observations, and questions.
    Be specific (file:line). Do not propose fixes — flag and let the
    author decide.
  """
})
```

Capture the subagent's response either in the commit message body (preferred — travels with the change in git) or, if verbose, in `docs/scratch/.review-<slug>.md`. Explicit disposition per item: **fixed / deferred (→ FOLLOWUPS.md) / rejected (with reason)**.

## When NOT to use this skill

- Single-line README typo fixes
- `.gitignore` additions
- Dependency bumps without behavior change
- Reverts

Use it for everything else, including refactors.

## Related skills

- `superpowers:test-driven-development` — invoke for fingerprint, idempotency, contract tests.
- `superpowers:writing-plans` → `superpowers:executing-plans` — drive the slice queue.
- `handoff` — when pausing mid-slice across sessions.
- `superpowers:using-git-worktrees` — when running parallel slices.
- `superpowers:dispatching-parallel-agents` — when delegating slices to subagents in worktrees.
