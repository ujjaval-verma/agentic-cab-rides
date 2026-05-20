# Follow-ups

This file is the **only** place "TODO"-class items live in this repo. The lint rule (`ruff FIX` + `no-todo-in-code` hook) forbids `TODO`/`FIXME`/`XXX`/`HACK` in source. Promote durable items here instead of leaving them inline.

## Format

```
- [ ] <one-line description> — <why deferred> — <slice-or-commit-ref>
```

Group by category. Keep entries actionable; delete when done (don't archive — git log is the archive).

---

## Architecture / engineering

- [ ] **Intent-bound idempotency keys.** Today the tool layer mints a fresh ULID at the top of `confirm_booking` / `cancel_booking`, so an agent retry after a network timeout mints a NEW key and bypasses the dedupe barrier. Real fix: derive the key deterministically from intent state, e.g. `sha256(draft_id + ":confirm")`. Tracked in `WRITEUP.md` as "what breaks first in production." — P3 contract suite + retrofit.
- [ ] **AutoApproveChannel structural guard.** When `adapters/confirmation/auto.py` lands (P1), its `__init__` must assert `os.environ.get("ALLOW_AUTO_APPROVE") == "1"` and raise otherwise. Add a unit test that asserts construction raises by default and succeeds with the env var set. Right now this is only enforced by prose in the spec; the assignment grader will rightly probe it. — P1 confirmation slice.
- [ ] **Mock `AppointmentProvider` in the contract suite.** The Zocdoc-reach claim is currently paper-only — the architecture supports it but no test exercises an `AppointmentIntent` end-to-end. Ship a minimal in-memory `MockAppointmentProvider` that satisfies the contract suite, proving the abstraction holds beyond ride-share. Strong signal for the writeup. — P3 contract suite expansion.
- [ ] **Action-log retention/rotation.** Logs currently grow without bound under `logs/<session>.jsonl`. Add a `make logs-rotate` target and a retention policy doc (likely just "keep last 100 sessions" for the prototype) before any long-running demo. — post-P6.

## Tests / coverage gaps

- [ ] **PII redaction integration test.** `redact()` must be applied at the log boundary; today only the spec describes this. Add a test that feeds a `RideIntent` containing `"pickup at Jane Doe's house, 555-1234"` and asserts no substring of that raw text appears in the resulting JSONL. — P1 action_log slice.

## Product / UX

- (none yet)

## Operational

- [ ] **`make reset` should re-apply `schema.sql` after wiping `data/`.** Currently it deletes `data/` and recreates an empty directory; the first run after reset relies on startup schema application. Make reset explicit: `rm -rf data && mkdir -p data && uv run python -m agentic_cab init-db`. — P1 state_store slice.
