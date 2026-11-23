# Plan: #62, reword the test docstring that trips `no-todo-in-code`

## Goal
Stop the `no-todo-in-code` pre-commit hook from failing every commit on `main`. The cause is one docstring in [tests/unit/test_cost_ledger.py](../../tests/unit/test_cost_ledger.py). Its line 6 reads `13 (no TODOs).`, and the hook pattern `TODO|FIXME|XXX|HACK` matches that text. The fix rewords the docstring so it still names invariant 13 but contains none of the marker words. The hook itself is not changed.

## Acceptance criteria
- [ ] On a clean checkout of `main`, a commit that touches only a docs file passes the `no-todo-in-code` hook.
- [ ] The docstring in `tests/unit/test_cost_ledger.py` still names invariant 13, but no longer contains a marker word the hook matches.
- [ ] `make verify` passes.

## Invariants touched
- **12 (no `--no-verify`; a hook change goes in its own commit with a one-line rationale):** Preserved. The hook in [.pre-commit-config.yaml](../../.pre-commit-config.yaml) is not changed, and the fix commit is made with hooks on, never with `--no-verify`.
- **13 (no `TODO`/`FIXME`/`XXX` in code):** Restored. After the reword, the hook pattern has no matches in `src/`, `tests/` or `scripts/`. The docstring still points readers to invariant 13.
- **15 (public repo):** Preserved. The change is one docstring line with nothing sensitive in it.

## Tasks
1. **Confirm the failing check, then reword the docstring.** File: [tests/unit/test_cost_ledger.py](../../tests/unit/test_cost_ledger.py).
   - *Failing check first:* run the hook's own pattern, `grep -RnE "TODO|FIXME|XXX|HACK" src/ tests/ scripts/`. It currently returns exactly one text match, `tests/unit/test_cost_ledger.py:6: ... 13 (no TODOs).`
   - *Change:* replace `13 (no TODOs).` with `13 (no deferred-work markers in code).` Leave the rest of the docstring as it is. If the new line goes over ruff's line length, re-wrap it.
   - *Passing check:* the same grep finds no text matches.
2. **Check for other matches.** No file change. The pattern was searched across text files in `src/`, `tests/` and `scripts/` on this branch, and `test_cost_ledger.py:6` is the only match. Before committing, re-run the grep from task 1 to confirm nothing new has landed. Delete any local `__pycache__/` directories under `tests/` first (`make reset` does not clear them). Otherwise stale `.pyc` files can still make the local hook fail, because `grep -R` reads binary files (see Out of scope).
3. **Run the gate and the docs-only commit check.** No file change.
   - Run `make verify`: format check, lint (ruff, including the `FIX` rule), typecheck and fast tests. It must be green.
   - Commit the reword with hooks on, using the message `chore(tests): reword invariant-13 docstring so no-todo-in-code passes (#62)`. The `no TODO/FIXME in code` hook must pass.
   - Then, on a clean tree, stage only a docs file (for example this plan under `docs/plans/`) and run `git commit`. The `no-todo-in-code` hook must pass. This covers acceptance criterion 1.

risk: none

## Out of scope
- Hardening the hook: skipping binary files with `grep -I`, excluding `__pycache__`, or checking only staged files. Each of these changes a hook, so under invariant 12 it needs its own commit and rationale. It can be filed as a follow-up in [docs/FOLLOWUPS.md](../FOLLOWUPS.md) or as a separate issue.
- Rewording the invariant-13 row in [docs/ARCHITECTURE.md](../ARCHITECTURE.md) or the invariant text in [CLAUDE.md](../../CLAUDE.md). The hook does not scan `docs/` or the root files.
- Any other change to the tests or the code under test.

Self-check: Task 1 covers criterion 2, task 3 covers criteria 1 and 3, and task 2 confirms the reword is the only change needed.
