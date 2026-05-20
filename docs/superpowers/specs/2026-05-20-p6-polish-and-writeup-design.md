# P6 — Polish + Writeup + Final Verification — Design

**Date:** 2026-05-20
**Owner:** Ujjaval Verma (with Claude Opus 4.7 collaboration)
**Phase contract:** `docs/PHASES.md` §P6
**Prior phases:** P0–P5 shipped (see `git log --oneline --grep='(P[0-5])'`)

## 1. Goal

Bring the repo to deliverable state: a fresh reader can clone, run, read the architecture, and read the writeup in under 5 minutes. Everything below the docs layer has shipped — P6 is the polish + final-verification pass that closes the brief.

## 2. Acceptance contract (verbatim from `docs/PHASES.md` §P6)

- [ ] Fresh clone + `uv sync` + `ANTHROPIC_API_KEY=...` + `make repl` works in under 3 minutes on a clean machine (verified by clean checkout).
- [ ] `docs/ARCHITECTURE.md` written — layer diagram + port boundaries.
- [ ] `docs/WRITEUP.md` written — the 1-pager deliverable (marketplace swap + what breaks first).
- [ ] `README.md` quickstart validated on a clean checkout.
- [ ] All 5 transcripts re-rendered and committed.
- [ ] `make verify` clean on `main`.
- [ ] `make transcripts-ci` green in CI.

## 3. Starting state (2026-05-20, HEAD `d5e030a`)

- `docs/ARCHITECTURE.md` — does not exist (P6's largest single deliverable).
- `docs/WRITEUP.md` — 71 lines; substantive 1-pager content already present, but framed as a P4-era status note ("> Status: P4 complete; P5 (Web UI) shipped"). Needs reframing as a pure deliverable.
- `README.md` — 75 lines. Quickstart is correct end-to-end. Status section is stale ("P0 shipped, P1 next" — actually P0–P5 shipped).
- `transcripts/01..05_*.md` — committed; 4 of them currently appear dirty in the workspace with model-output content variation. Stashed pre-P6 (`git stash list`) so they don't pollute slice diffs; will be discarded in favour of a fresh `make transcripts` run at slice 5.
- `make verify` — green locally; CI green on `main` (last run: `26183365474` for `d5e030a`).
- `FOLLOWUPS.md` — 17 entries remaining post-P4. None are P6 blockers; flake entry closed in `d5e030a`.

## 4. Decisions

### 4.1 ARCHITECTURE.md depth — **terse**

Target ~150–250 lines. Sections:

1. **One-paragraph orientation** — what the system is (agent → tools → ports → adapters), what it does (book gated rides), what it deliberately is not (multi-tenant, paid).
2. **Layer diagram** — mermaid `graph TD`, rendered inline by GitHub (native support since 2022, no plugin). Dependency-direction arrows point down only. CLI/Web → Agent → Tools → Ports → Adapters. Each node labelled with its primary file path. Mermaid is chosen over ASCII because the audience reads this on github.com; the only thing lost is rendering in `cat`/`grep`, which is not the target surface for an architecture doc.
3. **Port table** — markdown table, five rows: `BookingProvider`, `ConfirmationChannel`, `ActionLog`, `StateStore`, `Clock`. Columns: `port (file)`, `one-line responsibility`, `today's impls`. Pulls directly from `core/ports.py`. Kept as markdown (not mermaid) so it reads cleanly in both rendered and raw form.
4. **Invariants-to-layer map** — markdown table mapping each `CLAUDE.md` invariant (1–14) to the layer that enforces it. Surfaces *where* a reader looks to verify a given guarantee.
5. **Persistence split** — short paragraph: SQLite for relational/transactional state, JSONL for append-only audit. Why the split, not just the what.
6. **Pointers** — three links: design spec, phases, writeup. No deep linking into `docs/scratch/`.

Excluded by design: per-port walkthroughs, mermaid sequence diagrams, data-flow examples. The 1-pager spirit of the rest of the docs governs. Format remains `.md` (not `.html`) — GitHub renders `.html` as raw source in repo views, so `.md` is strictly better for the first-impression reader path the brief depends on.

### 4.2 WRITEUP.md — **full rewrite**

Drop the phase-status framing entirely. The brief asks for a 1-pager on marketplace swap and what breaks first — not on what shipped vs. is planned. Rewrite preserves the load-bearing nuance (Lyft is trivial; Zocdoc forces the abstraction; idempotency key binding is the first thing that breaks) while removing:

- The `> Status: P4 complete; P5 shipped` banner.
- The repeated `— shipped (Tnn)` callouts attached to each subsystem (by P6 everything is shipped; the callouts add noise).
- Phase numbers in section bodies.

New section structure:

1. **Marketplace swap** — Lyft (trivial), Zocdoc (the abstraction's stress test). Includes the contract-suite-as-proof sentence (already strong).
2. **What breaks first in production** — idempotency under retries (the gap is cross-session key binding via `draft_id`), price drift quote↔confirm, agent↔reality state drift. Three landmines, mitigations or honest "not implemented".
3. **What I'd build next** — priority-ordered list (the existing list is good; trim to 5).

Target: ≤ 1 printed page (≈ 60 lines markdown).

### 4.3 README polish

Three concrete changes:

- **Status section rewrite.** Replace "P0 shipped, P1 next" with a 2-line description of what works today: REPL agent, web UI, 5 scenarios, contract suite, replayable log. Do not enumerate phases — tracker is `git log`.
- **Strip the `*(will be validated end-to-end in P6)*` qualifier** from the quickstart heading, since P6 is the validation pass.
- **Optional: web-UI screenshot embed.** If the web UI screenshot exists or is easy to capture, embed it under the `make web` line. If capturing it is non-trivial in the available environment, skip — not an acceptance bullet, just polish.

### 4.4 Clone-to-run validation — Docker proxy

Run, once, in the README polish slice:

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

Acceptance interpretation: total elapsed time (`uv sync` + first import) ≤ 3 minutes inside the container, with a host network that can reach PyPI. The command intentionally does not invoke `make repl` because that needs `ANTHROPIC_API_KEY` and a TTY; `uv run python -m agentic_cab --help` proves the package imports cleanly, which is the load-bearing step the README claim depends on. The timing evidence is captured in the slice 3 commit message — not enshrined in README (machine-dependent).

### 4.5 Final transcript re-render

Run `make transcripts` at slice 5. For each of the 5 files:

- If `git diff` shows session-id / ULID churn only → discard (`git checkout transcripts/NN_*.md`). Same lesson as commit `8492eb6` / P3 retro.
- If `git diff` shows material content change (different wording, different invariant assertion, different option ordering) → commit. Material = "would a reader form a different impression of what the agent does".

If all 5 produce only id-churn, slice 5 is a no-op closeout commit with a one-line message recording that the re-render was run and produced no material diffs. (This is honest; "commit verbatim" was explicitly rejected during brainstorming.)

The stashed pre-P6 transcript edits are *not* the canonical re-render — they are an artifact of an earlier, unrelated `make transcripts` run. Drop the stash at the end of P6.

### 4.6 Closeout

Final slice 6: confirm `make verify` clean on the branch and a fresh `make transcripts-ci` CI run green. The CI run ID for the slice-5 push is the evidence; closeout commit (if any code changes) cites it. If slice 5 produced no commits, slice 6 is satisfied by the CI run for slice 5 itself — no closeout commit needed.

## 5. Slice plan

| # | Slice | Files touched | Acceptance bullets covered |
|---|-------|--------------|---------------------------|
| 1 | Write `docs/ARCHITECTURE.md` | `docs/ARCHITECTURE.md` (new) | ARCHITECTURE.md written |
| 2 | Rewrite `docs/WRITEUP.md` | `docs/WRITEUP.md` | WRITEUP.md final form |
| 3 | Polish `README.md` + capture clone-to-run timing evidence in commit msg | `README.md` (+ optional screenshot asset) | README quickstart validated; 3-min clone-to-run verified |
| 4 | (folded into 3) | — | — |
| 5 | Re-render transcripts; commit only material changes | `transcripts/*.md` (only those with material diffs) | All 5 transcripts re-rendered |
| 6 | Closeout — verify CI green for the final slice push | — | `make verify` clean + `make transcripts-ci` green |

## 6. Ordering + dependencies

```
1 ARCHITECTURE.md ─┐
                  ├─→ 3 README + clone-to-run ─→ 5 transcripts ─→ 6 closeout
2 WRITEUP rewrite ─┘
```

1 and 2 are independent; doing them sequentially (1 then 2) is fine and avoids worktree overhead — the cost is one extra `make ship` cycle, not extra coordination. 3 depends on both because README links to and reflects them. 5 is last because anything earlier may shift the agent/tool behaviour transcripts capture (unlikely in P6, but cheap to honour). 6 is a meta-gate.

## 7. Per-slice risks / pre-commit notes

- **Slice 1:** `pre-commit no-stray-root-md` will not trigger (file is under `docs/`). Hook `relative markdown links must resolve` will trigger — ensure all links work. No links into `docs/scratch/` or `notes/` (CLAUDE.md rule).
- **Slice 2:** deletion-heavy diff. Verify no load-bearing technical nuance is lost: idempotency-key derivation specifics, contract-suite-as-proof claim, the `Intent` tagged union + `Option.details` discriminated union + `supported_intent_kinds` triad.
- **Slice 3:** README must not enumerate phase numbers (drift risk; tracker is `git log`). Docker proxy command needs PyPI reachability — fail-fast and document in commit message if the container couldn't reach the network rather than fudging the evidence.
- **Slice 5:** if `make transcripts` itself fails (LLM transient error), retry up to twice. If persistent failure, the slice is blocked, not "no-op-closed" — file a FOLLOWUP and exit P6 incomplete.
- **Slice 6:** must be on `main` with CI green. If CI flakes, re-trigger via empty commit `chore: re-trigger CI` only if there's a real reason; otherwise wait it out.

## 8. Out of scope (explicit)

- New tests, new features, new scenarios. P6 is documentation + verification, not code.
- Rewriting the spec (`docs/superpowers/specs/2026-05-19-agentic-cab-rides-design.md`). The spec is the historical contract; ARCHITECTURE.md is its present-day companion.
- Closing remaining `FOLLOWUPS.md` entries. They are durable and survive into post-P6 life.
- Any change to `CLAUDE.md` invariants. P6 enforces them through ARCHITECTURE.md's mapping; it does not edit them.

## 9. Done definition

Every box in §2 is checked. Every slice in §5 is shipped via `make ship`. The HEAD commit on `main` after slice 6 has a corresponding green CI run. A reviewer reading only `README.md`, `docs/WRITEUP.md`, and `docs/ARCHITECTURE.md` — in that order, on a fresh clone — can answer: what does this do, how do I run it, how does it fit together, and what would break first in production.
