# Agentic Cab Rides

> *A take-home prototype that books rides end-to-end — structural confirmation gates, fingerprint-verified drafts, and a replayable four-phase action log.*

The repo's name is `agentic-cab-rides` because the visible deliverable today is booking Uber rides — the architecture is intentionally not. The marketplace-agnostic adapter port means swapping Uber for Lyft is trivial; swapping for [Zocdoc](https://zocdoc.com) (not a cab service) requires only a new `Intent` variant and a new adapter. See [`docs/superpowers/specs/2026-05-19-agentic-cab-rides-design.md`](docs/superpowers/specs/2026-05-19-agentic-cab-rides-design.md) for the design and [`docs/PHASES.md`](docs/PHASES.md) for what is shipped.

## What this is

A prototype that demonstrates three properties:

1. **A swappable platform adapter.** A generic `BookingProvider` port over `Intent → Option → Draft → Booking`. Adding Lyft = a new adapter passing the contract test suite. Adding Zocdoc = one new `AppointmentIntent` variant + a new adapter + an intent router. *Agent tools, confirmation gate, action log, fingerprint logic: unchanged.*
2. **Structural confirmation gates.** State-mutating tools (`confirm_booking`, `cancel_booking`) cannot self-approve. Every gated call goes through a `ConfirmationChannel` whose decision the agent does not control — CLI reads from `/dev/tty`, the web UI uses a button click, scenario mode uses a scripted approver. Fingerprints reject mutated drafts before any provider call.
3. **A replayable action log.** Append-only JSONL emitting four row kinds per gated action: `requested → verified → executed → outcome`, linked by `action_id`. The log answers, for every attempt, what was requested, what was verified, what executed, and what happened.

## Status

Working today: REPL agent (`make repl`), web UI (`make web`), 5 end-to-end
scenarios with replayable action logs, a contract suite that proves the
`BookingProvider` port survives a non-ride second adapter, and a green
`make verify` gate on every push.

Tracker = `git log --oneline --grep='(P[0-9])'`. There is no status file; status lives in commits.

## Quickstart

**Requires:** [`uv`](https://docs.astral.sh/uv/getting-started/installation/) (Python is installed by `uv` automatically; 3.12+ pinned in `.python-version`).

```bash
git clone <repo>
cd agentic-cab-rides

# First-time secret setup (once per machine — kept outside the repo so multiple
# worktrees share one canonical key file; rotate in one place):
mkdir -p ~/.config/agentic-cab-rides && chmod 700 ~/.config/agentic-cab-rides
cp .env.example ~/.config/agentic-cab-rides/.env && chmod 600 ~/.config/agentic-cab-rides/.env
$EDITOR ~/.config/agentic-cab-rides/.env     # set ANTHROPIC_API_KEY=sk-ant-...

# Per worktree (idempotent):
make link-env                                # symlinks ./.env -> ~/.config/agentic-cab-rides/.env

make install                                 # uv sync + install pre-commit + pre-push hooks
make repl                                    # CLI agent
# or
make web                                     # browser UI at http://localhost:8000
# or
make transcripts                             # regenerate all 6 scenario transcripts
```

`make help` lists every target. The Makefile is the discovery surface; pre-commit and CI both call into it so there is one path.

## Deliverables (per the brief)

- Working repo, clone-to-run in 3 minutes
- 6 transcripts under [`transcripts/`](transcripts/): happy path, price comparison, bad address, surge/edge case, cancellation, cross-session dedup
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
