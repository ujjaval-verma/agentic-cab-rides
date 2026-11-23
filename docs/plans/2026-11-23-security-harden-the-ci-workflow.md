# Plan: Issue #21, harden the CI workflow (least privilege, pinned actions, secret scanning)

## Goal

Harden [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) without changing what the existing `verify` job runs (`make verify` and `make transcripts-ci`):

- Grant the workflow token read access to repo contents only.
- Pin every action to a full commit SHA with a version comment, upgraded to its current major.
- Add a `secret-scan` job that runs gitleaks over the full git history on push to `main` and on PRs.
- Publish a security policy that says this is a prototype, that nothing is deployed, and how to report issues.

Facts checked against the current tree:

- `ci.yml` has no `permissions:` block.
- It uses `actions/checkout@v4` and `astral-sh/setup-uv@v3` (with `enable-cache: true`). Neither is SHA-pinned.
- The triggers are `push: branches: [main]` and `pull_request`.
- There are no other files under `.github/`.

## Acceptance criteria

- `.github/workflows/ci.yml` declares `permissions: contents: read` at workflow level.
- Every third-party action is pinned to a full commit SHA with a version comment; actions upgraded to current majors.
- A gitleaks (or equivalent) job scans full history on push to main and on PRs; current history passes.
- `SECURITY.md` states this is a prototype, how to report issues, and that no deployment exists.

## Invariants touched

- **12 (No `--no-verify`).** No pre-commit hook changes.
  - The `no-stray-root-md` hook only matches top-level `^[^/]+\.md$` files that are added or renamed, outside `CLAUDE.md` and `README.md`. A root `SECURITY.md` would be rejected, so the policy goes in `.github/SECURITY.md`. GitHub recognises that location, and the hook passes unchanged.
  - Every commit goes through the normal pre-commit and pre-push hooks. `check-yaml` validates `ci.yml`. `docs-links` checks the new markdown links.
- **13 (No `TODO`/`FIXME`/`XXX`).** The new test file must not contain `TODO`, `FIXME`, `XXX` or `HACK`. The `no-todo-in-code` hook greps `tests/` for all four, and ruff `FIX` stays green.
- **14 (No `print()`).** The new test uses asserts only, so ruff `T20` stays green.
- **15 (Public repo).** This change strengthens it.
  - gitleaks runs with `--redact`, so a finding is never echoed into public CI logs.
  - `SECURITY.md` contains no email or personal details. Reports go through GitHub private vulnerability reporting.
  - `scripts/check_secrets.py` keeps its pre-commit role.
  - The current tree only holds placeholder or regex text (`sk-ant-...` in `README.md`, regex literals in `check_secrets.py`). Neither is a full-length token, so no false positive is expected.
- **Invariants 1–11** are not touched. There are no `src/` changes.

## Tasks

**Task 1: failing structural test for the workflow.**
- Files: `tests/unit/test_ci_workflow.py` (new).
- Failing test first. Load `.github/workflows/ci.yml` with `yaml.safe_load` (pyyaml is already a runtime dependency, and other unit tests import it). Assert:
  - (a) The top-level `permissions == {"contents": "read"}`, and no job declares its own `permissions`.
  - (b) On the raw text, every line containing `uses:` matches `^\s*-?\s*uses:\s*[\w.-]+/[\w./-]+@[0-9a-f]{40}\s+#\s*v\d+(\.\d+){0,2}\s*$`, and at least one such line exists.
  - (c) A job `secret-scan` exists. Its `actions/checkout` step has `with.fetch-depth == 0`. One of its `run:` steps contains `gitleaks git` and `--redact`.
  - (d) The triggers include `push.branches == ["main"]` and a `pull_request` key.
- PyYAML parses `on:` as boolean `True`, so read `doc.get("on", doc.get(True))`.
- Resolve the path from `Path(__file__).resolve().parents[2]`.
- All four assertions fail on the current file. The test runs in `make test-fast`, so it runs in CI too. Pyright runs in basic mode on `tests/`, so annotate the loaded document as `dict[str, Any]`.

**Task 2: least privilege.**
- Files: `.github/workflows/ci.yml`.
- Add a workflow-level block, `permissions:` with `contents: read`, between `on:` and `concurrency:`. No job gets a job-level override.
- Add `persist-credentials: false` to every `actions/checkout` step. That input still exists in checkout v7.
- Assertion (a) turns green.

**Task 3: pin and upgrade actions.**
- Files: `.github/workflows/ci.yml`.
- Resolve each pin with `gh api` and do not guess. Re-run these at implementation time in case a newer release has landed:
  - `gh api repos/actions/checkout/releases/latest --jq .tag_name` currently returns `v7.0.1`.
  - `gh api repos/actions/checkout/git/ref/tags/v7.0.1 --jq '.object.type + " " + .object.sha'` currently returns `commit 3d3c42e5aac5ba805825da76410c181273ba90b1`.
  - The same two calls for `astral-sh/setup-uv` currently return `v10.4.0` and `commit 1db35dd8eee11c5894872e0b383ae6c406cf738d`.
- If `.object.type` is ever `tag` (an annotated tag), dereference it with `gh api repos/<owner>/<repo>/git/tags/<sha> --jq .object.sha` and pin that commit SHA.
- Write each pin as:
  - `uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1`
  - `uses: astral-sh/setup-uv@1db35dd8eee11c5894872e0b383ae6c406cf738d # v10.4.0`
- Input compatibility, checked against `action.yml` at v10.4.0 with `gh api "repos/astral-sh/setup-uv/contents/action.yml?ref=v10.4.0" -H "Accept: application/vnd.github.raw"`:
  - `enable-cache` is still valid, but its default is now `auto`. Keep `enable-cache: true` explicit.
  - The action now runs on `node24`, which GitHub-hosted `ubuntu-latest` supports.
  - `uv python install` and `uv sync --frozen` need no change.
- Assertion (b) turns green. The PR's green `verify` check shows the upgraded actions still work.

**Task 4: `secret-scan` job.**
- Files: `.github/workflows/ci.yml`.
- Add job `secret-scan`: `runs-on: ubuntu-latest`, `timeout-minutes: 5`. It inherits the workflow triggers and the `contents: read` permission. It has no `continue-on-error`.
- Steps:
  1. Pinned `actions/checkout` (same SHA as Task 3) with `fetch-depth: 0` and `persist-credentials: false`.
  2. Install the gitleaks CLI from a pinned release, hard-coding the version and the tarball sha256:
     - `gh api repos/gitleaks/gitleaks/releases/latest --jq .tag_name` currently returns `v8.30.1`.
     - `gh api repos/gitleaks/gitleaks/releases/latest --jq '.assets[] | select(.name=="gitleaks_8.30.1_linux_x64.tar.gz") | .digest'` currently returns `sha256:551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb`.
     - In the step, download the tarball from `https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/` and run `echo "<sha256>  gitleaks_8.30.1_linux_x64.tar.gz" | sha256sum --check`, then `tar -xzf` and run `./gitleaks version`. This runs on the runner, not locally.
     - Hard-coding the digest is stronger than trusting the release's own checksums file.
  3. Run `./gitleaks git --redact --verbose --exit-code 1 .`. `git` is the history subcommand from v8.19 onward. With no `--log-opts`, it scans every reachable commit.
- Use the CLI rather than `gitleaks/gitleaks-action`. The action wants `GITHUB_TOKEN` and calls the API to comment on PRs, which conflicts with `contents: read`, and it would add one more third-party action to pin. The CLI needs no token.
- Assertions (c) and (d) turn green.

**Task 5: show that current history passes. The evidence comes from CI.**
- Files: `.gitleaks.toml` (new, only if CI reports a false positive).
- No credible Python-installable equivalent of gitleaks that scans git history can be run with `uv tool run`. `detect-secrets` scans files, not history, and the Python trufflehog v2 is deprecated. So the local check is deferred, and the PR's `secret-scan` check is the evidence.
- Evidence to record:
  - `gh pr checks <pr>` shows `secret-scan` green.
  - `gh run view <run-id> --log --job <secret-scan-job-id>` contains gitleaks' `commits scanned` line and `no leaks found`.
  - The scanned-commit count is plausible against `git rev-list --count HEAD`.
- Local supporting check: `uv run python scripts/check_secrets.py` (no args and nothing staged, so it scans every tracked file) exits 0.
- If gitleaks reports a false positive:
  - Add a `.gitleaks.toml` with `[extend] useDefault = true` and a narrowly scoped `[allowlist]` (exact path plus regex), and point the run step at it with `--config .gitleaks.toml`.
  - Do not use blanket path excludes.
  - Push again until the check is green.
- If gitleaks finds a **real** credential, stop and escalate to the human. Key rotation and any history rewrite are human decisions.

**Task 6: security policy.**
- Files: `.github/SECURITY.md` (new) and `tests/unit/test_ci_workflow.py` (extended).
- Failing test first. Assert that `.github/SECURITY.md` exists and, case-insensitively, contains `prototype`, `no deployment`, and `report`. Also assert that a root `SECURITY.md` does not exist, which documents the hook-driven placement.
- Content:
  - This is a prototype with no deployment: no hosted instance, no production environment, no user data.
  - Supported version: `main` only.
  - How to report: GitHub private vulnerability reporting ("Report a vulnerability" on the Security tab), not public issues.
  - A leaked key will be revoked and rotated. Public history cannot be un-published.
- Use relative links only, such as `../CLAUDE.md` (invariant 15) and `../docs/ARCHITECTURE.md` (its "Not deployed today" note). They must pass the `docs-links` hook (`scripts/check_links.py`).

**Task 7: register the durable doc.**
- Files: `CLAUDE.md` and `README.md`.
- `CLAUDE.md`: add one row to the "Durable" table for `.github/SECURITY.md`. Invariant text is unchanged.
- `README.md`: add one "Where things live" bullet that links `.github/SECURITY.md`.
- Check first: `docs-links` passes only once Task 6's file exists. Before this task, `SECURITY.md` appears in neither file.

**Task 8: verification gate.**
- Files: none.
- Run `make verify`.
- Run `uv run pre-commit run --all-files`. This covers `check-yaml`, `docs-links`, `no-secrets`, `no-stray-root-md` and `no-todo-in-code`.
- Push the branch and open the PR. Confirm with `gh pr checks` that both `verify` and `secret-scan` are green. Capture the Task 5 log evidence.

## Risk

`risk: none`

- `verify` runs the same commands. Only the action versions underneath it change.
- `secret-scan` is additive.
- Coordination note: #20 also edits `ci.yml` to add an `audit` job. Whichever PR merges second must rebase. Any `uses:` that #20 adds must follow the SHA-plus-comment form, and the Task 1 test enforces that.

## Out of scope

- Changing any pre-commit hook, including `no-stray-root-md` and the hook `rev:` pins.
- Dependabot or Renovate config, and the CI `audit` job. Both are planned in #20, so this PR adds no Dependabot file and no follow-up entry for it.
- Adding gitleaks to pre-commit or to `make verify`.
- Rewriting history or rotating keys, unless Task 5 finds a real secret, which escalates.
- Repo settings: branch protection, making `secret-scan` required, and enabling private vulnerability reporting. These are a human's job; flag them in the PR description.
- CodeQL, SBOM, OpenSSF Scorecard.
- Any `src/` change.

## Self-check

AC1 is covered by Tasks 1 and 2, AC2 by Tasks 1 and 3, AC3 by Tasks 1, 4 and 5 (CI log as evidence), and AC4 by Task 6 (placed at `.github/SECURITY.md` because of the root-markdown hook), with Tasks 7 and 8 for wiring and verification.
