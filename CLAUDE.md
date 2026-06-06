# CLAUDE.md — agentic-cab-rides

This repo is a prototype: an AI agent that books cab rides end-to-end, with a swappable platform adapter, structural confirmation gates, and a replayable action log. The harness here matters too — process discipline is part of the artifact.

**Read first:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — layer diagram, ports, type contracts, the `confirm_booking` core flow, and the invariant audit map. This file owns the invariants and how to work in the repo.

## Skills to invoke

- **`slice-delivery`** — invoke for any non-trivial change. It owns the slice lifecycle, the Ralph review loop, TDD scope, and the start-lane gate.
- **`superpowers:writing-plans`** then **`superpowers:executing-plans`** — drive the slice queue from `docs/ARCHITECTURE.md` + `docs/PHASES.md`. New plans land in `docs/plans/YYYY-MM-DD-<slug>.md`.
- **`superpowers:test-driven-development`** — for fingerprint, idempotency, and provider contract work.
- **`superpowers:systematic-debugging`** — when a test fails or behavior diverges from the architecture contract.
- **`handoff`** — when pausing mid-slice across sessions; writes to `$TMPDIR`, not the repo.
- **`superpowers:verification-before-completion`** — before any "done" claim. Evidence required.

**SOLID + refactor-scan** are owned by `slice-delivery` (Rigor pillar). Consult after every green test, not as an afterthought at PR time. Tests pin non-determinism via dependency injection, not via `_override` fields on production types.

Do not use LangChain, LlamaIndex, instructor, dspy, or any ORM. The Anthropic SDK and stdlib `sqlite3` are sufficient and keep the engineering discipline visible.

## Invariants — never violate without an explicit spec update

1. **Layer dependency direction is strict.** CLI/Web → Agent → Tools → Ports → Adapters. No upward edges. The agent never touches an adapter directly; it only sees tools.
2. **The confirmation gate is structurally enforced.** Every state-mutating action (`confirm_booking`, `cancel_booking`) goes through a `ConfirmationChannel`. The agent never sees a `confirmed=True` parameter; the channel owns the y/n decision. Idempotency-key replays do not re-invoke the channel (the original approval is canonical; see spec §10a "Idempotency replay vs. structural gate"). CLI reads from `/dev/tty`, not stdin.
3. **Fingerprint check before every gated provider call.** Tool layer computes `sha256(canonical_json(option_snapshot))` at `prepare`, re-verifies at `confirm`, and aborts on mismatch. A draft whose params were mutated between prepare and confirm must never reach the provider.
4. **Re-quote before confirm.** Inside `confirm_booking`, after fingerprint check, re-call `provider.discover()`. If price drift exceeds the threshold, return `PriceChanged` with a new draft — do not invoke the gate with stale price.
5. **Idempotency keys are required.** `BookingProvider.confirm` and `cancel` take an `idempotency_key`. SQLite enforces `UNIQUE`. `claim_or_recall` short-circuits the second provider call. The confirm key is derived from a hash of the user's stable intent (pickup, dropoff, 10-minute-bucketed `when`, party size, vehicle class), a generation counter advanced when a prior booking with this intent reaches terminal status, and an optional nonce supplied by the agent when the user explicitly asks for a second identical booking. This keeps the key invariant across `draft_id` regenerations and across process restarts. Cancel keys remain bound to `(booking_id, fee_cents, status_at_quote)`. A user-decline at the gate calls `release_claim` (both `confirm` and `cancel`), so a retry with identical intent re-derives the same key but finds it fresh — declines do not become poison pills.
6. **The action log emits four row kinds per gated action.** `requested` → `verified` → `executed` → `outcome`, linked by `action_id`. Append-only JSONL at `logs/<session_id>.jsonl`. `verified.result == "rejected"` short-circuits — no `executed` row follows. Read-only tools emit `requested` + `outcome` only.
7. **Persistence split is fixed.** SQLite (`data/state.db`) for drafts, bookings, booking_events, idempotency_keys. JSONL for action log, chat history, geocode cache. Do not move append-only artifacts into SQLite — the audit story depends on flat files.
8. **PII redaction at the log boundary.** Raw address text never enters logs; only `{formatted, lat, lng}`. Phone numbers and names through `redact()`.
9. **Determinism in scenarios — partial, by design.** Model ID pinned in `core/constants.py` (`claude-opus-4-7`), seeded `random.Random` passed via DI, `FrozenClock` in scenario mode. No module-level `random` or `datetime.now()` reads. **Caveat:** `claude-opus-4-7` is an extended-thinking model and rejects the `temperature` parameter (HTTP 400), so the agent runs at the model's default sampling. `AGENT_TEMPERATURE = 0.0` in `core/constants.py` exists as documentation of intent only — it is **not** forwarded to the API. Consequence: provider, store, log, and clock layers are bit-deterministic across re-runs, but the agent's *reasoning chain* (which option it discusses first, exact prose, occasionally which tool it calls in ambiguous turns) can vary. All structural invariants checked by `tests/scenarios/` are insensitive to this — they assert on action-log shape, not LLM prose. Re-rendering transcripts may produce visually-different chat traces but identical four-row action-log structure.
10. **Money is `Money(amount_cents: int, currency: str)`, never a float.**
11. **IDs are ULIDs.** Sortable, no DB index entropy. `session_id`, `turn_id`, `action_id`, `draft_id`, `idempotency_key`.
12. **No `--no-verify`. Ever.** Settings deny list enforces. If a hook is wrong, change it in a separate commit with a one-line rationale.
13. **No `TODO`/`FIXME`/`XXX` in code.** Promote to `docs/FOLLOWUPS.md` or fix now. Ruff `FIX` rule enforces.
14. **No `print()` outside the CLI/web rendering layer or scripts.** Use `structlog`. Ruff `T20` enforces.
15. **This repo is public — treat every commit as world-readable and permanent.** Secrets never enter the tree: `ANTHROPIC_API_KEY` and all credentials live only in a chmod-600 `.env` outside the repo (see the README quickstart). Never commit, hardcode, or paste a key, token, or PII into code, docs, transcripts, or logs. `scripts/check_secrets.py` runs pre-commit as a backstop, not the first line of defense. Before publishing any new artifact (a transcript, a sample log), confirm it carries no real key or personal data. A push to a public repo cannot be un-published — mirrors and caches retain it.

## Golden commands

```bash
make install         # uv sync + install pre-commit + pre-push hooks
make repl            # CLI agent (CLIConfirmationChannel)
make web             # FastAPI + browser at :8000 (WebConfirmationChannel)
make transcripts     # Regenerate all 6 transcripts from scenarios/
make test            # Full pytest suite
make test-fast       # Unit + contract only
make verify          # Pre-push gate: fmt-check + lint + typecheck + test-fast
make replay SESSION=<id>   # Pretty-print a session log
make reset           # Wipe data/, logs/, chat/
make status          # Orientation: branch, dirty files, recent commits, current slice
make help            # Discover targets
```

`python -m agentic_cab run-scenario <name>` runs a single scenario directly. The Makefile is the discovery surface — every command goes through it so hooks and humans use the same path.

## Repo conventions

- **`uv` for everything.** `uv sync`, `uv run`, `uv lock`. `uv.lock` is committed.
- **`ruff` for lint and format.** Single tool, configured in `pyproject.toml`. No black/isort/flake8.
- **`pyright` strict** on `core/` and `tools/`; `basic` elsewhere.
- **`pytest` markers:** `contract` (provider contract suite), `scenario` (end-to-end with LLM), `slow`. `test-fast` excludes the last two.
- **Conventional Commits** for messages (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`).
- **One concern per commit.** No bundling refactors with features.
- **Worktrees** under `../agentic-cab-rides-<slug>` when running parallel slices.

## Determinism / model pin

- Model: `claude-opus-4-7` (or whatever is pinned in `src/agentic_cab/core/constants.py:MODEL_ID`). Do not change without re-rendering all 6 transcripts.
- `temperature=0` is the documented intent but **not actually applied** — `claude-opus-4-7` rejects the parameter. See invariant 9 for the full caveat. Determinism is enforced only on the deterministic layers (provider, store, clock, RNG).
- Scenarios use `FrozenClock` and seeded RNG; interactive mode uses `SystemClock` and `random.Random(os.urandom(8))`.

## Documentation: durable vs transient

The repo has two kinds of markdown: docs that travel with the code (versioned, referenced by other artifacts) and scratch that exists only to support the current task (gitignored, deletable). Mixing them rots the repo silently.

### Durable — committed, part of the contract

| Path | Purpose |
|---|---|
| `CLAUDE.md` (root) | Invariants every session must respect |
| `README.md` (root) | 3-min quickstart for first-time readers |
| `docs/ARCHITECTURE.md` | Layer diagram, ports, type contracts, core flow, invariant audit map (the durable design home) |
| `docs/plans/*.md` | Implementation plans (output of `writing-plans`), date-prefixed |
| `docs/PHASES.md` | Static contract: Definition of Done + phases with acceptance bullets. No status field — git log is the tracker. |
| `docs/WRITEUP.md` | The production-readiness writeup (marketplace swap + failure modes) |
| `docs/FOLLOWUPS.md` | The only legal home for TODO-class items |
| `transcripts/*.md` | Generated, but committed — a demo artifact |

### Transient — gitignored, do not commit

| Path | Purpose |
|---|---|
| `docs/scratch/` (except its README) | Agent thinking-out-loud, exploratory notes, Ralph-loop review outputs |
| `notes/` (except its README) | Personal scratchpad — meta notes, command snippets, references-to-read-later |
| `*.draft.md` anywhere | Naming-convention fallback for in-flight docs near related code |
| `*.local.md` anywhere | Local-only annotations |
| `.review-*.md` anywhere | Ralph-loop review outputs (dispositions distilled into `build-progress.md`) |
| `$TMPDIR/handoff-*.md` | Handoff docs live outside the repo entirely (per the `handoff` skill) |

### Decision tree

```
Will this doc still be useful in 7 days?
├── No  → docs/scratch/  (or *.draft.md if it belongs near code)
└── Yes → Will multiple sessions/agents need to read it?
         ├── No  → notes/  or  docs/scratch/
         └── Yes → Does it define a contract, invariant, or deliverable?
                  ├── Yes → docs/  (and link from CLAUDE.md or README.md)
                  └── No  → append to docs/FOLLOWUPS.md
```

**Tie-breaker:** when unsure, it's scratch. Promoting scratch → durable later is cheap; removing committed history is not.

### Enforcement

- `.gitignore` excludes `docs/scratch/*`, `notes/*`, `*.draft.md`, `*.local.md`, `.review-*.md` (READMEs in scratch/notes are whitelisted)
- Pre-commit hook `no-stray-root-md` rejects net-new top-level `*.md` files outside `{CLAUDE.md, README.md}` — forces them under `docs/` or `docs/scratch/`
- Durable docs **must not link into** `docs/scratch/` or `notes/` — those links would 404 for anyone cloning fresh

## Where to put things

- New ports: `src/agentic_cab/core/ports.py`
- New types: `src/agentic_cab/core/types.py`
- New tools: `src/agentic_cab/tools/<name>.py` (one file per tool)
- New adapters: `src/agentic_cab/adapters/<area>/<name>.py`
- New scenarios: `scenarios/<NN>_<slug>.yaml` + invariants
- Contract tests: `tests/contract/`
- Unit tests: `tests/unit/`
- Scenario tests: `tests/scenarios/`
- Follow-ups: `docs/FOLLOWUPS.md` (never inline `TODO`)
- Phases + DOD: `docs/PHASES.md` (static contract; status comes from `git log`, not a tracker)

## When something is unclear

Re-read the relevant section of `docs/ARCHITECTURE.md` (or the invariant above) before asking. Most ambiguity is resolved there. If the architecture doc is genuinely ambiguous, ask once with a specific quote, then update it in the same PR as the resolution.
