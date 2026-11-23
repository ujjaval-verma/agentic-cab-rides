# Plan: validate `session_id` at every boundary to prevent path escape (#23)

## Goal

`session_id` should only enter the system as a canonical ULID, through one shared `SessionId` validator. That covers web request bodies, query and path parameters, the `scripts/replay.py` argument and `make replay SESSION=`. Separately, every place that turns a `session_id` into a file path should resolve the final path and refuse it unless it sits directly inside the configured base dir. A bad value reaching a path builder, whether from an unvalidated caller or a later regression, then fails closed instead of writing outside `logs/`.

## Acceptance criteria

- A single `SessionId` validator (ULID format) is applied at the web request model, CLI args, and `make replay SESSION=`; invalid values are rejected (HTTP 422 / CLI error).
- Path builders resolve the final path and refuse anything outside the configured base dir.
- Tests cover absolute paths, `..` segments, and a valid ULID.

## Validation decisions

- **Exact format:** `SESSION_ID_PATTERN = r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$"`. That is Crockford base32, 26 characters, first character 0–7 so the 128-bit value cannot overflow, and no `I L O U`.
- **Case policy: uppercase only.** `core/ids.py::_new()` returns `str(ULID())` from `python-ulid`, which is always uppercase. Every `session_id` in `transcripts/*.md` is uppercase too (for example `01KS5HMK9QJZ8PCJ92B9VM7BRA` in `transcripts/01_happy_path.md`). Lowercase is rejected rather than normalised. On a case-insensitive filesystem, `01abc…` and `01ABC…` would point at the same log file, so only the canonical form is allowed.
- **Single source:** `SessionId = Annotated[str, StringConstraints(pattern=SESSION_ID_PATTERN)]` lives in `core/ids.py`. `parse_session_id(raw: str) -> str` is `TypeAdapter(SessionId).validate_python(raw)` and raises `ValueError`. Web gets HTTP 422 from FastAPI's handling of the pydantic type. The CLI and the script call `parse_session_id`.
- **Scenario session IDs are already ULIDs.** `scenarios/runner.py` uses `new_session_id()`, and no file under `scenarios/*.yaml` contains a `session_id`. All six committed transcripts carry valid uppercase ULIDs, so no transcript needs re-rendering. One test pins this by parsing every `"session_id": "…"` in `transcripts/*.md` through `parse_session_id`. The same reasoning covers existing JSONL and transcript fixtures: any `logs/<id>.jsonl` written by the REPL, web or scenarios was named by `new_session_id()`, so it still validates.
- **Path builders check where the file lands, not the ULID format.** Over 100 unit and contract tests pass `session_id="S"`, `"S-redact-1"` and similar through tools into `JSONLActionLog`; `tests/unit/test_action_log.py` even reads back `tmp_path / "S.jsonl"`. The issue asks path builders to resolve the path and refuse escapes, not to re-check the format. So the builder enforces `resolved.parent == base.resolve()`. That rejects absolute paths, `..`, nested segments and symlink escapes, and leaves those tests green without touching them. Format checking stays at the input boundaries (criterion 1).
- **The web UI must change too.** `web/static/app.js` creates its session ID with `crypto.randomUUID()`. A UUID would get a 422 once validation is on, so the browser has to obtain a server-minted ULID instead (Task 6).
- **`make transcripts-ci`** runs `render-transcripts --dry-run --assert-invariants`, which only validates the YAML schema. Nothing it reads changes, so it stays green. **`make verify`** (fmt-check, lint, typecheck, test-fast) is the gate for every task. pyright strict already covers `core/`, `tools/` and `adapters/`, and the new code must pass it with no new ignores.

## Invariants touched

- **1. Layer direction.** `SessionId` and `parse_session_id` go in `core/ids.py`. The new path helper goes in `core/paths.py`. `core/` is the shared kernel at the bottom of the graph, imported by every layer already:
  - `tools/*` and `agent/chat.py` import `core.ids`.
  - adapters import `core.types`.
  - `web/app.py` imports `core.ids` and `core.ports`.
  - `scripts/replay.py` already imports from `agentic_cab`.

  `core/paths.py` imports only `pathlib`, so it adds no new edges. Web, CLI, scripts, the scenario runner and `adapters/action_log.py` all import downward. Nothing in `core/` imports `web`, `agent` or `adapters`.
- **6 and 7. Action log and persistence split.** The file names stay `logs/<session_id>.jsonl` and `logs/<session_id>.chat.jsonl`. The four-row log shape is unchanged. Only how the path is built changes.
- **9. Determinism.** No clock or RNG change, and transcripts are not re-rendered.
- **11. IDs are ULIDs.** This change enforces the invariant at the trust boundary, where before it held only by convention. Generation stays in one place, `core/ids.py::new_session_id`. The web UI stops generating UUIDs and asks the server for an ID instead. The `ARCHITECTURE.md` audit row is updated (Task 8).
- **13 and 14.** No `TODO`. `scripts/replay.py` keeps writing errors with `sys.stderr.write`, which scripts are allowed to do.

## Tasks

Each task writes its failing test first, then the code, then runs `make verify`. One commit per task.

1. **`SessionId` validator.** Files: `src/agentic_cab/core/ids.py`, `tests/unit/test_ids.py`.
   - Failing test first: `test_session_id_validation`, parametrised.
     - Accepts: `new_session_id()` output and the literal `01KS5HMK9QJZ8PCJ92B9VM7BRA`.
     - Rejects: `""`, `"../etc/passwd"`, `"/etc/passwd"`, `"..\\x"`, the lowercase form of a valid ULID, 25 and 27 characters, first character `8`, any of `I L O U`, and a UUID.
   - Also add `test_committed_transcript_session_ids_validate`, which scans `transcripts/*.md`.
   - Add `SESSION_ID_PATTERN`, `SessionId` and `parse_session_id`.

2. **Path containment helper.** Files: `src/agentic_cab/core/paths.py` (new), `tests/unit/test_paths.py` (new).
   - Failing test first: `session_log_path` and `session_chat_path` raise `UnsafePathError` (a `ValueError` subclass) for:
     - an absolute session_id (`/tmp/x`),
     - `../escape` and `a/../../b`,
     - a nested `a/b`,
     - a symlink inside `tmp_path` pointing outside it.

     With a valid ULID, they return `tmp_path/<ULID>.jsonl` and `.chat.jsonl`.
   - Implementation: `candidate.resolve()` with `parent == base.resolve()`.

3. **Action log uses the helper.** Files: `src/agentic_cab/adapters/action_log.py`, `tests/unit/test_action_log.py`.
   - Failing test first: `emit` with `session_id` set to `"../escape"` or to an absolute path raises `UnsafePathError` and creates no file outside `tmp_path`. `read("../escape")` raises too. A valid ULID round-trips.
   - `_path` delegates to `core.paths.session_log_path`. The existing `"S"` tests stay untouched and green.

4. **Chat-history writers use the helper.** Files: `src/agentic_cab/agent/chat.py`, `src/agentic_cab/scenarios/runner.py`, `src/agentic_cab/__main__.py`, `tests/unit/test_paths.py`.
   - Failing test first: `test_no_adhoc_session_path_joins`. It scans `src/` and `scripts/` and fails on any f-string `{session_id}.` or `{sid}.` file-name join outside `core/paths.py`. It is red at this point, because `chat.py`, `runner.py`, `__main__.py` and `scripts/replay.py` all still join paths by hand.
   - Replace those joins with `session_chat_path`. The test turns green once Task 7 also converts `scripts/replay.py` (mark it `xfail(strict=True)` until then, or land it with Task 7; either way it is never skipped at the end).
   - `fixtures/geocode` paths are sha1-derived, not built from a session ID, so they stay untouched.

5. **Web boundary.** Files: `src/agentic_cab/web/app.py`, `tests/unit/test_web_app_routes.py`.
   - Failing tests first:
     - `POST /chat` with `session_id` set to `"../../etc/x"`, `"/abs"`, a lowercase ULID or a UUID returns 422 and creates no file under `log_dir`.
     - The same inputs on `GET /events?session_id=` and `GET /sessions/{id}/log` return 422.
     - A valid ULID returns 200.
     - New `POST /sessions` returns `{"session_id": <value that passes parse_session_id>}`.
   - Change `ChatRequest.session_id` to `SessionId`, and type the `/events` query and `/sessions/{session_id}/log` path parameters as `SessionId`. Add `POST /sessions` backed by `new_session_id()`.
   - Existing route tests that post non-ULID literals to `/chat` or GET `/sessions/.../log` switch to ULIDs, with their echo assertions updated. Direct `_get_session("…")` calls are not request boundaries and stay unchanged. The tests now feed the gate valid input; the gate itself does not get looser.

6. **Browser obtains a server-minted ULID.** Files: `src/agentic_cab/web/static/app.js`, `tests/unit/test_web_app_routes.py`.
   - Failing test first: `test_app_js_uses_server_minted_session_id`. It asserts that `app.js` no longer contains `randomUUID` and does reference `"/sessions"`. This is a text check, following the Makefile-text precedent in `tests/unit/test_audit_deps.py`.
   - On load, `app.js` calls `POST /sessions` and only then opens the `EventSource` and enables the form.

7. **Replay CLI and Makefile.** Files: `scripts/replay.py`, `Makefile` (the `replay` target), `tests/unit/test_replay_script.py` (new).
   - Failing tests first. Load the script with `importlib`, as `test_audit_deps.py` does, and `monkeypatch` `sys.argv` and the working directory to `tmp_path`:
     - `../x`, `/etc/passwd` and a lowercase ULID each exit 2 with `invalid session_id` on stderr and read nothing.
     - A valid ULID with no log exits 1, as today.
     - A valid ULID with a seeded `logs/<ULID>.jsonl` exits 0.
     - A Makefile-text assertion that the recipe passes `"$(SESSION)"` quoted.
   - `replay.py` calls `parse_session_id`, then uses `session_log_path` and `session_chat_path`. The Makefile quotes `"$(SESSION)"` so shell metacharacters cannot get past the validator. `transcripts-ci` and `verify` recipes are untouched.

8. **Docs.** Files: `docs/ARCHITECTURE.md`.
   - No test (docs only); checked by `make verify` and the pre-commit hooks.
   - Invariant 11 row reads `core/ids.py` (generation and `SessionId` boundary validation) plus `core/paths.py` (path containment).
   - Fix the Web runtime row: "one `session_id` cookie" becomes "one server-minted `session_id` per tab (`POST /sessions`)".
   - Note boundary validation in the "Action log + chat history" bullet.

risk: none

`session_id` is not an input to the confirm or cancel idempotency keys, the fingerprint or the confirmation gate, and no locking changes.

## Out of scope

- Validating the other ID kinds (`turn_id`, `action_id`, `draft_id`, `subject_id` on `POST /confirmations/{id}`). None of them is joined into a path today; follow-up candidate for `docs/FOLLOWUPS.md`.
- Making `JSONLActionLog` or the tools layer enforce the ULID format, which would mean rewriting more than 100 fixtures that use `"S"`.
- Adding a `--session-id` option to `repl` or `run-scenario`. Neither exists today, and both mint their own ID.
- The `run-scenario` subcommand that `CLAUDE.md` mentions but `__main__.py` does not implement.
- Re-rendering transcripts, and any change to the `transcripts-ci` or `verify` gates or the hooks.
- Hardening the scenario runner's DB path built from the scenario name. That value comes from trusted repo YAML, not user input.

Self-check:
- **Criterion 1 (validator applied at every boundary, 422 / CLI error):** Task 1 builds the validator, Task 5 applies it on the web side, Task 7 on replay and Makefile, and Task 6 keeps the web UI working with it.
- **Criterion 2 (path builders resolve and refuse escapes):** Task 2 builds the helper; Tasks 3, 4 and 7 apply it to every builder.
- **Criterion 3 (tests for absolute paths, `..` and a valid ULID):** Tasks 1, 2, 3, 5 and 7.
