# Plan: turn on ruff bandit (`S`) rules and bump ruff to 0.17.6 (issue #22)

> Links are relative to `docs/plans/`.
>
> **Scope:** 7 tasks. No gate, hook or invariant change is needed. [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml) is not touched: its prerequisite is already on main (local `uv run ruff` hooks, pre-commit-hooks v6.0.0). The [`Makefile`](../../Makefile) is not touched either.

## Goal

Add the bandit rules (`S`) to ruff's `select`. Every finding is either fixed in code or suppressed on its own line with `# noqa: S###` and a reason. Bump the pinned ruff from **0.7.4 to 0.17.6** (the latest release) in [`pyproject.toml`](../../pyproject.toml) and [`uv.lock`](../../uv.lock). Absorb the bump's format and lint drift so `make verify` stays green on every commit.

### What was measured (on main, against the repo's own config)

**Current ruff (0.7.4), `ruff check --select S src tests scripts`: 15 findings**
- S603 ×3 and S607 ×3: `subprocess.run(["git", ...])` in [`scripts/check_links.py`](../../scripts/check_links.py) and [`scripts/check_secrets.py`](../../scripts/check_secrets.py).
- S324: `hashlib.sha1` used as the geocode cache key in [`src/agentic_cab/adapters/geocoding/cache.py`](../../src/agentic_cab/adapters/geocoding/cache.py).
- S311 ×3: `random.Random(...)` in [`mock_appointment/provider.py`](../../src/agentic_cab/adapters/mock_appointment/provider.py) (lines 53 and 73) and [`uber_sim/provider.py`](../../src/agentic_cab/adapters/uber_sim/provider.py) (line 84).
- S101: a module-level `assert` in [`web/channel.py`](../../src/agentic_cab/web/channel.py) line 47.
- S105 ×3: the `SECRET_PICKUP`, `SECRET_DROPOFF` and `SECRET_QUERY` constants (fake addresses) in [`tests/unit/test_discover_args_redaction.py`](../../tests/unit/test_discover_args_redaction.py).
- S108: the string `"/tmp/x"` used as a test input in [`tests/unit/test_paths.py`](../../tests/unit/test_paths.py) line 17.

**ruff 0.17.6 with `--extend-select S`: 15 findings, a different mix**
- S603 is gone: newer ruff no longer flags a literal argv.
- **3 new PLR0917 findings** ("too many positional arguments"):
  - `_compute_intent_context` (7 positional) and `_handle_claim_shortcircuit` (12 positional) in [`src/agentic_cab/tools/confirm.py`](../../src/agentic_cab/tools/confirm.py).
  - `_run_happy_path_sequence` (7 positional) in [`tests/scenarios/test_repl_web_parity.py`](../../tests/scenarios/test_repl_web_parity.py).

**ruff 0.17.6 `format --check src tests scripts`: 7 files would be reformatted**
- [`adapters/state_store.py`](../../src/agentic_cab/adapters/state_store.py): two implicitly joined strings merged into one.
- Six test files: the newer formatter puts the condition of a multi-line `assert` on one line and wraps the message in parentheses instead. The files are `test_scenario_06_cross_session_dedup.py`, `test_scenarios.py`, `test_chat_repl.py`, `test_confirm_dedup.py`, `test_scenario_runner_meter_identity.py` and `test_web_app_routes.py`.

**Hidden by config:** **870 `assert`s in 64 test files** are covered by the file-level `"tests/**" = [..., "S101"]` entry. Measured with `--isolated --select S101`. For every finding, the line ruff wants the `noqa` on is the line the finding starts on.

### Decision: the file-level `tests/**` S101 ignore

**Recommendation: read the criterion literally.** Remove `"S101"` from the `tests/**` entry and run `ruff check --select S101 --add-noqa="pytest assertion" tests`. ruff 0.17.6 supports `--add-noqa=<REASON>`, so every suppression gets the same reason and the result matches "line-level `# noqa: S###` with a reason". RUF100 is already on (through `RUF`), so any of these that go stale are flagged automatically.

- **Downside:** about 870 lines of noise across 64 files. Every new test `assert` also needs the pragma, because `ruff check --fix` does not add it.
- **Alternative:** keep the file-level ignore, which is standard pytest practice. That means amending the criterion before merge, which is the maintainer's call. If they choose it, drop Task 6 and the per-file part of Task 5's test.

## Acceptance criteria

- `pyproject.toml` ruff `select` includes `S`; every remaining suppression is a line-level `# noqa: S###` with a reason.
- ruff bumped to the current release in `pyproject.toml` and `uv.lock`. The pre-commit ruff hooks run the locked ruff (`uv run ruff`), so there is no second pin to keep in sync.
- `make verify` passes.

## Invariants touched

| # | Where | How it is preserved |
|---|---|---|
| 2 (confirmation gate) | `web/channel.py` loses a module-level `assert` | The assert always passes: a Protocol class is always a `type`. Protocol conformance is already tested by `isinstance(channel, ConfirmationChannel)` in [`tests/unit/test_web_confirmation_channel.py`](../../tests/unit/test_web_confirmation_channel.py). `request()` does not change. |
| 3 and 5 (fingerprint, idempotency) | `tools/confirm.py` helpers become keyword-only | Only the signatures change; logic, call order and arguments stay the same. Existing tests for confirm, dedup and idempotency must stay green unchanged. |
| 7 and 8 (persistence split, redaction) | Geocode cache key; the redaction test | `hashlib.sha1(..., usedforsecurity=False)` gives the same digest, so existing cache files still match. Renaming the `SECRET_*` constants to `RAW_*` keeps the redaction test's assertions the same. |
| 9 (determinism) | S311 sites | The seeded `random.Random` stays injected through the constructor. The suppression reason says it is simulated data, not crypto. No module-level `random` is added. |
| 12 (no `--no-verify`) | All commits | Every commit goes through the hooks. Task order keeps each commit green. |
| 13 and 14 (`FIX`, `T20`) | ruff bump | Both stay in `select`. The bump is verified to keep them firing (no new findings). |
| 15 (public repo) | `S105` | The constants were fake addresses, not secrets. With `S` on, ruff becomes an extra backstop next to `check_secrets.py`. |

## Tasks

**Ordering constraint:** RUF100 flags a `# noqa` for a rule that is not enabled. So no `# noqa: S###` can land before `S` is in `select`. The bump also has to come before the S suppressions: under 0.7.4 the scripts would need `S603` noqas that become "unused" after the bump.

### 1. Make the three PLR0917 functions keyword-only

- **Files:**
  - `src/agentic_cab/tools/confirm.py`: add `*` after `draft` in `_compute_intent_context` and after `claim` in `_handle_claim_shortcircuit`. Update the two call sites to pass keywords.
  - `tests/scenarios/test_repl_web_parity.py`: add `*` as the first parameter of `_run_happy_path_sequence`. Its callers already pass keywords.
- **Failing check first:** `uv tool run --from ruff==0.17.6 ruff check src tests` reports 3× PLR0917. After the change it reports 0.
- **Also:** `make verify` stays green on 0.7.4; the confirm, dedup and idempotency tests pass unchanged.
- **Commit:** `refactor(tools): make confirm_booking helpers keyword-only`

### 2. Bump ruff to 0.17.6 and apply its formatter output

- **Files:**
  - `pyproject.toml`: change `ruff==0.7.4` to `ruff==0.17.6`.
  - `uv.lock`: run `uv lock --upgrade-package ruff`.
  - Then run `uv run ruff format src tests scripts`, which rewrites the 7 files listed above.
  - CI runs `uv sync --frozen`, so the pin and the lock must change in the same commit.
- **Failing check first:** after the pin change and `uv sync`, `make fmt-check` fails on exactly those 7 files.
- **Then green:** `make verify`, `uv lock --check`, and `uv run ruff check src tests scripts` all pass with no findings. Also look for any deprecated-config warnings.
- **Commit:** `chore(deps): bump ruff to 0.17.6 and apply formatter drift`

### 3. Fix the src findings without suppressions

- **Files:**
  - `adapters/geocoding/cache.py`: use `hashlib.sha1(..., usedforsecurity=False)` and drop the now-redundant inline comment.
  - `web/channel.py`: delete the always-true module-level `assert` and the empty `if TYPE_CHECKING: pass` block.
- **Failing check first:** `uv run ruff check --extend-select S src` reports S324 and S101 (plus S311 ×3). After the change only S311 remains.
- **Test:** existing geocode-cache tests and `test_web_confirmation_channel.py` stay green. A cache key computed before and after for the same query is identical.
- **Commit:** `fix(security): mark geocode cache sha1 as non-crypto and drop tautological assert`

### 4. Fix the test findings without suppressions

- **Files:**
  - `tests/unit/test_discover_args_redaction.py`: rename `SECRET_PICKUP`, `SECRET_DROPOFF` and `SECRET_QUERY` to `RAW_PICKUP`, `RAW_DROPOFF` and `RAW_QUERY` everywhere in the file.
  - `tests/unit/test_paths.py`: change `"/tmp/x"` to `"/abs/x"`. It still tests that absolute paths are rejected.
- **Failing check first:** `uv run ruff check --extend-select S --extend-per-file-ignores 'tests/**:S101' tests` reports S105 ×3 and S108. After the change it reports 0.
- **Then green:** `make test-fast`.
- **Commit:** `test: rename fake-address constants and drop /tmp literal flagged by bandit`

### 5. Enable `S` and suppress the remaining six findings line by line

- **Files:**
  - `pyproject.toml`: add `"S",  # bandit (security)` to `select`. For now keep `"S101"` in the `tests/**` per-file ignore; Task 6 removes it.
  - Line-level suppressions, in the same form `--add-noqa` writes (`# noqa: S### <reason>`):
    - S311 at `mock_appointment/provider.py` lines 53 and 73, and `uber_sim/provider.py` line 84. Reason: `simulated provider data via injected seed, not crypto (invariant 9)`.
    - S607 at `scripts/check_links.py` line 34 and `scripts/check_secrets.py` lines 74 and 84. Reason: `git resolved from PATH by design for repo hooks`.
  - New test `tests/unit/test_lint_config.py`. It uses `tomllib` to read `pyproject.toml` and asserts `"S" in select`.
- **Failing check first:** the new test fails. Then, with `"S"` added, `make lint` reports 3× S311 and `uv run ruff check scripts` reports 3× S607.
- **Then green:** `make verify` and `uv run ruff check src tests scripts`. RUF100 confirms that each noqa is used.
- **Commit:** `feat(lint): enable ruff bandit rules with justified line-level suppressions`

### 6. Replace the file-level tests S101 ignore with line-level noqas

- **Files:**
  - `pyproject.toml`: the entry becomes `"tests/**" = ["ANN", "PLR2004", "PLC0415"]`.
  - Run `uv run ruff check --select S101 --add-noqa="pytest assertion" tests`. This edits about 870 lines in 64 files, plus the new test from Task 5.
  - Then run `uv run ruff format src tests scripts`.
  - Extend `test_lint_config.py`: no per-file-ignores value may start with `S`.
- **Failing check first:** the extended test fails. Removing the entry makes `make lint` report about 870 S101.
- **Then green:**
  - `make verify`, which includes the format check: the formatter does not count trailing pragma comments toward line width.
  - `uv run pre-commit run --all-files`.
  - No E501: ruff skips line length for trailing `# noqa` pragmas. If any line still trips it, shorten the reason. Do not add an ignore.
- **Commit:** `chore(lint): replace file-level test S101 ignore with line-level noqa`

### 7. Record follow-ups found during this work

- **File:** [`docs/FOLLOWUPS.md`](../FOLLOWUPS.md). Add these items:
  - **Non-deterministic seed (possible invariant 9 breach):** `mock_appointment/provider.py` line 73 seeds with `hash(seed_str)`. Python randomises string hashes per process (`PYTHONHASHSEED`), so this seed changes between runs. Fix with a stable digest of `seed_str`.
  - **Lint coverage gap:** `make lint` checks only `src tests`. `scripts/` is linted only by the pre-commit hook on staged files.
  - **Missing reasons:** existing non-`S` noqas have no reasons: `T201` in `__main__.py`, `E402` and `F401` in tests.
- **Check:** the pre-commit `docs-links` hook passes, and there is no TODO/FIXME in code.
- **Commit:** `docs(followups): record seed hash nondeterminism and lint coverage gaps`

**Final gate after Task 7:**
- `uv sync --frozen`
- `make verify`
- `uv run ruff check --extend-select S src tests scripts`
- `git grep -n "noqa: S" -- src tests scripts`: every hit carries a reason.

risk: gate idempotency fingerprint

Task 1 changes helper signatures inside `confirm_booking`, which is the gate, idempotency and fingerprint path. The change is signature-only, but review it against the four-path confirm tests. Task 3 removes an assert in the web `ConfirmationChannel` module.

## Out of scope

- Any edit to `.pre-commit-config.yaml`; the prerequisite has already landed.
- Adding `scripts` to `make lint` (that would be a gate change); recorded as a follow-up.
- Fixing the `hash()` seed nondeterminism in `mock_appointment`; recorded as a follow-up.
- Adding reasons to non-`S` suppressions, and the other `tests/**` per-file ignores (ANN, PLR2004, PLC0415).
- Enabling other new rule families or preview rules that the bump makes available.
- Re-rendering transcripts: no runtime behaviour changes.

## Self-check

- **"`select` includes `S`; every remaining suppression is line-level `# noqa: S###` with a reason":**
  - Task 5 adds `S` and the six reasoned noqas.
  - Tasks 3 and 4 remove eight findings in code.
  - Task 6 removes the only S code from per-file-ignores and adds reasoned line noqas.
  - The test from Tasks 5 and 6 guards against regression.
- **"ruff bumped in `pyproject.toml` and `uv.lock`, hooks use the locked ruff":** Task 2, with Task 1 clearing the new PLR0917 findings beforehand. The hooks already run `uv run ruff`, so there is no second pin.
- **"`make verify` passes":** checked at the end of every task and in the final gate.

All three criteria are covered.
