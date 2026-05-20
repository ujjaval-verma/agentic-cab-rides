# Agentic Cab Rides

> An AI agent that books rides end-to-end — built on a **marketplace-agnostic adapter port** designed so that swapping Uber for Lyft is trivial, and swapping for **Zocdoc** (which is not a cab service) requires only a new `Intent` variant and a new adapter. The tool layer, confirmation gate, fingerprint logic, and action log do not change.

The repo's name is `agentic-cab-rides` because the visible deliverable today is booking Uber rides. The architecture is intentionally not: see [`docs/superpowers/specs/2026-05-19-agentic-cab-rides-design.md`](docs/superpowers/specs/2026-05-19-agentic-cab-rides-design.md) for the design, and [`docs/PHASES.md`](docs/PHASES.md) for what is shipped vs. planned.

## What this is

A take-home prototype that demonstrates three properties the brief grades hardest:

1. **A swappable platform adapter.** A generic `BookingProvider` port over `Intent → Option → Draft → Booking`. Adding Lyft = a new adapter passing the contract test suite. Adding Zocdoc = one new `AppointmentIntent` variant + a new adapter + an intent router. *Agent tools, confirmation gate, action log, fingerprint logic: unchanged.*
2. **Structural confirmation gates.** State-mutating tools (`confirm_booking`, `cancel_booking`) cannot self-approve. Every gated call goes through a `ConfirmationChannel` whose decision the agent does not control — CLI reads from `/dev/tty`, the web UI uses a button click, scenario mode uses a scripted approver. Fingerprints reject mutated drafts before any provider call.
3. **A replayable action log.** Append-only JSONL emitting four row kinds per gated action: `requested → verified → executed → outcome`, linked by `action_id`. The log answers, for every attempt, what was requested, what was verified, what executed, and what happened — exactly the brief's phrasing.

## Status

**P0 — Harness:** shipped. Tooling, hooks, skills, invariants, phase contract.
**P1 — Tracer bullet:** next. See [`docs/PHASES.md`](docs/PHASES.md).

Tracker = `git log --oneline --grep='(P[0-9])'`. There is no status file; status lives in commits.

## Quickstart *(will be validated end-to-end in P6)*

```bash
git clone <repo>
cd agentic-cab-rides
cp .env.example .env && echo "ANTHROPIC_API_KEY=..." >> .env
make install         # uv sync + install pre-commit + pre-push hooks
make repl            # CLI agent
# or
make web             # browser UI at http://localhost:8000
# or
make transcripts     # regenerate all 5 scenario transcripts
```

`make help` lists every target. The Makefile is the discovery surface; pre-commit and CI both call into it so there is one path.

## Deliverables (per the brief)

- Working repo, clone-to-run in 3 minutes
- 5 transcripts under [`transcripts/`](transcripts/): happy path, price comparison, bad address, surge/edge case, cancellation
- 1-page writeup at [`docs/WRITEUP.md`](docs/WRITEUP.md) on swapping for a different marketplace and what breaks first in production

## Stack & non-negotiables

- Python 3.12 + `uv` + Pydantic v2 + Anthropic SDK (native tool use, no LangChain)
- FastAPI + vanilla HTML/JS (no build step) for the web UI
- Stdlib `sqlite3` for state (drafts, bookings, idempotency keys) — no ORM
- JSONL for action log, chat history, geocode cache
- `ruff` for lint/format, `pyright` for typecheck (strict on `core/` and `tools/`)
- `pre-commit` framework for git hooks; `--no-verify` is blocked by `.claude/settings.json`

## Where things live

- [`CLAUDE.md`](CLAUDE.md) — invariants every session must respect
- [`docs/superpowers/specs/`](docs/superpowers/specs/) — design spec(s)
- [`docs/PHASES.md`](docs/PHASES.md) — Definition of Done + phases
- [`docs/FOLLOWUPS.md`](docs/FOLLOWUPS.md) — durable follow-ups (only legal home for TODO-class items)
- [`.claude/skills/slice-delivery/`](.claude/skills/slice-delivery/) — execution wrapper for the repo
- [`docs/scratch/`](docs/scratch/) and [`notes/`](notes/) — gitignored work-in-progress (see [CLAUDE.md § Documentation: durable vs transient](CLAUDE.md))

## What this is *not*

Not multi-tenant, not authenticated, not payment-aware (assumes account-level pre-auth like real Uber), not routed via real maps (straight-line × fudge factor), not running against a real Uber API (which third parties haven't had book-grade access to in years — the sim is deterministic and seeded). See [`docs/WRITEUP.md`](docs/WRITEUP.md) for what would break first in production.
