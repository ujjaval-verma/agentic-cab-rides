# Plan: upgrade locked dependencies that have published advisories

## Goal
Clear the published advisories against `uv.lock` by moving five transitive packages to their fixed releases. Only the lock file should change. No direct constraint in [pyproject.toml](../../pyproject.toml) blocks any of the fixes, so a targeted `uv lock --upgrade-package` should be enough.

The issue's list was re-checked against current `main` on 2026-11-23 with `pip-audit` run over `uv export --frozen --all-groups --no-hashes --no-emit-project`. It still reports **46 known vulnerabilities in 5 packages**. One correction to the issue text: filelock's fix needs a **major** bump.

| Package | Locked | Highest fix version needed | Pulled in by | Group |
|---|---|---|---|---|
| starlette | 1.0.0 | 1.3.1 | fastapi 0.136.1, sse-starlette 3.4.4 | runtime |
| anyio | 4.13.0 | 4.14.2 | anthropic, httpx, starlette, sse-starlette, watchfiles | runtime |
| certifi | 2026.4.22 | 2026.10.18 | httpx, httpcore | runtime |
| virtualenv | 21.3.3 | 21.14.10 | pre-commit 4.6.0 | dev |
| filelock | 3.29.0 | **4.2.1** (3.x → 4.x; the issue did not give a floor) | virtualenv, python-discovery | dev |

## Acceptance criteria
- `uv export --frozen --all-groups --no-hashes | uvx pip-audit -r /dev/stdin` reports zero known vulnerabilities (or each remaining one is listed with a justification in `docs/FOLLOWUPS.md`).
- `uv.lock` updated; `make verify` and `make transcripts-ci` pass.

## Invariants touched
- **2 (confirmation gate is structurally enforced):** Preserved. The web `WebConfirmationChannel` streams prompts through `sse_starlette.sse.EventSourceResponse` ([src/agentic_cab/web/app.py](../../src/agentic_cab/web/app.py)), which runs on starlette and anyio. The route tests in [tests/unit/test_web_app_routes.py](../../tests/unit/test_web_app_routes.py) run inside `make verify` and must stay green after the bump. No gate code changes.
- **9 (determinism in scenarios):** Preserved. No change to the model pin, RNG or clock. `make transcripts-ci` checks the scenario invariants without re-rendering transcripts.
- **12 (no `--no-verify`):** Preserved. pre-commit builds its hook environments with virtualenv and filelock. After the bump, the commit goes through the real hooks (run `pre-commit run --all-files` first) and never uses `--no-verify`. [.pre-commit-config.yaml](../../.pre-commit-config.yaml) is not edited.
- **15 (public repo):** Preserved. Only `uv.lock` (plus [docs/FOLLOWUPS.md](../FOLLOWUPS.md) if needed) is committed. The exported requirements file used for the audit stays in gitignored `docs/scratch/`.

## Tasks
1. **Record the failing audit (baseline).** No file change.
   - *Failing check:* run `uv export --frozen --all-groups --no-hashes --no-emit-project -o docs/scratch/requirements-audit.txt`, then `uv tool run pip-audit --disable-pip --no-deps -r docs/scratch/requirements-audit.txt`. Today this exits 1 with 46 vulnerabilities in 5 packages, as in the table above.
   - *Why `--no-emit-project`:* the criterion's pipeline exports the project itself as `-e .`, which pip-audit would have to resolve and build. The project has no advisories of its own, so leaving it out audits the same third-party set without the build step.

2. **Upgrade the five packages in the lock.** File: [uv.lock](../../uv.lock).
   - Run `uv lock --upgrade-package starlette --upgrade-package anyio --upgrade-package certifi --upgrade-package virtualenv --upgrade-package filelock`.
   - Confirm in `uv.lock` that the new versions meet the floors: starlette ≥ 1.3.1, anyio ≥ 4.14.2, certifi ≥ 2026.10.18, virtualenv ≥ 21.14.10, filelock ≥ 4.2.1.
   - If fastapi 0.136.1 or sse-starlette 3.4.4 caps starlette below 1.3.1, add `--upgrade-package fastapi` / `--upgrade-package sse-starlette`. Do the same with `python-discovery` if it holds filelock below 4.
   - Do **not** run a blanket `uv lock --upgrade`. Do not touch `pyproject.toml` unless the resolver cannot otherwise reach a fixed version.
   - Run `git diff --stat uv.lock` and check that only the intended packages (plus any forced co-upgrades) moved.

3. **Sync, then run the runtime and dev gates.** No file change.
   - Run `uv sync --frozen` (the CI install step).
   - *Checks:* `make verify` (fmt-check, ruff, pyright, test-fast, including the web SSE route tests) and `make transcripts-ci` must both pass.
   - Then run `uv run pre-commit run --all-files`. This exercises the new virtualenv and filelock through real hook environment creation. If anything fails, fix it with a further lock change, not by changing a hook.

4. **Re-run the audit (should now pass).** No file change unless something is left over.
   - Repeat both audit commands from task 1. They must report "No known vulnerabilities found".
   - If any advisory cannot be cleared (no fixed release, or a resolver conflict), add one entry per advisory to [docs/FOLLOWUPS.md](../FOLLOWUPS.md) under a new `## Security / dependencies` heading, in the file's format: `- [ ] <package> <version> <advisory id> — <why it cannot be upgraded / why it is not reachable> — dependency-advisory sweep`.

5. **Commit.** Stage `uv.lock`, plus `docs/FOLLOWUPS.md` only if task 4 added entries. Commit with hooks on, using the message `chore(deps): upgrade starlette, anyio, certifi, virtualenv, filelock past published advisories`. If FOLLOWUPS changed, put that in a separate `docs:` commit, following one concern per commit.

risk: none

## Out of scope
- Adding a `pip-audit` step or Makefile target to CI ([.github/workflows/ci.yml](../../.github/workflows/ci.yml)). Worth filing as its own follow-up issue.
- Adding `[tool.uv] constraint-dependencies` floors to stop a downgrade later.
- Bumping direct-dependency floors in `pyproject.toml`, or upgrading packages with no advisory.
- Any application code change. If the starlette 1.3 or anyio 4.14 upgrade breaks something, that gets its own fix, not a quiet code change inside this lock bump.
- Re-rendering transcripts (`make transcripts`).

Self-check: task 2 covers the "`uv.lock` updated" criterion, task 3 covers `make verify` and `make transcripts-ci`, and tasks 1 and 4 cover the zero-vulnerability audit (or FOLLOWUPS justifications), so every criterion is covered.
